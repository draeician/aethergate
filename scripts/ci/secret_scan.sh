#!/usr/bin/env bash
#
# Repository secret/canary scan. Fails (nonzero) if tracked content contains
# credential-shaped material. This is a heuristic gate, not a substitute for a
# proper secret scanner; it targets AetherGate's own key prefixes and common
# high-risk shapes.
#
# Usage: scripts/ci/secret_scan.sh
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO_ROOT"

# Only scan tracked files.
FILES=$(git ls-files)

# Key patterns. `agk_`/`ags_`/`agb_` are AetherGate key prefixes; the rest are
# common credential shapes. Test fixtures use obviously-synthetic values, so we
# exclude the tests/ and e2e canary fixtures explicitly.
PATTERNS='(agk_[A-Za-z0-9]{16,}|ags_[A-Za-z0-9]{16,}|agb_[A-Za-z0-9]{16,}|-----BEGIN [A-Z ]*PRIVATE KEY-----|AKIA[0-9A-Z]{16})'

status=0
for file in $FILES; do
    # Skip the log-safety test, which intentionally embeds synthetic canaries.
    case "$file" in
        tests/test_logging_safety.py|tests/test_diagnostics.py|scripts/ci/secret_scan.sh) continue ;;
    esac
    if [[ ! -f "$file" ]]; then continue; fi
    # Skip binary files.
    if ! grep -Iq . "$file" 2>/dev/null; then continue; fi
    if grep -nEI "$PATTERNS" "$file" >/tmp/ag_secret_hit 2>/dev/null; then
        if [[ -s /tmp/ag_secret_hit ]]; then
            echo "possible secret in $file:" >&2
            sed 's/^/    /' /tmp/ag_secret_hit >&2
            status=1
        fi
    fi
done

# Also fail on committed .env / key material (should be gitignored).
LEAKED=$(git ls-files | grep -E '(^|/)(\.env|\.pem|\.key)$|\.env\.(prod|local)$' || true)
if [[ -n "$LEAKED" ]]; then
    echo "tracked secret-bearing file(s):" >&2
    echo "$LEAKED" >&2
    status=1
fi

if [[ "$status" -eq 0 ]]; then
    echo "secret scan: clean"
fi
exit "$status"
