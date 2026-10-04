# AetherGate v2 — Scheduler and Queueing

Living document. Derived from the audit. Distinguish **settled** / **direction** / **deferred**.

## Purpose

Safely schedule concurrent requests against provider/endpoint limitations. When capacity is
temporarily unavailable, eligible requests queue within bounded deadlines rather than being
rejected. Scheduling must remain correct with multiple workers. (Settled)

## Contract

For an endpoint with two concurrent slots and six valid arrivals A-F: A/B dispatch first and C-F
queue. Releasing one completed slot admits the next eligible queued request only when every other
constraint also permits it. FIFO describes admission/dispatch order, not completion order. (Settled)

FIFO is enforced **per effective admission scope** (`endpoint + quota group + project`). A saturated
endpoint must never block an unrelated endpoint with available capacity: the scheduler dispatches
the oldest eligible queued request within each scope, and chooses among scopes by each scope's oldest
eligible queued request. Adding the project to the scope means a budget-blocked project does not
head-of-line block another project that shares the same endpoint/quota resources. (Settled)

## Lifecycle

```
validated -> queued -> reserved -> dispatched -> streaming -> succeeded/failed/cancelled/expired
```

Also represent `outcome_unknown` for ambiguous failures. (Direction)

## Phase 1 — implemented (durable endpoint concurrency)

Scheduler phase 1 (AGV2-005) enforces **physical endpoint concurrency only**. It establishes durable
queueing, multi-worker ownership, and encrypted queued content. It deliberately does **not** enforce
provider RPM/TPM, token reservations, shared-account quota windows, project budgets, or retry/cooldown;
those are later phases and must eventually be reserved together with endpoint capacity.

Implemented behavior:

- `POST /v1/chat/completions` validates/resolves, enqueues an **encrypted** request durably, then the
  API waits for a terminal result (non-stream) or streams the encrypted event sequence (SSE). The API
  process never calls the provider adapter directly.
- A separate worker process (`python -m aethergate.worker`) claims the oldest eligible queued request
  (FIFO, `FOR UPDATE SKIP LOCKED`), re-resolves/revalidates the route immediately before reservation,
  locks the endpoint row, counts active reservations against `endpoint.max_concurrency`, then
  transactionally creates a reservation + execution attempt with a lease and fencing token. Dispatch
  intent is recorded durably (a separate commit) **before** contacting upstream.
- Inference runs outside any database transaction; the worker publishes an encrypted result or
  encrypted per-request stream events, then settles terminal state and releases capacity only when the
  outcome is known.
