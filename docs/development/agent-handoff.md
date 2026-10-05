# AetherGate Agent Handoff

## Current State
- Branch: v2
- AGV2-013 implementation commit: `feat(identity): add admin RBAC and one-use bootstrap`
  (created this session), pushed to `origin/v2`.
- Prior commits remain: `fca2359` (AGV2-012V lifecycle hardening), `68a8844` (AGV2-012V handoff),
  `2c8af13` (task queue doc). This handoff supersedes the AGV2-012V handoff.

## Task Completed
AGV2-013 — Identity phase 2: admin service authentication, RBAC, and one-use bootstrap.

### Admin audience / scopes
- New typed admin permissions on `CredentialScope`: `admin:credentials:read|write`,
  `admin:projects:read|write`, `admin:principals:read|write`, `admin:catalog:read|write`,
  `admin:accounting:read|write`, `admin:queue:read|write`, `admin:audit:read`.
- Audience/scope coherence is now enforced **both ways**: inference-audience credentials may carry
  only `inference:invoke`; admin-audience credentials may carry only `admin:*`. Cross-audience scope
  is rejected at create/rotate and at authentication.

### RBAC model
- `Role` (`system_admin` | `project_admin` | `project_viewer`) and `ResourceScopeType`
  (`deployment` | `project`). Durable `RoleAssignment` entity + `role_assignments` table with a
  partial unique index `uq_role_assignments_active_equivalent` preventing duplicate active equivalent
  assignments. `system_admin` is deployment-scoped; project roles require a project scope.
- Centralized `authorize_admin(context, permission, resource_type, resource_id)` in
  `src/aethergate/identity/rbac.py` + `admin.py` — not scattered router checks. Authentication
  (`authenticate_admin` -> `AdminRequestContext`) and authorization are separate steps; active role
  assignments are re-checked on every protected request.

### Bootstrap
- `AETHERGATE_BOOTSTRAP_TOKEN` is a `SecretStr`, no default, empty/unset disables bootstrap,
  placeholders rejected, never logged. `POST /admin/v1/bootstrap` transactionally establishes the
  initial project/principal, grants `system_admin`, creates the admin credential, marks
  `bootstrap_state` complete, and returns the raw admin key once. `SELECT ... FOR UPDATE` + the
  singleton state make concurrent bootstrap exactly-one-wins; restart does not reopen bootstrap.

### Admin HTTP surface (minimal)
- `POST /admin/v1/bootstrap`, `GET /admin/v1/whoami`, `GET /admin/v1/projects/{id}/credentials`,
  `POST /admin/v1/credentials`, `POST /admin/v1/credentials/{id}/rotate` and `/revoke`.
- Admin error envelope `{"error":{"code","message","request_id"}}`; auth/authorization/bootstrap
  failures are fixed and indistinguishable. No query-string credentials; raw key never logged.

### Audit
- `audit_events` table (JSON column `details`, avoiding the SQLAlchemy-reserved `metadata`). Records
  `bootstrap.completed` (NULL actor), `role_assignment.created`/`revoked`,
  `admin_credential.created`/`rotated`/`revoked` with actor principal ID, action, resource
  type/ID, timestamp, safe metadata only.

## Migration
- New revision `0010` (identity phase 2): `role_assignments`, `bootstrap_state`, `audit_events`.
  `0009 -> 0010` succeeded on the live DB and is green in the suite; empty-DB -> latest is green;
  `0001`–`0009` untouched; old inference credentials remain valid (proven live).

## Real nomnom verification (dynamic port 36299; backend ollama `qwen3.8-2b-distill:Q6_K`)
- **A. Bootstrap** — missing token `401 bootstrap_token_rejected`; wrong token `401`; correct token
  `201` (raw key once); second use `409 bootstrap_already_completed`; API restart keeps it closed and
  the admin key valid; DB holds only completion state, role assignment, credential metadata; raw
  admin key and bootstrap token absent from all tables (SQL grep = 0).
- **B. Concurrent bootstrap** — 5 concurrent calls: exactly one `201`, four `409`.
- **C. whoami** — admin key `200` (safe principal/role/audience, no secret); inference key `401`.
- **D. Project RBAC** — `system_admin` lists A and B; `project_admin(A)` lists/writes A (200/201),
  `403` on B (list + write); `project_viewer(A)` lists A (200) but write `403` and `403` on B.
