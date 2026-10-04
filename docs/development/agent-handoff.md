# AetherGate Agent Handoff

## Current State
- Branch: v2
- Starting commit: 7fe496a (pulled latest origin/v2; AGV2-007 queued)
- Implementation commit(s): `4865b66` `fix(scheduler): close residual invariants before quota expansion` (this task)

## Task Completed
AGV2-007 — close residual scheduler invariants before quota expansion. Made every coupled
request+attempt transition all-or-nothing (lease renewal, dispatch, settle, recovery
`outcome_unknown`), enforced total request lifetime independently of the API waiter, restricted
reconciliation to verifiable dispositions, exposed endpoint `max_concurrency` on the admin DTOs,
and documented the stream `[DONE]` marker. No schema change (migration `0004` already present);
logic-only.

## Scheduler invariant closure (AGV2-007) behavior
- **Atomic coupled transitions**: `renew_lease`, `mark_dispatched`, `settle`, and recovery
  `mark_outcome_unknown_expired` all transition request+attempt together. Any rowcount mismatch on
  the second half raises `SchedulerInvariantError` (new in `errors.py`) and rolls the transaction
  back, so a half-renewal / half-dispatch / half-settle is never persisted.
- **Lease ownership**: renewal now also verifies `worker_id` (not just fencing token), and clamps
  the renewed lease so it never outlives the request's `expires_at`. A heartbeat past the total
  lifetime returns `False` and stops, so an expired execution can never be kept alive.
- **Total-lifetime enforcement (worker-side)**: dispatch refuses a reserved request past
  `expires_at` (expires it, abandons its attempt, releases capacity); `_settle` overrides any
  local outcome to `expired`/`lifetime_exceeded` once `expires_at` has passed, so a result is
  never persisted for a request the client already abandoned. Ambiguous post-lifetime cases
  (worker dead / upstream still running) still surface as `outcome_unknown` via lease recovery.
- **Reconciliation restricted**: only `failed|cancelled` are reconcilable. `succeeded` is rejected
  (CLI + service) because an `outcome_unknown` request's result was never persisted.
- **Admin DTOs**: `EndpointCreate`/`EndpointRead`/`EndpointUpdate` now carry `max_concurrency`
  (positive via `ge=1`); `EndpointRead` exposes it.
- **Stream `[DONE]`**: documented as a transport termination marker (not a success signal); it is
  emitted on every stream ending, which the official OpenAI SDKs treat only as end-of-stream.

## Scheduler hardening behavior
- **Lease heartbeat**: `run_complete`/`run_stream` spawn a heartbeat task that renews both request
  and attempt leases for the current fencing token, conditional on worker/fence/state. A stale fence
  cannot renew. Heartbeat stops promptly on termination; no DB transaction spans provider inference.
  Settings validated so `worker_heartbeat_seconds < worker_lease_seconds` (both positive).
- **Consistent recovery**: post-dispatch lease expiry transitions request + active attempt to
  `outcome_unknown` together (row-locked, idempotent under concurrent recovery); reservation stays
  held; no auto-retry. Pre-dispatch (`reserved`) expiry is requeued, or expired if past queue-wait.
- **Per-endpoint FIFO**: workers iterate endpoints ordered by each endpoint's oldest eligible queued
  request and skip saturated endpoints, so a saturated endpoint never blocks an unrelated endpoint.
- **Atomic queue cap**: count-then-insert serialized by a PostgreSQL transaction advisory lock
  (`0x41475144`); no process-local counter.
- **Queue-wait vs lifetime**: queued requests crossing `queue_wait_until` expire without contacting
  upstream; dispatched requests are governed by total lifetime only. The API's wait uses the
  persisted `expires_at` and durably cancels still-queued work on deadline.
- **Cancellation/disconnect**: `request_cancellation` terminally cancels queued/reserved work
  (releasing capacity) and flags dispatched/streaming work for the worker. API disconnect schedules
  a detached durable-cancel task so uvicorn's response-cycle cancellation cannot abort the write.
  Streaming never emits `finish_reason="stop"` for a failed/cancelled/expired/unknown stream.
- **Graceful worker shutdown**: SIGINT/SIGTERM stop new claiming via a shutdown event; in-flight
  execution finishes cooperatively. A hard kill that abandons dispatched work surfaces as
  `outcome_unknown` via lease recovery.
- **Reconciliation**: `python -m aethergate.reconcile list|resolve` (and `scripts/dev/v2 reconcile`)
  inspects `outcome_unknown` requests without content and resolves them to `failed|cancelled` only
  on explicit operator action, recording `reconciled_state/at/by` and releasing the held
  reservation in the same transaction. `succeeded` is rejected. No bulk-release shortcut.

## Key files
- `src/aethergate/scheduler/repository.py` — admission lock, per-endpoint claim/list, atomic
  lease renewal / dispatch / settle / recovery (`mark_dispatched`, `settle`, `renew_lease`,
  `mark_outcome_unknown_expired`, `reclaim_expired_reserved`), queue-wait expiry,
  `reconcile_request`, `list_outcome_unknown`.
- `src/aethergate/scheduler/service.py` — heartbeat loop, worker-side total-lifetime enforcement,
  `request_cancellation`, `reconcile`, `list_outcome_unknown`, per-endpoint `claim_and_reserve`,
  persisted-deadline `wait_for_terminal`.
- `src/aethergate/errors.py` — `SchedulerInvariantError` (internal atomicity signal).
- `src/aethergate/api/openai_chat.py` — durable disconnect cancellation, no synthetic stop,
  `[DONE]` semantics.
