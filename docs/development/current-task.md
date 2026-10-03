# AetherGate v2 — Current Task

## Task ID
AGV2-002

## Title
Establish contracts and domain-model foundation

## Ownership
Primary: contracts  
Coordinating: identity/auth, catalog/routing, scheduler, accounting/audit, admin API, platform/testing

## Before You Start

1. Work on branch `v2`.
2. Run `git pull --ff-only origin v2` before making changes.
3. Read, in this order:
   - `AGENTS.md`
   - `project_spec.md`
   - `docs/development/agent-handoff.md`
   - `docs/development/agent-workstreams.md`
   - `docs/architecture/v2-overview.md`
   - `docs/architecture/provider-model.md`
   - `docs/architecture/scheduler.md`
   - `docs/architecture/admin-api.md`
   - `docs/audits/v2-architecture-audit-2026-10-03.md`
4. Treat the dated audit as historical evidence and `project_spec.md` as the current product direction.
5. Do not commit unrelated untracked support files or agent scaffolding.
6. Do not modify the v1 runtime under `app/` or `frontend/src/` in this task.

## Goal

Create the first executable v2 foundation that other workstreams can import without coupling to
persistence or provider implementations.

This task establishes:

- the Python v2 package skeleton;
- shared domain identifiers/value objects/enums;
- cross-domain entity contracts;
- initial admin resource DTOs;
- the pinned OpenAI compatibility baseline;
- deterministic unit/contract tests for those foundations.

It does **not** implement database persistence, scheduling, provider calls, authentication, billing,
or HTTP routes.

## OpenAI Compatibility Baseline

Before finalizing the contract documentation, verify the current official OpenAI API reference and
the official `openai/openai-openapi` repository.

Record the exact upstream OpenAPI commit SHA used as the compatibility baseline.

The initial AetherGate v2 compatibility target is:

- `GET /v1/models`
- model retrieval where supported by the current official contract
- `POST /v1/chat/completions`
- `POST /v1/responses`
- `POST /v1/embeddings`

Responses is the preferred modern API surface, but Chat Completions remains a supported
compatibility surface.

Do not vendor the entire multi-megabyte upstream OpenAPI file in this task.

Create:

`docs/contracts/openai-compatibility-baseline.md`

Document:

- upstream repository/source;
- exact commit SHA/date used;
- target AetherGate endpoints;
- compatibility principles;
- additive-field tolerance;
- streaming/event compatibility expectations;
- request-id/error compatibility expectations;
- which OpenAI features are deliberately not yet implemented.

Do not claim compatibility for functionality that AetherGate has not implemented/tested.

## Package Foundation

Create a standard package layout under:

`src/aethergate/`

Do not move or delete the existing v1 `app/` package.

Recommended initial structure:

```text
src/aethergate/
├── __init__.py
├── contracts/
│   ├── __init__.py
│   ├── common.py
│   └── admin_v1.py
└── domain/
    ├── __init__.py
    ├── ids.py
    ├── enums.py
    ├── value_objects.py
    └── entities.py
```

The exact file split may change if there is a strong reason, but preserve the contracts/domain
boundary.

Add the smallest appropriate `pyproject.toml` needed to package and test the v2 code.

Do not remove or rewrite the existing `requirements.txt` or Docker runtime configuration in this
task.

Use Python 3.12+.

## Domain Identifiers

Define explicit typed identifiers for the core resources. The implementation may use UUID internally,
but public/admin identifiers must be treated as opaque values by consumers.

At minimum cover:

- ProjectId
- PrincipalId
- ApiCredentialId
- ProviderId
- ProviderAccountId
- SecretRefId
- EndpointId
- QuotaGroupId
- ModelAliasId
- RouteBindingId
- RequestId
- ExecutionAttemptId
- ReservationId
- UsageRecordId
- PriceSnapshotId
- LedgerEntryId
- AuditEventId

Do not encode database table names or integer auto-increment assumptions into public contracts.

Exact external prefix/format is still allowed to remain deferred.

## Domain Concepts

Establish typed, persistence-independent contracts for at least these concepts:

### Identity / access
- Project
- Principal
- ApiCredential metadata

### Catalog / routing
- Provider
- ProviderAccount
- SecretRef
- Endpoint
- QuotaGroup
- ModelAlias
- RouteBinding

### Scheduler / execution
- InferenceRequest
- ExecutionAttempt
- Reservation

### Accounting / audit
- UsageRecord
- PriceSnapshot
- LedgerEntry
- AuditEvent

These are domain contracts, not ORM models.

