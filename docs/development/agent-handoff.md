# AetherGate Agent Handoff

## Current State
- Branch: `v2`.
- AGV2-019 (Linux CLI foundation + human OAuth device-flow auth) is **complete**: implementation, migration
  `0016`, automated tests, docs, and every required live nomnom scenario (A–I) plus the OpenAI SDK
  regression (J) verified against the real stack.
- Migration head: `0016` (adds `device_authorizations` + `cli_sessions`). `0001`–`0015` untouched.
- Full suite (with `DATABASE_URL` + `AETHERGATE_TEST_DATABASE_URL`): **470 passed** (was 425; +20
  `test_cli_session.py`, +1 `test_migrations.py` 0015→0016, +24 `test_cli.py`). `ruff check src tests`
  clean. Containerized suite (`scripts/dev/v2 test`) also green.
- Live stack: dynamic API port `127.0.0.1:43001`, deterministic local IdP with device support on the
  host at `http://192.168.22.50:8491` (issuer `http://192.168.22.50:8491`, device client
  `aethergate-device-client`), real Ollama backend `http://192.168.22.50:11434`, upstream
  `qwen3.8-2b-distill:Q6_K` alias `gpt-4`, `AETHERGATE_ALLOW_INFERENCE_AUTH_BYPASS=false`.

## AGV2-019 Completed — Linux CLI foundation and human OAuth device-flow authentication

### 1. Device-flow architecture (RFC 8628)
- Typed config: `AETHERGATE_OIDC_DEVICE_CLIENT_ID` (public client, no secret), `AETHERGATE_OIDC_DEVICE_SCOPES`
  (`openid` always enforced and de-duplicated), `AETHERGATE_CLI_SESSION_TTL_SECONDS` (default 43200s).
  `device_flow_enabled` is derived (`oidc_enabled AND oidc_device_client_id`); the browser confidential
  client secret is never placed in the CLI.
- `POST /admin/v1/auth/device/start` calls the provider `device_authorization_endpoint`, persists a
  short-lived durable transaction storing only a one-way SHA-256 `device_code_hash` (never the raw
  code), and returns the one-time `device_code` plus non-secret `user_code`/verification URIs.
- `POST /admin/v1/auth/device/poll` hashes the submitted `device_code`, enforces expiry and the
  provider poll interval (too-fast poll → `slow_down` without calling the provider; provider `slow_down`
  bumps the stored interval +5s capped at 60s), translates `authorization_pending`/`slow_down`, and on
  success validates the ID token (iss/aud/sig/exp/nbf, required `sub`) through the same production
  verification path as browser OIDC, resolves the `ExternalIdentity` by `(issuer, subject)`, and
  creates a durable CLI session exactly once. Unknown identity follows the same JIT policy (unprivileged
  `user`, no role from claims).

### 2. Durable CLI sessions (migration 0016)
- `CliSession`: stable `CliSessionId`, `principal_id`, one-way SHA-256 `token_hash`, `created_at`,
  `expires_at`, throttled `last_seen_at`, `revoked_at`. Raw token is `ags_<4-hex>_<token_urlsafe(32)>`,
  returned exactly once, never persisted/logged.
- `DeviceAuthorization`: one-way `device_code_hash`, `user_code` reference, poll `interval`,
  `expires_at`, `consumed_at`, terminal outcome. One-time + expiry constrained.
- `POST /admin/v1/auth/cli/logout` durably revokes the current CLI session (idempotent). Session
  revalidation re-checks revocation/expiry/active principal/active project on every request.

### 3. Admin authentication unification
- `AdminAuthenticationKind.CLI_SESSION`; `AdminRequestContext` gains `cli_session_id`.
- `_admin_context` bearer routing is prefix-dispatched: `ags_` → `authenticate_cli_session`, otherwise
  `agk_` → `authenticate_admin`. CLI sessions use the same Principal/RoleAssignment RBAC engine as
  browser sessions; an invalid `Authorization` never falls through to a cookie; CLI bearer requests
  need no CSRF; a CLI session never authenticates `/v1` inference.

### 4. Deterministic local device-flow IdP
- `DevOidcIdp` (in `dev_oidc_idp.py`) advertises `device_authorization_endpoint`, serves
  `/device_authorization`, `/token` (device grant), and control endpoints `/device/approve` /
  `/device/deny` / `/device/expire` (by `user_code`). Approval accepts an optional `subject` so one
  issuer mints ID tokens for multiple human identities. No Internet IdP, no real provider credentials.

### 5. Installable CLI (`aethergate`)
- Console entry point `aethergate = "aethergate.cli.main:main"`; deps `click`, `keyring`, `tomli-w`
  (profile write), stdlib `tomllib` (profile read). `main()` wraps `cli()` to map `CliError` to stable
  exit codes without tracebacks (a real bug found: the entry point previously pointed at `cli`, so
  `CliError` escaped as an unhandled traceback/exit 1 — fixed and covered by a test).
- CLI is a thin HTTP client over `/admin/v1`; it never imports persistence/repository modules.

