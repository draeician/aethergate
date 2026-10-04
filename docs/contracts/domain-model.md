# Domain Model

Living document. Describes the persistence- and provider-independent domain contracts in
`src/aethergate/domain/`. These are contracts, not ORM models: entities reference other
resources by typed ID, with no bidirectional object graphs or lazy relationships.

## Typed identifiers

`domain/ids.py` defines one distinct `str`-subclass type per core resource
(`ProjectId`, `ProviderId`, `ProviderAccountId`, `ModelAliasId`, `RequestId`, etc.).
Consumers treat these as opaque values; the implementation may use UUIDs internally.
Empty/whitespace identifiers are invalid, and database auto-increment or table names are
not part of any contract.

## Enumerations

`domain/enums.py` defines `Capability`, `BillingUnit`, `PrincipalKind`, `RequestState`,
`ExecutionAttemptState`, `LedgerEntryType`, `QuotaMetric` (`requests`|`tokens`), and
`QuotaReservationState` (`reserved`|`committed`|`released`), plus `BudgetReservationState`
(`reserved`|`committed`|`released`), `CredentialAudience` (`inference`|`admin`), and
`CredentialScope` (`inference:invoke`). `LedgerEntryType` is `usage_debit`|`adjustment_credit`|
`adjustment_debit`. `RequestState` includes the full
lifecycle through `outcome_unknown`:

`validated -> queued -> reserved -> dispatched -> streaming -> succeeded/failed/cancelled/expired`
plus `outcome_unknown`.

`outcome_unknown` is resolved only by explicit operator reconciliation: an operator supplies a
disposition (`failed`/`cancelled`/`succeeded`) for a request whose post-dispatch lease expired, and
the reconciliation action is recorded durably (`reconciled_state`, `reconciled_at`, `reconciled_by`)
as part of releasing the held reservation. There is no automatic replay or bulk slot release.

## Value objects

`domain/value_objects.py` defines `Money`, `NonNegativeMoney`, and `PositiveMoney` as fixed-point
`Decimal` values, `Currency` (normalized uppercase ISO-style three-letter code), and
`quantize_money()` (rounds half-up to `MONEY_PRECISION = 12`, `MONEY_QUANTUM = 1e-12`). Binary
floating point (`float`) is rejected for money and pricing. There is no
`balance` field that universally determines authorization.

## Entities

`domain/entities.py` groups contracts by domain:

- Identity/access: `Project`, `Principal`, `ApiCredential`, `RequestContext` (see
  "Identity and request context" below).
- Catalog/routing: `Provider`, `ProviderAccount`, `SecretRef`, `Endpoint`, `QuotaGroup`,
  `QuotaLimit`, `ModelAlias`, `RouteBinding`.
- Scheduler/execution: `InferenceRequest`, `ExecutionAttempt`, `Reservation`.
- Accounting/audit: `PricePolicy`, `PriceSnapshot`, `ProjectBudgetPolicy`, `BudgetWindow`,
  `BudgetReservation`, `UsageRecord`, `LedgerEntry`, `AuditEvent`.

### Identity and request context (AGV2-012)

- `ApiCredential` is a one-way-verifiable scoped client credential. It carries a stable opaque
  `ApiCredentialId`, `project_id`, optional `principal_id`, `name`, non-secret `key_prefix`, SHA-256
  `key_hash` verifier, `audience` (`CredentialAudience.INFERENCE`/`ADMIN`), `scopes` (phase 1
  `CredentialScope.INFERENCE_INVOKE`), and lifecycle timestamps (`created_at`, `expires_at`,
  `revoked_at`, optional `last_used_at`, `is_active`). The raw key is never a field; `secret_ref_id`
  was removed from the credential contract because retrievable-secret semantics do not fit
  high-entropy client keys (upstream provider secrets still use `SecretRef`).
- `RequestContext` is a frozen typed value resolved from an authenticated request: `project_id`,
  `principal_id`, `api_credential_id`, `audience`, `scopes`. The scheduler/admission stack carries it
  (not a bare tuple) so usage/accounting attribution stays correct; it is safe to log by opaque IDs
  only and can never be client-overridden in the JSON body.
