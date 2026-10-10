#!/usr/bin/env bash
#
# Refresh constraints.txt from a clean, known-green environment.
#
# Usage: scripts/ci/refresh_lock.sh
#
# Dependency resolution is intentionally explicit: this script builds a throwaway
# virtualenv, installs the current pyproject ranges, and rewrites the pinned
# version block of the committed constraints.txt (everything after the
# ``# BEGIN LOCKED VERSIONS`` marker). The explanatory header is preserved.
#
# A dependency refresh can legitimately change the generated OpenAPI contract, so
# after running this you MUST regenerate the OpenAPI schema + frontend types and
# run the full local gates before committing the refreshed lock together with any
# regenerated artifacts. Never run this automatically.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO_ROOT"

LOCK_FILE="$REPO_ROOT/constraints.txt"
MARKER="# BEGIN LOCKED VERSIONS"

PYTHON="${PYTHON:-python3}"
VENV_DIR="$(mktemp -d /tmp/ag-lock-XXXXXX)"

cleanup() { rm -rf "$VENV_DIR"; }
trap cleanup EXIT

printf 'building lock in %s\n' "$VENV_DIR" >&2
"$PYTHON" -m venv "$VENV_DIR"
"$VENV_DIR/bin/pip" install --upgrade pip >&2
"$VENV_DIR/bin/pip" install -e ".[dev]" >&2

NEW_BODY="$VENV_DIR/constraints.body"
{
    printf '%s\n' "$MARKER"
    "$VENV_DIR/bin/pip" freeze --exclude-editable | sort -f
} >"$NEW_BODY"

if ! grep -qF "$MARKER" "$LOCK_FILE"; then
    printf 'error: marker "%s" not found in %s; refusing to rewrite\n' \
        "$MARKER" "$LOCK_FILE" >&2
    exit 1
fi

# Replace everything from the marker onward, keeping the header above it.
awk -v marker="$MARKER" '
    index($0, marker) == 1 { exit }
    { print }
' "$LOCK_FILE" >"$VENV_DIR/constraints.new"
cat "$NEW_BODY" >>"$VENV_DIR/constraints.new"
mv "$VENV_DIR/constraints.new" "$LOCK_FILE"

printf '\nRewrote %s. Regenerate OpenAPI + frontend types and run the full gates\n' "$LOCK_FILE" >&2
printf 'before committing the refreshed lock.\n' >&2
