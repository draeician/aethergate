# AetherGate Agent Handoff

## Current State
- Branch: v2
- AGV2-015 implementation commit: `805c403` (`feat(identity): add OIDC human sessions and CSRF
  protection`), pushed to `origin/v2`.
- AGV2-015 hardening follow-up commit: `d8f9298` (login transaction cookie binding, audit
  state-redaction fix, login-state cleanup), pushed to `origin/v2` (this session).
- Prior commits remain: `d7842c9` (AGV2-014V), `d283fd2` (AGV2-014), `5a81425` (AGV2-013). This
  handoff supersedes the AGV2-014V handoff.

## Hardening follow-up (this session)
Three scoped fixes on top of AGV2-015, all around the OIDC login transaction:

1. **Login transaction cookie binding (login-CSRF / state-injection defense).** `/auth/oidc/login`
   now sets a dedicated short-lived `ag_oidc_txn` cookie (`HttpOnly`, `SameSite=Lax`, `Secure` in
   prod, `Path=/admin/v1/auth/oidc`, `Max-Age` = login TTL). `OidcLoginState` persists only the
   one-way SHA-256 `txn_cookie_hash` of the raw cookie (new `txn_cookie_hash` column, migration
   `0013`). `/auth/oidc/callback` requires the cookie to match in constant time; a missing, wrong,
   or cross-browser cookie fails with `400 invalid_login_state`. The raw transaction cookie value is
   never persisted or logged.
2. **Raw OIDC state removed from audit.** `_record_login_failed` previously wrote the raw `state` as
   `resource_id` under `resource_type=session`; it now records an opaque transaction id under
   `resource_type=oidc_login` (metadata carries only the failure `reason`). Raw `state`/`nonce`/PKCE
   verifier never appear in audit.
3. **Login-state cleanup.** The login transaction is now **deleted on consumption** (instead of
   marking `consumed_at`), and expired transactions are **deleted when a new transaction is
   created**. `consumed_at` is retained on the entity/table but is no longer used for consumption
   semantics; rows do not accumulate.

Live nomnom verification (host-network IdP `http://192.168.22.50:8490`, pinned API port `44777`,
`AETHERGATE_ALLOW_INFERENCE_AUTH_BYPASS=false`): happy path still `200` + `system_admin` + `whoami`
via cookie; cross-browser callback with no/forged transaction cookie → `400`; genuine browser still
completes; replay → `400`; forged code → `401` and the resulting `human.login_failed` audit row used
`resource_type=oidc_login` with an opaque id (no raw state); `oidc_login_states` empty after logins.


## Task Completed
AGV2-015 — Human identity phase: OIDC authorization-code flow and secure admin browser sessions.

### OIDC library / validation approach
- Added `PyJWT>=2.8,<3` and `httpx>=0.27,<1` to runtime dependencies. ID-token signature verification
  is delegated to PyJWT against the provider JWKS; only asymmetric algorithms (`RS256/384/512`,
  `ES256/384/512`, `PS256/384/512`) are accepted — `none` and symmetric `HS*` are rejected regardless
  of provider metadata. `iss`, `aud` (client ID), `exp`/`nbf`, and `nonce` are validated (nonce in
  constant time); a missing/empty `sub` is rejected. Discovery/JWKS are derived only from the
  configured issuer, fetched over `httpx`, and cached in memory with a bounded TTL; signing keys are
  selected by `kid` (single-key fallback).

### External identity model
- `ExternalIdentity`: stable ID, `principal_id`, `issuer`, opaque case-sensitive `subject`, optional
  safe display claims (`email`, `display_name`), `created_at`, `last_login_at`, `is_active`.
  `(issuer, subject)` is unique and is the identity key; **email is never the identity key**; one
  external identity maps to exactly one principal; no ID/access/refresh token is persisted.

### Session model and cookie properties
- `BrowserSession` persists the authoritative session in PostgreSQL: stable ID, principal ID, one-way
  SHA-256 `session_hash` (raw cookie never persisted), `created_at`, `last_seen_at` (throttled idle
  slide), idle/absolute expiry, `revoked_at`, and a one-way `csrf_token_hash`.
- Cookie: cryptographically random (>=256 bits), `HttpOnly`, `SameSite=Lax`, `Secure` in `prod`,
  `Path=/admin`, `Max-Age` = absolute lifetime (43200s default; idle 3600s). No raw token in JSON or
  URL.