Avoid bidirectional object graphs and implicit lazy relationships.

Reference other resources by typed ID.

## Required Separation of Concerns

The domain model must preserve these separations from `project_spec.md`:

- authorization / entitlement
- quota / capacity policy
- budget policy
- usage accounting
- pricing
- settlement / billing

Do not make balance a field that universally determines authorization.

Do not make prepaid billing mandatory.

Do not foreclose prepaid billing as a later policy/module.

Use `Decimal` or fixed-point-safe types for monetary values. Never use binary floating point for
money/pricing contracts.

## Provider / Routing Model

The model must keep these separate:

- Provider: adapter/provider type and capability metadata.
- ProviderAccount: credential/account boundary and provider-side account/project identity.
- SecretRef: reference to secret material; never the secret itself.
- Endpoint: actual deployment/base destination and physical capacity configuration.
- QuotaGroup: shared allowance scope that may span multiple routes/endpoints/models.
- ModelAlias: stable public model identity.
- RouteBinding: permitted route from alias to endpoint/account with policy metadata.

Creating an additional alias or route must not imply additional provider capacity.

Do not put plaintext provider credentials into any DTO/entity.

## Scheduler States

Define explicit state enums/contracts consistent with the existing architecture direction.

The request lifecycle must be able to represent:

- validated
- queued
- reserved
- dispatched
- streaming
- succeeded
- failed
- cancelled
- expired
- outcome_unknown

Execution-attempt state may be modeled separately when useful.

Do not implement scheduler transitions or database locking in this task.

## Initial Admin v1 DTOs

Create persistence-independent request/response DTO foundations for the resources needed by the
future `/admin/v1` API.

At minimum establish read/create/update shapes for:

- providers
- provider accounts
- endpoints
- quota groups
- model aliases
- route bindings
- projects
- principals
- API credentials metadata

Requirements:

- stable opaque IDs;
- explicit optionality;
- no secret values on normal read DTOs;
- no database-specific fields;
- no business logic in DTOs;
- no assumption that the web console is the only client.

Do **not** finalize every route/path/filter or implement FastAPI routers yet.

Document unresolved admin-contract choices in `docs/contracts/admin-v1-foundation.md`.

## Testing

Create a deterministic offline test foundation under `tests/`.

No provider/network calls.

Tests must verify at least:

1. monetary/price fields do not use float;
2. provider-account/read DTOs cannot serialize plaintext secret material;
3. resource references use typed IDs rather than integer database IDs;
4. request-state contract includes `outcome_unknown`;
5. model alias is distinct from endpoint/provider identity;
6. creating/serializing a RouteBinding does not imply quota ownership;
7. DTO validation rejects clearly invalid required values;
8. package imports work from a clean test process.

If using Pydantic, target Pydantic v2 semantics and make that explicit in packaging metadata.

Keep the dependency addition minimal.

## Documentation

Create:

- `docs/contracts/openai-compatibility-baseline.md`
- `docs/contracts/admin-v1-foundation.md`
- `docs/contracts/domain-model.md`

Update existing architecture docs only when necessary to keep them consistent with the concrete
contracts.

Do not modify the dated audit.

## Out of Scope

Do not implement:

- PostgreSQL models or repositories;
- Alembic migrations;
- scheduler dispatch logic;
- Redis;
- LiteLLM calls;
- provider retries;
- OIDC/session authentication;
- budget reservation/ledger settlement logic;
- FastAPI admin routes;
- OpenAI runtime proxy routes;
- React changes;
- Docker/deployment changes.

## Verification

Before committing:

1. Run the new unit/contract tests.
2. Run lint/type checks introduced by this task.
3. Verify `app/` and `frontend/src/` are untouched.
4. Verify the dated audit is unchanged.
5. Inspect `git diff --check`.
6. Inspect the complete diff for unrelated files.
7. Ensure no credentials/secrets are present.

## Handoff

At the end, update:

`docs/development/agent-handoff.md`

Keep it concise and include:

- branch;
- starting commit;
- implementation commit(s);
- files/components changed;
- contracts established;
- decisions made;
- decisions deferred;
- exact verification commands/results;
- risks/issues;
- one recommended next step.

Do not put credentials or large logs in the handoff.

## Commit and Push

Commit the task on branch `v2` using conventional commits.

A reasonable primary commit message is:

`feat(contracts): establish v2 domain foundation`

A separate handoff-only follow-up commit is allowed if needed to record the primary commit SHA.

**Push all completed commits to `origin/v2`.**

Never push directly to `main`.

The task is not complete until `origin/v2` contains the completed commits and the updated handoff.
