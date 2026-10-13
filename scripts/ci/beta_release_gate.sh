#!/usr/bin/env bash
#
# AetherGate v2 — Beta 1 production-policy release gate (disposable stack).
#
# Starts the isolated production profile (deploy/beta1/compose.yaml) with the
# deterministic fake Ollama upstream on a fixed literal IP (compose.smoke.yaml),
# then proves:
#   1. a fresh empty database migrates to head and config_check is VALID in prod;
#   2. readiness (API) and the worker heartbeat healthcheck pass;
#   3. one-use bootstrap + literal-IP catalog + official SDK Models/Chat/Embeddings
#      with inference-auth bypass off (scripts/ci/beta_smoke.py --phase main);
#   4. a worker restart recovers cleanly (readiness + worker healthy);
#   5. backup -> destructive restore (disposable DB only) -> migration head 0017 ->
#      post-restore SDK with the SAME credentials (beta_smoke.py --phase post);
#   6. safe teardown of only the disposable project.
#
# Never touches the long-lived aethergate-v2 dev stack or any nomnom service, and
# never downloads a model. This is CI/nomnom acceptance tooling only.
#
# Environment overrides:
#   BETA_SMOKE_PROJECT   compose project name (default ag-beta1-smoke)
#   BETA_SMOKE_WORKDIR   scratch directory (default /tmp/ag-beta1-smoke)
#   BETA_SMOKE_API_PORT  loopback API host port (default 18081)
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO_ROOT"

PROJECT="${BETA_SMOKE_PROJECT:-ag-beta1-smoke}"
WORKDIR="${BETA_SMOKE_WORKDIR:-/tmp/ag-beta1-smoke}"
ENV_FILE="$WORKDIR/env/.env"
SECRET_DIR="$WORKDIR/secrets"
API_PORT="${BETA_SMOKE_API_PORT:-18081}"

# Set to 1 only after this run has created compose project resources, so the
# teardown trap never tears down a project this run did not itself create.
STARTED=0

log() { printf '[beta-release-gate] %s\n' "$*"; }
die() { printf '[beta-release-gate] error: %s\n' "$*" >&2; exit 1; }

# Prefer the repo venv (has httpx/openai); fall back to python3 on PATH.
PYTHON_BIN="${BETA_SMOKE_PYTHON:-}"
if [[ -z "$PYTHON_BIN" ]]; then
    if [[ -x "$REPO_ROOT/.venv/bin/python" ]]; then
        PYTHON_BIN="$REPO_ROOT/.venv/bin/python"
    else
        PYTHON_BIN="$(command -v python3 || command -v python)"
    fi
fi

mkdir -p "$WORKDIR/env" "$SECRET_DIR"
chmod 700 "$WORKDIR" "$WORKDIR/env" "$SECRET_DIR"

compose() {
    docker compose \
        --project-name "$PROJECT" \
        --project-directory deploy/beta1 \
        --env-file "$ENV_FILE" \
        -f deploy/beta1/compose.yaml \
        -f deploy/beta1/compose.smoke.yaml \
        "$@"
}

# A leftover compose project with our fixed name must never be silently adopted
# or torn down. Fail closed before creating anything if the project already
# owns containers, networks, or volumes (compose labels them with the project name).
project_exists() {
    local containers networks volumes
    containers="$(docker ps -aq --filter "label=com.docker.compose.project=${PROJECT}" 2>/dev/null)"
    networks="$(docker network ls -q --filter "label=com.docker.compose.project=${PROJECT}" 2>/dev/null)"
    volumes="$(docker volume ls -q --filter "label=com.docker.compose.project=${PROJECT}" 2>/dev/null)"
    [[ -n "${containers}${networks}${volumes}" ]]
}

# Teardown on success, `set -e` failure, and INT/TERM alike. Only tears down the
# project this run created (STARTED=1), and only by its own project name, never a
# shared dev/nomnom stack. Idempotent.
cleanup() {
    if [[ "$STARTED" == "1" ]]; then
        STARTED=0
        log "tearing down disposable project '$PROJECT'"
        compose down -v >/dev/null 2>&1 || true
    fi
}
trap cleanup EXIT
trap 'cleanup; exit 130' INT
trap 'cleanup; exit 143' TERM

