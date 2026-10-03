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
| `up` | Start PostgreSQL + the v2 API, then print the discovered base/health URLs. |
| `url` | Print the current `AETHERGATE_BASE_URL` / `AETHERGATE_HEALTH_URL`. |
| `wait [url]` | Block until readiness passes. |
| `migrate` | Run the Alembic migrations (`upgrade head`) inside the API container. |
| `test` | Run the full test suite inside the API container against a throwaway test DB. |
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
- The Compose project is `aethergate-v2`; container names are `aethergate-v2-api` and
  `aethergate-v2-postgres` (distinct from the legacy v1 stack).

## Configuration

- `AETHERGATE_ENV` selects the environment/mode (`dev`/`test`/`prod`).
- Database settings come from `DATABASE_URL`, or from the `POSTGRES_HOST`/`POSTGRES_PORT`/
  `POSTGRES_DB`/`POSTGRES_USER`/`POSTGRES_PASSWORD` parts (assembled safely; secrets masked in
  repr/logs).
- `scripts/dev/v2 up` generates a random `POSTGRES_PASSWORD` into `deploy/v2/.env` on first run
  (gitignored, never committed).

## Endpoints

- `GET /health/live` — process liveness.
- `GET /health/ready` — readiness; returns `503` with `{"status":"not_ready"}` when PostgreSQL is
  unavailable.

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

The v2 schema baseline lives under `src/aethergate/migrations/` (revision `0001`). Schema is
applied only via `scripts/dev/v2 migrate`; startup never calls `create_all()`.
