# AetherGate Agent Handoff

## Current State
- Branch: `v2`.
- AGV2-017V complete (see below); AGV2-017 remains complete underneath. This handoff
  supersedes the AGV2-017 handoff.
- Migration head: `0014` (unchanged; AGV2-017V added no migration and modified none of
  `0001`–`0014`).
- Full containerized suite: **402 passed** (was 399; +2 concurrent price-policy race tests,
  +1 budget double-commit settlement-replay test). `ruff check src tests` clean;
  `git diff --check` clean; staged secret/token/content canary scan clean.

## AGV2-017V Completed — close accounting concurrency + release verification gaps

### 1. True concurrent PricePolicy race (new tests, no production change)
Added to `tests/test_accounting_admin.py` (DB-gated, independent `async_sessionmaker`
sessions, `asyncio.Barrier(2)` synchronization so this is a genuine race, not sequential
calls):

- `test_concurrent_price_policy_create_race` — two sessions race to create the first enabled
  policy on the same route. Exactly one winner; the loser receives the typed
  `PricePolicyConflictError`; exactly one enabled policy remains; no raw `IntegrityError`
  escapes; and the loser's aborted transaction does not corrupt a subsequent disabled create.
- `test_concurrent_price_policy_enable_race` — two disabled policies race to enable via
  `repository.update_price_policy` under the same barrier. Same invariants.

No production change was required: the partial unique index
`uq_price_policies_one_enabled_per_route` (migration `0008`) plus the
`_is_price_policy_conflict` `IntegrityError` translation already backstop the race correctly.

### 2. Live released pre-dispatch budget reservation (nomnom, 18/18)
Real admin API setup (bootstrap, `ollama` provider/account/endpoint, model alias
`gpt-4-release`, request-priced `0.05` policy, project, inference credential, budget
`1.00`/3600s), then the real `SchedulingService` drove `admit_and_enqueue` +
`claim_and_reserve` (acquiring a budget reservation + immutable price snapshot, state
`reserved`). The request was reverted to the pre-dispatch `reserved` window (the same harness
step as the deterministic `test_pre_dispatch_cancel_releases_budget`) and then
`request_cancellation` ran the real release lifecycle (`_release_pre_dispatch_accounting`).

Proven from PostgreSQL **and** the admin API:

- `BudgetReservation.state == released`, `reserved_amount == 0`, `committed_amount == 0`,
  `price_snapshot_id == null`;
- the pre-dispatch `PriceSnapshot` was discarded;
- no `UsageRecord` and no `usage_debit LedgerEntry` for that request;
- the `InferenceRequest` is `cancelled`; the budget window `reserved_amount == 0` and
  `committed_amount == 0`;
- no upstream dispatch evidence (`upstream_request_id` null; worker was stopped so
  `run_complete`/`run_stream` never executed);
- `GET /admin/v1/budget-reservations?request_id=…` serializes `price_snapshot_id: null` and
  `state: "released"`; `GET /admin/v1/projects/{id}/budget-status` reports
  `reserved=0 committed=0`.

The released row came from the real scheduler/accounting lifecycle, not a manual insert.

### 3. Settlement idempotency (rerun existing regression + 1 narrow addition)
The prior "repeated GET reads prove idempotency" claim was removed. Settlement replay is now
proven by the containerized regression in `tests/test_accounting.py`:

- `test_usage_record_and_ledger_idempotent` — a same-canonical settlement replay does not
  duplicate the `UsageRecord` (unique per `request_id`) and a duplicate ledger
  `idempotency_key` collapses to one entry.
- `test_settlement_replay_does_not_double_commit_budget` (**new**) — replays
  `settle_budget_reservations_to_actual` for the already-committed request (no second upstream
  charge); the budget window `committed_amount` stays `0.05`, never `0.10`.
- `test_conflicting_usage_record_replay_raises` and `test_conflicting_ledger_entry_replay_raises`
  — a conflicting replay raises `AccountingInvariantError` and mutates nothing.

### 4. Regression / SDK
- Full containerized suite: **402 passed**.
- Official OpenAI SDK non-stream and stream (`32` chunks) inference with
  `AETHERGATE_ALLOW_INFERENCE_AUTH_BYPASS=false` — `3/3` green (alias listed by
  `models.list()`, non-stream completion, stream completion).
- Backend Ollama `http://192.168.22.50:11434`, upstream `qwen3.8-2b-distill:Q6_K`, alias
  `gpt-4`/`gpt-4-release`; API dynamic loopback host port (release proof ran the scheduler
  driver inside the `api` container against `http://api:8000`).

## Task Completed (AGV2-017, still the base)
AGV2-017 — Accounting admin API: pricing, project budgets, usage/ledger, and audit reads.

