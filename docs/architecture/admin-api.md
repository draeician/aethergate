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

## Catalog/routing admin CRUD surface (AGV2-016)

`/admin/v1` now exposes the deployment-scoped catalog/routing control plane through the same thin
routers + centralized catalog admin service + authorization/pagination foundations. Catalog resources
are **deployment infrastructure, not project-owned**: every list/read/mutation requires
deployment-scoped authority (`system_admin`) **and** the matching `admin:catalog:read`/`write`
permission. A `project_admin`/`project_viewer` credential is denied regardless of its `admin:catalog:*`
scopes, because deployment scope is also required.

- Providers: `POST /providers`, `GET /providers` (paginated), `GET /providers/{id}`,
  `PATCH /providers/{id}` (deactivate via `is_active`; no delete).
- Secret refs (metadata only): `POST /secret-refs`, `GET /secret-refs`, `GET /secret-refs/{id}`.
- Provider accounts: `POST /provider-accounts`, `GET /provider-accounts` (filter `provider_id`),
  `GET /provider-accounts/{id}`, `PATCH /provider-accounts/{id}`.
- Endpoints: `POST /endpoints`, `GET /endpoints` (filter `provider_account_id`),
  `GET /endpoints/{id}`, `PATCH /endpoints/{id}`.
- Quota groups: `POST /quota-groups`, `GET /quota-groups` (filter `provider_account_id`),
  `GET /quota-groups/{id}`, `PATCH /quota-groups/{id}`.
- Quota limits: `POST /quota-limits`, `GET /quota-limits` (filter `quota_group_id`),
  `GET /quota-limits/{id}`, `PATCH /quota-limits/{id}`.
- Model aliases: `POST /model-aliases`, `GET /model-aliases`, `GET /model-aliases/{id}`,
  `PATCH /model-aliases/{id}` (deactivate/reactivate via `is_active`; no delete).
- Route bindings: `POST /route-bindings`, `GET /route-bindings` (filter `model_alias_id` /
  `provider_account_id`), `GET /route-bindings/{id}`, `PATCH /route-bindings/{id}`.

### Invariants and error translation

- **One active route per alias.** A partial unique index (`model_alias_id WHERE is_active = true`,
  migration `0014`) plus service-layer validation guarantees exactly one active `RouteBinding` per
  model alias. Creating/activating a second active route returns `409 active_route_conflict`; an
  inactive alternate is allowed; deactivate-then-activate swaps are supported; concurrent activations
  produce exactly one winner (translated from the index, never a raw `IntegrityError`/500).
- **Parent consistency.** A route binding's `provider_account_id` must equal its endpoint's
  `provider_account_id`, and its optional quota group must belong to the same account; violations
  return `400 parent_mismatch` and are never persisted.
- **Egress validation.** Endpoint create and `base_destination` updates run the same
  `DestinationPolicy` used before dispatch: non-allowlisted hosts, URL userinfo, metadata/link-local/
  loopback/reserved destinations return `400 destination_denied`; allowlisted private-LAN hosts are
  accepted. A destination the dispatch path would reject is never persisted.
- **PATCH omitted-vs-null.** `external_account_id`, `secret_ref_id`, `upstream_model`,
  `quota_group_id`, `default_output_tokens`, `description`, and `name` (where nullable) distinguish
  omitted (unchanged) from explicit `null` (clear) via `model_fields_set`.
- **Uniqueness.** Duplicate provider/account/alias/quota-group names return `409 resource_conflict`
  (translated, including DB unique-constraint races), never raw constraint text.
- **Secret refs are metadata only.** No raw secret value is accepted or returned; the production
  secret backend remains deferred and `EnvSecretResolver` remains a dev/test convenience.
- **Audit.** Catalog create/update emit immutable audit events (`provider.created`/`updated`,
  `secret_ref.created`, `provider_account.created`/`updated`, `endpoint.created`/`updated`,
  `quota_group.created`/`updated`, `quota_limit.created`/`updated`, `model_alias.created`/`updated`,
  `route_binding.created`/`updated`) with the actor principal ID and safe metadata only; idempotent
  no-op PATCHes emit no event.
- Quota limit edits (`limit_units`/`window_seconds`/`enabled`) take effect on future scheduler
  admission without rewriting historical reservation/window rows; `metric` and `quota_group_id` are
  immutable.

## Accounting admin surface (AGV2-017)

`/admin/v1` now exposes the accounting control plane through the same thin routers + centralized
accounting admin service + authorization/pagination foundations. Accounting resources split into
**deployment-scoped** pricing (route `PricePolicy`/`PriceSnapshot`) and **project-scoped** budget/usage/
ledger state; authorization follows the existing typed permissions `admin:accounting:read`/`write` and
`admin:audit:read`.

