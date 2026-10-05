# AetherGate Agent Handoff

## Current State
- Branch: v2
- AGV2-014 implementation commit: `feat(admin): complete identity management and authorization boundaries`
  (this session), pushed to `origin/v2`.
- Prior commits remain: `1ddfcbb` (AGV2-014 queued), `5a81425` (AGV2-013),
  `fca2359`/`68a8844` (AGV2-012V). This handoff supersedes the AGV2-013 handoff.

## Task Completed
AGV2-014 — Admin identity CRUD and cross-project authorization hardening.

### Anti-enumeration (cross-project resource visibility)
- `src/aethergate/identity/authorization.py` is the single resource-scope resolver. Opaque-ID
  read/mutate operations (project/principal/credential/role-assignment GET/PATCH/rotate/revoke) go
  through `resolve_admin_resource`, which maps a resource to its authorization scope
  (`project` -> its `id`, `principal`/`credential` -> `project_id`, role assignment -> project or
  deployment) and returns it only when it exists **and** is within the caller's scope.
- A nonexistent ID and a cross-project ID are indistinguishable (`404 not_found` via
  `AdminResourceNotFound`). A resource inside the caller's scope but denied by the specific
  read/write permission still returns `403` (it is already visible via read).
- Live proof: `project_admin(A)` GET/rotate/revoke on B's credential vs a nonexistent ID both `404
  not_found`; B's credential remained `is_active: true`.

### Generic credential management
- `/admin/v1/credentials` is now the administrative surface for client credentials of **either**
  audience. Service methods renamed to neutral `create_credential` / `rotate_credential` /
  `revoke_credential`; audit actions are `credential.created` / `credential.rotated` /
  `credential.revoked`, with the safe `audience` value recorded in audit metadata. No name implies a
  managed credential is itself an admin credential.

### Project / principal / role-assignment CRUD
- New HTTP endpoints (see admin-api doc): project list/create/read/patch, principal
  list/create/read/patch, role-assignment create/list/read/revoke, credential read, plus paginated
  credential list. All `system_admin`/`project_admin`/`project_viewer` scope rules are enforced
  centrally; project creation is deployment-level (`system_admin`) only; no delete (inactive state).

### Service-layer authorization hardening
- Mutation services now take a typed `AdminRequestContext` and authorize internally
  (`authorize_admin` / `authorize_role_grant` / `resolve_admin_resource`), so an internal caller
  cannot bypass RBAC by passing a forged actor ID. Routers stay thin.

### Delegation / escalation defense
- `authorize_role_grant` centrally rejects (a) incoherent role/scope shapes (`system_admin` must be
  deployment-scoped; project roles must be project-scoped) as `400 invalid_request`, and (b) any grant
  broader than the caller's own authority as `403` (e.g. `project_admin(A)` cannot grant
  `system_admin` or any role for project B; `project_viewer` cannot mutate roles).

### Resource-scope resolution
- Centralized mapping in `authorization.py`: `_scope_of` + `_in_scope` + `resolve_admin_resource` +
  `authorized_project_ids`. Routers/services never compare opaque resource IDs to project IDs.

### Audit idempotency
- Repeated credential/role-assignment revoke emits no second `revoked` event; duplicate active
  role-assignment create emits no second `created` event. Audit metadata contains no raw key, hash,
  token, or Authorization header (proven by test + live SQL grep = 0).

### Pagination / list safety
- Shared `Page[T]` (`items`, `limit`, `offset`, `total`) contract across
  projects/principals/credentials/role-assignments; `limit` bounded `1..200`, default 50, stable sort
  (created_at, id). Counts use `SELECT count(*)`.

## Migration
- New revision `0011` adds two role/scope coherence CHECK constraints on `role_assignments`:
  `ck_role_assignments_system_admin_deployment` (`system_admin` => deployment scope + empty resource
  ID) and `ck_role_assignments_project_role_project_scope` (`project_admin`/`project_viewer` =>
  project scope). `0010 -> 0011` and empty-DB -> latest are green; `0001`–`0010` untouched.

## Real nomnom verification (dynamic port 38359)
- **A. Anti-enumeration** — `project_admin(A)` get/rotate/revoke of B's credential and of a
  nonexistent ID both `404 not_found`; B's credential untouched (`is_active: true`). Repeated for
  principal and role-assignment IDs.
- **B. Project CRUD** — `system_admin` created A and B; `project_admin(A)` read/patch A (`200`),
  read B (`404`), read nonexistent (`404`), create project (`403`); `project_viewer(A)` read A (`200`),
  patch A (`403`).
- **C. Principal CRUD** — `project_admin(A)` listed A principals (`total: 2`), read B principal
  (`404`) and nonexistent (`404`); deactivating the project invalidated the admin credential (`401`)
  and reactivation restored it (`200`).
- **D. Escalation** — `project_admin(A)` grant `system_admin` `403`, grant role for B `403`;
  `project_viewer` grant `403`.
- **E. Audience separation** — inference key `401` on `/admin/v1/whoami`; admin key `401` on
  `/v1/models`; inference key `200` on `/v1/models`.
- **F. Audit** — neutral action names (`bootstrap.completed`, `project.created`,
  `principal.created`, `role_assignment.created`, `credential.created`, ...); no `agk_`/`agb_`/
  `key_hash` in `details` (SQL grep = 0).
- **G. Regression** — bootstrap one-use (`201` then `409`) after restart; admin whoami `200`; the full
  containerized suite (306 tests) covers scheduler/quota/accounting/identity/migrations. Real SDK
  non-stream/stream inference was exercised in AGV2-013 and is unchanged by this admin-surface task;
  it requires a seeded provider backend (`scripts/dev/v2 devseed`) not part of this task.

## Automated tests
`scripts/dev/v2 test` -> **306 passed** (was 290; +16). `ruff check src tests` clean;
`git diff --check` clean; staged-content secret scan clean. New coverage: cross-project
non-enumeration (credential/principal/role-assignment), resource-scope resolver mapping, service-layer
escalation defense, project/principal CRUD + pagination/bounds, project/principal deactivation auth
impact, role-assignment CRUD + delegation, generic credential audience separation, idempotent
revoke/duplicate-assignment audit, audit metadata secret absence, migration `0010 -> 0011`.

## Key files
- `src/aethergate/identity/authorization.py` — centralized `authorize_admin`,
  `authorize_role_grant`, `resolve_admin_resource`, `authorized_project_ids`.
- `src/aethergate/identity/admin.py` — authenticate/bootstrap + project/principal/role/credential
  services (context-driven, internal authorization, neutral credential audit actions).
- `src/aethergate/identity/service.py` — shared create/rotate/revoke + audience coherence.
- `src/aethergate/api/admin.py` + `api/admin_errors.py` — full admin CRUD surface + error envelope.
- `src/aethergate/contracts/admin_v1.py` — `RoleAssignmentCreate`, `RoleAssignmentRevokeRequest`,
  `PrincipalCreate` (project_id dropped; it is in the path).
- `src/aethergate/persistence/models.py` / `repository.py` — role/scope constraints + list/count
  functions.
- `src/aethergate/migrations/versions/0011_role_scope_coherence.py` — new migration.
- `tests/test_admin_auth.py`, `tests/test_admin_crud.py`, `tests/test_migrations.py`.

## Decisions
- `resolve_admin_resource` returns `404` only for out-of-scope or nonexistent resources, and `403` for
  in-scope-but-insufficient-permission, so a project_viewer's write attempt on its own project is
  `403` (not misleading `404`), while cross-project enumeration stays impossible.
- Generic credential endpoint (section 2 "preferred" model): neutral service/audit names, audience in
  metadata, coherence enforced by the shared identity service.
- Migration `0011` adds DB backstops for role/scope coherence so direct writes cannot bypass the
  application invariants; both models and migration carry the same constraints.

## Issues / Risks
- `docker` on nomnom is podman; keep compose healthchecks single-token; `docker compose run` needs
  `--no-deps`. Dynamic host port changes every `up`; re-run `scripts/dev/v2 url` (this session: 38359).
- litellm 1.104.0 has no trusted token estimator, so token-quota/budget routes fail closed
  (`quota_token_estimator_unavailable`); live inference smoke used request-only quota.
- The dev DB was reset for a clean live-verification slate; the prior AGV2-013 smoke data is gone.
- Project creation requires `system_admin`; project_admin/project_viewer cannot create projects.

## Recommended Next Step
Introduce human OIDC authorization-code flow plugged into the same `Principal`/`RoleAssignment`
model, then extend the remaining catalog/accounting/queue admin CRUD over `/admin/v1` reusing the
centralized authorization and pagination foundations.
