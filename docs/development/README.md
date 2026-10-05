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
| `inspect` | Print a scheduler queue-state summary (state counts, endpoints, active reservations, quota windows, queued requests' limiting reasons). |
| `reconcile list` | List `outcome_unknown` requests (metadata only, never content). |
| `reconcile resolve <id> --disposition <state> --by <op>` | Explicitly reconcile an `outcome_unknown` request to `failed`/`cancelled`/`succeeded` and release its held reservation. || `down` | Stop the stack, keeping the PostgreSQL volume. |
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
  content encryption) and `AETHERGATE_BOOTSTRAP_TOKEN` (the one-use admin bootstrap secret) if
  absent. All are required and never committed/logged; the bootstrap token should be removed after
  the initial admin bootstrap.

## Endpoints

- `GET /health/live` — process liveness.
- `GET /health/ready` — readiness; returns `503` with `{"status":"not_ready"}` when PostgreSQL is
  unavailable.
- `GET /v1/models` — list active public model aliases.
- `GET /v1/models/{model}` — retrieve a public model alias.
- `POST /v1/chat/completions` — Chat Completions (non-streaming + SSE `stream=true`).

Admin control-plane endpoints (minimal surface, AGV2-013):

- `POST /admin/v1/bootstrap` — one-use admin bootstrap (bearer = `AETHERGATE_BOOTSTRAP_TOKEN`);
  returns the initial `system_admin` credential's raw key once.
- `GET /admin/v1/whoami` — safe metadata for an authenticated admin key.
- `GET /admin/v1/projects/{project_id}/credentials` — protected read (list, metadata only).
- `POST /admin/v1/credentials` — protected create (raw key returned once).
- `POST /admin/v1/credentials/{id}/rotate` / `.../revoke` — protected lifecycle.

Admin auth requires an `admin`-audience credential (`Authorization: Bearer agk_...`) plus an active
role assignment granting the needed `admin:*` permission (`system_admin` deployment-wide;
`project_admin`/`project_viewer` scoped to one project). Admin errors use
`{"error":{"code","message","request_id"}}`; auth/authorization/bootstrap failures are fixed and
indistinguishable.

Inference is authenticated with an AetherGate-issued Bearer API key
(`Authorization: Bearer agk_...`). A missing/invalid/wrong-scheme/malformed header returns a
structured `401` with `WWW-Authenticate: Bearer`; a valid key resolves to the credential's
project/principal and persists that attribution on the queued request. Credentials are scoped to the
`inference` audience with the `inference:invoke` scope, and are revalidated again immediately before
worker dispatch so a credential revoked while queued never reaches upstream.

Inference is a **durable queue** path: the API enqueues an encrypted request and the worker claims
and dispatches it. Physical endpoint concurrency is enforced by `endpoint.max_concurrency`, and
shared provider-account request/token quotas are reserved transactionally with endpoint capacity
(migration `0005`). Accounting (migration `0007`) adds immutable price snapshots, usage records,
optional project budget policy, monetary budget reservations, and an append-only ledger, reserved in
the same atomic admission transaction (see `docs/architecture/accounting.md`). Queued work persists
in PostgreSQL and is recovered when workers restart. Inference is unauthenticated only under
`AETHERGATE_ALLOW_INFERENCE_AUTH_BYPASS=true` and only when no Authorization header is supplied
(development only, and rejected in `prod` mode); a supplied header is always authenticated normally
and an invalid key never falls through to the bypass.
Upstream hosts must be explicitly allowlisted via `AETHERGATE_UPSTREAM_ALLOWLIST` (comma-separated);
empty means deny all.

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
  --max-concurrency 2 \
  --quota-group shared \
  --request-limit 30/60 \
  --token-limit 60000/60 \
  --default-output-tokens 64
