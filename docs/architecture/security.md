# AetherGate v2 — Security Model

Living document. Derived from the audit. Distinguish **settled** / **direction** / **deferred**.

## Identity and authentication

- OIDC-backed named identities; role/resource authorization; company MFA policy. (Settled)
- Scoped, expiring service-account authentication for automation. (Settled)
- Human Linux CLI login uses a supported OAuth device flow or equivalent approved flow. (Settled)
- Administrative and inference audiences/permissions are kept separate. (Settled)
- The inference data plane authenticates AetherGate-issued Bearer API keys (`agk_...`) and resolves
  them to a durable, typed request context (project / principal / credential / audience / scopes).
  (Settled — AGV2-012)

## Identity phase 1 — scoped inference API credentials (AGV2-012)

Machine/service authentication for the inference surface. Human OIDC login, browser sessions,
OAuth device flow, and full admin RBAC remain the next identity/admin phase.

- API keys are high-entropy random tokens (`agk_<display-prefix>_<secret>`, at least 256 bits of
  CSPRNG entropy) generated with Python `secrets`; the raw key is returned exactly once at
  create/rotate and is never stored. Only a SHA-256 verifier (`key_hash`) and the non-secret
  display prefix (`key_prefix`) are persisted. (Settled)
- SHA-256 verification is acceptable **only** because these are generated high-entropy tokens, not
  human-chosen passwords; this is not a password-hashing scheme and must not be reused for
  passwords. Exact lookup is by full-key hash (an indexed fixed-cost verifier); the display prefix
  is never the authentication selector. (Settled)
- Credentials carry an `audience` (`inference` | `admin`) and `scopes` (`inference:invoke` for
  phase 1). The inference endpoint rejects a wrong audience and any credential missing
  `inference:invoke`; a future admin credential is never valid for inference merely because it
  exists, and there is no shared master key. (Settled)
