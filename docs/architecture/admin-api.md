# AetherGate v2 — Administration API

Living document. Derived from the audit. Distinguish **settled** / **direction** / **deferred**.

## Contract

- Versioned management API under `/admin/v1`. (Settled)
- Typed schemas, stable opaque resource IDs, PATCH semantics, optimistic versioning, pagination,
  filtering, and safe import/diff/apply workflows. (Direction)
- The web console and the Linux CLI consume the **same** contracts and service layer; normal
  administrative tooling never edits the database directly. (Settled)

## Resource coverage

Providers, accounts, secrets, endpoints, quota groups, model aliases, routes, users, service
accounts, keys, budgets, queues, audit, and configuration. (Settled)

## Semantics

- Invalid IDs/values yield `4xx`; omitted vs `null` fields behave predictably; conflicting edits are
  detected via optimistic versioning. (Settled — AG-022.)
- Stable opaque resource IDs in administrative paths; public model names are data, not path segments,
  so aliases containing any permitted character work without changing spelling or case. (Settled — AG-023.)
- Lists are server-paginated and filtered; imports are typed, versioned, previewed/diffed, and
  authorized; privileged overwrite behavior is explicit. (Settled — AG-024.)
- Secrets are returned only at explicit create/rotate boundaries, never in normal reads or exports. (Settled)

## Auth model

- Administrative endpoints authenticate a named principal (OIDC/session or service account), not a
  shared master secret. (Settled)
- The v1 `x-admin-key` master-key scheme is superseded; it exists only as a one-use bootstrap path. (Direction)
- Admin bearer authentication is a distinct dependency from inference auth: it requires an
  `admin`-audience credential (`Authorization: Bearer agk_...`) and a permission granted by an active
  role assignment; an inference credential is never accepted. (Settled — AGV2-013)
- One-use bootstrap: `POST /admin/v1/bootstrap` consumes a configured `AETHERGATE_BOOTSTRAP_TOKEN`
  once and returns the initial `system_admin` credential's raw key a single time. (Settled — AGV2-013)

## Audience separation

Administrative and inference permissions are separate; the same credential should not gate both
surfaces. (Settled)

Enforced in both directions (AGV2-013): admin-audience credentials carry only `admin:*` scopes and
inference-audience credentials carry only `inference:invoke`; a cross-audience scope is rejected, an
inference key is rejected by admin endpoints, and an admin key is rejected by `/v1/models` and
`/v1/chat/completions`.

## Minimal protected surface (AGV2-013)

Proving the model only; full admin CRUD reuses the same dependencies/services later:

- `POST /admin/v1/bootstrap` — one-use initialization (no admin auth; bootstrap token).
- `GET /admin/v1/whoami` — safe principal/role/audience metadata for the authenticated admin key.
- `GET /admin/v1/projects/{project_id}/credentials` — protected read (list, metadata only).
- `POST /admin/v1/credentials` — protected create (raw key returned once).
- `POST /admin/v1/credentials/{id}/rotate` / `.../revoke` — protected lifecycle.

Admin errors use a structured envelope `{"error":{"code","message","request_id"}}`; auth/authorization/
bootstrap failures are fixed and indistinguishable.

## Admin CRUD surface (AGV2-014)

`/admin/v1` now exposes the identity-management CRUD, all through the centralized authorization and
pagination foundations (no DB policy in routers):

- Projects: `POST /projects`, `GET /projects` (paginated), `GET /projects/{id}`, `PATCH /projects/{id}`.
- Principals: `POST /projects/{project_id}/principals`, `GET /projects/{project_id}/principals`
  (paginated), `GET /principals/{id}`, `PATCH /principals/{id}`.
- Role assignments: `POST /role-assignments`, `GET /role-assignments` (paginated, authorized filter),
  `GET /role-assignments/{id}`, `POST /role-assignments/{id}/revoke`.
- Credentials: `POST /credentials`, `GET /projects/{project_id}/credentials` (paginated),
  `GET /credentials/{id}`, `POST /credentials/{id}/rotate`, `POST /credentials/{id}/revoke`.

### Anti-enumeration / resource-scope resolution

Opaque-ID read/mutate operations resolve through a centralized resolver that maps a resource to its
authorization scope (project -> its id; principal/credential -> `project_id`; role assignment ->
project or deployment). A nonexistent ID and a cross-project ID are indistinguishable (`404
not_found`); an in-scope resource denied by a specific read/write permission returns `403`. `system_admin`
resolves any target; `project_admin`/`project_viewer` resolve only their authorized project scope.

### Delegation rules

`system_admin` may grant/revoke any role; `project_admin` may grant/revoke `project_admin`/
`project_viewer` only within its own project; `project_viewer` cannot mutate roles; no caller may grant
a role/scope broader than its own authority (enforced centrally in the service, not the router).

A duplicate active-equivalent role assignment is idempotent and concurrency-safe: a concurrent
duplicate that races past the application pre-check is translated back to the canonical winner via the
partial unique index (savepoint + `IntegrityError` recovery), producing exactly one active row and one
`role_assignment.created` audit event. Unrelated `IntegrityError`s are re-raised rather than swallowed.

### Generic credential management

`/admin/v1/credentials` administers client credentials of either audience. `admin:credentials:write`
is required; the shared identity service validates audience/scope coherence; audit actions are neutral
(`credential.created`/`rotated`/`revoked`) with the safe audience recorded in metadata.

### Pagination contract

List endpoints return `{"items":[...],"limit":N,"offset":N,"total":N}` with `limit` bounded
`1..200` (default 50) and stable sort (created_at, id).

## Deferred

- Concrete schema/OpenAPI layout for `/admin/v1` (owned by the `contracts` workstream, established first).
- Import/export format and the diff/apply UX details.
- Whether `PATCH` (RFC 7386) or typed PATCH bodies are used per resource — decided with contracts.
