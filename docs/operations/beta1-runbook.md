# AetherGate — Beta 1 Operator Runbook

Audience: an operator deploying the **Beta 1 release candidate** of AetherGate v2.
Beta 1 introduces a dedicated **production deployment profile** that is separate
from the Alpha/dev profile in `deploy/v2/`. It is an internal/self-hosted release
candidate, not a public release.

There is one supported runtime: the current PostgreSQL/Alembic/scheduler
implementation under `src/aethergate/`. The retired v1 SQLModel/SQLite application
has been removed; the supported transition is a clean cutover to a fresh
PostgreSQL database (no v1 data import). See `docs/migrations/v1-to-v2.md`.

## Beta 1 scope

Beta 1 carries the full Alpha 1 OpenAI-compatible + admin surface (see
`docs/releases/alpha1-readiness.md`) and adds production deployment hardening:

- a **production compose profile** (`deploy/beta1/compose.yaml`): postgres + api +
  worker only, non-root runtime, read-only rootfs with a tmpfs `/tmp`, bounded
  resources, real liveness healthchecks, distinct project/network/volume names;
- **strict literal-IP egress** with an explicit allowlist and
  `AETHERGATE_EGRESS_REQUIRE_LITERAL_IP=true` (DNS-name upstreams rejected);
- **file-backed provider secrets** (`AETHERGATE_SECRET_BACKEND=file`) with
  inference-auth bypass forced off;
- a **worker heartbeat healthcheck** so the worker (which has no HTTP surface) is
  observed by the container runtime;
- a **release gate** (`scripts/ci/beta_release_gate.sh`) that proves the profile
  from a clean, empty database through migration, SDK inference, worker restart
  recovery, a real PostgreSQL outage/recovery gate, and backup/restore — offline,
  with no model downloads.

Migration head: **0018**.

## Layout

- `deploy/beta1/compose.yaml` — the Beta 1 production profile (postgres + api +
  worker). This is the only Beta 1 deployment file an operator edits.
- `deploy/beta1/env.example` — a template of the required secrets. Copy it to
  `deploy/beta1/.env`, fill real values, and `chmod 600`.
- `deploy/beta1/compose.smoke.yaml` — CI/acceptance-only override (adds the
  deterministic fake upstream on a literal IP). **Never used in production.**
- `deploy/v2/` — the Alpha/dev profile and its web console + deterministic IdP.
- `scripts/ops/v2` — operator recovery tooling (backup/restore/migrate/
  diagnostics). Operates directly on PostgreSQL.
- `scripts/ci/beta_release_gate.sh` — the disposable production release gate.
- `scripts/ops/beta1-access` — host-level LAN + Tailscale Serve front-end for the
  loopback API. See `docs/operations/beta1-access.md`.

## Secrets

Required (never commit these; `.env` files are gitignored):

- `POSTGRES_PASSWORD` — database password.
- `AETHERGATE_QUEUE_KEY` — Fernet key for queue-content encryption. Lives
  **outside** PostgreSQL. Losing it makes queued encrypted payloads
  unrecoverable — back it up separately from the database.
- `AETHERGATE_BOOTSTRAP_TOKEN` — one-use bootstrap secret (`agb_...`). Remove it
  after the initial bootstrap so it cannot be replayed.
- `AETHERGATE_SECRET_HOST_DIR` — an absolute host path to a directory of
  per-`SecretRef.id` files containing provider credentials. It is bind-mounted
  read-only to `/run/secrets/aethergate-provider` inside the container.
- `AETHERGATE_UPSTREAM_ALLOWLIST` — a literal IP (IPv4/IPv6) of the upstream
  provider. Empty = deny-all egress (config_check warns).

Generate them (example):

```bash
python3 -c 'import secrets; print("POSTGRES_PASSWORD=" + secrets.token_urlsafe(24))'
python3 -c 'from cryptography.fernet import Fernet; print("AETHERGATE_QUEUE_KEY=" + Fernet.generate_key().decode())'
python3 -c 'import secrets; print("AETHERGATE_BOOTSTRAP_TOKEN=agb_" + secrets.token_urlsafe(32))'
```

Then:

```bash
cp deploy/beta1/env.example deploy/beta1/.env   # then fill real values
chmod 600 deploy/beta1/.env
mkdir -p /run/secrets/aethergate-provider       # or your AETHERGATE_SECRET_HOST_DIR
```

### Production invariants

The production profile **hardcodes** the safety-critical values so a copied or
edited file cannot silently weaken them:

- `AETHERGATE_ENV=prod`
- `AETHERGATE_ALLOW_INFERENCE_AUTH_BYPASS=false`
- `AETHERGATE_SECRET_BACKEND=file`
- `AETHERGATE_SECRET_DIRECTORY=/run/secrets/aethergate-provider`
- `AETHERGATE_EGRESS_REQUIRE_LITERAL_IP=true`

The upstream allowlist entries **must be literal IPv4/IPv6 addresses** (the
validated address is the connected address, so DNS rebinding cannot occur).
Provider HTTP redirects are never followed. Provider credentials are resolved
only from the file backend; the `env` secret backend is dev/test only and is
rejected in `prod`.