### Deployment-scoped pricing (`system_admin` only)

- Price policies: `POST /price-policies`, `GET /price-policies` (filter `route_binding_id`, `enabled`,
  `billing_unit`), `GET /price-policies/{id}`, `PATCH /price-policies/{id}`.
- Price snapshots (immutable, read-only): `GET /price-snapshots` (filter `route_binding_id`,
  `model_alias_id`, `provider_account_id`, `source_price_policy_id`), `GET /price-snapshots/{id}`.

Project roles must not mutate route pricing; snapshots carry no prompt/completion/provider-secret
content.

### Project-scoped budget/usage/ledger (system_admin any project; project_admin read/write own;
project_viewer read own)

- Project budget policies: `POST /project-budget-policies`, `GET /project-budget-policies` (filter
  `project_id`, `enabled`, `currency`), `GET /project-budget-policies/{id}`,
  `PATCH /project-budget-policies/{id}`.
- Budget status/headroom: `GET /projects/{project_id}/budget-status` — one `BudgetStatusRead` per
  applicable policy/current window (`limit_amount`, `committed_amount`, `reserved_amount`, `headroom`,
  `window_start`/`window_end`, `enabled`). A read computes current-window zero state when no persisted
  `BudgetWindow` exists, without fabricating reservation history; negative headroom is allowed after
  honest overage.
- Budget reservations (read-only): `GET /budget-reservations` (filter `project_id`, `request_id`,
  `budget_policy_id`, `state`), `GET /budget-reservations/{id}`. A released pre-dispatch reservation
  returns `price_snapshot_id: null`.
- Usage records (immutable, read-only): `GET /usage-records` (filters incl. `project_id`, `request_id`,
  `principal_id`, `api_credential_id`, `model_alias_id`, `route_binding_id`, `provider_account_id`,
  `billing_unit`, `currency`, `recorded_at` range), `GET /usage-records/{id}`.
- Ledger entries (immutable, append-only, read-only): `GET /ledger-entries` (filter `project_id`,
  `usage_record_id`, `entry_type`, `currency`, `created_at` range), `GET /ledger-entries/{id}`.
  `usage_debit` links to its `UsageRecord`; adjustment entries may have no `usage_record_id`; signed
  fixed-point `Decimal` amounts are preserved exactly.
- Audit reads: `GET /audit-events` (filter `actor_principal_id`, `project_id`, `action`,
  `resource_type`, `resource_id`, `occurred_at` range), `GET /audit-events/{id}` — `admin:audit:read`.
  `system_admin` reads all; project roles read only events explicitly scoped to their authorized
  project; a `project_id = NULL` event is deployment-scoped and not exposed to project roles.

### Invariants and semantics

- **Money is fixed-point `Decimal`** (`Numeric(24,12)`); binary float is rejected at the contract
  boundary. Request-priced policies require `request_price` and forbid input/output price; token-priced
  require `input_price` + `output_price` and forbid `request_price`; `unit_scale > 0`; prices
  non-negative; currency validated.
- **At most one enabled price policy per route** (migration `0008` partial unique index + service
  validation): a conflicting second enabled policy returns `409 price_policy_conflict`; disabled
  historical/edit records may coexist; DB unique races are translated (never raw `IntegrityError`/500).
- **`PriceSnapshot` is immutable historical evidence**; editing a policy never rewrites existing
  snapshot rows.
- **PricePolicy PATCH validates the complete resulting shape**, not just present fields; omitted means
  unchanged, explicit `null` clears a price only when the resulting billing-unit shape stays valid.
- **Budget mutation safety.** `name`, `limit_amount`, `enabled` are mutable for future admission;
  `currency` and `window_seconds` are immutable after creation (changing them requires a replacement
  policy). A PATCH attempting to change an immutable field returns a stable `400`. Edits never rewrite
  historical `BudgetWindow`/`BudgetReservation` rows.
- **No manual ledger adjustment writes** in this task; adjustment HTTP writes stay deferred (policy not
  settled).
- **Price/budget config writes serialize with scheduler admission** using the existing canonical lock
  order (price-policy lock; budget policy-row lock); no reverse-order deadlock is introduced.
- **No-op PATCH emits no audit event**; reads never create usage/ledger/snapshot/reservation history.

### Error translation

Stable codes, never SQL/constraint names/stack traces/secrets: `404 not_found` (cross-project opaque ID
is indistinguishable from nonexistent), `403 forbidden`, `400 invalid_request` (shape/currency/Decimal/
time-range), `400 parent_mismatch`, `409 price_policy_conflict`, plus immutable-field validation errors
for budget currency/window. Conflict races are translated via savepoint + `IntegrityError` recovery.