### CSRF model
- `X-CSRF-Token` header required for `POST/PUT/PATCH/DELETE` when authenticated by browser session;
  compared in constant time against the one-way verifier. `GET`/`HEAD` and Bearer service-account
  requests are exempt. Missing/invalid => fixed `403 invalid_csrf_token`. The raw token is delivered
  exactly once in the callback response.

### Authentication precedence
- A supplied `Authorization` header is authoritative and must authenticate successfully; an invalid
  header never falls through to a cookie. CSRF applies only when the selected mechanism is browser
  session. `AdminRequestContext` carries `authentication_kind`
  (`service_credential`|`browser_session`) and `browser_session_id` vs `api_credential_id`.

### Human provisioning / linking policy
- A known `(issuer, subject)` resolves to its linked, active principal. A `system_admin` links an
  identity via `POST /admin/v1/oidc/identities`. Unknown identities are denied unless
  `AETHERGATE_OIDC_JIT_PROVISIONING=true`, which creates an unprivileged `user` principal with zero
  roles. No role is derived from email domain, group, username, or claims.

### Local test IdP strategy
- `aethergate.dev_oidc_idp.DevOidcIdp` implements discovery, JWKS (RS256), an auto-approving
  authorize endpoint, and a PKCE S256 token endpoint that returns a signed ID token. It is
  deterministic and offline; not production identity infrastructure. It generates its RSA key in
  memory, so restarting it changes the signing key (restart the API to clear its in-memory JWKS cache).

### Real nomnom verification (dynamic host port 44777)
Live via a one-off host-network IdP container (`http://192.168.22.50:8490`) and a pinned API host
port (uncommitted compose override) so the redirect URI was stable; backend Ollama
`http://192.168.22.50:11434`, upstream `qwen3.8-2b-distill:Q6_K`, alias `gpt-4`.

- **A happy path.** Linked USER principal + `system_admin` role; `/auth/oidc/login` -> IdP authorize
  (S256 PKCE) -> callback `200`, `SessionEstablished` with `roles:["system_admin"]` + `csrf_token`,
  session cookie set; `GET /admin/v1/whoami` `200` via cookie only (`authentication_kind:
  browser_session`, `api_credential_id: null`).
- **B cookie properties.** `Set-Cookie: ag_session=...; HttpOnly; Max-Age=43200; Path=/admin;
  SameSite=lax` (no `Secure` in dev). No raw token in response JSON or URL.
- **C state/nonce/PKCE failures.** Wrong state -> `400 invalid_login_state`; replayed callback ->
  `400 invalid_login_state` (no session); forged code (real state) -> `401 oidc_authentication_failed`
  (no session, exchange failed). Nonce/PKCE-verifier mismatches are covered by offline unit tests.
- **D unknown identity.** JIT off: valid but unlinked IdP identity -> `401`, no session. JIT on: same
  identity provisions a `user` principal in the JIT project with zero roles; `whoami` `roles:[]` and a
  protected list returns an empty page.
- **E RBAC continuity.** `system_admin` human listed projects; revoking the role via a Bearer admin
  made the next `whoami` report `roles:[]` and the next protected list return an empty page — no
  re-login, immediate effect.
- **F principal/project deactivation.** Deactivating the human principal (or its project) made the
  next session-authenticated `whoami` return `401`; reactivation restored access — revalidated on
  every request.
- **G CSRF.** Cookie POST without token -> `403`; wrong token -> `403`; correct token -> `201`;
  Bearer admin POST without token -> `201`.
- **H logout.** Correct CSRF -> `revoked:true`; next session read -> `401`; repeated logout ->
  `revoked:false` (idempotent).
- **I audience regression.** Inference key on `/admin/v1/whoami` -> `401`; admin key on `/v1/models` ->
  `401`; OIDC session cookie on `/v1/models` -> `401`; inference key on `/v1/models` -> `200`.
- **J real SDK inference** (`AETHERGATE_ALLOW_INFERENCE_AUTH_BYPASS=false`): official OpenAI Python
  SDK non-stream and stream chat completions succeeded against `gpt-4`; both requests recorded
  `succeeded` with `project_id`/`principal_id`/`api_credential_id` matching the inference credential.

## Migration
- New migration `0012` adds `external_identities` (unique `(issuer, subject)`), `browser_sessions`
  (one-way session/CSRF verifiers, expiry), and `oidc_login_states` (one-time PKCE transactions).
  `0001`–`0011` untouched; `0011 -> 0012` and empty-DB -> latest both succeeded live.
