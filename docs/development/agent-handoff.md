# AetherGate Agent Handoff

## Current State
- Branch: v2
- Starting commit: 705c5b892772889679495b99454c2188f750454c
- Implementation commit: 28f4e571a92caac0337e171048b7a00c813e1c89

## Task Completed
AGV2-003 — first runnable v2 application shell and its authoritative PostgreSQL persistence
foundation, container-first. Added a multi-stage non-root Docker image, an ephemeral loopback
Compose stack, typed runtime settings, a FastAPI shell with liveness/readiness, SQLAlchemy async
models/repositories for the core configuration/identity entities, an Alembic migration baseline,
a secret-reference resolver, and a model-alias -> route resolution service. No inference, scheduler,
auth, billing, or admin CRUD was implemented.

## Docker / Compose layout
- `deploy/v2/Dockerfile` — multi-stage (builder venv -> slim runtime), non-root user (uid 10001).
- `deploy/v2/compose.yaml` — project `aethergate-v2`; services `api` (`aethergate-v2-api`) and
  `postgres` (`aethergate-v2-postgres`); named volume `aethergate-v2-pgdata`.
  - API published loopback-only with an **ephemeral** host port (`127.0.0.1::8000`), no fixed port.
  - PostgreSQL has **no** `ports` mapping (Compose-network only).
- `deploy/v2/.gitignore` — ignores `.env` (generated `POSTGRES_PASSWORD`).
- `scripts/dev/v2` — `build` / `up` / `url` / `wait` / `migrate` / `test` / `down` / `reset`.

## Nomnom verification (actual commands/results)
- Host = nomnom; `docker` is podman 4.9.3 + docker-compose 2.40.3. Ports 8000 (node) and
  127.0.0.1:5432 (langfuse-postgres) were already taken, so ephemeral allocation was required.
- `scripts/dev/v2 build` -> image `aethergate-v2:local` built.
- `scripts/dev/v2 up` -> printed `AETHERGATE_BASE_URL=http://127.0.0.1:<port>/v1` and
  `AETHERGATE_HEALTH_URL=http://127.0.0.1:<port>/health/ready`.
- Dynamically allocated nomnom test port (last run): **41735** (each `up` allocates a fresh port).
- Liveness `/health/live` -> `200 {"status":"alive"}`; readiness `/health/ready` -> `200 {"status":"ready"}`.
- Readiness with postgres stopped -> `503 {"status":"not_ready"}` (liveness still 200).
- `docker compose port postgres 5432` -> no mapping (not host-published).
- `scripts/dev/v2 migrate` -> applied `0001`; `alembic_version=0001`, 10 tables created.
- `scripts/dev/v2 test` -> **47 passed** (full suite incl. DB-gated, migration-from-empty, compose static).
- Restart persistence: inserted a `projects` row, `down`+`up`, row and `alembic_version` survived.
- `scripts/dev/v2 down` -> clean stop; containers/network removed; named volume retained.

## Persistence
- Async stack: SQLAlchemy 2.x (`sqlalchemy[asyncio]`) + asyncpg + Alembic.
- Models: Project, Principal, ApiCredential, SecretRef, Provider, ProviderAccount, Endpoint,
  QuotaGroup, ModelAlias, RouteBinding — opaque `String` UUID PKs, timezone-aware timestamps, FKs,
  uniqueness on names; no plaintext secret material; no monetary floats.
- Repositories return/accept domain entities; ORM objects never escape upward.
- Schema is applied only via `scripts/dev/v2 migrate`; startup never calls `create_all()`.

## Secret reference foundation
- `aethergate.secrets.SecretResolver` (Protocol) + `EnvSecretResolver` (`AETHERGATE_SECRET_*`,
  dev/test only, explicitly not the production backend). Material never appears in DTOs/logs/reprs.

## Catalog resolution service
- `aethergate.catalog.resolve_model_alias` resolves an active alias to its single active
  RouteBinding/Endpoint/ProviderAccount/Provider; raises `ModelAliasNotFound`, `ResourceInactive`,
  or `AmbiguousRoute` (no silent default, no "unknown -> Ollama" fallback).

## Decisions
- Alembic chosen as the migration framework; baseline revision `0001` under
  `src/aethergate/migrations/` with a downgrade.
- Container healthcheck uses `python -m aethergate.healthcheck` (podman/compose shell-splits the
  list-form `-c "code with spaces"` form).
- `scripts/dev/v2 migrate/test` use `docker compose run --rm --no-deps` so they do not recreate the
  running postgres container.
- `pytest-asyncio` loop scope set to `session` (async engine/session fixtures must share the test loop).
- Tests + `pyproject.toml` + `deploy/v2/{compose.yaml,Dockerfile}` are copied into the image so the
  containerized test workflow runs; `deploy/v2/.env` is explicitly never copied.
- `SecretRef.created_at` is DB-assigned (server default) and read back via `session.refresh`.

## Deferred
- v1 SQLite -> v2 data conversion (this task only establishes the empty-schema baseline).
- Scheduler/execution/ledger tables and services.
- Production secret backend (Vault/KMS/envelope encryption).
- Provider capability/limit profile schema; route load-balancing/selection policy.

## Issues / Risks
- `docker` on nomnom is podman; the compose healthcheck list form is shell-split — keep healthchecks
  to single-token commands or a module path.
- The dynamic host port changes on every `up`; always re-run `scripts/dev/v2 url` after `up`.
- DB-gated tests skip unless `AETHERGATE_TEST_DATABASE_URL` is set (the helper sets it).
- Pre-commit secret scan flags `password=...`/`secret=...` string literals; keep secret-adjacent
  fixtures value-neutral (a test literal was reworked for this reason).

## Recommended Next Step
Implement the inference request entrypoint consuming `aethergate.catalog.resolve_model_alias` and
the `SecretResolver` (auth is out of scope until the identity workstream lands), coordinated with the
contracts workstream for the OpenAI `/v1` surface.
