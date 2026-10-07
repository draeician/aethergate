# AetherGate Agent Handoff

## Current State
- Branch: `v2`.
- AGV2-019V (close CLI token-handling and protected-storage gaps) is **complete**: login token
  redaction, keyring backend-failure hardening, centralized stale-session clearing on `401`, and
  logout/`clear-token` semantics, plus deterministic tests and live verification.
- Migration head is unchanged at `0016` (no new migration; `0001`–`0016` untouched).
- Full suite (host, `DATABASE_URL` + `AETHERGATE_TEST_DATABASE_URL`): **495 passed** (was 470; +25
  new `tests/test_cli.py` cases — the file now has 49 tests, up from 24). `ruff check src tests` clean,
  `git diff --check` clean, and a token/secret canary scan of the diff is clean. Containerized suite
  (`scripts/dev/v2 test`) also green.
- Live verification re-run: real `aethergate auth login` (human + `--json`) proved the raw `ags_...`
  token never appears on stdout or stderr, and the official OpenAI Python SDK non-stream + stream
  inference regression passed with `AETHERGATE_ALLOW_INFERENCE_AUTH_BYPASS=false`.

## AGV2-019V Completed — close CLI token-handling and protected-storage gaps

Final code review of AGV2-019 found three narrow CLI contract violations. These were closed without
touching the server, the device-flow architecture, or the migration graph.

### 1. Never print the raw CLI session token (`auth login`)
- `auth login` now consumes the one-time `ags_...` token for protected storage and emits only a safe
  success object via a new `_safe_login_result` helper: `status=success`, `stored=true`,
  `authentication_kind`, `principal_id`, `roles`, `expires_in`. The raw token is stripped before any
  output formatting, in both human and `--json` modes.
- The one-shot `--no-store` flag was **removed** (its in-memory store vanished on exit, so it was not
  a useful subsequent authenticated command); the `TokenStoreUnavailable` message no longer references
  it. `auth clear-token` is the explicit local-credential removal path instead.
- Canary tests assert a raw `ags_CANARY_...` token is absent from human stdout, human stderr, `--json`
  stdout, and `--json` stderr, and that `--json` stdout remains exactly one valid JSON document.

### 2. Translate keyring backend failures safely (`KeyringTokenStore`)
- `get`/`set`/`delete` now catch `keyring.errors.KeyringError` (incl. `NoKeyringError`,
  `KeyringLocked`, `PasswordSetError`) and raise the existing `TokenStoreUnavailable` with a fixed,
  non-secret backend description — no raw traceback, no plaintext fallback, never the token in the
  message.
- `delete` distinguishes `PasswordDeleteError` (credential absent → idempotent success) from other
  backend failures (→ `TokenStoreUnavailable`, because silent success would break the removal
  postcondition for e.g. stale-session clearing).

### 3. Clear stale stored human CLI sessions on 401 (centralized)
- `_resolve_token` now returns `(token, source)` (`env`/`store`/`none`); `_client_ctx` catches
  `AuthError` and calls `_maybe_clear_stale_session`, which deletes the profile token only when it is
  a persisted `ags_...` token. Persisted `agk_...` credentials are retained, `AETHERGATE_TOKEN`
  overrides never mutate the keyring, and `403`/`404`/`409`/`5xx`/network errors never clear a token.
  A cleared stale session returns auth exit code `3` with a re-login hint.

### 4. Logout / local-credential clearing
- `auth logout` revokes the server-side session first, then clears the local token. An
  already-expired/revoked session (`401`) still clears the local token (returns `{"revoked": false}`
  rather than trapping the user). A stored service credential is rejected with a usage error pointing
  at `auth clear-token` and is never sent to the CLI-session logout endpoint.
- `auth clear-token` removes the locally stored token (any kind) without contacting the server.

## Automated tests
- `tests/test_cli.py` grew from 24 to 49 tests: login token redaction (human + `--json`, canary),
  keyring backend-failure translation (no backend, get/set/delete failure, missing-credential delete,
  no-token-in-error, no-traceback via `main()`), stale `ags_` 401 clearing, `agk_` 401 retention,
  env-token non-clearing, non-401 error retention, logout/`clear-token` semantics.
- Full suite: **495 passed** host and containerized; `ruff check src tests` clean.

## Live verification status
**Complete.** Re-run against the real stack (device-capable local IdP on the host at
`http://192.168.22.50:8491`, real Ollama `qwen3.8-2b-distill:Q6_K` alias `gpt-4`, dynamic API port
`127.0.0.1:43001`, `AETHERGATE_ALLOW_INFERENCE_AUTH_BYPASS=false`). Driver:
`/tmp/opencode/agv2019v_live.py` (all green).

- **Human device login**: `aethergate auth login` stored `ags_...` in the keyring; the captured
  stdout/stderr contained no raw token and no `token=` line — only the safe `status`/`stored`/
  `authentication_kind`/`principal_id`/`roles`/`expires_in` confirmation.
- **JSON device login**: `aethergate auth login --json` stdout was exactly one valid JSON document
  with `stored=true`, `authentication_kind=cli_session`, and no `token` key; the raw keyring token was
  absent from both stdout and stderr.
- **SDK regression**: `models.list()` → `gpt-4`, plus non-stream and stream completions, all green
  with the inference auth bypass disabled.

## Key files
- `src/aethergate/cli/main.py` — `_resolve_token` returns `(token, source)`; `_maybe_clear_stale_session`;
  `_client_ctx` 401 clearing; `auth login` redaction (`_safe_login_result`, `--no-store` removed);
  `auth logout` rewrite; new `auth clear-token`.
- `src/aethergate/cli/tokenstore.py` — `KeyringTokenStore` error translation (`_backend_detail`),
  idempotent missing-credential delete.
- `src/aethergate/cli/errors.py` — `TokenStoreUnavailable` message (no `--no-store` reference).
- `tests/test_cli.py` — 25 new cases (49 total).
- Docs: `docs/cli.md`, `docs/development/README.md`.

## Decisions
- The raw CLI session token is a server-side one-time reveal; the CLI stores it and never emits it.
  There is no escape hatch that prints it (removed `--no-store` rather than expose the token).
- 401-based clearing is centralized at the `_client_ctx` request boundary and keyed on token source
  (`store`) + prefix (`ags_`), not on error codes.
- Keyring "credential not found" (`PasswordDeleteError`) is idempotent; every other keyring failure is
  a surfaced `TokenStoreUnavailable`, because silent success would violate the removal postcondition.
- No migration for AGV2-019V: the changes are CLI-side only.

## Issues / Risks
- The local IdP generates its RSA key in memory, so each restart changes the signing key; the API
  caches JWKS for 300s (`_JWKS_TTL_SECONDS`). The live pass reset the stack (new API process) after
  (re)starting the IdP to avoid a signature mismatch — worth remembering for any future live run.
- `ruff format` remains out of scope (73 pre-existing files would reformat); `ruff check` is the
  enforced gate and is clean.

## Recommended Next Step
Commit and push the AGV2-019V handoff and all work to `origin/v2` (implementation, tests, docs, and
all live evidence are green), then remove `.aethergate-wip` and pick up the next scheduled v2
workstream (web console or CLI write-expansion follow-up).
