#!/usr/bin/env bash
#
# AetherGate v2 — Beta 1 live nomnom acceptance (real internal Ollama).
#
# This is the live production-policy acceptance for the Beta 1 release candidate,
# distinct from the offline CI gate (scripts/ci/beta_release_gate.sh, which uses
# the deterministic fake Ollama). It runs the isolated production profile
# (deploy/beta1/compose.yaml) against the real internal Ollama on its literal-IP
# allowlisted address, with no model downloads, and tears down only its own
# disposable project.
#
# Environment:
#   BETA_NOMNOM_UPSTREAM_BASE    real Ollama base URL (default http://192.168.22.50:11434)
#   BETA_NOMNOM_UPSTREAM_MODEL   real completion model (default qwen3.8-2b-distill:Q6_K)
#   BETA_NOMNOM_PROJECT          compose project name (default ag-beta1-nomnom)
#   BETA_NOMNOM_WORKDIR          scratch dir (default /tmp/ag-beta1-nomnom)
#   BETA_NOMNOM_API_PORT         loopback api host port (default 18082)
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO_ROOT"

UPSTREAM_BASE="${BETA_NOMNOM_UPSTREAM_BASE:-http://192.168.22.50:11434}"
UPSTREAM_MODEL="${BETA_NOMNOM_UPSTREAM_MODEL:-qwen3.8-2b-distill:Q6_K}"
PROJECT="${BETA_NOMNOM_PROJECT:-ag-beta1-nomnom}"
WORKDIR="${BETA_NOMNOM_WORKDIR:-/tmp/ag-beta1-nomnom}"
API_PORT="${BETA_NOMNOM_API_PORT:-18082}"
ENV_FILE="$WORKDIR/env/.env"
SECRET_DIR="$WORKDIR/secrets"

log() { printf '[beta-nomnom] %s\n' "$*"; }
die() { printf '[beta-nomnom] error: %s\n' "$*" >&2; exit 1; }

# Prefer the repo venv (has httpx/openai); fall back to python3 on PATH.
PYTHON_BIN="${BETA_NOMNOM_PYTHON:-}"
if [[ -z "$PYTHON_BIN" ]]; then
    if [[ -x "$REPO_ROOT/.venv/bin/python" ]]; then
        PYTHON_BIN="$REPO_ROOT/.venv/bin/python"
    else
        PYTHON_BIN="$(command -v python3 || command -v python)"
    fi
fi

mkdir -p "$WORKDIR/env" "$SECRET_DIR"
chmod 700 "$WORKDIR" "$WORKDIR/env" "$SECRET_DIR"

UPSTREAM_HOST="$(python3 - <<PY
from urllib.parse import urlsplit
print(urlsplit("${UPSTREAM_BASE}").hostname or "localhost")
PY
)"

compose() {
    docker compose \
        --project-name "$PROJECT" \
        --project-directory deploy/beta1 \
        --env-file "$ENV_FILE" \
        -f deploy/beta1/compose.yaml \
        "$@"
}

wait_ready() {
    local url="$1" tries="${2:-90}"
    for _ in $(seq 1 "$tries"); do
        if curl -fsS "$url" >/dev/null 2>&1; then return 0; fi
        sleep 2
    done
    die "readiness did not pass: $url"
}

log "building Beta 1 image"
docker build -t aethergate-beta1:local -f deploy/v2/Dockerfile . >/dev/null

log "writing disposable production-policy secrets (allowlist=${UPSTREAM_HOST})"
cat > "$ENV_FILE" <<EOF
POSTGRES_PASSWORD=$(python3 -c 'import secrets; print(secrets.token_urlsafe(24))')
AETHERGATE_QUEUE_KEY=$(python3 -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')
AETHERGATE_BOOTSTRAP_TOKEN=agb_$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')
AETHERGATE_SECRET_HOST_DIR=${SECRET_DIR}
AETHERGATE_UPSTREAM_ALLOWLIST=${UPSTREAM_HOST}
EOF
chmod 600 "$ENV_FILE"

log "starting postgres + migrating empty DB to head"
compose up -d --no-deps postgres >/dev/null 2>&1 || compose up -d postgres >/dev/null 2>&1
for _ in $(seq 1 30); do
    if docker exec "${PROJECT}-postgres-1" pg_isready -U aethergate -d aethergate >/dev/null 2>&1; then break; fi
    sleep 1
done
compose run --rm --no-deps api alembic -c src/aethergate/migrations/alembic.ini upgrade head >/dev/null

log "config_check (prod) must be VALID"
compose run --rm --no-deps api python -m aethergate.config_check | tee "$WORKDIR/config_check.txt"
grep -q "RESULT: VALID" "$WORKDIR/config_check.txt" || die "prod config_check was not VALID"

log "starting api + worker"
compose up -d >/dev/null 2>&1
# The beta1 profile publishes an ephemeral loopback port; discover it.
API_ADDR="$(compose port api 8000 2>/dev/null || echo "127.0.0.1:${API_PORT}")"
if [[ "$API_ADDR" == *":"* ]]; then
    API_BASE_URL="http://${API_ADDR}"
else
    API_BASE_URL="http://127.0.0.1:${API_PORT}"
fi
log "API loopback address: ${API_BASE_URL}"
wait_ready "${API_BASE_URL}/health/ready"

log "live SDK proof: bootstrap + catalog + official SDK Models/Chat (bypass off)"
AETHERGATE_BOOTSTRAP_TOKEN="$(grep '^AETHERGATE_BOOTSTRAP_TOKEN=' "$ENV_FILE" | cut -d= -f2-)" \
BASE_URL="$API_BASE_URL" \
UPSTREAM_BASE="$UPSTREAM_BASE" \
UPSTREAM_MODEL="$UPSTREAM_MODEL" \
    "$PYTHON_BIN" - <<'PY'
import os
import sys

sys.path.insert(0, os.path.join(os.getcwd(), "scripts", "ci"))

import httpx
import golden_path as gp

base_url = os.environ["BASE_URL"]
gp._log("waiting for readiness")
gp._wait_ready(base_url)

with httpx.Client(base_url=base_url, timeout=120.0) as client:
    gp._log("bootstrap admin (one-use)")
    admin_key = gp._bootstrap(client, os.environ["AETHERGATE_BOOTSTRAP_TOKEN"])

with gp._admin_client(base_url, admin_key) as client:
    gp._log("create catalog + route to literal-IP upstream")
    gp._create_catalog(client, os.environ["UPSTREAM_BASE"], os.environ["UPSTREAM_MODEL"])
    projects = client.get("/admin/v1/projects?limit=200").json()
    project_id = projects["items"][0]["id"]
    gp._log("create inference credential")
    key, _credential_id = gp._inference_credential(client, project_id, "nomnom-cred")

from openai import OpenAI

client = OpenAI(base_url=f"{base_url}/v1", api_key=key, timeout=120.0)
gp._log("official SDK models.list")
if not client.models.list().data:
    gp._fail("models.list returned no models")
gp._log("official SDK chat non-stream")
if gp._sdk_inference(base_url, key, "golden-model", stream=False) != "ok":
    gp._fail("non-stream inference did not return ok")
gp._log("official SDK chat stream")
if gp._sdk_inference(base_url, key, "golden-model", stream=True) != "ok_stream":
    gp._fail("stream inference did not return ok_stream")
gp._log("beta live nomnom SDK Models/Chat complete")
PY

log "tear down disposable project"
compose down -v >/dev/null 2>&1 || true
log "Beta 1 live nomnom acceptance complete"
