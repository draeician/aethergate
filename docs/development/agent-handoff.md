# AetherGate Agent Handoff

## Current State
- Branch: `v2`.
- AGV2-017 implementation complete on `v2` (see "Task Completed"). This handoff supersedes the
  AGV2-016V handoff.
- Migration head: `0014` (no new migration; the nullable `budget_reservations.price_snapshot_id` was
  already introduced by `0008`, so the AGV2-017 read-contract fix needs no schema change; empty ->
  `0014` re-verified live).
- Full containerized suite: **399 passed** (was 386; +13 new `tests/test_accounting_admin.py`). `ruff
  check src tests` clean; `git diff --check` clean; staged secret scan clean.

## Task Completed
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

## Live nomnom verification (all scenarios green)

Clean reset -> `scripts/dev/v2 migrate` (empty -> `0014`) -> one-use bootstrap -> catalog built
entirely through `/admin/v1`. Backend Ollama `http://192.168.22.50:11434`, upstream
`qwen3.8-2b-distill:Q6_K`, alias `gpt-4`, `AETHERGATE_ALLOW_INFERENCE_AUTH_BYPASS=false`. Bearer
scenarios A-H/J (35/35) ran against a dynamic loopback port; browser scenario I (7/7) ran against a
pinned `44777` port with an uncommitted compose override + a local `MultiSubjectIdp`.

- **A price policy + snapshot + inference.** Created request-priced `0.05` policy via `/admin/v1`; read
  returned fixed-point `0.050000000000`; real SDK inference succeeded; immutable `PriceSnapshot`
  captured; PATCH to `0.10`; second inference; both `0.05` and `0.10` snapshots preserved.
- **B one-enabled invariant.** Disabled alternate created; enabling it while another enabled =>
  `409 price_policy_conflict`.
- **C budget live.** Budget created via `/admin/v1`; status zero-state `headroom == limit`; inference
  committed `0.10`; an over-budget request returned `502 budget_request_too_large`; a budget-exhausted
  request blocked in the queue with `wait_reason = budget_window_exhausted`; PATCH `limit_amount`
  admitted a new request and `committed_amount` reflected the raised limit (historical window intact).
- **D budget scope RBAC.** `project_admin(A)` read its own budget; `project_admin(B)` saw only B's
  budget; `project_viewer` write => `403`; `project_admin(A)` PATCH own budget => `200`.
- **E usage + ledger.** Real request-priced inference produced exactly one `UsageRecord` and one
  `usage_debit` `LedgerEntry` with positive fixed-point amounts matching snapshot pricing.
- **F idempotent settlement.** Repeated usage/ledger reads did not change row counts or amounts.
- **G reservation read.** `budget-reservations` listed the committed reservation (with snapshot id).
- **H audit reads.** `system_admin` saw deployment + project events; `project_admin(A)` saw its own
  `project_budget_policy.*` events but not deployment `price_policy.*`; a project credential without
  `admin:audit:read` => `403`; audit payloads contained no secret material.
- **I browser session/CSRF (local IdP).** Real OIDC login for `dev-admin` (system_admin),
  `dev-padmin` (project_admin), `dev-pviewer` (project_viewer). system_admin price-policy create with
  correct CSRF => `201`; missing CSRF => `403`; wrong CSRF => `403`; project_admin budget PATCH with
  CSRF => `200`; project_admin missing CSRF => `403`; project_viewer mutation => `403`; Bearer
  mutation without CSRF => `201`.
- **J SDK regressions.** Official OpenAI SDK `models.list()` listed `gpt-4`; non-stream and stream
  chat completions both succeeded with inference auth bypass false.

## Migration
- None. Head remains `0014`; `0001`–`0014` untouched. `budget_reservations.price_snapshot_id`
  nullable came from `0008`, so the AGV2-017 read-contract fix required no schema change.

## Automated tests
- Full containerized suite: **399 passed**. New `tests/test_accounting_admin.py` (13 tests: service-layer
  authorization cannot be bypassed; price-policy CRUD/shape/one-enabled/409 + PATCH full-shape;
  immutable snapshot after edit; budget CRUD/immutable-currency/window + RBAC + non-enumeration;
  budget status zero-state + exact headroom; reservation nullable snapshot; usage/ledger reads +
  non-enumeration + signed Decimal; audit scoping + no-op-audit + no-secret; Bearer CSRF-exempt).
  `ruff check src tests` clean; `git diff --check` clean; staged secret scan clean (only the test-only
  `test-bootstrap-secret-*` literal, matching existing test conventions).

## Key files
- `src/aethergate/accounting/admin.py` — accounting admin service (authz, invariants, audit,
  `BudgetStatus` dataclass, conflict translation).
- `src/aethergate/api/accounting_admin.py` — thin `/admin/v1` accounting router + converters.
- `src/aethergate/contracts/admin_v1.py` — `PriceSnapshotRead`, `AuditEventRead`, `enabled` on
  `BudgetStatusRead`, nullable `BudgetReservationRead.price_snapshot_id`.
- `src/aethergate/domain/entities.py` — `BudgetReservation.price_snapshot_id` nullable.
- `src/aethergate/persistence/repository.py` — new accounting read/list/update repository functions.
- `src/aethergate/api/admin_errors.py` — `PricePolicyConflictError` handler.
- `src/aethergate/main.py` — router + exception registration.
- `tests/test_accounting_admin.py` — new suite.
- Docs: `docs/architecture/accounting.md`, `docs/architecture/admin-api.md`,
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

## Issues / Risks
- `docker` on nomnom is podman; keep compose healthchecks single-token; `docker compose run` needs
  `--no-deps`.
- Pre-existing flaky concurrency tests (`test_scheduler_quota.py::test_saturated_quota_group_does_not_block_unrelated_group`
  and `test_accounting.py::test_two_workers_cannot_oversubscribe_budget`) can fail intermittently only
  under full-suite load; they pass in isolation/targeted runs and were green this session.
- `deploy/v2/compose.yaml` still does not wire OIDC env passthrough; live scenario I used an
  uncommitted compose override (pinned host port `44777`, local `MultiSubjectIdp` on `8491`) — the same
  mechanism as AGV2-016V. A budget-exhausted request stays held until its next eligible window by
  design; a PATCH `limit_amount` raise affects *future* admission (and is proven), it does not
  auto-wake the already-held request.
- litellm still has no trusted token estimator; token-priced quotas fail closed (request-only pricing
  used in the smoke).

## Recommended Next Step
Queue the queue/operator admin API (`admin:queue:*` — queue state inspection, outcome_unknown
reconciliation, and explicit queue lifecycle) as the next workstream task, reusing the now-proven
thin-router + centralized-service + RBAC + pagination pattern.