- New migration `0013` adds the one-way `txn_cookie_hash` column to `oidc_login_states`
  (`NOT NULL`, server default dropped after backfill). Live `0012 -> 0013` succeeded.

## Automated tests
- `scripts/dev/v2 test` -> **353 passed** (was 348; +5 for the hardening follow-up). New coverage
  includes OIDC config validation, HTTPS-in-prod enforcement, discovery issuer mismatch, login-state
  entropy/expiry, nonce/PKCE/state one-time consumption, callback replay, ID-token
  issuer/audience/expiry/subject validation, unsafe algorithm rejection, unique `(issuer,subject)`,
  email-not-key, JIT-off denial, JIT unprivileged, inactive principal denial, service-credential vs
  browser-session `AdminRequestContext`, raw cookie never persisted, idle/absolute expiry, revoked
  session, cookie attributes, CSRF missing/wrong/correct, Bearer-no-CSRF,
  invalid-header-no-cookie-fallback, role revocation and project/principal deactivation on next
  request, logout idempotency, audit secret redaction, login transaction cookie binding
  (missing/forged/cross-browser), login-state delete-on-consume, login-state expiry cleanup, failed
  login audit raw-state redaction, and migration `0012 -> 0013`.
- `ruff check src tests` clean; `git diff --check` clean; staged secret scan clean.

## Key files
- `src/aethergate/config.py` — OIDC/session settings, `_validate_oidc`, empty-env handling, scope parsing.
- `src/aethergate/identity/oidc.py` — `OidcProvider` (discovery/JWKS/PKCE/token exchange/ID-token validation).
- `src/aethergate/identity/session.py` — session service (login transactions, sessions, CSRF, linking, JIT).
- `src/aethergate/api/session_auth.py` — login/callback/session/logout endpoints.
- `src/aethergate/api/admin.py` / `identity/admin.py` / `identity/authorization.py` — kind-aware admin auth.
- `src/aethergate/dev_oidc_idp.py` — local deterministic OIDC provider.
- `src/aethergate/migrations/versions/0012_human_oidc_sessions.py` — migration 0012.
- `src/aethergate/migrations/versions/0013_login_transaction_cookie_binding.py` — migration 0013
  (`txn_cookie_hash`).
- `tests/test_oidc_session.py` — new test suite (login/session/CSRF/binding/cleanup/audit).
- `tests/test_migrations.py` — migration round-trip tests incl. `0012 -> 0013`.

## Decisions
- External identity key is `(issuer, subject)`; email is display-only. JIT provisioning is off by
  default and creates zero-role users; roles are never derived from claims.
- `AdminAuthenticationKind` distinguishes `service_credential` from `browser_session` so a browser
  session never fabricates an `ApiCredential`; scope checks apply only to service credentials.
- The session cookie and CSRF token are stored only as one-way SHA-256 verifiers (high-entropy
  random tokens, so SHA-256 — not a password hash — is appropriate).
- The login transaction is bound to the initiating browser via a dedicated short-lived `ag_oidc_txn`
  cookie (persisted only as `txn_cookie_hash`), and consumed transactions are deleted rather than
  marked, so replay and cross-browser state theft fail closed.
- `last_seen_at` is slid on a throttled basis (not per request) to avoid a hot-row write.

## Issues / Risks
- `docker` on nomnom is podman; keep compose healthchecks single-token; `docker compose run` needs
  `--no-deps`.
- The local IdP's RSA key is generated in memory, so restarting it changes the key; the API's 300s
  in-memory JWKS cache must be cleared (API restart) when the IdP is restarted.
- The committed `deploy/v2/compose.yaml` does not wire OIDC env passthrough (kept out to avoid leaking
  `.env` OIDC settings into `scripts/dev/v2 test`); live OIDC verification used an uncommitted compose
  override to pin the host port and inject `AETHERGATE_OIDC_*`.
- litellm still has no trusted token estimator, so token-priced quotas/budgets fail closed; the
  inference smoke used a request-only quota and a request-priced policy.

## Recommended Next Step
Build the React web console against the new OIDC login/session/CSRF contracts, or extend the remaining
catalog/accounting/queue admin CRUD over `/admin/v1` reusing the same authorization/pagination
foundations.