### Endpoints added (`/admin/v1`)
- **Deployment-scoped** (route pricing, `system_admin` + `admin:accounting:read`/`write`):
  `POST /price-policies`, `GET /price-policies` (filter `route_binding_id`/`enabled`/`billing_unit`),
  `GET /price-policies/{id}`, `PATCH /price-policies/{id}`; immutable read-only
  `GET /price-snapshots`, `GET /price-snapshots/{id}`.
- **Project-scoped** (budget/usage/ledger; `system_admin` any project, `project_admin` read/write own,
  `project_viewer` read own): `POST /project-budget-policies`, `GET /project-budget-policies`,
  `GET /project-budget-policies/{id}`, `PATCH /project-budget-policies/{id}`; `GET
  /projects/{project_id}/budget-status`; read-only `GET /budget-reservations`,
  `GET /budget-reservations/{id}`; `GET /usage-records`, `GET /usage-records/{id}`;
  `GET /ledger-entries`, `GET /ledger-entries/{id}`.
- **Audit** (`admin:audit:read`): `GET /audit-events`, `GET /audit-events/{id}`.

Routers are thin (`src/aethergate/api/accounting_admin.py`); all authorization/validation lives in
`src/aethergate/accounting/admin.py`, which accepts a typed `AdminRequestContext` and authorizes
internally — an internal caller cannot bypass RBAC merely by supplying an actor principal ID.

### Deployment vs project authorization
Route pricing (deployment infrastructure) requires `system_admin` authority **and** the matching
`admin:accounting:*` permission; project roles are denied (`403`) regardless of their scopes. Project
budget policy/status/reservations/usage/ledger are project-scoped; cross-project opaque IDs (and
deployment-scoped IDs for a project-scoped caller) are indistinguishable from nonexistent (`404`).
Audit reads are deployment-vs-project scoped: `system_admin` reads all; project roles read only events
with `project_id` in their scope; a `project_id = null` event is deployment-scoped and hidden from
project roles.

### Price policy mutation / locking
`PricePolicy` CRUD enforces the AGV2-010/011 invariants: route must exist; request pricing requires
`request_price` and forbids token prices; token pricing requires `input_price`+`output_price` and
forbids `request_price`; `unit_scale > 0`; non-negative prices; at most one enabled policy per route
(partial unique index `0008` + service validation => stable `409 price_policy_conflict`, DB unique
races translated via savepoint + `IntegrityError` recovery). PATCH validates the complete resulting
shape, not just present fields; omitted means unchanged; explicit `null` clears a price only if the
resulting shape stays valid. Edits serialize with scheduler admission via the existing price-policy
lock order; no reverse-order deadlock.

### Immutable snapshot proof
`PriceSnapshot` is read-only (no POST/PATCH/DELETE). Editing a live `PricePolicy` never rewrites
existing snapshot rows; live scenario A proved an old `0.05` snapshot and a new `0.10` snapshot coexist
after a PATCH. Snapshots expose no prompt/completion/provider-secret content.

### Budget policy mutation safety
`name`, `limit_amount`, `enabled` are mutable for future admission; `currency` and `window_seconds`
are immutable after creation (a PATCH attempting to change them returns a stable `400`; changing them
requires a replacement policy). Edits never rewrite historical `BudgetWindow`/`BudgetReservation`
rows; budget config writes serialize with scheduler budget admission using the policy-row lock order.

### Budget status / headroom
`GET /projects/{id}/budget-status` returns one `BudgetStatusRead` per applicable policy/window with
`limit_amount`, `committed_amount`, `reserved_amount`, `headroom = limit - committed - reserved`,
`window_start`/`window_end`, `enabled`. A read computes current-window zero state without fabricating
reservation history; negative headroom is allowed after honest overage.

### Budget reservation reads
Read-only. `BudgetReservationRead.price_snapshot_id` is now **nullable** (contract + domain entity
aligned to persistence: a released/detached pre-dispatch reservation stores `NULL`). No reservation
mutation via admin HTTP.

### Usage/ledger reads
Read-only immutable/append-only reads with bounded pagination (`Page[T]`, default 50, max 200, stable
sort) and the documented filters. No content/secrets; signed fixed-point `Decimal` amounts preserved
exactly; `usage_debit` links its `UsageRecord`; adjustment entries may have no `usage_record_id`.
Manual ledger adjustment writes remain deferred (no HTTP path).

### Audit behavior
Config changes emit immutable audit events — `price_policy.created`/`updated` (deployment-scoped,
`project_id = null`) and `project_budget_policy.created`/`updated` (project-scoped) — with actor
principal ID and safe metadata. No-op PATCHes emit no event; usage/ledger/snapshot reads create no
audit noise.