### Audit

Config state changes emit immutable audit events — `price_policy.created`/`updated` (deployment-scoped,
`project_id = null`) and `project_budget_policy.created`/`updated` (project-scoped) — with the actor
principal ID and safe non-secret metadata only.

## Queue / operator admin surface (AGV2-018)

`/admin/v1/queue` exposes the queue/operator control plane through the same thin routers +
centralized scheduler admin service (`src/aethergate/scheduler/admin.py`) + authorization/pagination
foundations. Two authorization shapes apply:

- **Project-scoped queue reads/cancellation** (`admin:queue:read`/`write`): `system_admin` reads all
  and cancels any eligible request; `project_admin` reads/cancels its own project's requests;
  `project_viewer` reads its own project only. Cross-project opaque request IDs are non-enumerating
  (`404`). Project roles never see `project_id = NULL` requests.
- **Deployment-only operations** require `system_admin` deployment authority **and** `admin:queue:*`
  regardless of scopes: endpoint runtime/slot summaries, pause/drain/resume, global provider-quota
  runtime status, and outcome_unknown reconciliation. A `project_admin` cannot gain operator authority
  merely by holding `admin:queue:*`.

### Endpoints

- Queue reads: `GET /queue/requests` (paginated, filters incl. `project_id`, `principal_id`,
  `api_credential_id`, `model_alias_id`, `endpoint_id`, `state`, `stream`, `wait_reason`, time range),
  `GET /queue/requests/{request_id}`. Safe metadata only — never `payload_encrypted`,
  `result_encrypted`, decrypted prompt/completion, stream-event content, `fencing_token`, or provider
  secret material. A queued request under an operator hold surfaces a computed `effective_wait_reason`
  (`endpoint_paused`/`endpoint_draining`) without overwriting persisted quota/budget wait metadata.
- Queue summary: `GET /queue/summary` — side-effect-free aggregate counts by state, queued total,
  in-flight total, `outcome_unknown` total, oldest queued timestamp, counts by effective wait reason.
- Endpoint runtime: `GET /queue/endpoints`, `GET /queue/endpoints/{endpoint_id}` (deployment-only) —
  `operational_state`, catalog `is_active`, `max_concurrency`, `occupied_slots` (active physical
  reservations only), `available_slots = max(0, max_concurrency - occupied_slots)`, `draining_complete`.
- Pause/drain/resume: `POST /queue/endpoints/{endpoint_id}/pause` / `drain` / `resume`
  (deployment-only) — idempotent; audit actual state changes only (`endpoint_dispatch.paused` /
  `.draining` / `.resumed`); durable across restart; no-op repeat creates no duplicate audit.
- Cancellation: `POST /queue/requests/{request_id}/cancel` — returns a typed result distinguishing
  `cancelled_now` / `cancellation_requested` / `already_cancelled` / `terminal` / `outcome_unknown`;
  audit `request.cancelled` vs `request.cancellation_requested`; actor principal/project/resource ID,
  no content.
- outcome_unknown: `GET /queue/outcome-unknown` (deployment-only, safe metadata),
  `POST /queue/requests/{request_id}/reconcile` (deployment-only, disposition `failed` | `cancelled`).
  Repeat reconciliation returns a stable conflict and never double-releases or double-commits.
- Quota runtime status: `GET /queue/quota-status` (deployment-only) — safe runtime metadata per
  configured quota limit/window (`quota_group_id`/name, `provider_account_id`, `quota_limit_id`/name,
  `metric`, `limit_units`, `window_seconds`, window start/end, `committed_units`, `reserved_units`,
  `remaining`, `enabled`, group `cooldown_until`). Never fabricates `QuotaWindow` rows; a missing
  current window reports zero computed state.

### Error translation

Stable codes: `404 not_found` (request/endpoint missing; cross-project non-enumeration),
`403 forbidden`, `400 invalid_request` (invalid reconcile disposition), `409 invalid_lifecycle`
(cancellation of a terminal request; reconciliation of a non-`outcome_unknown` request). No SQL,
stack traces, raw provider errors, or encryption data.

## Human OIDC/session auth surface (AGV2-015)

Human administrators authenticate with an OIDC Authorization Code + PKCE flow and receive a
server-managed browser session that authorizes through the same RBAC engine as service accounts:

- `GET /admin/v1/auth/oidc/login` — start login; `302` redirects to the configured provider's
  authorization endpoint (state/nonce/PKCE generated server-side). `503 oidc_unavailable` if OIDC is
  not configured/enabled.
