# AetherGate v2 — Current Task

## Task ID
AGV2-003

## Title
Containerized v2 runtime and PostgreSQL persistence foundation

## Ownership
Primary: platform/migrations/observability/testing  
Coordinating: contracts, identity/auth, provider catalog and routing

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
   - `docs/architecture/security.md`
   - `docs/migrations/v1-to-v2.md`
   - `docs/contracts/domain-model.md`
   - `docs/contracts/admin-v1-foundation.md`
4. Preserve the contracts established by AGV2-002 unless a concrete persistence need exposes a real
   defect. If a contract must change, document why in the handoff and update its tests/docs.
5. Do not commit unrelated untracked support files.
6. Do not modify or delete the legacy v1 `app/` or `frontend/src/` code in this task.

## User Requirement: Container-First Runtime

AetherGate v2 is expected to run in Docker. The nomnom host is the current development/test bed.

Do **not** guess or hard-code a host port.

Rules:

- A stable container-internal API port is fine (for example 8000).
- The development stack must bind the API only to loopback by default.
- The host port must be dynamically allocated/discovered at runtime rather than committed as a
  fixed number.
- Prefer letting Docker allocate a free host port and then query/report the actual mapping.
- If the installed Docker Compose version cannot express an ephemeral host-port mapping, implement
  a helper that discovers a free loopback port immediately before startup and verifies the result.
- Any fallback port-selection helper must inspect both host listeners and Docker-published ports
  before choosing, and must verify the final mapping after startup.
- PostgreSQL must not publish its port to the host by default.
- A developer command must print the resulting `AETHERGATE_BASE_URL` after the stack starts.
- Do not commit a nomnom-specific chosen port.
- Do not bind the development API to `0.0.0.0` by default.

Update `project_spec.md` so containerized deployment is recorded as project direction and
dynamic/non-guessed development host-port allocation is recorded as the local-test convention.

## Goal

Create the first runnable v2 application shell and its authoritative PostgreSQL persistence
foundation inside Docker.

At the end of this task, we should be able to:

1. build the v2 container image;
2. start PostgreSQL + the v2 API on nomnom;
3. discover the actual free host port used for the API;
4. call liveness/readiness endpoints;
5. prove readiness fails when PostgreSQL is unavailable;
6. run the v2 migrations from an empty PostgreSQL database;
7. run the contract/unit tests inside the container;
8. persist and retrieve the core configuration entities needed by the next inference task.

This task does **not** send inference to a model yet.

## Docker Layout

Do not overwrite the legacy v1 Docker/runtime files yet.

Create a clearly isolated v2 development/deployment layout, for example:

```text
deploy/v2/
├── Dockerfile
├── compose.yaml
└── ...
```

A different equivalent layout is acceptable if it is clearer.

Requirements:

- multi-stage image if useful;
- Python 3.12+;
- non-root runtime user;
- deterministic dependency installation;
- healthcheck(s) where appropriate;
- API service;
- PostgreSQL service;
- persistent named PostgreSQL volume;
- no committed credentials;
- PostgreSQL reachable only on the Compose network by default;
- API loopback-only from the host by default;
- no fixed host API port.

The Compose project must not collide with the existing legacy v1 stack's service/container names.

## Runtime Configuration

Add a typed v2 settings layer under `src/aethergate/`.

Requirements:

- environment-driven configuration;
- fail clearly on missing required production/runtime settings;
- no insecure placeholder secrets;
- database URL assembled/read safely;
- explicit environment/mode;
- settings testable without Docker;
- secrets excluded from repr/logging where applicable.

Use a maintained settings library compatible with Pydantic v2 if needed.

## FastAPI Application Shell

Create a v2 FastAPI application entrypoint under `src/aethergate/`.

At minimum provide:

- `GET /health/live`
- `GET /health/ready`

Semantics:

- liveness reports only process/application liveness;
- readiness checks PostgreSQL connectivity and returns non-ready if the authoritative database is
  unavailable;
- no fake "always healthy" readiness response;
- structured JSON;
- do not leak database URLs/credentials/errors.

Do not implement OpenAI inference routes or admin CRUD routes in this task.

## PostgreSQL Persistence

Use PostgreSQL as the v2 authoritative data store.

Use a modern async persistence stack appropriate for FastAPI/Python 3.12, with version ranges/pins
recorded in `pyproject.toml`.

Create persistence models/repositories for the configuration/identity subset needed to resolve a
future inference request:

- Project
- Principal
- ApiCredential metadata
- SecretRef metadata
- Provider
- ProviderAccount
- Endpoint
- QuotaGroup
- ModelAlias
- RouteBinding

Requirements:

- persistence models are separate from domain contracts;
- repositories return/accept domain or explicit persistence DTOs, not ORM objects escaping upward;
- stable opaque IDs, not public auto-increment integers;
- foreign keys and uniqueness constraints where identity requires them;
- timestamps stored timezone-aware;
- no plaintext provider/API secret material in PostgreSQL;
- no monetary floats;
- no direct database manipulation from routers;
- no `create_all()` startup schema mutation.

Do not create scheduler/execution/ledger tables yet unless strictly required by a migration-framework
constraint. Those belong to later workstreams.

## Secret Reference Foundation

The database may store secret-reference metadata, but never provider secret material.

