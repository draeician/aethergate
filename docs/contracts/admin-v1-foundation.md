# Admin v1 Contract Foundation

Living document. Records the initial `/admin/v1` DTO foundation and the choices that
remain unresolved. The concrete DTOs live in
`src/aethergate/contracts/admin_v1.py`; this document describes intent and deferred
decisions. FastAPI routes, paths, and filters are **not** finalized yet.

## Established DTO shapes

Read/create/update foundations exist for: providers, provider accounts, endpoints, quota
groups, quota limits, model aliases, route bindings, projects, principals, and API credential
metadata.

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

These are service/repository/contract foundations only; no HTTP admin CRUD router exists yet.

Quota DTOs (scheduler phase 2):

- `QuotaGroupCreate` requires `provider_account_id`; `QuotaGroupRead` exposes it.
- `QuotaLimitCreate` validates a positive `limit_units`, positive `window_seconds`, and a valid
  `metric` enum (`requests`|`tokens`); `QuotaLimitRead`/`QuotaLimitUpdate` mirror it.
- `RouteBindingCreate`/`Read`/`Update` carry `default_output_tokens` (positive) and
  `quota_group_id`, for the default bounded output-token reservation.

Accounting DTOs (AGV2-010/011):

- `PricePolicyCreate`/`Read`/`Update` — route pricing (request or token billing unit), currency,
  positive `unit_scale`, non-negative `request_price`/`input_price`/`output_price`, `enabled`.
  Billing-unit price shape is validated at the DTO boundary: request billing requires `request_price`
  and rejects token prices; token billing requires both input and output prices and rejects
  `request_price`; explicit zero prices are allowed. `PricePolicyUpdate` rejects incompatible price
  fields (partial updates cannot require a price).
- `ProjectBudgetPolicyCreate`/`Read`/`Update` — project spending-cap policy (positive
  `limit_amount`, positive `window_seconds`, currency, `enabled`). Update DTOs do not expose mutable
  historical accounting fields.
- `BudgetStatusRead` — project/policy headroom (`limit - committed - reserved`), current
  window start/end.
- `BudgetReservationRead` — per-request reservation (reserved/committed, state, settlement reason).
- `UsageRecordRead` — measured usage read shape (no content/secrets).
- `LedgerEntryRead` — ledger entry read shape (signed amount, entry type, idempotency key, reason).

No admin HTTP CRUD routes yet; these are the persistence/transport-independent foundations.

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
