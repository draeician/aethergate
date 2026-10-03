# AetherGate v2 — Architecture Overview

Living document. Derived from `docs/audits/v2-architecture-audit-2026-10-03.md`, which remains the
dated, immutable baseline. Distinguish **settled** (required) from **direction** (current approach)
and **deferred** (deliberately open).

## Product shape

A company-operated gateway publishes authorized model aliases through an OpenAI-compatible data API.
Administrators configure providers, provider accounts, endpoints/deployments, models, routing,
users/projects, API keys, quotas, budgets, and policies using a web console or a Linux CLI, both
backed by the same versioned management API. Valid requests wait within bounded, documented
deadlines when provider capacity is temporarily unavailable.

## Topology

- **Modular monolith** with separate API and execution-worker entrypoints from one codebase.
  (Settled — not a fleet of microservices.)
- **API entrypoint**: authentication, request validation, enqueue, and thin protocol/status routers.
- **Worker entrypoint**: claims queued work, dispatches to providers, streams responses, settles
  accounting.
- **PostgreSQL** is the authoritative store for identities, configuration, durable queued work,
  quota reservations, accounting, and audit. (Direction)

## Transactional authority

For the first enterprise release, admission and budget reservation stay in the same transactional
authority (PostgreSQL) rather than splitting correctness across SQL and Redis. Caching/wake-up
infrastructure is added only after measurement and is never a second quota authority. No database
transaction remains open while waiting for inference. (Direction)

## Domain model (see provider-model.md)

Organization/project, user/service-account principal, scoped API credential, provider, provider
account, secret reference, endpoint/deployment, public model alias, route binding, shared quota
group, policy, request, execution attempt, reservation, usage record, ledger entry, and
administrative audit event.

## Execution lifecycle (see scheduler.md)

`validated -> queued -> reserved -> dispatched -> streaming -> succeeded/failed/cancelled/expired`,
plus `outcome_unknown` for ambiguous failures.

## Security (see security.md)

OIDC identities, role/resource authorization, encrypted/secret-referenced upstream credentials,
server-managed sessions, approved egress, content logging off by default, fail-closed bootstrap.

## Administration (see admin-api.md)

`/admin/v1` with typed schemas, stable opaque resource IDs, PATCH semantics, optimistic versioning,
pagination, filtering, and safe import/diff/apply. CLI and web console share these contracts.

## Delivery sequence (summary from the audit)

1. Contain current risks (reject unsafe admin config and unknown/inactive routes; correct protocol
   serialization; stop SQL/prompt leakage; repair destructive audit behavior; pin dependencies and
   streaming proxy settings).
2. Establish v2 contracts, authoritative data model, migrations, and identity; build and test a
   repeatable migration from a copy of the old database.
3. Implement scheduler, execution adapters, cancellation/recovery, and ledger; pass concurrency and
   fault tests before cosmetic features.
4. Move web and Linux CLI onto the same management API and expose queue/provider operations.
5. Run official-SDK contract tests, migration/restore rehearsals, and production-like load/failure
   tests through the real reverse proxy, then cut over by draining existing work.

This sequence is direction, not a hard ordering contract; revisit as workstreams stabilize.

## Deferred decisions

The audit deliberately leaves these open; do not assume them:

- Exact token-reservation rules per provider (provider-specific and verified, not assumed).
- Choice of caching/wake-up infrastructure (only after measurement).
- Precise fairness/reordering policy for head-of-line blocking (must be explicit, not silent).
- Concrete performance targets and percentile goals (not established by the source-only review).
- Whether long-lived asynchronous jobs are ever introduced (a separately documented extension API).
- The final operating/commercial model (internal company access, project budgets, showback/chargeback,
  prepaid balances, or reseller access, singly or combined). The primitives are settled; which model
  is deployed is deferred — see `project_spec.md` §Accounting, pricing, and commercial model. A
  positive balance is not a universal authorization requirement, and prepaid billing remains possible.