### Configuration preflight

Validate configuration before starting (without contacting a provider):

```bash
docker compose --project-directory deploy/beta1 --env-file deploy/beta1/.env \
  -f deploy/beta1/compose.yaml run --rm --no-deps api python -m aethergate.config_check
```

`RESULT: VALID` and exit 0 = ready. It checks the DB config and queue key,
rejects prod inference-auth bypass, validates the literal-IP allowlist rules, and
reports the file secret backend. It never prints secret values.

## Clean install

```bash
# 1. Build the image
docker build -t aethergate-beta1:local -f deploy/v2/Dockerfile .

# 2. Start PostgreSQL and migrate the empty DB to head (0017)
docker compose --project-directory deploy/beta1 --env-file deploy/beta1/.env \
  -f deploy/beta1/compose.yaml up -d postgres
docker compose --project-directory deploy/beta1 --env-file deploy/beta1/.env \
  -f deploy/beta1/compose.yaml run --rm --no-deps api \
  alembic -c src/aethergate/migrations/alembic.ini upgrade head

# 3. Start API + worker
docker compose --project-directory deploy/beta1 --env-file deploy/beta1/.env \
  -f deploy/beta1/compose.yaml up -d

# 4. Health (loopback port is published ephemerally by default)
curl -fsS http://127.0.0.1:<api-port>/health/live
curl -fsS http://127.0.0.1:<api-port>/health/ready   # 200 only when schema == head
```

Discover the API loopback port with
`docker compose --project-directory deploy/beta1 port api 8000`.

### One-use bootstrap

```bash
TOKEN=$(grep '^AETHERGATE_BOOTSTRAP_TOKEN=' deploy/beta1/.env | cut -d= -f2-)
curl -fsS -X POST http://127.0.0.1:<api-port>/admin/v1/bootstrap \
  -H "Authorization: Bearer $TOKEN"
```

Then remove the bootstrap token and recreate the API so bootstrap is disabled:

```bash
sed -i '/^AETHERGATE_BOOTSTRAP_TOKEN=/d' deploy/beta1/.env
docker compose --project-directory deploy/beta1 --env-file deploy/beta1/.env \
  -f deploy/beta1/compose.yaml up -d --no-deps --force-recreate api
```

Set the returned `agk_...` key aside as your initial `system_admin` admin
credential. It authenticates `/admin/v1` (never `/v1` inference).

### OIDC configuration (optional)

Human browser/CLI login is optional. When enabled, set the OIDC variables in
`deploy/beta1/.env` and recreate the API. In `prod`, `AETHERGATE_OIDC_ISSUER`
**must be HTTPS**. Link an OIDC `(issuer, subject)` to a durable `system_admin`
principal, or enable JIT provisioning deliberately. The dev IdP
(`python -m aethergate.dev_oidc_idp_server`) is verification tooling only.

### TLS / reverse proxy expectations

- The API binds loopback only (`127.0.0.1:<port>`). Terminate TLS at an
  operator-managed reverse proxy in front of it.
- The proxy must preserve `Authorization`, `Cookie`, and `X-CSRF-Token` headers,
  set `Host`, and not strip `Set-Cookie`; long-held non-stream and SSE inference
  need generous read timeouts.
- PostgreSQL is **not** host-published; the API must sit behind TLS termination.
- To keep a host proxy's upstream stable across container restart/recreation, set
  `AETHERGATE_API_HOST_PORT` in `deploy/beta1/.env` to a fixed loopback port
  (otherwise Docker assigns an ephemeral port on each recreate).
- LAN + Tailscale access (host listeners, ports, plaintext-HTTP exposure, and the
  HTTPS migration path) is documented in `docs/operations/beta1-access.md`.

## Catalog / provider setup

Through `/admin/v1` (or the CLI), in order: SecretRef (metadata only) -> Provider
(`kind: ollama` etc.) -> Provider account (optionally references the SecretRef) ->
Endpoint (`base_destination` must be a **literal-IP** allowlisted host) -> Model
alias -> Route binding (exactly one active route per alias) -> optional price
policy and project budget. Egress policy is enforced at endpoint-create time and
again before every dispatch.

## Creating credentials

Inference credentials (audience `inference`, scope `inference:invoke`) authenticate
`/v1`; admin credentials (audience `admin`) authenticate `/admin/v1`. The raw key
is returned exactly once at create/rotate and is never recoverable from reads.

```python
from openai import OpenAI
client = OpenAI(base_url="http://<gateway>/v1", api_key="agk_...")
client.chat.completions.create(model="<alias>", messages=[{"role":"user","content":"hi"}])
```

## Health / readiness

- `GET /health/live` — process liveness.
- `GET /health/ready` — `200` only when PostgreSQL answers **and** the applied
  schema revision equals the code head (`503 schema_not_current` otherwise).
