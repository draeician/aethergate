#!/usr/bin/env bash
#
# Nomnom fresh-stack Alpha acceptance (real internal Ollama).
#
# This is the pre-release live gate (see docs/operations/alpha-runbook.md),
# distinct from offline CI. It runs against a disposable compose project so the
# long-lived dev database is never used as the proof.
#
# Usage (environment):
#   AETHERGATE_UPSTREAM_BASE   real Ollama base URL (required)
#   AETHERGATE_UPSTREAM_MODEL  real model name (required)
#   ALPHA_HOST_IP              host LAN IP reachable by browser+container (default: auto)
#   ALPHA_API_PORT             loopback api port (default 18080)
#   ALPHA_WEB_PORT             loopback web port (default 18081)
#   ALPHA_IDP_PORT             idp port (default 18090)
#   AETHERGATE_CSV             path to the committed backup/restore compose override
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO_ROOT"

UPSTREAM_BASE="${AETHERGATE_UPSTREAM_BASE:?set AETHERGATE_UPSTREAM_BASE}"
UPSTREAM_MODEL="${AETHERGATE_UPSTREAM_MODEL:?set AETHERGATE_UPSTREAM_MODEL}"
API_PORT="${ALPHA_API_PORT:-18080}"
WEB_PORT="${ALPHA_WEB_PORT:-18081}"
IDP_PORT="${ALPHA_IDP_PORT:-18090}"
HOST_IP="${ALPHA_HOST_IP:-$(python3 - <<'PY'
import socket
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
try:
    s.connect(("8.8.8.8", 80)); print(s.getsockname()[0])
except OSError:
    print("127.0.0.1")
finally:
    s.close()
PY
)}"

WORKDIR="${ALPHA_WORKDIR:-/tmp/ag-nomnom-alpha}"
ENVDIR="$WORKDIR/env"
OVERRIDE="$WORKDIR/compose.alpha.yaml"
mkdir -p "$WORKDIR" "$ENVDIR"
chmod 700 "$WORKDIR" "$ENVDIR"

idp_issuer="http://${HOST_IP}:${IDP_PORT}"

write_override() {
    cat > "$OVERRIDE" <<EOF
services:
  postgres:
    container_name: ag-alpha-postgres
    volumes:
      - ag-alpha-pgdata:/var/lib/postgresql/data
  api:
    container_name: ag-alpha-api
    ports: !override
      - "127.0.0.1:${API_PORT}:8000"
    environment:
      AETHERGATE_UPSTREAM_ALLOWLIST: "${UPSTREAM_HOST}"
  worker:
    container_name: ag-alpha-worker
    environment:
      AETHERGATE_UPSTREAM_ALLOWLIST: "${UPSTREAM_HOST}"
  web:
    container_name: ag-alpha-web
    ports: !override
      - "127.0.0.1:${WEB_PORT}:80"
  dev-oidc-idp:
    container_name: ag-alpha-oidc-idp
    ports: !override
      - "${IDP_PORT}:8090"
volumes:
  ag-alpha-pgdata:
EOF
    # Derive the upstream host (hostname[:port]) from the base URL for the
    # egress allowlist.
}

UPSTREAM_HOST="$(python3 - <<PY
from urllib.parse import urlsplit
print(urlsplit("${UPSTREAM_BASE}").hostname or "localhost")
PY
)"

write_env() {
    cat > "$ENVDIR/.env" <<EOF
POSTGRES_PASSWORD=$(python3 -c 'import secrets;print(secrets.token_urlsafe(24))')
AETHERGATE_QUEUE_KEY=$(python3 -c 'from cryptography.fernet import Fernet;print(Fernet.generate_key().decode())')
AETHERGATE_BOOTSTRAP_TOKEN=agb_$(python3 -c 'import secrets;print(secrets.token_urlsafe(32))')
AETHERGATE_UPSTREAM_ALLOWLIST=${UPSTREAM_HOST}
AETHERGATE_OIDC_ENABLED=true
AETHERGATE_OIDC_ISSUER=${idp_issuer}
AETHERGATE_OIDC_CLIENT_ID=aethergate-dev-client
AETHERGATE_OIDC_REDIRECT_URI=http://127.0.0.1:${WEB_PORT}/admin/v1/auth/oidc/callback
AETHERGATE_OIDC_SCOPES=openid
AETHERGATE_OIDC_SUBJECT=dev-user
AETHERGATE_OIDC_WEB_CALLBACK_PATH=/auth/callback
EOF
    chmod 600 "$ENVDIR/.env"
}

