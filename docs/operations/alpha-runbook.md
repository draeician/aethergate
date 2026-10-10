# AetherGate v2 — Alpha Operator Runbook

Audience: a new operator who did not develop AetherGate. This runbook is written
for the **internal/self-hosted Alpha 1** release. It is not a production/beta
claim.

## Alpha 1 supported scope

Supported:

- `GET /v1/models`, `GET /v1/models/{model}`;
- `POST /v1/chat/completions` (non-streaming **and** streaming/SSE);
- the `/admin/v1` management API (identity, catalog, accounting, queue,
  observability, OIDC/session, CLI/device-flow);
- the Linux CLI over `/admin/v1`;
- the web console (operator + management + accounting + observability).

Explicitly **not** in Alpha 1 (deferred):

- the OpenAI Responses API;
- embeddings;
- v1 -> v2 data migration;
- production-grade HA/failover;
- external Prometheus/OpenTelemetry/TSDB exporters, paging, alerting;
- billing/invoice/payment semantics;
- hosted multi-company SaaS tenancy.

Migration head: **0017**.

## Prerequisites

- Linux host with Docker + Docker Compose (rootless is supported).
- Python 3.12+ (for the CLI/SDK helpers and offline tooling) and Node 22 (only to
  build the web image).
- A PostgreSQL 16 instance — the bundled Compose service is sufficient for Alpha.
- An OIDC provider for human login (the repo ships a deterministic IdP for
  dev/verification; see below).
- An upstream OpenAI-compatible provider (e.g. Ollama) reachable from the
  API/worker, on an allowlisted host.

## Layout

- `deploy/v2/compose.yaml` — core stack (postgres + api + worker). This alone is
  **not** a production deployment.
- `deploy/v2/compose.web.yaml` — dev web console + deterministic OIDC IdP.
- `deploy/v2/compose.ci.yaml` — offline CI override (fake Ollama). Never used in
  production/alpha.
- `scripts/dev/v2` — dev helper (build/up/migrate/test/workers/reconcile/web).
- `scripts/ops/v2` — operator recovery tooling (backup/restore/migrate/
  diagnostics). Operates directly on PostgreSQL; see Backup/Restore below.

## Secrets

Required (never commit these; `.env` files are gitignored):

- `POSTGRES_PASSWORD` — database password.
- `AETHERGATE_QUEUE_KEY` — Fernet key for queue-content encryption. Lives
  **outside** PostgreSQL. Losing it makes queued encrypted payloads
  unrecoverable — back it up separately.
- `AETHERGATE_BOOTSTRAP_TOKEN` — one-use bootstrap secret. Remove/unset it after
  bootstrap.

Generate them (example):

```bash
python3 -c 'import secrets; print("POSTGRES_PASSWORD=" + secrets.token_urlsafe(24))'
python3 -c 'from cryptography.fernet import Fernet; print("AETHERGATE_QUEUE_KEY=" + Fernet.generate_key().decode())'
python3 -c 'import secrets; print("AETHERGATE_BOOTSTRAP_TOKEN=agb_" + secrets.token_urlsafe(32))'
```

Store `deploy/v2/.env` with restrictive permissions (`chmod 600`).

### Configuration preflight

Before starting, validate configuration without connecting to any provider:

```bash
python -m aethergate.config_check
```

