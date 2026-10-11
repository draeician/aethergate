#!/usr/bin/env bash
#
# AetherGate legacy-v1 reintroduction guard.
#
# Fails (nonzero) if the active repository reintroduces retired v1 runtime
# artifacts or instructions. This is a deterministic structural/string gate, not
# a history diff: it inspects the current tracked tree so the retired v1 runtime
# can never come back as an active, buildable path.
#
# The immutable architecture audit
# (docs/audits/v2-architecture-audit-2026-10-03.md) and the migration-retirement
# document (docs/migrations/v1-to-v2.md) may describe the historical v1 system
# without failing the guard. Only the *active* product tree is checked.
#
# Usage: scripts/ci/no_legacy_v1.sh [--self-test]
#
#   --self-test  additionally prove the guard detects synthetic forbidden
#                content (used by tests/CI); exits 0 only if the guard passes the
#                real tree and rejects a synthetic legacy-v1 tree.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

# Historical/exempt paths that may legitimately mention v1 (prose only).
EXEMPT_GREP=(
    ":!docs/audits/*"
    ":!docs/migrations/v1-to-v2.md"
    ":!docs/development/current-task.md"
    ":!docs/development/agent-handoff.md"
    ":!scripts/ci/no_legacy_v1.sh"
)

# Forbidden tracked paths (retired v1 runtime + ad-hoc SQLite scripts).
FORBIDDEN_PATHS=(
    "app/auth.py"
    "app/database.py"
    "app/main.py"
    "app/models.py"
    "Dockerfile"
    "docker-compose.yml"
    "requirements.txt"
    "init_db.py"
    "manage.py"
    "debug_inference.py"
    "scripts/add_model_config_columns.py"
    "scripts/add_rate_limit_column.py"
    "scripts/migrate_endpoints.py"
)

# run_checks <repo-dir> -> prints violations, returns 0 clean / 1 violations.
run_checks() {
    local root="$1" fail=0
    cd "$root"

    _bad() { printf 'no-legacy-v1: %s\n' "$*" >&2; fail=1; }

    for path in "${FORBIDDEN_PATHS[@]}"; do
        if git ls-files --error-unmatch "$path" >/dev/null 2>&1; then
            _bad "forbidden retired v1 path is tracked: $path"
        fi
    done

    if git ls-files 'app/*' | grep -q .; then
        _bad "forbidden retired v1 directory is tracked: app/"
    fi

    # Active Python imports of the retired `app` package (real import lines only).
    local hits
    hits="$(git grep -nE '^[[:space:]]*(from|import)[[:space:]]+app([.]|[[:space:]]|$)' \
        -- '*.py' "${EXEMPT_GREP[@]}" 2>/dev/null || true)"
    if [[ -n "$hits" ]]; then
        printf '%s\n' "$hits" >&2
        _bad "active Python code imports the retired v1 'app' package"
    fi

    # Active SQLModel/aiosqlite runtime references (code/packaging/CI, not docs).
    hits="$(git grep -nE 'sqlmodel|aiosqlite|sqlite\+aiosqlite' \
        -- '*.py' '*.toml' '*.txt' '*.yaml' '*.yml' '*.sh' \
        "${EXEMPT_GREP[@]}" 2>/dev/null || true)"
    if [[ -n "$hits" ]]; then
        printf '%s\n' "$hits" >&2
        _bad "active SQLModel/aiosqlite v1 runtime reference reintroduced"
    fi

    # v1 master-key admin model in active code/config. Documentation and agent
    # instructions are intentionally excluded: retirement docs must be able to
    # name the retired scheme, and agent files must be able to say "do not use
    # MASTER_API_KEY / x-admin-key" without tripping the guard. Reintroducing it
    # in executable code or configuration is what fails.
    hits="$(git grep -nE 'MASTER_API_KEY|x-admin-key' \
        -- '*.py' '*.toml' '*.yaml' '*.yml' '*.sh' '*.ts' '*.tsx' '*.js' '*.mjs' \
        "${EXEMPT_GREP[@]}" 2>/dev/null || true)"
    if [[ -n "$hits" ]]; then
        printf '%s\n' "$hits" >&2
        _bad "active v1 master-key admin model (MASTER_API_KEY/x-admin-key) reintroduced"
    fi

    return "$fail"
}

if run_checks "$REPO_ROOT"; then
    status=0
else
    status=$?
fi

if [[ "$status" -eq 0 ]]; then
    echo "no-legacy-v1: clean (no retired v1 runtime artifacts in the active tree)"
fi

if [[ "${1:-}" == "--self-test" ]]; then
    if [[ "$status" -ne 0 ]]; then
        printf 'no-legacy-v1: self-test aborted (real tree already failed)\n' >&2
        exit 2
    fi
    tmp="$(mktemp -d /tmp/ag-nolegacy-XXXXXX)"
    trap 'rm -rf "$tmp"' EXIT
    (
        cd "$tmp"
        git init -q
        git config user.email t@example.com
        git config user.name t
        mkdir -p app scripts
        printf 'print("v1")\n' > app/main.py
        printf 'from app.database import init_db\n' > init_db.py
        printf 'sqlmodel\n' > requirements.txt
        printf 'DATABASE_URL=sqlite+aiosqlite:///x.db\n' > docker-compose.yml
        printf 'MASTER_API_KEY=x\n' > settings.yaml
        git add -A
        git -c commit.gpgsign=false commit -qm synthetic
    )
    # The synthetic tree must be REJECTED by the exact same checks.
    if run_checks "$tmp" >/dev/null 2>&1; then
        printf 'no-legacy-v1: self-test failed (synthetic legacy v1 content was not detected)\n' >&2
        exit 1
    fi
    echo "no-legacy-v1: self-test ok (synthetic legacy v1 content detected)"
    exit 0
fi

exit "$status"