- **E. Role revocation** — revoked the viewer assignment; its next protected request `403` while the
  credential stayed active; the project_admin still worked.
- **F. Credential lifecycle** — create/list/rotate/revoke over HTTP; list carries no raw key/hash;
  rotate -> old key `401`/new key `200`; revoke -> `401`; audit events for create/rotate/revoke with
  the real actor principal ID.
- **G. Audience separation** — inference key `401` on `/admin/v1/whoami`; admin key `401` on
  `/v1/models` and `/v1/chat/completions`.
- **H. Regression** — official OpenAI SDK non-stream and stream both succeeded on a fresh inference
  key; scheduler/quota/accounting/migration suites green in the full containerized suite.

## Automated tests
`scripts/dev/v2 test` -> **290 passed** (was 268; +22). `ruff check src tests` clean;
`git diff --check` clean; staged-content secret scan clean. New coverage: audience coherence both
ways, system_admin deployment-wide, project_admin/project_viewer isolation + read-only, revoked
assignment denied, duplicate active assignment prevented, admin request context, cross-audience
rejection both directions, bootstrap missing/wrong/correct/second-use/concurrent/restart-survival/
raw-secret-absence, whoami safety, role-revocation immediate block, admin credential lifecycle
authorization, audit events, migration `0009 -> 0010`.

## Key files
- `src/aethergate/identity/admin.py` — authenticate/authorize/bootstrap/RBAC/audit service.
- `src/aethergate/identity/rbac.py` — centralized role->permission policy.
- `src/aethergate/identity/service.py` — shared create/rotate/revoke + audience coherence.
- `src/aethergate/api/admin.py` + `api/admin_errors.py` — minimal admin HTTP surface + envelope.
- `src/aethergate/persistence/models.py` / `repository.py` — role/bootstrap/audit persistence.
- `src/aethergate/migrations/versions/0010_admin_identity.py` — new migration.
- `src/aethergate/config.py` — `bootstrap_token` (`AETHERGATE_BOOTSTRAP_TOKEN`).
- `deploy/v2/compose.yaml` + `scripts/dev/v2` — bootstrap-token env + `ensure_bootstrap_token`.
- `tests/test_admin_auth.py`, `tests/test_migrations.py` — new/updated tests.

## Decisions
- Authorization is centralized in `authorize_admin` with role assignments as the single gate; a
  credential carrying an `admin:*` scope alone does not grant access — the active role assignment
  does. This keeps the design open to later custom roles without a rewrite (custom-role CRUD is out
  of scope).
- `AuditEvent` ORM maps its JSON column to `details` (SQLAlchemy reserves `metadata`); the domain
  entity field stays `metadata`.
- `AETHERGATE_BOOTSTRAP_TOKEN` empty/unset disables bootstrap (safe), rather than failing startup, so
  the token can be removed after bootstrap; placeholder strings are still rejected.
- Bootstrap's actor is `NULL` in audit (no authenticated actor yet), not a synthetic principal.

## Issues / Risks
- `docker` on nomnom is podman; keep compose healthchecks single-token; `docker compose run` needs
  `--no-deps`. Dynamic host port changes every `up`; re-run `scripts/dev/v2 url` (this session: 36299).
- litellm 1.104.0 has no trusted token estimator, so token-quota/budget routes fail closed
  (`quota_token_estimator_unavailable`); live inference smoke used request-only quota.
- `qwen3.8-2b-distill:Q6_K` emits `</think>` reasoning content; gateway timings don't reflect
  upstream parallelism.
- Admin role-assignment/credential creation is currently exercised via the service layer (no HTTP
  endpoint yet for role assignment or project/principal create) — expected for the minimal surface.
- The dev DB volume retains leftover inference credentials/model aliases from AGV2-012V smoke tests;
  not committed.

## Recommended Next Step
Expose the remaining admin identity CRUD (role-assignment create/revoke, project/principal create)
over `/admin/v1` reusing the centralized `authorize_admin`/service layer, then proceed to the human
OIDC authorization-code flow plugged into the same principal/RoleAssignment model.
