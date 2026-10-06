# AetherGate Agent Handoff

## Current State
- Branch: `v2`.
- AGV2-018 (queue/operator admin API) implementation and automated tests are complete; live nomnom
  verification (scenarios A–L) has **not** been re-run this session and remains the open item (see
  "Live verification status").
- Migration head: `0015` (adds `endpoints.operational_state`). `0001`–`0014` untouched.
- Full suite (with `DATABASE_URL` + `AETHERGATE_TEST_DATABASE_URL`): **425 passed** (was 402; +22
  queue-admin DB-gated tests + 1 migration test). `ruff check src tests` clean.

## AGV2-018 Completed — queue and operator admin API

### 1. Durable endpoint operational state (migration 0015)
- `EndpointOperationalState` enum (`active` | `paused` | `draining`) in `domain/enums.py`.
- `domain.Endpoint.operational_state` (default `active`), mapped in `persistence/repository.py`.
- `models.Endpoint.operational_state` column + CHECK `IN ('active','paused','draining')`, server
  default `active`; migration `0015` (linear, `down_revision="0014"`, downgrade implemented).

### 2. Scheduler operator service boundary
`src/aethergate/scheduler/admin.py` — centralized operator service that accepts a typed
`AdminRequestContext`, authorizes internally, resolves project/deployment scope centrally, and uses
safe scheduler transition primitives (no lifecycle logic in routers). Routers (`api/queue_admin.py`)
are thin.

### 3. Refactor to a single cancellation/reconciliation state machine
`SchedulingService.request_cancellation`, the reconcile CLI path, and the new admin service all reuse
the module-level `cancel_request_transition` / `reconcile_transition` / `_release_pre_dispatch_accounting`
primitives in `scheduler/service.py`. No divergent lifecycle copies remain.

### 4. Pause/drain admission gate
The worker re-checks `operational_state` under the same endpoint `FOR UPDATE` row lock used for
physical capacity admission, immediately before reserving. Filtering paused/draining endpoints out of
queue scans is an optimization only. A paused claim returns `"paused"` (worker waits, no hot-spin); a
draining endpoint with occupied slots returns `"full"`; draining with zero occupied slots resolves the
request to a failure. In-flight reserved/dispatched/streaming work is never killed; `resume` restores
dispatch; `draining_complete` flips when active physical reservations reach zero.

### 5. Endpoints added (`/admin/v1/queue`)
- Reads: `GET /queue/requests` (paginated, filters), `GET /queue/requests/{request_id}`,
  `GET /queue/summary`, `GET /queue/outcome-unknown`, `GET /queue/quota-status`.
- Endpoint runtime: `GET /queue/endpoints`, `GET /queue/endpoints/{endpoint_id}`.
- Mutations: `POST /queue/endpoints/{endpoint_id}/pause` / `drain` / `resume`,
  `POST /queue/requests/{request_id}/cancel`, `POST /queue/requests/{request_id}/reconcile`.

### 6. Queue DTO safe-field list
`QueueRequestRead` exposes only: `request_id`, `project_id`, `principal_id`, `api_credential_id`,
`model_alias_id`, `endpoint_id`, `quota_group_id`, `state`, `stream`, `queued_at`, `started_at`,
`finished_at`, `queue_wait_until`, `expires_at`, `cancellation_requested`, `wait_reason`,
`wait_limit_id`, `wait_limit_metric`, `next_eligible_at`, `error_code`, `price_snapshot_id`,
`reconciled_state`, `reconciled_at`, `reconciled_by`, optional `worker_id`/`lease_expires_at`, and a
computed `effective_wait_reason`. It never exposes `payload_encrypted`, `result_encrypted`, decrypted
prompt/completion, stream-event bodies, `fencing_token`, or provider secret material.

### 7. Authorization / scoping
- Project-scoped (`admin:queue:read`/`write`): `system_admin` reads all + cancels any eligible request;
  `project_admin` reads/cancels own project; `project_viewer` reads own project only. Cross-project
  opaque request IDs are non-enumerating (`404`); project roles never see `project_id = NULL` requests.
- Deployment-only (`system_admin` deployment authority + `admin:queue:*`, regardless of scopes):
  endpoint runtime, pause/drain/resume, quota-status, outcome_unknown reconciliation. A `project_admin`
  cannot gain operator authority merely by holding `admin:queue:*`.

### 8. Cancellation semantics
`queued`/`reserved` → terminal `cancelled` immediately (release endpoint/quota/budget reservations,
discard pre-dispatch price snapshot); `dispatched`/`streaming` → set `cancellation_requested` only
(never claim upstream stopped; owning worker settles conservatively); already-cancelled → idempotent
`already_cancelled`; `succeeded`/`failed`/`expired` → stable `terminal` no-op conflict;
`outcome_unknown` → `outcome_unknown` (cannot cancel; requires reconciliation). Typed result
distinguishes all five; audit distinguishes `request.cancelled` vs `request.cancellation_requested`.