- FIFO is admission/dispatch order for requests competing for the same endpoint, enforced per
  endpoint. A saturated endpoint does not block an unrelated endpoint with available capacity; a
  worker iterates endpoints (ordered by each endpoint's oldest eligible queued request) and skips
  saturated ones rather than returning early.

Schema: migration `0003` adds `endpoints.max_concurrency` and tables `inference_requests`,
`reservations`, `execution_attempts`, and `stream_events`. Prompt/message, completion, and stream-event
content is encrypted (Fernet, key outside PostgreSQL); scheduling metadata is plaintext and carries no
content.

## Phase 2 — correctness hardening (implemented)

AGV2-006 hardened phase-1 correctness before quota expansion:

- **Lease heartbeat.** A healthy dispatched/streaming request renews both its request and attempt
  lease for the current fencing token. Renewal is conditional on worker identity/fence/active state,
  so a stale worker cannot renew after ownership changes. Heartbeat stops promptly when execution
  terminates. Settings are validated (`worker_heartbeat_seconds < worker_lease_seconds`, both
  positive); no long-lived DB transaction spans provider inference.
- **Consistent recovery.** Post-dispatch lease expiry transitions the request **and** its active
  execution attempt to `outcome_unknown` together; the reservation stays held, the fence history
  stays auditable, and there is no automatic retry. Pre-dispatch (`reserved`) expiry is reclaimed
  safely (requeued, or expired if past its queue-wait deadline). Row locks make concurrent recovery
  loops idempotent.
- **Per-endpoint FIFO / no head-of-line blocking.** See the contract above.
- **Atomic queue-cap admission.** Count-then-insert is serialized by a transaction-scoped PostgreSQL
  advisory lock (`ADMISSION_LOCK_KEY = 0x41475144`, "AGQD", a single global scope) so concurrent API
  admissions cannot exceed `queue_max_requests`. No process-local counter is used.
- **Queue-wait vs total lifetime.** `queue_max_wait_seconds` bounds how long a request may remain
  undispatched; `queue_total_lifetime_seconds` bounds end-to-end lifetime including execution. A
  queued request crossing its `queue_wait_until` expires without contacting upstream; a dispatched
  request is governed by total lifetime, not the queue-wait deadline. The API's wait uses the
  persisted deadline, and on deadline durably cancels any still-queued/reserved work.
- **Cancellation.** Client disconnect/cancel reaches durable state: queued/reserved requests are
  terminally cancelled (never dispatch, reservation released); dispatched/streaming requests are
  flagged for the owning worker, which settles cancelled or `outcome_unknown` conservatively.
  Streaming never synthesizes `finish_reason="stop"` for a failed/cancelled/expired/unknown stream.
- **Endpoint concurrency positivity.** `max_concurrency >= 1` is enforced in the domain entity, a
  database CHECK constraint (migration `0004`), and the dev seed.
- **Graceful worker shutdown.** SIGINT/SIGTERM stop new claiming; in-flight execution is allowed to
  finish (cooperative), and a hard stop that abandons dispatched work surfaces conservatively via
  lease recovery.
- **Explicit reconciliation.** `outcome_unknown` requests are inspected without content and
  reconciled (failed/cancelled/succeeded) only by an explicit operator action
  (`python -m aethergate.reconcile`), which records the action and releases the held reservation as
  part of the same operation. No generic "release all stuck slots" shortcut exists.

Schema: migration `0004` adds `endpoints` positive-concurrency CHECK and `inference_requests`
reconciliation metadata (`reconciled_state`, `reconciled_at`, `reconciled_by`).

## Phase 3 — shared provider-account request/token quotas (implemented)

AGV2-008 reserves shared quota capacity transactionally with endpoint physical capacity. It does
**not** add monetary project budgets (those need the accounting/pricing foundation and are still
deferred).

Quota model:

- A `QuotaGroup` belongs to exactly one `ProviderAccount` (`provider_account_id`, NOT NULL). A route
  may reference a group only when the route's provider account matches the group's; multiple
  aliases/endpoints/routes may share one group. No quota is inferred from URL or provider name. A
  route without a group is governed only by endpoint concurrency.
- A group holds one or more `QuotaLimit`s: `metric` (`requests`|`tokens`), positive `limit_units`,
  positive `window_seconds`, `enabled`, optional `name`. Multiple simultaneous request and token
  windows are supported without special-casing minute/day.
- Windows are **fixed and anchored deterministically to the UTC epoch**:
  `window_start = (epoch_seconds // window_seconds) * window_seconds`, range
  `[window_start, window_start + window_seconds)`. No sliding-window semantics are claimed.
- `QuotaWindow` is the authority row: `committed_units` + `reserved_units`, unique
  `(quota_limit_id, window_start)`. Admission evaluates
  `committed_units + reserved_units + requested_units <= limit_units` under row locks.
- `QuotaReservation` records each per-request reservation (`reserved`|`committed`|`released`),
  denormalized `metric`, and `reserved_units`/`committed_units`.

Request-quota semantics:

- A request unit is consumed when durable dispatch intent is committed (immediately before upstream
  contact). Queued/pre-dispatch-cancelled work consumes no request quota; once dispatch intent is
  durable, the unit stays consumed even if upstream later fails. One execution attempt == one
  request-unit commitment (retries still out of scope).

Token-quota semantics:

- Pre-dispatch, the adapter produces a conservative input estimate (`estimate_input_tokens`, no
  upstream contact) plus a bounded output reservation (`max_tokens` if the client supplies it, else
  the route's `default_output_tokens`). A token-quota request with no way to bound output is
  rejected explicitly rather than dispatched without a safe reservation.
- On success with trustworthy usage, reserved tokens settle to actual reported total usage (releasing
  unused units within the window). If actual usage exceeds the reservation, the overage is recorded
  honestly (committed may exceed the limit; nothing is truncated or hidden).
- On a post-dispatch failure/cancellation with unknown usage, the reserved amount is committed
  conservatively (never released, never invented). `outcome_unknown` keeps its token reservation
  until explicit reconciliation or window expiry; reconciliation commits (never creates) capacity.
  Pre-dispatch cancellation/reclaim releases token reservations (upstream never contacted).

Atomic admission and lock order (deterministic, tested). As of AGV2-011 the canonical order across
quota, budget, and endpoint admission is:

1. request / scheduling-scope row(s);
2. active price policy;
3. project budget policy / budget windows (stable ID order);
4. quota group (FOR UPDATE), then quota limits and quota-window rows in stable ID order;
5. endpoint row / physical concurrency;
6. request/attempt/reservation state.

If any required quota lacks capacity, none of the request's capacity is acquired; no DB transaction
remains open while waiting for a reset; multiple workers cannot oversubscribe a window. A request
that cannot fit even an empty token window is failed explicitly.

Provider feedback:

- The adapter boundary preserves safe structured feedback (`status_code`, `retry_after_seconds`); no
  raw headers or URLs leak. On provider 429 the group enters a cooldown (`cooldown_until`) so future
  work for that scope stays queued; the failed attempt remains a consumed request unit. If no
  reliable Retry-After is present, a small configurable conservative cooldown
  (`provider_429_cooldown_seconds`) is applied. No automatic retry occurs.

Eligibility/queue behavior:

- When a free physical slot exists but a quota window is exhausted, the request stays queued with a
  non-content `wait_reason` (e.g. `quota_window_exhausted`), no execution attempt is created, and the
  worker does not spin hot. An unrelated endpoint/group with capacity still dispatches. FIFO within
  the same endpoint + quota group + project scope is preserved.

Schema: migration `0005` adds `quota_groups.provider_account_id` (+ NOT NULL) and
`quota_groups.cooldown_until`; creates `quota_limits`, `quota_windows` (unique
`(quota_limit_id, window_start)`), and `quota_reservations`; adds
`route_bindings.default_output_tokens` and `inference_requests.wait_reason`.

## Phase 4 — accounting foundation (implemented)

AGV2-010 adds immutable pricing snapshots, authoritative usage records, optional project budget
policy, monetary budget reservations, an append-only ledger, and idempotent settlement. Full
semantics live in `docs/architecture/accounting.md`; the scheduler-relevant parts are summarized
here.

- **Pricing snapshot.** At the final admission/dispatch decision, the effective `PricePolicy` is
  captured into an immutable `PriceSnapshot` (referenced by `inference_requests.price_snapshot_id`).
  Editing a price policy never alters an existing snapshot; a request that never dispatches never
  creates priced usage.
- **Budget admission.** Budget evaluation participates in the same all-or-nothing admission
  transaction as request/token quota and endpoint physical capacity. `_evaluate_budget` locks (never
  mutates) the active price policy and budget windows, then reserves only when every constraint admits.
  Lock order: request/scope rows -> active price policy -> project budget policy/windows -> provider
  quota group/limit/windows -> endpoint physical capacity -> request/attempt/reservation mutations.
- **Snapshot lifecycle.** The effective price is captured at reservation (so budget reservations are
  priced deterministically) and becomes the immutable historical snapshot only for work that reaches
  durable dispatch intent; a pre-dispatch cancel/reclaim/dispatch-failure discards the snapshot so no
  orphan snapshot survives for work that never dispatched.
- **Budget wait metadata.** A budget-exhausted request stays queued with
  `wait_reason=budget_window_exhausted`, `wait_limit_metric="budget"`, and `next_eligible_at` (next
  window reset) — distinct from quota and endpoint-full waiting. Budget-blocked work holds no
  endpoint slot and no monetary reservation.
- **Fail-closed token budget.** A token-priced route with an active budget fails closed when the
  token estimator is unavailable (`budget_token_estimator_unavailable`) or output is unbounded
  (`budget_unbounded_output`). A request whose minimum reservation cannot fit an empty window fails
  with `budget_request_too_large`.
- **Settlement.** Success settles the budget reservation to actual (releasing unused), creates the
  `UsageRecord` and its `usage_debit` ledger entry idempotently. Unknown-usage post-dispatch
  failure/cancellation conservatively commits the reservation with no measured usage.
  `outcome_unknown` keeps the reservation held; explicit reconciliation conservatively commits it.
  Pre-dispatch cancel/reclaim releases the reservation completely.
- **No-budget / no-price path.** A route with no price policy, or a project with no active budget
  policy, is never monetarily blocked and creates no accounting rows.

Schema: migration `0007` creates `price_policies`, `price_snapshots`, `project_budget_policies`,
`budget_windows`, `budget_reservations`, `usage_records`, and `ledger_entries`; adds
`inference_requests.price_snapshot_id` (and its index) and `inference_requests.wait_limit_metric`;
adds CHECK constraints for currency format, positive budget/window amounts, nonnegative
reserved/committed amounts, supported reservation/ledger states/types, positive unit scale, and
idempotency uniqueness. Migration `0008` adds the one-enabled-price-policy-per-route partial unique
index, billing-unit-specific price-shape CHECK constraints, and relaxes
`budget_reservations.price_snapshot_id` to nullable for the snapshot lifecycle.

## Identity revalidation before dispatch (AGV2-012)

Before the worker resolves the route or reserves any capacity, it re-validates the request's
credential through the single identity/auth service (`authorize_for_dispatch`): project active,
principal active (and belonging to the project), credential active/not-expired/not-revoked, and
inference audience + `inference:invoke` scope. A request whose credential was revoked/expired while
queued terminates with an explicit safe `authorization_failed` state: it never contacts upstream, and
no quota/budget/endpoint reservation, price snapshot, usage record, or ledger entry survives. The
revalidation policy lives in one place (`aethergate/identity/service.py`), shared by the API
enqueue-time authentication and the worker pre-dispatch check, so the two never diverge.

## Conservative lease / recovery (phase 1 rules)

- A request whose lease expires while still `reserved` (dispatch intent not yet durable) is safe to
  requeue; its reservation is released and its attempt abandoned.
- A request whose lease expires after durable dispatch intent (`dispatched`/`streaming`) transitions to
  `outcome_unknown` and **keeps** its physical slot; it is never auto-retried or auto-released.
- A fencing token gates terminal settlement; a stale worker cannot commit terminal state after
  ownership changed.

## Admission (settled)

Reserve together, in a consistent lock order:

- configured requests-per-window,
- token allowances,
- shared-account limits,
- physical concurrency,
- project budgets.

Also:

- Use provider-specific token reservation rules; do not assume uniform input/output or daily resets.
- Validate request size and context/output limits before admission.
- Recheck revocation and disabled routes immediately before dispatch.
- Keep ingress abuse throttling separate from provider capacity queueing.
- Queue bounds: request count, payload bytes, per-principal share, max waiting time, max total
  lifetime. Reject work that cannot fit even an empty quota window.

## Ownership, leases, and fencing (settled)

- Persist ownership and reservation identifiers before dispatch.
- Use leases and fencing so multiple workers never own the same attempt.
- A lease expiring does **not** prove upstream inference stopped. Do not auto-release capacity or
  replay an ambiguous attempt until upstream cancellation/completion, supported idempotency, or a
  conservative recovery policy resolves it.
- No database transaction remains open while waiting for inference.

## Provider feedback and retries (settled)

- Honor provider feedback, documented reset windows, and `Retry-After`.
- Apply cooldown to the actual shared quota scope.
- Retry only eligible failures, with bounded attempts and jitter; failed upstream attempts can still
  consume quota.
- Never restart generation after content has already been delivered as if it were the original stream.

## Quota-authority failure

On quota-authority failure, stop new dispatch rather than reverting to local counters. A
high-availability database deployment must preserve acknowledged reservation state across promotion;
durability and reconciliation are part of the guarantee. (Settled)

## Honest guarantee

Enforcement of known configured limits and provider feedback — not a promise of zero provider `429`
when limits are undisclosed or other applications consume the same account outside the gateway.
Dedicated provider projects/accounts and routing all relevant traffic through the gateway make
enforcement more predictable. (Settled)

## Deferred

- Fairness/reordering mode *within* a single endpoint (beyond per-endpoint FIFO) — must be explicit
  if introduced. Cross-endpoint head-of-line blocking is already prevented (see the contract above).
- Caching/wake-up mechanism for empty-queue -> arrival signaling (added only after measurement).
- Project monetary budgets and pricing/accounting settlement — implemented in AGV2-010 (see
  `docs/architecture/accounting.md`).
- Retries (bounded, jittered) for eligible failures — later phase.
- Key rotation for the queue-content encryption key (deferred unless safely straightforward).