Exit 0 = valid; nonzero = invalid. It validates DB config, the queue key, rejects
prod inference-auth bypass, checks OIDC prod issuer rules (https:// in prod),
validates the bootstrap-token shape, and warns when the upstream allowlist is
empty or optional features are disabled. It never prints secret values.

## Clean install

```bash
# 1. Provide secrets
mkdir -p deploy/v2
cat > deploy/v2/.env <<'EOF'
POSTGRES_PASSWORD=...
AETHERGATE_QUEUE_KEY=...
AETHERGATE_BOOTSTRAP_TOKEN=agb_...
AETHERGATE_UPSTREAM_ALLOWLIST=ollama.internal
EOF
chmod 600 deploy/v2/.env

# 2. Build images
docker build -t aethergate-v2:local -f deploy/v2/Dockerfile .

# 3. Start PostgreSQL and migrate the empty DB to head (0017)
docker compose --project-directory deploy/v2 --file deploy/v2/compose.yaml up -d postgres
docker compose --project-directory deploy/v2 --file deploy/v2/compose.yaml run --rm --no-deps api \
  alembic -c src/aethergate/migrations/alembic.ini upgrade head

# 4. Start API + worker
docker compose --project-directory deploy/v2 --file deploy/v2/compose.yaml up -d

# 5. Health
curl -fsS http://127.0.0.1:<api-port>/health/live    # process alive
curl -fsS http://127.0.0.1:<api-port>/health/ready   # DB reachable AND schema==head
```

The API publishes an **ephemeral loopback host port**; discover it with
`docker compose --project-directory deploy/v2 port api 8000`.

### One-use bootstrap

```bash
TOKEN=$(grep '^AETHERGATE_BOOTSTRAP_TOKEN=' deploy/v2/.env | cut -d= -f2-)
curl -fsS -X POST http://127.0.0.1:<api-port>/admin/v1/bootstrap \
  -H "Authorization: Bearer $TOKEN"
# -> {"credential": {...}, "raw_key": "agk_..."}   (raw key shown ONCE)
```

Then **remove the bootstrap token** from `deploy/v2/.env` and recreate the API so
bootstrap is disabled:

```bash
sed -i '/^AETHERGATE_BOOTSTRAP_TOKEN=/d' deploy/v2/.env
docker compose --project-directory deploy/v2 --file deploy/v2/compose.yaml up -d --no-deps --force-recreate api
```

Set the returned `agk_...` key aside as your initial `system_admin` admin
credential. It authenticates `/admin/v1` (never `/v1` inference).

### OIDC configuration

Set (in `deploy/v2/.env`) and recreate the API:

```
AETHERGATE_OIDC_ENABLED=true
AETHERGATE_OIDC_ISSUER=https://idp.example.com     # https:// required in prod
AETHERGATE_OIDC_CLIENT_ID=aethergate
AETHERGATE_OIDC_CLIENT_SECRET=...
AETHERGATE_OIDC_REDIRECT_URI=https://gw.example.com/admin/v1/auth/oidc/callback
AETHERGATE_OIDC_WEB_CALLBACK_PATH=/auth/callback   # web-console redirect mode
```

Link an OIDC `(issuer, subject)` to a durable USER principal that holds a
`system_admin` role, or enable JIT provisioning deliberately
(`AETHERGATE_OIDC_JIT_PROVISIONING=true`, JIT identities are unprivileged). The
dev IdP (`python -m aethergate.dev_oidc_idp_server`) is verification tooling and
must not be used as production identity infrastructure.

### TLS / reverse proxy expectations

- Terminate TLS at a reverse proxy in front of the web console/API.
- The proxy must preserve `Authorization`, `Cookie`, and `X-CSRF-Token` headers,
  set `Host`, and not strip `Set-Cookie`.
- Long-held non-stream inference and SSE need generous proxy read timeouts; the
  bundled `frontend/nginx.conf` uses `proxy_read_timeout 600s` /
  `proxy_send_timeout 600s` on `/v1/`.
- The API's own host port is loopback/ephemeral by default; do not expose
  PostgreSQL. In prod the OIDC issuer must be HTTPS.
- Route templates (not raw paths) are logged; ensure the proxy does not append
  sensitive query strings that would be logged by the proxy.

## Catalog / provider setup

Through `/admin/v1` (or the web console), in order:

1. SecretRef (metadata only; no raw secret value) if the provider needs a key.
2. Provider (`kind: ollama` etc.).
3. Provider account (optionally references the SecretRef; a local Ollama needs no
   secret).
4. Endpoint (`base_destination` must be an allowlisted host; egress policy is
   enforced at create time and again before dispatch).
5. Model alias (client-visible OpenAI model name).
6. Route binding (alias -> endpoint/account; optional quota group; exactly one
   active route per alias).
7. Optional: price policy (route pricing) and project budget policy.

## Creating credentials

- Admin creds (audience `admin`) authenticate `/admin/v1`.
- Inference creds (audience `inference`, scope `inference:invoke`) authenticate
  `/v1`.

Create an inference credential and use it with the official SDK:

```python
from openai import OpenAI
client = OpenAI(base_url="http://<gateway>/v1", api_key="agk_...")
client.chat.completions.create(model="<alias>", messages=[{"role":"user","content":"hi"}])
```

The raw key is returned exactly once at create/rotate; it is never recoverable
from reads.

## Health / readiness

- `GET /health/live` — process liveness only.
- `GET /health/ready` — `200` only when PostgreSQL answers **and** the applied
  schema revision equals the code's expected head. If the schema is unmigrated or
  incompatible, readiness is `503` with `reason: schema_not_current` so the
  instance never serves normal traffic against an unsupported schema.
- Readiness never depends on individual upstream providers: one unhealthy
  provider must not make the gateway not-ready.

Migrations are applied explicitly (`scripts/dev/v2 migrate` or the `alembic`
command). The worker never auto-runs destructive migrations.

## Logs and request correlation

- Prod/alpha: JSON logs (one object per line). Set `AETHERGATE_ENV=prod` (and/or
  `log_level`). Development keeps human-readable logs.
- Every request carries a **gateway request ID** (used in API responses and in
  that request's structured logs). This is distinct from the **upstream provider
  request ID** (`X-Upstream-Request-Id`); they are never the same value.
- Fields: `timestamp`, `level`, `logger`/`event`, `request_id`, `attempt_id`,
  `endpoint_id`, lifecycle `state`, HTTP `method`/`route` (template)/`status`,
  `duration_ms`, and safe numeric `upstream_status_code`.
- Never logged: `Authorization`, cookies, CSRF, OIDC tokens/codes/verifiers/
  nonces, `agk_`/`ags_`/bootstrap/provider keys, DB URLs/passwords,
  prompt/completion/tool content, encrypted payload/result bytes, raw provider
  bodies/headers. A central redaction filter scrubs known secret shapes as
  defense in depth.

## Queue / operator recovery

- Pause/drain/resume an endpoint: `POST /admin/v1/queue/endpoints/{id}/{pause|drain|resume}`.
- Cancel queued/in-flight work: `POST /admin/v1/queue/requests/{id}/cancel`.
- `outcome_unknown` (ambiguous post-dispatch failures) hold their slot; resolve
  them explicitly through `POST /admin/v1/queue/requests/{id}/reconcile`
  (`failed` or `cancelled`) or the dev `python -m aethergate.reconcile` path.
  There is no automatic retry and no "release all stuck slots" shortcut.
- Inspect queue state safely: `GET /admin/v1/queue/summary`,
  `GET /admin/v1/observability/summary`.

## Backup

Use PostgreSQL-native, complete backups with the operator tooling:

```bash
scripts/ops/v2 backup --output /var/backups/aethergate-$(date +%F).dump --force
```

- Custom (`-Fc`) format, compressed, restorable with `pg_restore`.
- Explicit output path required; refuses to overwrite without `--force`.
- Restrictive permissions (0600).
- The database password is never echoed.

**Dependency:** the queue encryption key (`AETHERGATE_QUEUE_KEY`) and any
external secret-backend material are **not** in the database backup. Back them up
separately and keep them for restore, or queued encrypted payloads cannot be
decrypted. PostgreSQL stores only SecretRef **metadata**, never raw provider
secrets.

## Restore

Restore is destructive and quiesces the API + worker first:

```bash
scripts/ops/v2 restore --input /var/backups/aethergate-2026-10-10.dump --force
```

- Requires `--force` (replaces the database).
- Automatically stops the API + worker before replacing the database; refuses to
  restore into a live-writing target.
- After restore it reports the applied migration revision and restarts the API +
  worker.
- Use the **same** queue key and external-secret configuration as the backup.
- On a non-default/disposable stack, target containers with
  `AETHERGATE_DB_CONTAINER`, `AETHERGATE_API_CONTAINER`,
  `AETHERGATE_WORKER_CONTAINER`.

Restart the web container after API recreation so nginx re-resolves the `api`
DNS name.

## Upgrade

1. Back up first (`scripts/ops/v2 backup ...`).
2. Pull the new images / rebuild.
3. Apply migrations: `scripts/dev/v2 migrate` (or `alembic upgrade head`).
4. Recreate the API + worker. Readiness confirms `0017`.
5. Verify a real request through the official SDK.

Migrations are explicit and reversible where practical; do not edit migrations
`0001`–`0017`.

## Restart / recovery

- **API restart**: readiness recovers; identity/config/accounting state is DB-
  backed and preserved. Browser sessions persist while the session rows remain
  active (DB-backed).
- **Worker restart**: queued undispatched work resumes; pre-dispatch lease expiry
  is reclaimed safely; dispatched ambiguous work follows the conservative
  `outcome_unknown` path; no duplicate execution or settlement.
- **Full stack restart**: stop API + workers + PostgreSQL cleanly **without
  deleting the volume**, then start again; persistent DB state and official-SDK
  inference remain intact. Never use a destructive `reset` on a real deployment.

## Common failure modes

- **`503 schema_not_current`**: migrations not applied. Run `alembic upgrade head`.
- **`destination_denied`**: endpoint host is not on `AETHERGATE_UPSTREAM_ALLOWLIST`,
  or is a loopback/link-local/metadata host.
- **`secret_resolution_failed`**: a provider account references a SecretRef with no
  resolvable value in the trusted runtime; this is a gateway/config failure, not
  an upstream-health failure.
- **Requests queued, not dispatched**: endpoint paused/draining, quota window
  exhausted, or budget `budget_window_exhausted`. Inspect `wait_reason` on the
  queue request and `GET /admin/v1/queue/summary`.
- **Repeated 429 cooldown**: a shared quota group is cooling down after a provider
  429. Check `GET /admin/v1/queue/quota-status`.
- **OIDC login lands back at `/auth/callback` without a session**: the subject is
  not linked to a principal with a role, or the issuer/JWKS cache is stale after
  an IdP restart. Restart the API to clear the JWKS cache, and confirm the
  identity link.

## Rollback expectations

- Schema: migration `0017` is reversible (`downgrade 0016`); restoring a
  pre-upgrade backup is the safe rollback for data.
- Config: keep the previous `deploy/v2/.env` and image tag to roll back a bad
  deploy.
- There is no automatic data rollback; use the PostgreSQL backup.

## Safe support bundle

Gather a diagnostics bundle with an explicit output path (nothing is uploaded):

```bash
scripts/ops/v2 diagnostics --output /tmp/ag-diagnostics.json --logs-lines 50
```

It contains only: app version, migration state, DB-reachable boolean, sanitized
feature flags, safe queue counts, observability summary (no content), and
redacted recent logs. It never includes credentials/tokens/cookies, DB
URLs/passwords, prompt/completion/encrypted content, or provider secrets.
