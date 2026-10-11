# AetherGate v1 -> v2 Cutover

This document records the supported transition from the retired v1 implementation to the current
AetherGate architecture.

## Current operator decision

The v1 deployment was never used for production and contains no important state. As of 2026-10-11,
the supported cutover is therefore **clean/destructive**:

- do not import the v1 SQLite database;
- do not preserve v1 users, API keys, balances, request logs, model rows, endpoint rows, or plaintext
  provider credentials;
- bootstrap a fresh v2 PostgreSQL deployment through the supported v2 APIs and operator workflow;
- preserve the dated v1 architecture audit only as historical evidence.

This decision supersedes the earlier plan to build a general SQLite -> PostgreSQL data converter for
the current deployment.

## Supported schema migration model

The active product uses PostgreSQL and Alembic:

- PostgreSQL is authoritative for identity, catalog/routing, queue state, quotas, budgets, accounting,
  audit, sessions, and observability metadata.
- Alembic revisions under `src/aethergate/migrations/` are the only supported application schema
  migration path.
- Application startup does not mutate schema automatically.
- Upgrades run explicitly and fail clearly if the database revision is incompatible with the code.
- Backup/restore uses the supported PostgreSQL operator tooling, not v1 SQLite scripts.

The current migration head is recorded by the release/handoff documents and must be verified during
cutover.

## Clean cutover procedure

1. Stop the legacy v1 containers/processes.
2. Confirm there is no production state that needs preservation. For this deployment that decision has
   already been made by the operator.
3. Remove/retire the v1 runtime and its SQLite volume/database.
4. Build the canonical current AetherGate image from the current repository.
5. Start a fresh PostgreSQL authority.
6. Run Alembic `upgrade head`.
7. Run configuration preflight and readiness checks.
8. Perform the one-use bootstrap flow.
9. Configure OIDC, projects/principals, providers/accounts/endpoints/model aliases/routes, quotas,
   budgets/pricing, and inference credentials through supported v2 surfaces.
10. Run the official-SDK golden path and browser/operator checks.
11. Back up the fresh PostgreSQL authority.
12. Keep the legacy v1 service disabled/removed; do not run the old and new gateways against the same
    public endpoint during normal operation.

## Legacy artifacts

The v1 implementation used:
- FastAPI + SQLModel/SQLite under `app/`;
- a root legacy `docker-compose.yml` / `Dockerfile`;
- root `requirements.txt`;
- direct SQLite initialization/management scripts;
- ad-hoc SQLite schema migration scripts;
- old admin/API/runbook/agent instructions.

The cutover task removes these from the active product tree so operators and coding agents cannot
mistake them for supported paths.

The immutable dated architecture audit under `docs/audits/` is retained.

## Future legacy-data requirement

If real legacy state ever needs preservation in another deployment, treat that as a new migration
project. It must:
- run against a copy, never the live legacy DB;
- preserve identity and attribution intentionally;
- never infer provider-account identity from URL alone;
- never copy plaintext provider credentials into normal v2 database columns;
- use fixed-point accounting conversions and explicit reconciliation rules.

Do not revive the retired ad-hoc v1 scripts.