### 6. Profiles / token storage / service credential
- Profiles under XDG `~/.config/aethergate/config.toml`, no secrets. `create/list/show/delete/set-default`;
  base-URL validation (https expected for remote; loopback http allowed; `--insecure` labeled dev-only);
  trusted `--ca-bundle`.
- Tokens via `TokenStore` (keyring: Secret Service/KWallet); write is verified by a read-back round
  trip; no silent plaintext fallback. `auth login --no-store` = ephemeral in-process login;
  `AETHERGATE_TOKEN` overrides storage for the current process only.
- `auth set-token --stdin` (or hidden prompt) validates the credential against `/admin/v1/whoami`
  before storing; inference-audience credentials are rejected for admin CLI use; argv never accepts a
  raw credential.

### 7. Output / exit-code / HTTP client contract
- Every command supports `--json` (exactly one JSON doc on stdout; diagnostics on stderr). Stable exit
  codes: `0` ok, `1` generic, `2` usage, `3` auth, `4` forbidden, `5` not found, `6` conflict,
  `7` network/TLS, `8` server. Device terminal codes map to `3`.
- One reusable `Client`: base URL + trusted CA, Authorization injection, JSON, timeout, structured
  error parsing, safe retry for idempotent GET only (never POST/PATCH), CLI-version User-Agent.

### 8. Command coverage / completion / version
- Core `profile`/`auth login|logout|whoami|set-token`/`completion`; `projects list/show/create/update`;
  `principals list/show/create`; `credentials list/show`; `role-assignments list/show`; catalog
  `providers/provider-accounts/endpoints/model-aliases/route-bindings/price-policies` (`list/show`) +
  `create-provider`; accounting `budgets/usage/ledger/audit`; queue/operator `queue
  list/show/summary/cancel/outcome-unknown/reconcile/quota-status` + `endpoint
  list/show/pause/drain/resume`. No generic "POST arbitrary JSON" escape hatch.
- `completion bash|zsh|fish` renders without contacting the server; `--version`/`--help` work offline.

## Automated tests
- `tests/test_cli_session.py` (new, DB-gated, 20 tests): device start/pending, slow_down interval
  enforcement, expiry, one-time consumption/replay, ID-token validation, unknown-identity/JIT,
  raw code not persisted, CLI session raw token not persisted, expiry/revocation, role-revocation and
  principal-deactivation reflected next request, CLI session rejected on inference, service-credential
  unchanged, and HTTP DTO safety.
- `tests/test_cli.py` (new, offline, 24 tests): profiles CRUD + no-token-in-config + https/localhost/CA
  behavior; token store (memory, keyring round trip, keyring-unavailable fail-safe); client error→exit
  mapping (401/403/404/409/400/500 + device-terminal→3 + network→7); GET-only retry; Authorization
  injection; JSON stdout purity; `--version`/`--help`/`completion` offline; `auth whoami`/`set-token
  --stdin`; and `main()` mapping `CliError`→exit code.
- `tests/test_migrations.py`: +1 `test_migration_0015_to_0016` (also covers empty→latest).
- Full suite: **470 passed** host and containerized; `ruff check src tests` clean.

## Live verification status
**Complete.** All required scenarios verified against the real stack (device-capable local IdP, real
Ollama backend, `AETHERGATE_ALLOW_INFERENCE_AUTH_BYPASS=false`, dynamic loopback API port). Driver:
`/tmp/opencode/agv2019_driver.py` (75 assertions, all green). Two real bugs found and fixed during the
pass (see below).

- **A WIP marker**: `.aethergate-wip` exists, `git check-ignore` succeeds, absent from tracked/staged.
- **B package/profile**: `aethergate --version` (0.1.0) and `--help` offline; profile created against
  live API; profile TOML contains no token; `completion bash` renders.
- **C human device login**: linked `dev-admin` subject → `system_admin`; `authorization_pending` before
  approval; approval → success; `whoami --json` reports `authentication_kind=cli_session` +
  `system_admin`; the real `aethergate auth login` persisted the token to the keyring and a **new**
  process read it back.
- **D device negatives**: `expired_token` → `device_expired`; unknown identity (JIT off) →
  `oidc_authentication_failed`; invalid/replayed code → `device_code_invalid`.
- **E RBAC continuity**: `project_admin(A)` reads/writes A, cannot see B (`404`); `project_viewer(A)`
  reads A but cannot mutate (`403`); revoke viewer role → next command denied (non-enumerating `404`);
  deactivate project_admin principal → next command denied.
- **F operator workflow**: `system_admin` CLI session ran `queue summary`/`list`, endpoint
  `pause`→`resume`, `outcome-unknown`; JSON outputs parsed cleanly.
- **G service credential**: `set-token --stdin` imported the admin credential (whoami
  `service_credential`); an inference-audience credential was rejected for admin CLI (exit 3).
- **H token persistence/logout**: stored CLI token survived a new process; logout revoked server-side
  and the replayed token returned `401`.
- **I error exits**: missing profile → `5`; invalid token → `3`; not-found → `5`; unreachable server →
  `7`.
