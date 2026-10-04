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

FIFO is enforced **per endpoint**. A saturated endpoint must never block an unrelated endpoint with
available capacity: the scheduler dispatches the oldest eligible queued request within each endpoint,
and chooses among endpoints by each endpoint's oldest eligible queued request. (Settled)

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
- Provider RPM/TPM windows; TPM/token reservation; shared provider-account quota windows; project
  budgets; retries/cooldown orchestration — later phases, to be reserved together with endpoint
  capacity.
- Key rotation for the queue-content encryption key (deferred unless safely straightforward).