```

Accounting seed options (AGV2-010), all idempotent:

- `--price-currency USD` — currency for seeded price/budget (default `USD`).
- `--request-price AMOUNT` — seed a request-priced policy on the route.
- `--token-price INPUT/OUTPUT/UNIT_SCALE` — seed a token-priced policy (e.g. `1/1/1000`).
- `--budget-limit LIMIT/WINDOW_SECONDS` — seed a project budget policy on the dev project
  (e.g. `0.10/60`).

Values may also be provided via `AETHERGATE_SEED_PROVIDER_KIND`, `AETHERGATE_SEED_UPSTREAM_MODEL`,
`AETHERGATE_SEED_PUBLIC_ALIAS`, `AETHERGATE_SEED_BASE_DESTINATION`,
`AETHERGATE_SEED_SECRET_REF_NAME`, and `AETHERGATE_SEED_MAX_CONCURRENCY`. The destination host must
already be present in the upstream allowlist. `--max-concurrency` sets (or updates) the endpoint's
physical concurrency limit. `--quota-group` creates (or reuses) a shared quota group on the same
account; `--request-limit LIMIT/WINDOW_SECONDS` and `--token-limit LIMIT/WINDOW_SECONDS` are
repeatable and idempotent; `--default-output-tokens` sets the route's default bounded output
reservation.

## Issuing an inference API credential

With bypass disabled (`AETHERGATE_ALLOW_INFERENCE_AUTH_BYPASS=false`), real inference requires an
AetherGate-issued key. Use the dev-safe credential tool (service/repository-backed; not a raw DB
write):

```bash
docker compose --project-directory deploy/v2 --file deploy/v2/compose.yaml \
  run --rm --no-deps api python -m aethergate.dev_credential create \
  --project <project-id> --principal <principal-id> --name smoke
```

`list` shows metadata only (never raw keys or hashes); `revoke <id>` disables a credential
(idempotently — repeated revokes preserve the original timestamp); `rotate <id>` issues a replacement
and atomically revokes the old one, but only for an active, non-revoked, non-expired credential whose
project/principal are still valid. The raw key is printed exactly once at `create`/`rotate`. `create`
defaults scopes from the audience (`inference` -> `inference:invoke`; `admin` -> none); pass
`--scopes` to override.

## One-use admin bootstrap

Initialize the control plane exactly once with the configured bootstrap token:

```bash
TOKEN=$(grep '^AETHERGATE_BOOTSTRAP_TOKEN=' deploy/v2/.env | cut -d= -f2-)
curl -sS -X POST "$AETHERGATE_BASE_URL/../admin/v1/bootstrap" -H "Authorization: Bearer $TOKEN"
```

The response returns the initial `system_admin` credential's raw key once. Verify it:

```bash
curl -sS "$BASE/admin/v1/whoami" -H "Authorization: Bearer <admin-raw-key>"
```

After bootstrap the token can never be reused (further calls return `409`), and it should be removed
from the environment.

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

The v2 schema baseline lives under `src/aethergate/migrations/` (revisions `0001`–`0009`; `0003`
adds scheduler tables, `endpoints.max_concurrency`, and a `BigInteger` fencing token; `0004` adds a
positive-concurrency CHECK on `endpoints` and reconciliation metadata on `inference_requests`;
`0005` adds shared provider-account request/token quotas — `quota_limits`, `quota_windows`,
`quota_reservations`, `quota_groups.provider_account_id`/`cooldown_until`,
`route_bindings.default_output_tokens`, `inference_requests.wait_reason`; `0006` hardens shared-quota
admission metadata; `0007` adds the accounting foundation — `price_policies`, `price_snapshots`,
`project_budget_policies`, `budget_windows`, `budget_reservations`, `usage_records`,
`ledger_entries`, `inference_requests.price_snapshot_id`; `0008` enforces accounting invariants — the
one-enabled-price-policy-per-route partial unique index, billing-unit price-shape CHECK constraints,
and a nullable `budget_reservations.price_snapshot_id` for the snapshot lifecycle; `0009` refines
`api_credentials` into one-way-verifiable scoped client credentials — `key_prefix`, `key_hash`
(unique), `audience`, `scopes`, `expires_at`/`revoked_at`/`last_used_at`, dropping `secret_ref_id`;
`0010` adds admin identity — `role_assignments` (with a partial-unique index preventing duplicate
active equivalent assignments), the singleton `bootstrap_state`, and `audit_events`).
Schema is applied
only via `scripts/dev/v2 migrate`; startup never calls `create_all()`.