- `GET /admin/v1/auth/oidc/callback` — complete login (exact `state`, one-time code exchange, ID-token
  validation). On success returns session metadata plus the one-time `csrf_token` and sets the session
  cookie; on failure returns a fixed `401 oidc_authentication_failed` / `400 invalid_login_state` with
  no session created. **Web-console mode** (`AETHERGATE_OIDC_WEB_CALLBACK_PATH` set): instead of JSON,
  a successful callback sets the `HttpOnly` session cookie plus a separate JS-readable `ag_csrf`
  cookie and `302`-redirects to the fixed configured path (the raw CSRF token is never in the URL);
  failures `302` to the same path and the frontend detects the absent session.
- `GET /admin/v1/auth/session` — resolve the current browser session (`SessionRead`).
- `POST /admin/v1/auth/logout` — revoke the session and clear the cookie (CSRF-protected; idempotent,
  `revoked: true/false`).
- `POST /admin/v1/oidc/identities` — link an external identity (`principal_id`, `issuer`, `subject`) to
  a principal (`admin:principals:write`).

All protected `/admin/v1` endpoints accept either a valid `admin`-audience Bearer credential or a valid
browser session cookie; the resulting `AdminRequestContext` flows into the same centralized
authorization service. A supplied `Authorization` header is authoritative and never falls through to a
cookie. Cookie-authenticated `POST/PUT/PATCH/DELETE` requests require `X-CSRF-Token`; Bearer requests
and `GET`/`HEAD` do not. `whoami` reports `authentication_kind` (`service_credential` | `browser_session`)
and populates `api_credential_id` or `browser_session_id` accordingly.

## CLI / device-flow auth surface (AGV2-019)

Human operators authenticate a Linux CLI through a gateway-mediated OAuth device flow and receive a
durable CLI session that authorizes through the same RBAC engine as browser sessions:

- `POST /admin/v1/auth/device/start` — start a device transaction (no auth); returns `user_code`,
  `verification_uri`, `verification_uri_complete` (when the provider supplies it), `expires_in`,
  `interval`, and the provider `device_code` (a bearer secret the CLI must send back on each poll).
  `503 device_flow_unavailable` when device flow is not configured/enabled.
- `POST /admin/v1/auth/device/poll` — submit the raw `device_code` in the JSON body; returns a typed
  status (`pending` | `slow_down` | `success` | `access_denied` | `expired_token`). `success`
  returns the raw CLI session token exactly once; terminal states consume the one-time transaction.
- `POST /admin/v1/auth/cli/logout` — revoke the current CLI session (idempotent).

`GET /admin/v1/whoami` now reports `authentication_kind` including `cli_session`, populating
`cli_session_id` for CLI sessions and `api_credential_id` / `browser_session_id` for the other kinds.

All protected `/admin/v1` endpoints accept an admin credential (`agk_...`), a browser session
cookie, or a CLI session Bearer (`ags_...`). The routing rule in `_admin_context` is
prefix-dispatched: `ags_` resolves as a CLI session; an otherwise-valid `agk_` credential resolves
as a service credential. A supplied `Authorization` header is authoritative and never falls through
to a cookie; CLI bearer requests do not require CSRF. Device-flow errors use the standard envelope
with stable codes: `503 device_flow_unavailable`, `404 device_code_invalid`, `401
device_access_denied` / `device_expired` / `cli_session_invalid`, and a `400 slow_down`-shaped poll
is never an error (it is a typed poll status).

## Operational observability surface (AGV2-023)

`/admin/v1/observability` exposes read-only operational metrics derived from
durable scheduler facts (see `docs/architecture/observability.md`):

- `GET /admin/v1/observability/summary` — window metadata plus queue-wait,
  streaming-TTFT, and retry metrics. Query: optional `window_seconds`
  (60..86400, default 900) and optional `project_id`. Authorization uses the
  existing `admin:queue:read` permission (no new scope): `system_admin` reads the
  deployment aggregate or an explicit project; `project_admin`/`project_viewer`
  read only their own project; a cross-project project-role query is
  non-enumerating (`404`).
- `GET /admin/v1/observability/upstreams` — paginated passive per-endpoint
  upstream health. `system_admin` deployment scope only; project roles get `403`
  and can never enumerate endpoint/provider-account health.

No prompt/completion/provider-secret material appears in either response.
Percentiles are NULL with no samples (never fabricated zero); success rate is
NULL with no definitive upstream evidence.

## Deferred

- Concrete schema/OpenAPI layout for `/admin/v1` (owned by the `contracts` workstream, established first).
- Import/export format and the diff/apply UX details.
- Whether `PATCH` (RFC 7386) or typed PATCH bodies are used per resource — decided with contracts.