- `CredentialAudience` (`inference` | `admin`) and `CredentialScope` (`inference:invoke`) are
  extensible `StrEnum`s so future resource/admin scopes can be added without replacing the model.

### Accounting entities (AGV2-010)

- `PricePolicy` — mutable pricing configuration associated with a `RouteBinding` (request or token
  billing unit, `currency`, positive `unit_scale`, non-negative prices, `enabled`). Editable; not the
  historical record. Billing-unit price shape is validated: request billing requires `request_price`
  and rejects token prices; token billing requires both `input_price` and `output_price` and rejects
  `request_price`. Missing price is not equivalent to zero (an explicit `Decimal("0")` is a valid zero
  price; a missing required price is rejected).
- `PriceSnapshot` — immutable capture of the effective price at a dispatch decision (source policy
  ID, route binding, provider account, model alias, billing unit, currency, unit scale, prices,
  captured timestamp). Never updated/deleted in normal operation.
- `ProjectBudgetPolicy` — optional project spending-cap policy (name, currency, positive
  `limit_amount`, positive `window_seconds`, `enabled`). Not a prepaid balance.
- `BudgetWindow` — authoritative committed/reserved monetary amounts per policy window.
- `BudgetReservation` — per-request monetary reservation (request, policy, snapshot, window,
  reserved/committed amounts, state, settlement reason).
- `UsageRecord` — immutable measured usage (one per request; no content/secrets).
- `LedgerEntry` — immutable append-only entry (`usage_debit` references a `UsageRecord`;
  adjustments do not; signed typed Decimal amount, idempotency key, optional reason).

## Separation of concerns

The model keeps these concepts separate (per `project_spec.md`):

- authorization / entitlement (project/principal/credential identity)
- quota / capacity policy (`QuotaGroup`, `Reservation`)
- budget policy (`ProjectBudgetPolicy`, `BudgetReservation`) — optional spending cap, not a
  mandatory balance gate
- usage accounting (`UsageRecord`)
- pricing (`PricePolicy` mutable, `PriceSnapshot` immutable)
- settlement / billing (`LedgerEntry`)

Prepaid billing is not mandatory and is not foreclosed; a positive balance is not a
universal authorization requirement.

## Provider / routing model

- `Provider` — adapter/provider type and capability metadata.
- `ProviderAccount` — credential/account boundary and provider-side account/project identity
  (never inferred from URL).
- `SecretRef` — reference to secret material; never the material itself.
- `Endpoint` — deployment/base destination and physical capacity configuration. Since scheduler
  phase 1 it carries `max_concurrency`, the physical concurrency limit for that endpoint/deployment.
  Capacity belongs to the physical endpoint, never to a public alias.
- `QuotaGroup` — shared allowance scope across routes/endpoints/models. Since scheduler phase 2 it
  belongs to exactly one `ProviderAccount` (`provider_account_id`, required) and is the scope on
  which a provider `429` cooldown is applied.
- `QuotaLimit` — a single configured limit inside a group: `metric` (`requests`|`tokens`), positive
  `limit_units` and `window_seconds`, `enabled`, optional `name`. Typed `QuotaLimitId` added.
- `ModelAlias` — stable public model identity (distinct from endpoint/provider identity).
- `RouteBinding` — permitted route from alias to endpoint/account with policy metadata.
  Since the first inference milestone it also carries `upstream_model`, the provider-facing
  model/deployment identifier to invoke. `upstream_model` is provider-specific opaque
  configuration kept separate from the public `ModelAlias.name` and never derived implicitly
  from it. A route without a configured `upstream_model` is unresolved and cannot be dispatched.
  Since scheduler phase 2 it also carries `quota_group_id` (association, not ownership) and
  `default_output_tokens` (the default bounded output reservation used when a token-quota request
  omits `max_tokens`).

Creating an additional alias or route does not imply additional provider capacity, and
referencing a quota group on a route does not imply quota ownership.