- The **worker** has no HTTP surface; its container health is the heartbeat file
  the worker refreshes on a fixed cadence from a dedicated event-loop task
  (`AETHERGATE_WORKER_HEARTBEAT_FILE`, default `/tmp/aethergate-worker-heartbeat`,
  freshness window `AETHERGATE_WORKER_HEARTBEAT_TIMEOUT_SECONDS`). The cadence is
  independent of the claim/dispatch loop, so a long-running inference does not
  make a healthy worker appear stale. If the worker truly stalls, the container
  is marked unhealthy by the runtime.

Migrations are applied explicitly. The worker never auto-runs destructive
migrations.

## Backup

```bash
AETHERGATE_DB_CONTAINER=aethergate-beta1-postgres-1 \
  scripts/ops/v2 backup --output /var/backups/aethergate-$(date +%F).dump --force
```

- PostgreSQL-native custom (`-Fc`) format, compressed, `pg_restore`-compatible.
- Explicit output path required; `--force` to overwrite; 0600 permissions.
- The queue encryption key and external secret-backend material are **not** in
  the database backup — back them up separately, or queued encrypted payloads
  cannot be decrypted after restore.

## Restore

```bash
AETHERGATE_DB_CONTAINER=aethergate-beta1-postgres-1 \
AETHERGATE_API_CONTAINER=aethergate-beta1-api-1 \
AETHERGATE_WORKER_CONTAINER=aethergate-beta1-worker-1 \
  scripts/ops/v2 restore --input /var/backups/aethergate-<date>.dump --force
```

- Destructive; `--force` required. Stops the API + worker first, restores, then
  restarts them, and reports the applied migration revision.
- Use the **same** queue key and external-secret configuration as the backup.

The `AETHERGATE_DB_CONTAINER` / `_API_CONTAINER` / `_WORKER_CONTAINER` overrides
target a disposable stack by name; omit them to target the standard v2 dev stack.

## Restart / recovery

- **API restart**: readiness recovers; identity/config/accounting state is
  DB-backed and preserved.
- **Worker restart**: queued undispatched work resumes; pre-dispatch lease expiry
  is reclaimed safely; dispatched ambiguous work follows the conservative
  `outcome_unknown` path; no duplicate execution or settlement.
- **Full stack restart**: stop API + worker + PostgreSQL cleanly **without
  deleting the volume**, then start again. Never use a destructive `reset` on a
  real deployment.
- **PostgreSQL outage**: while PostgreSQL is unreachable, `/health/ready` returns
  `503` and the API does not admit new requests (a valid request fails closed
  with a `5xx` error). The worker keeps its liveness heartbeat but performs no dispatch
  (claiming fails closed on the unreachable database). No work is silently lost:
  requests are either durably queued before the outage or rejected, never
  admitted-then-dropped. After PostgreSQL recovers, readiness returns `200` and
  the worker resumes draining the durable queue. A transient database error while
  an API call is waiting for a terminal result does **not** durably cancel the
  admitted request (only a genuine client disconnect does), so a request that was
  durably queued before the outage is preserved and drains exactly once after
  recovery.
- Prefer `docker compose stop <svc>` then `docker compose start <svc>` over
  `docker restart <container>`: a single `docker restart` does not reset the
  container healthcheck state machine, so the worker heartbeat probe can report a
  false `unhealthy` after a clean SIGTERM shutdown even though the worker is live.
  The dedicated heartbeat task keeps the file fresh during graceful shutdown of
  an in-flight request, but `stop`+`start` remains the recommended restart form.

## Common failure modes

- **`503 schema_not_current`**: migrations not applied. Run `alembic upgrade head`.
- **`503 database_unreachable`**: PostgreSQL is down or unreachable; the API fails
  closed and admits no new work until the database answers. Restore PostgreSQL,
  then confirm readiness returns `200`.
- **`destination_denied`**: endpoint host is not a literal-IP allowlisted address,
  or is loopback/link-local/metadata.
- **`secret_resolution_failed`**: a provider account references a SecretRef with no
  resolvable value in the file secret directory; check
  `AETHERGATE_SECRET_HOST_DIR` and the per-`SecretRef.id` files.
- **Requests queued, not dispatched**: endpoint paused/draining, quota window
  exhausted, or budget exhausted — inspect `wait_reason` and the queue summary.
- **`config_check` INVALID**: prod with `env` secret backend, a non-literal
  allowlist entry, a missing/non-absolute secret directory, or inference-auth
  bypass enabled.

## Rollback expectations

- Schema: migration `0017` is reversible (`downgrade 0016`); restoring a
  pre-upgrade backup is the safe rollback for data.
- Config: keep the previous `deploy/beta1/.env` and image tag to roll back a bad
  deploy.
- There is no automatic data rollback; use the PostgreSQL backup.

## Safe support bundle

```bash
docker compose --project-directory deploy/beta1 --env-file deploy/beta1/.env \
  -f deploy/beta1/compose.yaml run --rm --no-deps api \
  python -m aethergate.diagnostics --stdout --logs-lines 50 > /tmp/ag-diagnostics.json
```

Contains only app version, migration state, DB-reachable boolean, sanitized
feature flags, safe queue counts, observability summary, and redacted recent logs.
Never credentials, tokens, cookies, DB URLs/passwords, prompt/completion/encrypted
content, or provider secrets.
