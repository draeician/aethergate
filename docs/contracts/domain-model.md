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
`ExecutionAttemptState`, and `LedgerEntryType`. `RequestState` includes the full lifecycle
through `outcome_unknown`:

`validated -> queued -> reserved -> dispatched -> streaming -> succeeded/failed/cancelled/expired`
plus `outcome_unknown`.

## Value objects

`domain/value_objects.py` defines `Money` and `NonNegativeMoney` as fixed-point `Decimal`
values. Binary floating point (`float`) is rejected for money and pricing. There is no
`balance` field that universally determines authorization.

## Entities

`domain/entities.py` groups contracts by domain:

- Identity/access: `Project`, `Principal`, `ApiCredential` (material behind a `SecretRefId`).
- Catalog/routing: `Provider`, `ProviderAccount`, `SecretRef`, `Endpoint`, `QuotaGroup`,
  `ModelAlias`, `RouteBinding`.
- Scheduler/execution: `InferenceRequest`, `ExecutionAttempt`, `Reservation`.
- Accounting/audit: `UsageRecord`, `PriceSnapshot`, `LedgerEntry`, `AuditEvent`.

## Separation of concerns

The model keeps these concepts separate (per `project_spec.md`):

- authorization / entitlement (project/principal/credential identity)
- quota / capacity policy (`QuotaGroup`, `Reservation`)
- budget policy (later policy modules; not a mandatory balance gate)
- usage accounting (`UsageRecord`)
- pricing (`PriceSnapshot`, immutable)
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
- `QuotaGroup` — shared allowance scope across routes/endpoints/models.
- `ModelAlias` — stable public model identity (distinct from endpoint/provider identity).
- `RouteBinding` — permitted route from alias to endpoint/account with policy metadata.
  Since the first inference milestone it also carries `upstream_model`, the provider-facing
  model/deployment identifier to invoke. `upstream_model` is provider-specific opaque
  configuration kept separate from the public `ModelAlias.name` and never derived implicitly
  from it. A route without a configured `upstream_model` is unresolved and cannot be dispatched.

Creating an additional alias or route does not imply additional provider capacity, and
referencing a quota group on a route does not imply quota ownership.