- **J regression**: full containerized suite green; OpenAI Python SDK `models.list()` → `gpt-4` plus
  non-stream and stream completions green (bypass false).

### Fixed during this pass
1. **CLI entry point did not trap `CliError`.** `pyproject.toml` pointed the console script at the click
   `cli` group, so an expected error (e.g. rejected inference credential) surfaced as a raw traceback and
   exit 1 instead of the documented exit code. Fixed by pointing it at `main` (which maps `CliError` →
   exit code) and adding `test_main_maps_cli_error_to_exit_code`.
2. **Deterministic IdP could not select a device subject.** The device flow minted a fixed subject,
   which would make multi-identity RBAC device logins impossible. Added an optional `subject` override
   to the `/device/approve` control endpoint (backward compatible).

## Key files
- `src/aethergate/identity/cli_session.py` — device-flow + CLI-session service (start/poll/create/
  resolve/revoke, `DevicePollResult`, `generate_cli_session_token`).
- `src/aethergate/identity/oidc.py` — `device_authorization_endpoint`, `start_device_authorization`,
  `poll_device_token`, `validate_device_id_token`, shared `_decode_id_token`.
- `src/aethergate/identity/admin.py` — `authenticate_cli_session`.
- `src/aethergate/api/cli_auth.py` — `/admin/v1/auth/device/start|poll` + `/auth/cli/logout`.
- `src/aethergate/api/admin.py` — `_admin_context` `ags_`/`agk_` bearer routing + `whoami` CLI session id.
- `src/aethergate/api/admin_errors.py`, `errors.py`, `main.py` — new errors/handlers/registration.
- `src/aethergate/domain/{ids,enums,entities}.py` — `CliSessionId`/`DeviceAuthorizationId`,
  `AdminAuthenticationKind.CLI_SESSION`, entities + `AdminRequestContext.cli_session_id`.
- `src/aethergate/persistence/{models,repository}.py`; `migrations/versions/0016_device_flow_cli_sessions.py`.
- `src/aethergate/contracts/admin_v1.py` — `DeviceAuthorizationRead`, `DevicePollRequest/Read`,
  `CliSessionRead`, `WhoamiRead.cli_session_id`.
- `src/aethergate/config.py` — device client/scopes + CLI-session TTL config.
- `src/aethergate/dev_oidc_idp.py` — deterministic device-flow IdP with per-approval subject.
- `src/aethergate/cli/{main,client,profiles,tokenstore,output,errors}.py` — the CLI.
- `pyproject.toml` — CLI deps + console entry point.
- `tests/test_cli_session.py`, `tests/test_cli.py`, `tests/test_migrations.py`.
- Docs: `docs/cli.md` (new), `docs/architecture/security.md`, `docs/architecture/admin-api.md`,
  `docs/contracts/domain-model.md`, `docs/contracts/admin-v1-foundation.md`, `docs/development/README.md`.

## Decisions
- The CLI is a thin HTTP client over `/admin/v1` and never imports persistence/repository modules.
- Human CLI sessions and service-account admin credentials are distinct: `ags_` (CLI session, RBAC via
  Principal/RoleAssignment) vs `agk_` (service credential, RBAC via role assignment + scopes). A human
  login never fakes an `ApiCredential`.
- Only one-way SHA-256 verifiers are persisted for `device_code` and the CLI session token; both raw
  values are one-time reveals.
- Device-flow terminal error codes surface as CLI auth failures (exit 3), not usage errors.
- Profiles and tokens are strictly separated (no secret in TOML); keyring writes are round-trip verified.
- CLI write expansion for endpoints/model-aliases/routes is deferred (no unsafe passthrough); `providers`
  has `create-provider` only.

## Issues / Risks
- `ruff format` is **not** part of this repo's enforced workflow (`ruff check` is); 73 pre-existing files
  would be reformatted, so formatting was intentionally left alone to avoid unrelated churn.
- Token-quota admission fails closed for the Ollama `qwen3.8-2b-distill` model (`estimate_input_tokens`
  returns `None` because no model-specific tokenizer exists). This is pre-existing and correct
  (fail-closed); the live SDK regression was seeded request-quota-only. Token quotas for such models
  remain a documented limitation.
- The live device-flow IdP ran on the host (not a committed compose service); the OIDC/device env was
  injected via an **uncommitted** compose override (`/tmp/opencode/device_override.yml`). The underlying
  `deploy/v2/compose.yaml` still lacks first-class OIDC/device env blocks (same gap noted in AGV2-017/
  018 handoffs, for a later deployment/ops task).
- The device-flow IdP (host) and browser IdP (`dev-oidc-idp` container) were both present during some
  runs; the browser container is orphaned by the current compose file and should be removed in a later
  cleanup.

## Recommended Next Step
Commit and push the AGV2-019 handoff and all work to `origin/v2` (implementation, migration `0016`,
  tests, docs, and all A–J live evidence are green), then remove `.aethergate-wip` and pick up the next
  scheduled v2 workstream.
