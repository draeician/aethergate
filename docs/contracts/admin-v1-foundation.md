# Admin v1 Contract Foundation

Living document. Records the initial `/admin/v1` DTO foundation and the choices that
remain unresolved. The concrete DTOs live in
`src/aethergate/contracts/admin_v1.py`; this document describes intent and deferred
decisions. FastAPI routes, paths, and filters are **not** finalized yet.

## Established DTO shapes

Read/create/update foundations exist for: providers, provider accounts, endpoints, quota
groups, quota limits, model aliases, route bindings, projects, principals, API credential
metadata, and secret-reference metadata.

- Create shapes carry required fields plus defaults.
- Read shapes carry stable opaque IDs and no secret material.
- Update shapes use explicit optionality (all fields optional) for PATCH semantics.

API credential lifecycle DTOs (AGV2-012):

- `ApiCredentialCreate` — `project_id`, optional `principal_id`, `name`, `audience`
  (`CredentialAudience.INFERENCE` by default), `scopes` (defaults derived from `audience`:
  `inference:invoke` for inference, none for admin), optional `expires_at`. Creation is validated
  before a key is issued: project/principal must exist, be active, and be correctly matched, and an
  admin credential carrying `inference:invoke` is rejected.
- `ApiCredentialRead` — metadata only: `id`, `project_id`, optional `principal_id`, `name`,
  `key_prefix`, `audience`, `scopes`, `created_at`, optional `expires_at`/`revoked_at`, `is_active`.
  Never carries the raw key or its hash/verifier.
- `ApiCredentialCreateResult` / `ApiCredentialRotateResult` — the one-time raw key is revealed only
  at the create/rotate boundary alongside `ApiCredentialRead` metadata.
- `ApiCredentialRevokeRequest` / `ApiCredentialRevokeResult` — revocation reason (optional) and the
  resulting revoked metadata. Revocation is idempotent: the original `revoked_at` is preserved on
  repeated revokes.
- `ApiCredentialUpdate` — optional `name` / `is_active` for PATCH semantics.
- Rotation is an active-credential operation: rotating a revoked/expired/inactive credential fails
  with no replacement credential created.

Admin identity DTOs (AGV2-013):

- `BootstrapResult` — the one-time bootstrap response: `credential` (`ApiCredentialRead`) plus the
  single `raw_key` reveal of the initial `system_admin` credential. No secret is ever re-readable.
- `WhoamiRead` — safe metadata for the authenticated admin key: `principal_id`, `project_id`,
  `api_credential_id`, `audience`, `scopes`, `roles`. Never the raw key, hash, or verifier.
- `RoleAssignmentRead` — durable role-grant metadata: `id`, `principal_id`, `role`,
  `resource_scope_type`, optional `resource_id`, `created_by`, `revoked_at`, `is_active`, timestamps.
- `ApiCredentialCreate.scopes` defaults to `None` (audience-derived at the service boundary), keeping
  the DTO persistence- and audience-neutral; inference resolves to `inference:invoke` and admin to
  none.

Admin CRUD DTOs (AGV2-014):

- `RoleAssignmentCreate` — `principal_id`, `role`, `resource_scope_type`, optional `resource_id`
  (required for project-scoped roles; absent for deployment). `RoleAssignmentRevokeRequest` carries an
  optional `reason`. Role/scope coherence and privilege-escalation are enforced centrally in the
  service, not the DTO.
- `PrincipalCreate` no longer carries `project_id`: the project is the path segment
  (`POST /admin/v1/projects/{project_id}/principals`); it carries `kind`, `name`, `is_active`.
- List endpoints return a shared `Page[T]` shape (`items`, `limit`, `offset`, `total`) with `limit`
  bounded `1..200` (default 50) and stable sort.

These DTOs now back the full `/admin/v1` identity CRUD surface (project/principal/role-assignment/
credential list/create/read/update/revoke) introduced in AGV2-014.

Catalog/routing admin DTOs (AGV2-016):

- `SecretRefCreate`/`SecretRefRead` — metadata-only secret reference (`id`, `name`, `created_at`).
  No raw secret value is ever accepted or returned; there is no delete/rotate in this task.
- `ProviderCreate`/`Read`/`Update` — `kind`, `name`, `capabilities`, `is_active`. `kind` is a
  non-empty opaque string, not a restrictive enum (LiteLLM-backed providers must not be
  over-constrained).
- `ProviderAccountCreate`/`Read`/`Update` — `provider_id`, `name`, `external_account_id` (optional),
  `secret_ref_id` (optional), `is_active`. `provider_id` is immutable; PATCH distinguishes omitted
  vs explicit `null` for `external_account_id`/`secret_ref_id`.
- `EndpointCreate`/`Read`/`Update` — `provider_account_id`, `name`, `base_destination`,
  `max_concurrency` (>=1), `is_active`. `provider_account_id` is immutable; `base_destination`
  changes are egress-validated.
- `QuotaGroupCreate`/`Read`/`Update` — `provider_account_id` (immutable), `name`, `description`.
- `QuotaLimitCreate`/`Read`/`Update` — `quota_group_id` (immutable), `metric`
  (`requests`|`tokens`, immutable), positive `limit_units`/`window_seconds`, `enabled`, optional
  `name`. Only `limit_units`/`window_seconds`/`enabled`/`name` are mutable.
- `ModelAliasCreate`/`Read`/`Update` — `name`, `capabilities`, `is_active`. No delete; deactivate
  via `is_active`.
- `RouteBindingCreate`/`Read`/`Update` — `model_alias_id`, `endpoint_id`, `provider_account_id`,
  `upstream_model` (explicit provider-facing config, never inferred from the alias name),
  `quota_group_id` (optional), `default_output_tokens` (positive when present), `is_active`.
  PATCH distinguishes omitted vs explicit `null` for `upstream_model`/`quota_group_id`/
  `default_output_tokens`.

