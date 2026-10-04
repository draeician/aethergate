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
- FIFO is admission/dispatch order for requests competing for the same endpoint. Head-of-line
  implications are documented, not silently reordered.

Schema: migration `0003` adds `endpoints.max_concurrency` and tables `inference_requests`,
`reservations`, `execution_attempts`, and `stream_events`. Prompt/message, completion, and stream-event
content is encrypted (Fernet, key outside PostgreSQL); scheduling metadata is plaintext and carries no
content.

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

- Fairness/reordering mode for head-of-line blocking — must be explicit if introduced.
- Caching/wake-up mechanism for empty-queue -> arrival signaling (added only after measurement).
- Provider RPM/TPM windows; TPM/token reservation; shared provider-account quota windows; project
  budgets; retries/cooldown orchestration — later phases, to be reserved together with endpoint
  capacity.
- Key rotation for the queue-content encryption key (deferred unless safely straightforward).