### 9. Reconciliation semantics
Disposition `failed` | `cancelled` only (`succeeded` rejected); `reconciled_by` = authenticated
`principal_id` (never client-supplied); releases the held physical reservation exactly once; settles
budget conservatively; no fabricated `UsageRecord`; repeat reconciliation is a stable conflict with no
double release/commit.

### 10. Error translation
Stable codes: `404 not_found`, `403 forbidden`, `400 invalid_request` (bad disposition),
`409 invalid_lifecycle` (cancel a terminal request / reconcile a non-`outcome_unknown` request).
No SQL, stack traces, raw provider errors, or encryption data.

## Automated tests
- `tests/test_queue_admin.py` (new, DB-gated, 22 tests): operational-state default/slot calc,
  pause/drain prevent-reserve + survive restart + idempotent audit, pause-vs-claim DB row-lock race,
  queued/reserved/dispatched/idempotent/terminal/outcome_unknown cancellation, reconcile once + reject
  succeeded/project-role, quota zero-state, project RBAC/non-enumeration, and HTTP DTO-safety/404/403/
  cancel coverage.
- `tests/test_migrations.py`: +1 `test_migration_0014_to_0015`.
- Full suite: **425 passed** (`DATABASE_URL` must be set in addition to `AETHERGATE_TEST_DATABASE_URL`;
  two pre-existing tests `test_http_admin_key_rejected_on_inference` and
  `test_credential_audience_separation_via_endpoint` hit the real `get_settings()` and need
  `DATABASE_URL`).
- `ruff check src tests` clean.

## Live verification status
Not performed this session. Remaining scenarios A–L from `current-task.md`:
- A queue inspection/explanation (six concurrent, two slots), B project RBAC, C pause live +
  restart persistence, D drain live, E pause/claim race harness, F queued cancel, G reserved cancel,
  H in-flight cancel, I outcome_unknown reconciliation, J quota runtime status, K browser RBAC/CSRF
  (requires the local OIDC IdP), L full regression + official OpenAI SDK.
- Backend Ollama `http://192.168.22.50:11434` is reachable; image `aethergate-v2:local` is already
  built, so `scripts/dev/v2 up`/`migrate`/`workers` and the dynamic host port are the entry point.

## Key files
- `src/aethergate/migrations/versions/0015_endpoint_operational_state.py` — new migration.
- `src/aethergate/domain/enums.py`, `domain/entities.py` — `EndpointOperationalState` + field.
- `src/aethergate/persistence/models.py`, `persistence/repository.py` — column/check + mapping.
- `src/aethergate/scheduler/service.py` — shared transitions + pause/drain admission gate.
- `src/aethergate/scheduler/repository.py` — queue/runtime/quota aggregate helpers.
- `src/aethergate/scheduler/admin.py` — operator admin service.
- `src/aethergate/api/queue_admin.py` — `/admin/v1/queue` router.
- `src/aethergate/api/admin_errors.py`, `errors.py` — queue transition error handling.
- `src/aethergate/contracts/admin_v1.py` — queue DTOs.
- `src/aethergate/main.py`, `worker.py` — router/handler registration + `"paused"` handling.
- `tests/test_queue_admin.py`, `tests/test_migrations.py`.
- Docs: `docs/architecture/scheduler.md`, `docs/architecture/admin-api.md`,
  `docs/contracts/domain-model.md`, `docs/contracts/admin-v1-foundation.md`,
  `docs/development/README.md`.

## Decisions
- Pause/drain and catalog `is_active` are distinct; pause/drain never sets `is_active=false`.
- Pause and drain intentionally share the "no new dispatch" gate; `draining` additionally reports
  `draining_complete` at zero reservations and is not auto-flipped by a background job.
- The cancellation/reconciliation state machine is single-sourced in the shared transitions, reused by
  the normal scheduler path, the reconcile CLI, and the admin API.
- `terminal` and `outcome_unknown` cancel outcomes are returned (not raised) at the service layer and
  mapped to `409 invalid_lifecycle` at the HTTP boundary; reconciliation raises `QueueTransitionError`
  for repeat/non-`outcome_unknown` at the service layer.

## Issues / Risks
- `scripts/dev/v2` runs the full suite inside the container, which already sets both
  `DATABASE_URL` and `AETHERGATE_TEST_DATABASE_URL`; running DB-gated tests on the host requires
  setting **both** env vars (the two inference-path admin tests use the un-monkeypatched
  `get_settings()`).
- The pause-vs-claim race test uses `asyncio.sleep(0.05)` only to let the pause task reach its blocking
  `FOR UPDATE`; correctness is the endpoint row lock, not the sleep.
- Live browser CSRF (scenario K) needs the deterministic local OIDC IdP and an uncommitted compose
  override to wire OIDC env passthrough (the same gap noted in the AGV2-017 handoff remains).

## Recommended Next Step
Run the AGV2-018 live nomnom verification (scenarios A–L) against the reachable Ollama backend and
`scripts/dev/v2`, then record the live proofs (six/two queue, pause/drain + restart persistence,
outcome_unknown reconcile, browser CSRF, official SDK regression) here and close the task.