These DTOs back the full `/admin/v1` catalog/routing CRUD surface (provider/secret-ref/account/
endpoint/quota-group/quota-limit/alias/route-binding list/create/read/update) introduced in AGV2-016,
reusing the shared `Page[T]` shape and PATCH omitted-vs-null semantics.

Human OIDC/session DTOs (AGV2-015):

- `SessionRead` — safe current-session metadata: `principal_id`, `project_id`,
  `authentication_kind` (`browser_session`), `browser_session_id`, `roles`, and optional safe
  `issuer`/`subject` display fields. Never the session cookie, CSRF token, or any OIDC token.
- `SessionEstablished` — the successful-callback result: `session` (`SessionRead`) plus the one-time
  `csrf_token` and the `csrf_header` name (`X-CSRF-Token`). The CSRF token is revealed exactly once
  here for the in-memory web client and never re-exposed.
- `LogoutResult` — `revoked: bool` (idempotent; repeated logout is safe).
- `ExternalIdentityCreate` — `principal_id`, `issuer`, `subject` (the linking request).
- `ExternalIdentityRead` — safe metadata: `id`, `principal_id`, `issuer`, `subject`, optional
  `email`/`display_name`, `created_at`, `last_login_at`, `is_active`. Never raw tokens.
- `WhoamiRead` gains `authentication_kind` and optional `browser_session_id`, distinguishing
  service-credential vs browser-session callers (credential fields are absent for browser sessions).

Quota DTOs (scheduler phase 2):

- `QuotaGroupCreate` requires `provider_account_id`; `QuotaGroupRead` exposes it.
- `QuotaLimitCreate` validates a positive `limit_units`, positive `window_seconds`, and a valid
  `metric` enum (`requests`|`tokens`); `QuotaLimitRead`/`QuotaLimitUpdate` mirror it.
- `RouteBindingCreate`/`Read`/`Update` carry `default_output_tokens` (positive) and
  `quota_group_id`, for the default bounded output-token reservation.

Accounting DTOs (AGV2-010/011, refined AGV2-017):

- `PricePolicyCreate`/`Read`/`Update` — route pricing (request or token billing unit), currency,
  positive `unit_scale`, non-negative `request_price`/`input_price`/`output_price`, `enabled`.
  Billing-unit price shape is validated at the DTO boundary: request billing requires `request_price`
  and rejects token prices; token billing requires both input and output prices and rejects
  `request_price`; explicit zero prices are allowed. `PricePolicyUpdate` rejects incompatible price
  fields (partial updates cannot require a price).
- `ProjectBudgetPolicyCreate`/`Read`/`Update` — project spending-cap policy (positive
  `limit_amount`, positive `window_seconds`, currency, `enabled`). Update DTOs do not expose mutable
  historical accounting fields (`currency`/`window_seconds` are immutable after creation).
- `BudgetStatusRead` — project/policy headroom (`limit - committed - reserved`), current
  window start/end, plus `enabled` state.
- `BudgetReservationRead` — per-request reservation (reserved/committed, state, settlement reason);
  `price_snapshot_id` is **nullable** to match a released/detached pre-dispatch reservation.
- `PriceSnapshotRead` — immutable snapshot read shape (route/model/account/source-policy attribution,
  billing unit, currency, prices, captured timestamp; no content/secrets).
- `UsageRecordRead` — measured usage read shape (no content/secrets).
- `LedgerEntryRead` — ledger entry read shape (signed amount, entry type, idempotency key, reason).
- `AuditEventRead` — safe audit read shape (`actor_principal_id`, optional `project_id`, `action`,
  `resource_type`, `resource_id`, `occurred_at`, safe details; never secret material).

These DTOs now back the full `/admin/v1` accounting surface introduced in AGV2-017: route
price-policy CRUD, immutable price-snapshot reads, project budget-policy CRUD, budget-status/headroom,
budget-reservation reads, usage/ledger reads, and deployment-vs-project-scoped audit reads, all reusing
the shared `Page[T]` shape and PATCH omitted-vs-null semantics. A minimal protected admin surface
(bootstrap, whoami, credential list/create/rotate/revoke) exists since AGV2-013 to prove the identity
model; the remaining resource CRUD reuses the same DTOs and services.

## Requirements honored

- Stable opaque resource IDs (typed `*Id` values, no database auto-increment or table names).
- Explicit optionality: omitted vs `null` are distinct and modeled via `Optional` fields.
- No secret values on normal read DTOs; secret material is referenced via `SecretRefId`.
- No database-specific fields (no table names, integer PKs, or ORM state).
- No business logic in DTOs (validation only).
- No assumption that the web console is the only client.

## Deferred decisions

The following are intentionally unresolved and will be finalized with the `contracts`
workstream and integrator:

- Concrete routes/paths and HTTP verbs per resource.
- Filtering, sorting, and server-pagination query conventions.
- Optimistic versioning / concurrency-token header name and semantics.
- The one-time secret reveal shape for API credential creation/rotation is now established at the
  DTO level (`ApiCredentialCreateResult.raw_key` / `ApiCredentialRotateResult.raw_key`); the
  concrete HTTP route/path and transport for that boundary remain unfinalized. The material is
  never placed in a read DTO.
- Import/diff/apply ("safe import") payload format and preview UX.
- `PATCH` body style (RFC 7386 vs typed partial bodies) per resource.

## Relationship to runtime

The admin API will be thin routers over service contracts; these DTOs are the persistence-
and transport-independent foundation those routers will consume.
