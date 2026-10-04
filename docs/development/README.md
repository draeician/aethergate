# AetherGate v2 — Developer Instructions

Container-first development on the nomnom test bed. The v2 stack lives in `deploy/v2/`
(Compose project `aethergate-v2`), and a single helper wraps the workflow.

## One-command workflow

```bash
scripts/dev/v2 <command>
```

| Command | What it does |
|---|---|
| `build` | Build the v2 container image (`aethergate-v2:local`). |
| `up` | Start PostgreSQL + the v2 API + a scheduler worker, then print the discovered base/health URLs. |
| `url` | Print the current `AETHERGATE_BASE_URL` / `AETHERGATE_HEALTH_URL`. |
| `wait [url]` | Block until readiness passes. |
| `migrate` | Run the Alembic migrations (`upgrade head`) inside the API container. |
| `test` | Run the full test suite inside the API container against a throwaway test DB. |
| `workers [N]` | Scale the scheduler worker service to N replicas (default 2). |
| `inspect` | Print a scheduler queue-state summary (state counts, endpoints, active reservations). |
| `down` | Stop the stack, keeping the PostgreSQL volume. |
| `reset` | Stop the stack and **delete** the PostgreSQL volume (destructive). |

## Port and network rules

- The API listens on a **stable container-internal port** (8000) and is bound to the host
  **loopback only**. Docker assigns a free host port; there is no committed fixed port.
- After `up`, the helper prints the actual mapping, e.g.:

  ```text
  AETHERGATE_BASE_URL=http://127.0.0.1:<docker-assigned-port>/v1
  AETHERGATE_HEALTH_URL=http://127.0.0.1:<docker-assigned-port>/health/ready
  ```

- PostgreSQL is reachable **only on the Compose network**; it is never host-published.
- The Compose project is `aethergate-v2`; container names are `aethergate-v2-api`,
  `aethergate-v2-postgres`, and `aethergate-v2-worker-{1..N}` (distinct from the legacy v1 stack).
- The scheduler worker runs as a separate `worker` service (`python -m aethergate.worker`); it can be
  scaled independently with `scripts/dev/v2 workers N`.

## Configuration

- `AETHERGATE_ENV` selects the environment/mode (`dev`/`test`/`prod`).
- Database settings come from `DATABASE_URL`, or from the `POSTGRES_HOST`/`POSTGRES_PORT`/
  `POSTGRES_DB`/`POSTGRES_USER`/`POSTGRES_PASSWORD` parts (assembled safely; secrets masked in
  repr/logs).
- `scripts/dev/v2 up` generates a random `POSTGRES_PASSWORD` into `deploy/v2/.env` on first run
  (gitignored, never committed). It also generates `AETHERGATE_QUEUE_KEY` (Fernet key for queued
  content encryption) if absent. Both are required and never committed/logged.

## Endpoints

- `GET /health/live` — process liveness.
- `GET /health/ready` — readiness; returns `503` with `{"status":"not_ready"}` when PostgreSQL is
  unavailable.
- `GET /v1/models` — list active public model aliases.
- `GET /v1/models/{model}` — retrieve a public model alias.
- `POST /v1/chat/completions` — Chat Completions (non-streaming + SSE `stream=true`).

Inference is a **durable queue** path: the API enqueues an encrypted request and the worker claims
and dispatches it. Physical endpoint concurrency is enforced by `endpoint.max_concurrency`; queued
work persists in PostgreSQL and is recovered when workers restart. It does **not** yet reserve
provider RPM/TPM, token allowances, or shared-account/project budgets. Inference is unauthenticated
only under `AETHERGATE_ALLOW_INFERENCE_AUTH_BYPASS=true` (development only, and rejected in `prod`
mode). Upstream hosts must be explicitly allowlisted via `AETHERGATE_UPSTREAM_ALLOWLIST`
(comma-separated); empty means deny all.

## Seeding a development backend

Because the admin API is not implemented yet, seed the minimum inference configuration
idempotently through the service/repository layer:

```bash
docker compose --project-directory deploy/v2 --file deploy/v2/compose.yaml \
  run --rm --no-deps api python -m aethergate.devseed \
  --kind ollama \
  --upstream-model 'qwen3.8-2b-distill:Q6_K' \
  --alias gpt-4 \
  --base-destination http://<host>:11434 \
  --max-concurrency 2
```

Values may also be provided via `AETHERGATE_SEED_PROVIDER_KIND`, `AETHERGATE_SEED_UPSTREAM_MODEL`,
`AETHERGATE_SEED_PUBLIC_ALIAS`, `AETHERGATE_SEED_BASE_DESTINATION`,
`AETHERGATE_SEED_SECRET_REF_NAME`, and `AETHERGATE_SEED_MAX_CONCURRENCY`. The destination host must
already be present in the upstream allowlist. `--max-concurrency` sets (or updates) the endpoint's
physical concurrency limit.

## Tests

```bash
# host (offline; DB-gated tests skip)
.venv/bin/python -m pytest -q

# full suite including DB-gated tests, inside the container
scripts/dev/v2 test
```

DB-gated tests use `AETHERGATE_TEST_DATABASE_URL` and skip when it is unset; `scripts/dev/v2 test`
sets it automatically to a throwaway `aethergate_test` database.

## Migrations

The v2 schema baseline lives under `src/aethergate/migrations/` (revisions `0001`–`0003`; `0003`
adds scheduler tables, `endpoints.max_concurrency`, and a `BigInteger` fencing token). Schema is
applied only via `scripts/dev/v2 migrate`; startup never calls `create_all()`.