- A credential resolves only to an active project and active principal (principal belongs to the
  credential's project); inactive/expired/revoked/missing credential is denied with an
  indistinguishable authentication error. (Settled)
- Credential authorization is **revalidated immediately before scheduler dispatch**, not only at
  enqueue time, so revocation/expiry applied while a request is queued blocks upstream contact.
  (Settled)
- The development auth bypass (`AETHERGATE_ALLOW_INFERENCE_AUTH_BYPASS`) is a deliberate dev/test
  escape hatch only: rejected in `prod` mode, consulted only when no Authorization header is
  present, and never a fallback for an invalid supplied key. (Settled)

### Credential lifecycle invariants (AGV2-012V)

- Credential creation validates the target identity first: the project and principal must exist and
  be active, and the principal must belong to the requested project. A failing creation is rejected
  with an identity/domain error before any raw key is generated, so no credential row (and no secret)
  is ever produced for an invalid transition. (Settled)
- Rotation is an active-credential operation: the old credential must exist, be active, not revoked,
  and not already expired, and its project/principal must still be valid/active/matching. A failing
  rotation creates no replacement; the replacement raw key is revealed only after the transition can
  succeed. (Settled)
- Revocation is idempotent: the first revoke stamps `revoked_at` and disables the credential;
  repeated revokes preserve the original timestamp and never move it forward or reactivate the
  credential. (Settled)
- Audience/scope coherence: inference credentials default to `inference:invoke`; admin credentials
  have no default scopes, and an admin credential carrying `inference:invoke` is rejected, so a
  future admin credential can never be valid for inference merely because it exists. (Settled)
- `last_used_at` remains intentionally unset: no unconditional credential-row write is performed per
  inference request, avoiding a hot-row bottleneck. (Deferred)

## Identity phase 2 — admin RBAC and one-use bootstrap (AGV2-013)

The control plane has its own identity/authorization boundary, separate from the inference data
plane. Human OIDC/browser/device flow remains the next identity phase and must plug into the same
principal/RBAC model.

- **Audience separation is enforced both ways.** Admin-audience credentials may carry only
  `admin:*` permissions; inference-audience credentials may carry only `inference:invoke`. A
  cross-audience scope is rejected at create/rotate and at authentication, an inference credential
  never authorizes an admin endpoint, and an admin credential never authorizes inference merely
  because it exists. (Settled)
- **Typed admin permissions** (`CredentialScope`): `admin:credentials:read|write`,
  `admin:projects:read|write`, `admin:principals:read|write`, `admin:catalog:read|write`,
  `admin:accounting:read|write`, `admin:queue:read|write`, `admin:audit:read`. (Settled)
- **Centralized RBAC.** `authorize_admin(context, permission, resource_type, resource_id)` is the
  single authorization policy (not scattered router role checks). Roles: `system_admin`
  (deployment-wide), `project_admin` (one project, read+write), `project_viewer` (one project,
  read-only). Assignments are durable (`role_assignments`) with stable ID, principal, role, resource
  scope type, optional resource ID, created-by, revocation, and active state; duplicate active
  equivalent assignments are prevented by a partial unique index. (Settled)
- **Authentication and authorization are separate steps.** Credential authentication (active/not
  revoked/not expired/audience) produces a typed `AdminRequestContext`; role assignments are loaded
  once per request and re-checked on every protected action. Revoking a role assignment blocks the
  next protected request while the credential remains active. (Settled)
- **One-use bootstrap.** `POST /admin/v1/bootstrap` consumes a configured
  `AETHERGATE_BOOTSTRAP_TOKEN` (a `SecretStr`, no default, placeholders rejected, never logged)
  exactly once: it establishes the initial project/principal, grants `system_admin`, creates the
  first admin credential, marks durable `bootstrap_state` complete, and returns the raw admin key
  exactly once. Concurrent calls cannot both succeed (`SELECT ... FOR UPDATE` + partial-unique
  singleton); restart does not reopen bootstrap; the token never acts as an ongoing master key and
  should be removed after bootstrap. The raw bootstrap/admin secrets are never stored in PostgreSQL.
  (Settled)
- **Administrative audit.** Immutable `audit_events` record bootstrap completion, role-assignment
  create/revoke, and admin-credential create/rotate/revoke, with actor principal ID where available,
  action, resource type/ID, timestamp, and safe metadata only. Bootstrap has no authenticated actor
  and records `NULL` explicitly. No raw key, hash, Authorization header, bootstrap token, prompt, or
  provider secret is ever recorded. (Settled)
- **Authorization failures are indistinguishable.** Admin auth/authorization/bootstrap failures are
  fixed messages that do not leak bootstrap state or whether a target resource exists. (Settled)

## Identity phase 2 hardening — admin CRUD and cross-project non-enumeration (AGV2-014)

- **Cross-project resource non-enumeration.** Opaque-ID read/mutate operations (project/principal/
  credential/role-assignment) resolve through a centralized resource-scope resolver
  (`resolve_admin_resource`). A nonexistent ID and a cross-project ID are indistinguishable (`404
  not_found` via `AdminResourceNotFound`); a resource inside the caller's scope but denied by the
  specific read/write permission returns `403`. `system_admin` resolves any target; project-scoped
  callers resolve only their authorized project. (Settled)
- **Centralized privilege-escalation defense.** `authorize_role_grant` rejects incoherent role/scope
  shapes (`system_admin` must be deployment-scoped; project roles must be project-scoped) as `400`,
  and rejects any grant broader than the caller's own authority as `403`. `project_viewer` can never
  mutate roles. (Settled)
- **Service-layer authorization boundaries.** Administrative mutation services take a typed
  `AdminRequestContext` and authorize internally; an internal caller cannot bypass RBAC by passing a
  forged actor ID. Routers remain thin. (Settled)
- **Generic credential administration.** `/admin/v1/credentials` manages client credentials of either
  audience; service/audit actions are neutral (`credential.*`), audience is recorded in safe metadata,
  and audience/scope coherence is enforced by the shared identity service. (Settled)
- **Audit idempotency.** Repeated credential/role-assignment revoke emits no second `revoked` event;
  duplicate active role-assignment create emits no second `created` event. Audit metadata never
  contains raw keys, hashes, tokens, or Authorization headers. (Settled)
- **Database role/scope invariants (migration 0011).** `system_admin` must be deployment-scoped with
  no project resource ID; `project_admin`/`project_viewer` must be project-scoped — enforced by CHECK
  constraints so direct writes cannot bypass application invariants. (Settled)

## Identity verification hardening — bootstrap strength and RBAC race (AGV2-014V)

- **Bootstrap token strength floor.** `AETHERGATE_BOOTSTRAP_TOKEN` is a `SecretStr` with no default;
  empty/unset disables bootstrap and placeholder values are rejected. In addition, a configured token
  shorter than 32 characters is rejected at startup. This is a **floor against trivially short/guessable
  secrets, not a proof of entropy**; operators must still supply randomly generated high-entropy
  material. The dev helper (`scripts/dev/v2`) generates `agb_ + secrets.token_urlsafe(32)` and remains
  valid. The token value never appears in `repr`/validation/log output. (Settled)
- **Concurrency-safe, idempotent role grant.** A duplicate active-equivalent role assignment is
  idempotent both sequentially and under concurrency: a concurrent duplicate that races past the
  application pre-check is caught by the partial unique index and translated back to the canonical
  winner (savepoint + `IntegrityError` recovery narrowly around the active-equivalent uniqueness
  constraint). Exactly one active row and one `role_assignment.created` audit event result; unrelated
  `IntegrityError`s are re-raised, not swallowed. (Settled)

## Identity phase 3 — human OIDC sessions and CSRF (AGV2-015)

Human administrators authenticate through an external OpenID Connect provider using the
Authorization Code flow with PKCE and receive a server-managed browser session. The session
authenticates a durable `Principal` and authorizes through the **same** `RoleAssignment`/RBAC
service as service accounts. No provider token or session secret ever reaches browser
JavaScript storage.

- **OIDC provider configuration** is typed runtime config (`AETHERGATE_OIDC_*`): issuer, client ID,
  optional client secret (`SecretStr`, never logged, no default), fixed redirect URI, requested
  scopes (`openid` always enforced and de-duplicated), optional provider name, and an enable flag.
  Discovery/JWKS URLs are derived **only** from the configured issuer; no user-controlled
  discovery/JWKS/token URL is honored. The issuer must be `https://` in `prod`; HTTP is permitted
  only in `dev`/`test` for the local verification IdP. Discovery and JWKS are cached in memory with
  a bounded TTL and fetched over `httpx` with a short timeout. (Settled)
- **Authorization Code + PKCE.** Login generates a cryptographically random `state`, `nonce`, and
  PKCE `code_verifier`/S256 `code_challenge`; they are bound to the initiating browser via a
  one-time, short-lived `oidc_login_states` transaction **and** a dedicated short-lived `ag_oidc_txn`
  cookie (only its one-way SHA-256 `txn_cookie_hash` is persisted; the raw cookie value is never
  stored). The callback requires both an exact `state` match and the matching transaction cookie, so
  a login state stolen from or injected across another browser cannot be redeemed (login-CSRF /
  login-state-injection defense). The callback exchanges the code with a fixed `redirect_uri`, and
  validates the ID token's `iss`, `aud` (client ID), `exp`/`nbf`, and `nonce`; a missing/empty `sub`
  is rejected. Only asymmetric algorithms (`RS*/ES*/PS*`) are accepted — `none` and symmetric `HS*`
  are rejected regardless of provider metadata — and signature verification is delegated to PyJWT
  against the provider JWKS (keyed by `kid`). The authorization code is consumed once; the login
  transaction is deleted on consumption and expired transactions are deleted on creation, so a
  replayed callback fails. (Settled)
- **Durable external identity link.** `ExternalIdentity` stores a stable ID, `principal_id`,
  `issuer`, opaque case-sensitive `subject`, optional safe display claims (`email`, `display_name`),
  `created_at`, `last_login_at`, and `is_active`. `(issuer, subject)` is unique and is the identity
  key; **email is never the identity key**. No ID/access/refresh token is ever persisted. (Settled)
- **No automatic privilege from IdP claims.** A known identity resolves to its linked, active
  principal. An unknown identity is denied unless an explicit `AETHERGATE_OIDC_JIT_PROVISIONING`
  flag enables just-in-time provisioning, which creates an **unprivileged** `user` principal with
  zero roles (a `system_admin` must still grant roles). No role is ever derived from email domain,
  group, username, or arbitrary claims. (Settled)
- **Server-managed browser sessions.** `BrowserSession` persists the authoritative session in
  PostgreSQL: stable ID, principal ID, a **one-way SHA-256 verifier** of the raw cookie (the raw
  value is returned only in `Set-Cookie` and never stored), `created_at`, `last_seen_at`,
  idle/absolute expiry, `revoked_at`, and a one-way CSRF verifier. The cookie is cryptographically
  random (>=256 bits), `HttpOnly`, `SameSite=Lax`, `Secure` in `prod`, `Path=/admin`, and never
  appears in a URL or JSON. Idle/absolute lifetimes are configurable with safe defaults
  (3600s / 43200s). `last_seen_at` is slid on a **throttled** basis (not per request) to avoid a hot
  row. (Settled)
- **Session revalidation on every request.** Resolving a session re-checks revocation, absolute and
  idle expiry, active principal, and active project, so role revocation, principal deactivation,
  and project deactivation all take effect on the **next** request without re-login (and without
  destroying the session). (Settled)
- **CSRF protection.** Cookie-authenticated `POST/PUT/PATCH/DELETE` requests require an
  `X-CSRF-Token` header compared in constant time against a one-way verifier stored with the
  session. `GET`/`HEAD` do not require CSRF, and Bearer-authenticated service-account requests do
  not require CSRF (they carry no ambient cookie authority). A missing/invalid token returns a
  fixed `403`. The CSRF token is delivered exactly once at session establishment. (Settled)
- **Bearer-vs-cookie precedence.** A supplied `Authorization` header is authoritative: it must
  authenticate successfully; an invalid header never falls through to a cookie. CSRF applies only
  when the selected mechanism is the browser session. (Settled)
- **Session fixation prevention.** No authenticated session exists before a successful callback;
  the callback creates a fresh random session and replaces any previous cookie; a failed callback
  creates no session; the login transaction is one-time and expires. (Settled)
- **Audit.** Immutable events record `human.login_success`, `human.login_failed` (fixed failure
  category only), `session.logout`, and `external_identity.linked`. Login success identifies the
  resulting principal; pre-auth failures have no actor. Authorization code, ID/access/refresh
  tokens, state, nonce, PKCE verifier, raw session cookie, and raw CSRF token are never logged or
  persisted. (Settled)

## Bootstrap and fail-closed startup

- A bootstrap credential is one-use, explicitly configured, and disabled after setup. (Settled — AGV2-013)
- Management startup refuses absent, empty, or placeholder credentials; no shared default grants
  access. (Settled — closes AG-001.)

## Sessions and secrets

- Browser sessions are server-managed with Secure, HttpOnly cookies and CSRF protection. No
  long-lived master secret in JavaScript storage. (Settled)
- Upstream credentials are stored through a secrets service or envelope encryption with the
  decrypting key outside the database. (Settled)
- Secrets are returned only at explicit create/rotation boundaries, never in normal reads or exports. (Settled)
- Existing random client API keys may remain hash-verified; password hashing is not automatically
  required for high-entropy random tokens. (Direction)

## Authorization strictness

- Strict, standards-aware Bearer parsing; malformed and wrong-scheme headers fail predictably. (Settled — AG-020.)
- Cross-project resource access is denied consistently; revocation applies to queued work;
  administrative actions identify the actor. (Settled)

## Content and logging

- Content logging off by default; logs/traces redacted with defined retention. (Settled — AG-018.)
- SQL parameter logging disabled by default so prompt content and credentials do not leak. (Settled)
- Temporary queue payload storage is separate from permanent prompt logging, still encrypted,
  access-controlled, and expiring. (Settled)

## Egress control

- Enforce approved egress destinations; explicitly support authorized private LAN endpoints. (Settled)
- Block metadata destinations, DNS rebinding, and redirect escapes. (Settled)
- Inference callers never choose arbitrary server-side destinations, credentials, or trusted
  transport settings. (Settled — AG-033.)

## Transport

- TLS on management access, including LAN deployments; support a private management listener or
  ingress. (Settled)
- Honest identification to upstreams (no browser-impersonating User-Agent to bypass blocks). (Settled)

## Deferred

- Choice of secrets backend (KMS/HashiCorp Vault/envelope encryption library) — direction requires
  "secrets service or envelope encryption with keys outside the DB", specific tooling deferred.
- Exact retention/redaction schedule for personal information and prompt content.
- Whether password hashing is applied beyond high-entropy random tokens.