### Error translation
Stable codes only (no SQL/constraint names/stack traces/secrets): `404 not_found`,
`403 forbidden`, `400 invalid_request`, `400 parent_mismatch`, `409 price_policy_conflict`, plus
stable immutable-field validation errors for budget `currency`/`window_seconds`.

## Live nomnom verification (AGV2-017, still green)
Clean reset -> `scripts/dev/v2 migrate` (empty -> `0014`) -> one-use bootstrap -> catalog built
entirely through `/admin/v1`. Backend Ollama `http://192.168.22.50:11434`, upstream
`qwen3.8-2b-distill:Q6_K`, alias `gpt-4`, `AETHERGATE_ALLOW_INFERENCE_AUTH_BYPASS=false`. Bearer
scenarios A-H/J (35/35) and browser scenario I (7/7) were green; AGV2-017V re-verified the normal
inference path (SDK non-stream + stream) and added the released-reservation proof (18/18).

## Migration
- None (both AGV2-017 and AGV2-017V). Head remains `0014`; `0001`–`0014` untouched.
  `budget_reservations.price_snapshot_id` nullable came from `0008`, so the AGV2-017 read-contract
  fix required no schema change.

## Automated tests
- Full containerized suite: **402 passed**.
  - `tests/test_accounting_admin.py`: 15 tests (13 AGV2-017 + 2 new concurrent race tests).
  - `tests/test_accounting.py`: +1 new `test_settlement_replay_does_not_double_commit_budget`
    (settlement idempotency).
  - `ruff check src tests` clean; `git diff --check` clean; staged secret/token/content canary scan
    clean (only the pre-existing test-only `test-bootstrap-secret-*` literal).

## Key files
- `tests/test_accounting_admin.py` — accounting admin suite + concurrent price-policy race tests.
- `tests/test_accounting.py` — accounting foundation suite + settlement idempotency regressions.
- (AGV2-017, unchanged): `src/aethergate/accounting/admin.py`, `src/aethergate/api/accounting_admin.py`,
  `src/aethergate/contracts/admin_v1.py`, `src/aethergate/domain/entities.py`,
  `src/aethergate/persistence/repository.py`, `src/aethergate/api/admin_errors.py`,
  `src/aethergate/main.py`.
- Docs (AGV2-017): `docs/architecture/accounting.md`, `docs/architecture/admin-api.md`,
  `docs/contracts/domain-model.md`, `docs/contracts/admin-v1-foundation.md`,
  `docs/development/README.md`.

## Decisions
- Route pricing is deployment-scoped (`system_admin` only); project roles never mutate it.
- `PriceSnapshot` stays deployment-scoped and read-only (no project ownership inference from a
  route/catalog-history snapshot).
- Budget `currency`/`window_seconds` immutable after creation; `name`/`limit_amount`/`enabled` mutable.
- Manual ledger adjustment HTTP writes deferred (adjustment policy/approval not settled).
- No accounting-summary endpoint added (optional; not required, avoids multi-currency distortion).
- No new migration: the nullable snapshot column already existed in `0008`.
- The concurrent price-policy race needed no production fix; the `0008` partial unique index + typed
  `IntegrityError` translation already handled it.

## Issues / Risks
- `docker` on nomnom is podman; keep compose healthchecks single-token; `docker compose run` needs
  `--no-deps`. A transient podman DNS resolution failure (`Temporary failure in name resolution`)
  can surface on the first bootstrap immediately after a `reset`; wait for `/health/ready` and retry.
- Pre-existing flaky concurrency tests (`test_scheduler_quota.py::test_saturated_quota_group_does_not_block_unrelated_group`
  and `test_accounting.py::test_two_workers_cannot_oversubscribe_budget`) can fail intermittently only
  under full-suite load; they pass in isolation/targeted runs and were green this session.
- `deploy/v2/compose.yaml` still does not wire OIDC env passthrough; browser scenario I used an
  uncommitted compose override. A budget-exhausted request stays held until its next eligible window
  by design; a PATCH `limit_amount` raise affects *future* admission, it does not auto-wake the
  already-held request.
- litellm still has no trusted token estimator; token-priced quotas fail closed (request-only pricing
  used in the smoke).
- The live released-reservation proof requires the worker to be stopped (so the real scheduler driver
  can claim then cancel before autonomous dispatch); this is a test-harness orchestration step, not a
  production behavior.

## Recommended Next Step
Queue the queue/operator admin API (`admin:queue:*` — queue state inspection, outcome_unknown
reconciliation, and explicit queue lifecycle) as the next workstream task, reusing the now-proven
thin-router + centralized-service + RBAC + pagination pattern.