write_env() {
    cat > "$ENV_FILE" <<EOF
POSTGRES_PASSWORD=$(python3 -c 'import secrets; print(secrets.token_urlsafe(24))')
AETHERGATE_QUEUE_KEY=$(python3 -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')
AETHERGATE_BOOTSTRAP_TOKEN=agb_$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')
AETHERGATE_SECRET_HOST_DIR=${SECRET_DIR}
AETHERGATE_UPSTREAM_ALLOWLIST=10.90.0.10
EOF
    chmod 600 "$ENV_FILE"
}

bootstrap_token() {
    grep '^AETHERGATE_BOOTSTRAP_TOKEN=' "$ENV_FILE" | cut -d= -f2-
}

wait_ready() {
    local url="$1" tries="${2:-90}"
    for _ in $(seq 1 "$tries"); do
        if curl -fsS "$url" >/dev/null 2>&1; then return 0; fi
        sleep 2
    done
    die "readiness did not pass: $url"
}

wait_worker_healthy() {
    for _ in $(seq 1 45); do
        local status
        status="$(docker inspect --format '{{.State.Health.Status}}' "${PROJECT}-worker-1" 2>/dev/null || echo missing)"
        if [[ "$status" == "healthy" ]]; then return 0; fi
        sleep 2
    done
    die "worker did not become healthy"
}

if project_exists; then
    die "compose project '$PROJECT' already exists (leftover from a prior run). Remove it explicitly or set BETA_SMOKE_PROJECT to a different name; refusing to adopt or tear down unrelated resources."
fi

log "building Beta 1 image"
docker build -t aethergate-beta1:local -f deploy/v2/Dockerfile . >/dev/null

log "writing disposable secrets"
write_env

log "starting postgres + fake-ollama"
STARTED=1
compose up -d --no-deps postgres fake-ollama >/dev/null 2>&1 || compose up -d postgres fake-ollama >/dev/null 2>&1
for _ in $(seq 1 30); do
    if docker exec "${PROJECT}-postgres-1" pg_isready -U aethergate -d aethergate >/dev/null 2>&1; then break; fi
    sleep 1
done

log "migrate empty database to head"
compose run --rm --no-deps api alembic -c src/aethergate/migrations/alembic.ini upgrade head >/dev/null

log "config_check (prod) must be VALID"
compose run --rm --no-deps api python -m aethergate.config_check | tee "$WORKDIR/config_check.txt"
grep -q "RESULT: VALID" "$WORKDIR/config_check.txt" || die "prod config_check was not VALID"

log "starting api + worker"
compose up -d >/dev/null 2>&1

log "waiting for readiness"
wait_ready "http://127.0.0.1:${API_PORT}/health/ready"

log "waiting for worker heartbeat healthcheck"
wait_worker_healthy

log "production-policy SDK smoke (main phase)"
AETHERGATE_BOOTSTRAP_TOKEN="$(bootstrap_token)" \
    "$PYTHON_BIN" scripts/ci/beta_smoke.py \
    --phase main \
    --base-url "http://127.0.0.1:${API_PORT}" \
    --workdir "$WORKDIR" \
    --upstream-base "http://10.90.0.10:11434"

log "worker restart recovery"
# stop + start (NOT `docker restart`): a single `docker restart` does not reset
# the container healthcheck state machine, so the heartbeat probe can report
# stale results after a clean SIGTERM shutdown even though the worker is live.
compose stop worker >/dev/null 2>&1
compose start worker >/dev/null 2>&1
wait_worker_healthy
wait_ready "http://127.0.0.1:${API_PORT}/health/ready"

log "database outage / recovery gate (readiness fails closed, no lost work)"
compose stop postgres >/dev/null 2>&1

# Readiness must fail closed (503) while PostgreSQL is unreachable.
readiness_code=""
for _ in $(seq 1 30); do
    readiness_code="$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:${API_PORT}/health/ready" 2>/dev/null || echo 000)"
    if [[ "$readiness_code" == "503" ]]; then break; fi
    sleep 1
done
if [[ "$readiness_code" != "503" ]]; then
    die "readiness did not fail closed during DB outage (got ${readiness_code:-none})"
fi

# A new request during the outage must never be admitted; 401/500/503 all mean
# the request was not durably enqueued and nothing was dispatched upstream.
outage_code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 30 \
    -X POST "http://127.0.0.1:${API_PORT}/v1/chat/completions" \
    -H 'Content-Type: application/json' \
    -H 'Authorization: Bearer outage-probe' \
    -d '{"model":"golden-model","messages":[{"role":"user","content":"probe"}]}' \
    2>/dev/null || echo 000)"
if [[ "$outage_code" == "200" || "$outage_code" == "201" ]]; then
    die "request was admitted during DB outage (HTTP ${outage_code})"
fi
log "outage: readiness 503, request not admitted (HTTP ${outage_code})"

log "restoring PostgreSQL and verifying recovery"
compose start postgres >/dev/null 2>&1
wait_ready "http://127.0.0.1:${API_PORT}/health/ready"
wait_worker_healthy

log "post-outage SDK smoke (persisted state + settlement intact)"
"$PYTHON_BIN" scripts/ci/beta_smoke.py \
    --phase post \
    --base-url "http://127.0.0.1:${API_PORT}" \
    --workdir "$WORKDIR"

log "backup"
rm -f "$WORKDIR/backup.dump"
AETHERGATE_DB_CONTAINER="${PROJECT}-postgres-1" \
    scripts/ops/v2 backup --output "$WORKDIR/backup.dump" --force

log "destructive restore (disposable database only)"
AETHERGATE_DB_CONTAINER="${PROJECT}-postgres-1" \
AETHERGATE_API_CONTAINER="${PROJECT}-api-1" \
AETHERGATE_WORKER_CONTAINER="${PROJECT}-worker-1" \
    scripts/ops/v2 restore --input "$WORKDIR/backup.dump" --force --keep-stopped
compose up -d >/dev/null 2>&1
wait_ready "http://127.0.0.1:${API_PORT}/health/ready"

log "post-restore SDK smoke (same credentials)"
"$PYTHON_BIN" scripts/ci/beta_smoke.py \
    --phase post \
    --base-url "http://127.0.0.1:${API_PORT}" \
    --workdir "$WORKDIR"

log "Beta 1 release gate complete (disposable project torn down by exit trap)"