Establish an internal secret-resolution interface and one clearly marked **development/test**
environment-variable resolver so the next task can resolve a provider credential without storing it
in PostgreSQL.

Requirements:

- secret value never appears in read DTOs, logs, reprs, migrations, or test snapshots;
- resolver takes a SecretRef/reference and returns material only inside the trusted runtime;
- architecture allows a later production backend such as Vault/KMS/envelope encryption;
- environment backend is explicitly not treated as the final enterprise secret backend.

Do not build a full secret-management product in this task.

## Catalog Resolution Service

Implement a small service/repository path that can resolve an active public `ModelAlias` to its
active `RouteBinding`, `Endpoint`, `ProviderAccount`, and `Provider`.

Requirements:

- unknown alias => explicit not-found/domain error;
- inactive alias/route/endpoint/account/provider => explicit unavailable/domain error;
- no "unknown model -> default Ollama" fallback;
- no provider call yet;
- no routing load-balancing policy yet;
- if multiple active routes exist and selection policy is not yet defined, reject/return an explicit
  ambiguity instead of silently choosing.

This service is the configuration boundary the next inference task will use.

## Migrations

Introduce a real migration framework for v2, preferably Alembic unless a concrete technical reason
requires another choice.

Requirements:

- initial migration creates the v2 persistence schema above;
- migration is deterministic from an empty PostgreSQL database;
- include downgrade where practical;
- no handwritten ad-hoc migration script as the primary mechanism;
- application startup does not silently mutate schema;
- provide an explicit documented migration command.

Do not implement the v1 SQLite -> v2 data migration yet. This task only establishes the v2 migration
system and empty-schema baseline.

## Developer Commands

Provide a small, documented set of commands/scripts for nomnom development:

- build v2 images;
- start v2 PostgreSQL + API;
- discover/print the actual API host port/base URL;
- run migrations;
- run tests inside the container;
- stop the stack;
- remove test data/volume only via an explicitly destructive command.

Prefer a single helper entrypoint such as `scripts/dev/v2` with subcommands if it keeps the workflow
simple.

The start command must not require the user to manually guess a free port.

After successful startup it must print something equivalent to:

```text
AETHERGATE_BASE_URL=http://127.0.0.1:<actual-docker-assigned-port>/v1
AETHERGATE_HEALTH_URL=http://127.0.0.1:<actual-docker-assigned-port>/health/ready
```

Do not assume what `<actual-docker-assigned-port>` is.

## Tests

Add deterministic tests for at least:

1. settings validation;
2. liveness;
3. readiness success with PostgreSQL;
4. readiness failure when DB is unavailable;
5. migration from empty PostgreSQL;
6. repository create/read behavior for the core persisted entities;
7. no plaintext secret field is persisted;
8. model-alias route resolution;
9. unknown alias rejection;
10. inactive resource rejection;
11. ambiguous multi-route rejection;
12. API container runs as non-root;
13. Compose does not publish PostgreSQL to the host;
14. Compose does not contain a fixed host API port.

Tests requiring PostgreSQL should run through the containerized test workflow rather than expecting a
developer-installed host PostgreSQL.

## Documentation

Update/create as appropriate:

- `project_spec.md`
- `docs/architecture/v2-overview.md`
- `docs/architecture/provider-model.md`
- `docs/migrations/v1-to-v2.md`
- `docs/development/README.md` or equivalent v2 Docker developer instructions

Keep documentation concise and executable.

Do not modify the dated architecture audit.

## Out of Scope

Do not implement:

- OpenAI inference endpoints;
- LiteLLM/provider inference calls;
- scheduler queue/dispatch;
- quota reservation;
- accounting settlement;
- OIDC/session authentication;
- production secret backend;
- admin CRUD API;
- React changes;
- v1-to-v2 data conversion.

## Verification on nomnom

Before committing, execute the real Docker workflow on the current host.

At minimum:

1. inspect current listener/Docker port state before startup;
2. build the v2 image;
3. start the v2 stack without choosing a hard-coded host port;
4. capture the actual host port Docker assigned;
5. verify liveness;
6. verify readiness;
7. verify PostgreSQL is not host-published;
8. run migrations;
9. run the test suite inside the container;
10. restart the stack and verify persisted schema/data survives;
11. stop the stack cleanly.

Record the **actual commands and summarized results** in `docs/development/agent-handoff.md`.
Recording the dynamically assigned test port in the handoff is fine; do not encode it into committed
configuration.

Also run:

- `git diff --check`
- lint checks;
- complete diff review;
- secret scan required by repository policy.

## Handoff

At the end, update:

`docs/development/agent-handoff.md`

Include:

- branch;
- starting commit;
- implementation commit(s);
- Docker image/Compose layout;
- actual dynamically allocated nomnom test port;
- migration status;
- persisted entities;
- verification commands/results;
- decisions/deferred items;
- risks/issues;
- one recommended next step.

Do not include secrets or large logs.

## Commit and Push

Commit all task work on branch `v2` using conventional commits.

A reasonable primary commit message is:

`feat(platform): add containerized v2 runtime and persistence`

A separate handoff-only follow-up commit is allowed.

**Push all completed commits to `origin/v2`.**

Never push directly to `main`.

The task is not complete until `origin/v2` contains the completed work and updated handoff.