- `src/aethergate/config.py` — `worker_heartbeat_seconds` + scheduler-timing validation.
- `src/aethergate/domain/entities.py` — `Endpoint.max_concurrency` positivity validator.
- `src/aethergate/persistence/models.py` — endpoints CHECK constraint + `reconciled_*` columns.
- `src/aethergate/worker.py` — cooperative shutdown; `src/aethergate/reconcile.py` — reconcile CLI.
- `src/aethergate/migrations/versions/0004_scheduler_hardening.py` — CHECK + reconciliation columns.
- `src/aethergate/devseed.py` — `--max-concurrency` positivity guard; `scripts/dev/v2` — `reconcile`.
- Tests: `tests/test_scheduler_hardening.py` (22 scenarios), `tests/test_worker.py`,
  `tests/test_migrations.py` (0003->0004 + empty->head), `tests/test_settings.py`,
  `tests/test_domain_entities.py`, `tests/test_persistence_models.py`.

## Nomnom verification (actual commands/results)
- Host = nomnom (`192.168.22.50/24`); `docker` = podman 4.9.3 + docker-compose 2.40.3. Backend =
  ollama `qwen3.8-2b-distill:Q6_K`; aliases `gpt-4`/`gpt-4-beta` (two independent endpoints).
- `scripts/dev/v2 test` -> **140 passed in 26s** (containerized, real PostgreSQL; +5 AGV2-007
  scenarios: worker lifetime without API waiter, heartbeat refuses past lifetime, reserved-past-
  lifetime not dispatched, reconcile rejects `succeeded`, half-renewal rolls back).
- `ruff check` on changed files -> all checks passed.
- Stream smoke test: stream succeeded and emitted `[DONE]`; `scripts/dev/v2 inspect` showed only
  terminal states and 0 active reservations.
- `reconcile resolve --disposition succeeded` -> rejected at the CLI (`{failed,cancelled}` only).
- Migration from empty DB through `0004` and `0003`->`0004` both verified.
- Real inference (official OpenAI SDK): non-stream and stream both succeeded against the current
  backend (stream reassembled 31 chunks).
- Six-request/two-slot invariant: 6 concurrent requests, all succeeded; DB attempt timestamps show
  exactly two in-flight at a time (pairs at t=27.20/27.33, 27.67/27.91, 28.24/28.58), FIFO preserved.
- **Lease heartbeat**: worker with lease=3s/heartbeat=0.5s ran a 15.7s inference (>4 lease periods);
  request stayed `dispatched` with a live lease throughout and succeeded; 0 `outcome_unknown`.
- **Dead-worker**: `death-worker` (lease=5s) dispatched a long request, was SIGKILLed; recovery marked
  request+attempt `outcome_unknown`, reservation stayed held (1), exactly 1 attempt (no retry);
  `reconcile resolve --disposition failed --by operator-test` -> `failed`, reservation 1->0.
- **Cross-endpoint no-HOL**: A (ollama, concurrency 1) held busy until t=14.52; B (beta, concurrency
  1) dispatched at t=03.71 without waiting for A's queued request; A2 (queued) dispatched at t=14.53
  after A freed (FIFO within A). (Upstream ollama serializes generation, but gateway dispatch is
  correct.)
- **Queue-cap race**: `queue_max_requests=3`, 20 concurrent admissions -> 3 committed queued, 17
  returned 503; queued count never exceeded 3.
- **Queue-wait expiry**: 3 requests past `queue_wait_until` expired via recovery with 0 execution
  attempts (never contacted upstream).
- **Client disconnect**: streaming request disconnected before dispatch -> `cancelled` (0 attempts),
  no `CancelledError` traceback in API logs.
- **Graceful stop**: SIGTERM to a worker -> "received termination signal / no longer claiming",
  exit code 0.
- Dynamic AetherGate test port during verification: **44785** (changes on every `up`; re-run
  `scripts/dev/v2 url`).

## Decisions
- Recovery is conservative: post-dispatch lease expiry is never auto-retried or auto-released;
  `outcome_unknown` keeps its slot until explicit reconciliation.
- Queue cap is a single global bound (not per-endpoint), hence a single global advisory lock.
- FIFO is per-endpoint; cross-endpoint ordering is by each endpoint's oldest eligible request only,
  solely to avoid unrelated-endpoint starvation.
- Cancellation on disconnect is scheduled as a detached task so ASGI response-cycle cancellation
  cannot abort the durable write.

## Deferred
- Provider RPM/TPM windows; TPM/token reservation; shared provider-account quota windows; project
  budgets; retries/cooldown orchestration — later phases, to be reserved together with endpoint
  capacity.
- Fairness/reordering within a single endpoint beyond FIFO.
- Caching/wake-up for empty-queue arrival; queue-content encryption key rotation.
- `/v1/responses`; scoped API-credential auth to replace the dev bypass; full admin CRUD API; v1
  SQLite migration.

## Issues / Risks
- `docker` on nomnom is podman; keep compose healthchecks single-token. `docker compose run` needs
  `--no-deps`. The dynamic API host port changes every `up` (and when scaling workers recreates the
  api container); re-run `scripts/dev/v2 url`.
- `qwen3.8-2b-distill:Q6_K` emits `</think>` reasoning content (upstream behavior, not a gateway
  defect), and the ollama backend serializes concurrent generation even across distinct endpoints.
- The devseed derives endpoint name from provider `kind`, so a second distinct endpoint was seeded
  with a separate provider name; consider making endpoint identity an explicit seed arg later.

## Recommended Next Step
Begin quota expansion (provider RPM/TPM windows, token reservations, shared-account quota groups,
project budgets) on the now-solid scheduler invariants, reserved together with endpoint capacity.