# A distinct project name so the disposable acceptance stack never collides with
# the long-lived dev stack (which uses the committed ``name: aethergate-v2``).
PROJECT_NAME="${ALPHA_PROJECT_NAME:-ag-alpha-acceptance}"

compose() {
    docker compose --project-name "$PROJECT_NAME" \
        --project-directory deploy/v2 --env-file "$ENVDIR/.env" \
        --file deploy/v2/compose.yaml --file deploy/v2/compose.web.yaml \
        --file "$OVERRIDE" "$@"
}

wait_ready() {
    local url="$1" tries="${2:-60}"
    for _ in $(seq 1 "$tries"); do
        if curl -fsS "$url" >/dev/null 2>&1; then return 0; fi
        sleep 2
    done
    die "readiness did not pass: $url"
}

die() { printf 'error: %s\n' "$*" >&2; exit 1; }

log() { printf '[nomnom-alpha] %s\n' "$*"; }

reset_db() {
    docker exec ag-alpha-postgres psql -U aethergate -d postgres \
        -c "DROP DATABASE IF EXISTS aethergate WITH (FORCE);" \
        -c "CREATE DATABASE aethergate OWNER aethergate;" >/dev/null
}

migrate() {
    compose run --rm --no-deps api \
        alembic -c src/aethergate/migrations/alembic.ini upgrade head >/dev/null
}

golden() {
    local phase="$1"; shift
    local bt
    bt="$(grep '^AETHERGATE_BOOTSTRAP_TOKEN=' "$ENVDIR/.env" | cut -d= -f2-)"
    AETHERGATE_BOOTSTRAP_TOKEN="$bt" OIDC_ISSUER="$idp_issuer" \
        .venv/bin/python scripts/ci/golden_path.py \
        --phase "$phase" --base-url "http://127.0.0.1:${WEB_PORT}" \
        --workdir "$WORKDIR/golden" --upstream-base "$UPSTREAM_BASE" \
        --upstream-model "$UPSTREAM_MODEL" "$@"
}

log "building image"
docker build -t aethergate-v2:local -f deploy/v2/Dockerfile . >/dev/null
docker build -t aethergate-v2-web:local frontend/ >/dev/null

log "writing deterministic alpha env ($idp_issuer)"
write_env
write_override

log "starting fresh disposable stack"
compose up -d --no-deps postgres >/dev/null 2>&1 || compose up -d postgres >/dev/null 2>&1
sleep 3
reset_db
migrate
compose up -d >/dev/null 2>&1
docker restart ag-alpha-web >/dev/null 2>&1 || true
wait_ready "http://127.0.0.1:${WEB_PORT}/health/ready"

log "phase: main (bootstrap + OIDC + catalog + SDK)"
rm -rf "$WORKDIR/golden"
golden main

log "backup"
rm -f "$WORKDIR/backup.dump"
docker exec ag-alpha-postgres pg_dump -Fc -U aethergate -d aethergate > "$WORKDIR/backup.dump"
chmod 600 "$WORKDIR/backup.dump"
log "backup: $(wc -c < "$WORKDIR/backup.dump") bytes"

log "destroy + restore"
reset_db
AETHERGATE_DB_CONTAINER=ag-alpha-postgres \
AETHERGATE_API_CONTAINER=ag-alpha-api \
AETHERGATE_WORKER_CONTAINER=ag-alpha-worker \
    scripts/ops/v2 restore --input "$WORKDIR/backup.dump" --force
docker restart ag-alpha-web >/dev/null 2>&1 || true
wait_ready "http://127.0.0.1:${WEB_PORT}/health/ready"

log "phase: post-restore (state + SDK)"
golden post-restore

log "restart: API only"
docker restart ag-alpha-api >/dev/null
docker restart ag-alpha-web >/dev/null 2>&1 || true
wait_ready "http://127.0.0.1:${WEB_PORT}/health/ready"
log "  API readiness recovered"

log "restart: worker"
docker restart ag-alpha-worker >/dev/null
log "  worker restarted"

log "restart: full stack (persistent volume)"
compose restart >/dev/null 2>&1 || compose up -d >/dev/null 2>&1
docker restart ag-alpha-web >/dev/null 2>&1 || true
wait_ready "http://127.0.0.1:${WEB_PORT}/health/ready"

log "post-restart SDK inference"
OIDC_ISSUER="$idp_issuer" .venv/bin/python scripts/ci/golden_path.py \
    --phase sdk-only --base-url "http://127.0.0.1:${WEB_PORT}" \
    --workdir "$WORKDIR/golden" >/dev/null

log "nomnom Alpha acceptance complete"
