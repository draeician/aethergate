# AetherGate v2 — Current Task

## Task ID
AGV2-015

## Title
Human identity phase — OIDC authorization code flow and secure admin browser sessions

## Ownership
Primary: identity/auth
Coordinating: admin API, contracts, platform/testing, future web console

## Why This Task Exists

Machine inference authentication, admin service-account authentication, RBAC, bootstrap, and admin
identity CRUD are now complete and live-verified.

The project specification requires human administrators to use OIDC-backed named identities with
server-managed browser sessions, Secure/HttpOnly cookies, CSRF protection, and the same
Principal/RoleAssignment authorization model already used by service accounts.

This task implements that human-admin authentication boundary.

Do not build the React web console yet. Build the backend/session contracts the console will use.

## Compaction Recovery

If context is compacted, summarized, restarted, or uncertain:
1. re-read AGENTS.md;
2. re-read project_spec.md;
3. re-read this file;
4. re-read docs/development/agent-handoff.md;
5. inspect git status and recent commits;
6. continue from repository state.

docs/development/current-task.md is authoritative.

## Before You Start

1. Work on branch v2 and pull latest origin/v2.
2. Read:
   - AGENTS.md
   - project_spec.md
   - docs/development/current-task.md
   - docs/development/agent-handoff.md
   - docs/architecture/security.md
   - docs/architecture/admin-api.md
   - docs/contracts/domain-model.md
   - docs/contracts/admin-v1-foundation.md
   - current Principal / RoleAssignment / AdminRequestContext code
   - current admin auth dependencies and error handlers
3. Preserve all service-account admin and inference authentication behavior.
4. Do not modify/delete legacy v1 app/ or frontend/src/.
5. Do not commit unrelated local/untracked files.
6. Do not implement JWT/OIDC signature verification cryptography by hand when a vetted standards-aware
   library can provide it.

## Goal

A human administrator can authenticate through an external OpenID Connect provider using the
Authorization Code flow with PKCE and receive a server-managed AetherGate session.

That session authenticates the human as a durable AetherGate Principal and authorizes all admin
requests through the existing RoleAssignment/RBAC service.

No OIDC access token, ID token, client secret, session bearer secret, or bootstrap secret is stored in
browser JavaScript storage.

## 1. OIDC provider configuration

Add typed runtime configuration for one OIDC provider in this phase.

At minimum:
- issuer URL;
- client ID;
- client secret when the provider requires one;
- configured redirect/callback URL;
- requested scopes, with openid required;
- optional display/provider name;
- enable/disable flag.

Requirements:
- issuer must be HTTPS in production;
- localhost/internal HTTP may be permitted only in explicit dev/test mode for the local verification IdP;
- client secret is SecretStr and never logged;
- no default client secret;
- openid scope is mandatory;
- redirect URI must be fixed/configured, never accepted from an arbitrary request parameter;
- no user-controlled discovery/JWKS/token endpoint URL.

Use OIDC discovery from the configured issuer. Cache discovery/JWKS safely with bounded refresh.

## 2. Authorization Code + PKCE

Implement standards-aware human login endpoints, for example:

- GET /admin/v1/auth/oidc/login
- GET /admin/v1/auth/oidc/callback
- GET /admin/v1/auth/session
- POST /admin/v1/auth/logout

Exact naming may differ, but keep the surface under the admin authentication boundary and document it.

Login requirements:
- generate cryptographically random state;
- generate nonce;
- generate PKCE verifier/challenge using S256;
- bind state/nonce/PKCE transaction to the initiating browser;
- short expiration;
- one-time consumption;
- fixed callback URI;
- do not place client secret/session secret in query parameters.

Callback requirements:
- exact state match;
- exchange authorization code using configured provider metadata;
- validate ID token using provider keys and OIDC rules;
- validate issuer;
- validate audience/client ID;
- validate expiration/not-before as applicable;
- validate nonce;
- reject unsupported/unsafe algorithms according to the selected standards library/provider metadata;
- reject malformed/missing subject;
- authorization code may be consumed only once;
- no open redirect.

Do not treat email as the identity key.

## 3. Durable external-identity link

Add a durable external identity link instead of storing OIDC identity fields directly on Principal in a
provider-specific way.

Recommended shape:
- stable ExternalIdentityId;
- principal_id;
- provider/issuer;
- subject (sub);
- optional safe display claims such as email/display name;
- created_at;
- last_login_at;
- active flag if useful.

Database invariants:
- unique (issuer, subject);
- one external identity maps to exactly one Principal;
- subject is opaque/case-sensitive data;
- email is not unique identity authority;
- do not store raw ID/access/refresh tokens in this table.

Principal remains the authorization identity. RoleAssignment remains the authorization grant.

Add migration 0012 unless a different next linear revision is required by repository state.

## 4. Human principal provisioning/linking

Do not automatically grant administrative roles based solely on an IdP claim.

Use a safe phase-1 human provisioning policy.

Preferred model:
- an already-authorized system_admin creates a USER Principal in an AetherGate project and explicitly
  links/authorizes an OIDC identity by issuer + subject, OR
- first successful OIDC login may create an unprivileged USER Principal only if an explicit config
  flag permits just-in-time provisioning; it receives no admin role until a system_admin grants one.

No role may be created from email domain, group name, username, or arbitrary claim unless an explicit
mapping feature is designed in a later task.

Requirements:
- unknown identity without enabled JIT policy is denied safely;
- linked inactive principal is denied;
- disabled external link is denied if implemented;
- role revocation immediately affects the next session-authenticated request;
- project/principal deactivation immediately affects the next request;
- existing service-account principals continue unchanged.

## 5. AdminRequestContext must support multiple authentication methods

Do not invent a fake ApiCredential for a browser session.

Refine the admin request context so the actor can be authenticated by:
- admin API credential; or
- human browser session.

Represent authentication method explicitly.

At minimum expose internally:
- principal_id;
- project_id;
- active role assignments;
- authentication kind (service credential vs browser session);
- api_credential_id when applicable;
- browser_session_id when applicable;
- credential scopes when applicable.

Authorization decisions must continue to use the same centralized RBAC engine.

Service-account admin endpoints must remain backward compatible.

## 6. Server-managed browser sessions

Persist authoritative browser sessions in PostgreSQL.

At minimum:
- stable session ID;
- principal ID;
- one-way verifier/hash for the raw session cookie value;
- created_at;
- last_seen_at or equivalent;
- idle_expires_at;
- absolute_expires_at;
- revoked_at;
- CSRF verifier/state;
- safe OIDC login metadata if necessary, but no provider token secrets.

Session cookie:
- cryptographically random >=256-bit value;
- raw value returned only in Set-Cookie;
- PostgreSQL stores verifier/hash, not raw cookie;
- Secure in production;
- HttpOnly;
- SameSite=Lax unless documented provider flow requires a stricter/different safe setting;
- Path restricted appropriately;
- no session token in URL;
- no LocalStorage/sessionStorage auth token.

Define configurable idle and absolute lifetimes with safe defaults.

Do not update last_seen_at on every request if doing so would create an avoidable hot row; use a
throttled update or intentionally defer it while enforcing expiration correctly.

## 7. Session revocation and logout

Logout must durably revoke the server session and clear the browser cookie.

Requirements:
- repeated logout is safe/idempotent;
- revoked session fails next request;
- expired session fails;
- inactive project/principal fails;
- role revocation does not require session destruction; next request re-resolves active assignments
  and loses authority;
- do not depend only on cookie deletion for revocation.

Provide service/repository support for listing/revoking a user's sessions later, but full session-admin
CRUD is not required in this task.

## 8. CSRF protection

Cookie-authenticated state-changing admin requests require CSRF protection.

Use a server-backed synchronizer-token design or another well-established design compatible with the
future React console.

Recommended:
- create a random CSRF token when session is established;
- store only a verifier/hash with the session;
- expose the raw CSRF token through a safe session/bootstrap response for the in-memory web client;
- require a header such as X-CSRF-Token for POST/PATCH/PUT/DELETE requests authenticated by browser
  session;
- compare in constant time;
- rotate token when session rotates if session rotation is implemented.

Requirements:
- GET/HEAD safe methods do not require CSRF;
- Bearer-authenticated service-account requests do not require CSRF because they do not use ambient
  cookie authority;
- missing/invalid CSRF => fixed 403;
- Origin/Referer validation may be added as defense-in-depth but is not a substitute for the CSRF token.

Never put the authentication session secret in a readable CSRF cookie.

## 9. Session fixation / login transaction safety

Prevent session fixation.

Requirements:
- no authenticated session exists before successful callback;
- successful OIDC callback creates a fresh random session;
- any previous session cookie is replaced;
- failed callback creates no authenticated session;
- state/nonce/PKCE transaction is one-time and expires;
- replayed callback fails.

## 10. Admin authentication dependency unification

Protected /admin/v1 endpoints must accept either:
- valid admin Bearer credential; or
- valid browser session cookie.

The resulting AdminRequestContext must flow into the same service-layer authorization code.

Precedence/ambiguity:
- define behavior if both cookie and Authorization header are present;
- preferred: a supplied Authorization header is authoritative and must authenticate successfully;
  do not silently fall back to cookie when an invalid header is supplied;
- CSRF applies only when the selected auth mechanism is browser session.

Keep bootstrap separate and one-use as today.

## 11. Session/API contracts

Add transport-independent DTOs for at least:
- OIDC login/start result if the endpoint does not redirect directly;
- current human session / whoami metadata;
- CSRF token delivery;
- logout result;
- safe external identity metadata for admin read operations if exposed.

Do not expose:
- ID token;
- access token;
- refresh token;
- session hash/verifier;
- raw session cookie value in JSON;
- client secret.

whoami/session responses may expose safe issuer/provider name and subject only if the product actually
needs it; prefer minimal identity metadata.

## 12. Audit

Create immutable safe audit events for at least:
- human login success;
- human login failure category if useful and safe;
- session logout/revoke;
- external identity link/create if this task exposes such an admin action.

Audit actor:
- login success may identify the resulting principal;
- pre-auth failures have no actor;
- never put authorization code, ID token, access token, refresh token, state, nonce, PKCE verifier,
  cookie/session secret, or CSRF raw token in audit metadata.

## 13. Development/live verification provider

The task must be testable without depending on an external Internet IdP.

Use a deterministic local OIDC test provider or standards-aware mock service inside the test/dev
environment.

Requirements:
- no real company IdP credentials committed;
- provider supports discovery, authorization code, PKCE S256, token exchange, and signed ID tokens;
- deterministic automated tests remain offline;
- live nomnom verification uses this local/internal test IdP or an equivalent controlled fixture.

Do not add a heavyweight production dependency solely for tests if a small standards-compliant fixture
is sufficient.

## 14. Real nomnom verification — required

Use dynamic host ports.

### A. OIDC happy path
- configure local test IdP;
- create/link a USER Principal with an admin role;
- initiate login;
- complete authorization code + PKCE flow;
- callback creates session;
- browser/session whoami succeeds;
- no Bearer admin key is required for the browser request.

### B. Cookie properties
Prove Set-Cookie has expected properties for the environment:
- HttpOnly;
- SameSite;
- Secure in production-mode configuration;
- appropriate Path;
- no raw token in response JSON or URL.

### C. State / nonce / PKCE failures
Prove:
- wrong state fails;
- replayed state/callback fails;
- wrong nonce fails;
- bad PKCE verifier/code exchange fails;
- no session is created on any failure.

### D. Unknown identity
With JIT disabled:
- valid IdP identity not linked to AetherGate => denied;
- no privileged Principal/RoleAssignment is created.

If JIT unprivileged provisioning is implemented:
- principal is created with zero roles;
- protected admin operation remains denied.

### E. RBAC continuity
- human system_admin can perform deployment action;
- human project_admin(A) can administer A;
- human project_admin(A) cannot enumerate B;
- human project_viewer(A) can read but not write;
- revoke role during active session and prove next protected request loses access without relogin.

### F. Principal/project deactivation
During an active session:
- deactivate principal => next request rejected;
- reactivate if needed;
- deactivate project => next request rejected.

### G. CSRF
Using browser-session auth:
- GET works without CSRF;
- state-changing request without token => 403;
- wrong token => 403;
- correct token => succeeds.

Using Bearer admin service credential:
- same state-changing endpoint remains usable without browser CSRF token.

### H. Logout
- logout with correct CSRF succeeds;
- cookie cleared;
- DB session revoked;
- replaying old cookie fails;
- repeated logout is safe.

### I. Audience / existing-auth regression
- inference credential still cannot call admin;
- admin service credential still cannot call inference;
- admin service credential still works on admin endpoints;
- OIDC browser session is admin-only and cannot authenticate the OpenAI inference surface.

### J. Real inference regression
With AETHERGATE_ALLOW_INFERENCE_AUTH_BYPASS=false:
- official OpenAI Python SDK non-stream completion succeeds using inference credential;
- streaming completion succeeds;
- persisted attribution remains correct.

## Automated Tests

Add deterministic coverage for at least:

1. OIDC config validation;
2. production issuer HTTPS enforcement;
3. discovery issuer mismatch rejected;
4. login state entropy/expiry;
5. nonce validation;
6. PKCE S256 generation/use;
7. state one-time consumption;
8. authorization-code callback replay rejected;
9. ID token issuer validation;
10. ID token audience validation;
11. ID token expiry validation;
12. subject required;
13. unique issuer+subject external identity;
14. email not used as unique identity key;
15. unknown identity denied with JIT off;
16. JIT path unprivileged if implemented;
17. inactive linked principal denied;
18. service credential AdminRequestContext still works;
19. browser-session AdminRequestContext works without fake credential;
20. session raw cookie never persisted;
21. session hash lookup;
22. session idle expiry;
23. session absolute expiry;
24. revoked session denied;
25. cookie attributes;
26. session fixation prevented;
27. CSRF missing/wrong/correct;
28. Bearer admin mutation does not require CSRF;
29. invalid supplied Authorization never falls through to valid cookie;
30. role revocation reflected on next session request;
31. project/principal deactivation reflected on next session request;
32. logout durable/idempotent;
33. audit contains no OIDC/session/CSRF secrets;
34. migration 0011 -> 0012;
35. empty DB -> latest;
36. existing 315-test baseline remains green or higher;
37. official SDK inference regressions remain green.

## Migration

Do not rewrite migrations 0001 through 0011.

Add migration 0012 for external identities, browser sessions, OIDC login transactions, and required
indexes/constraints.

Requirements:
- live 0011 -> 0012 succeeds;
- empty DB -> latest succeeds;
- no raw token/session/cookie/nonce/PKCE/client secret invented or persisted by migration;
- existing service-account credentials and roles remain unchanged.

## Security / logging

Never log or persist in audit:
- OIDC client secret;
- authorization code;
- ID token;
- access token;
- refresh token;
- raw state;
- raw nonce;
- PKCE verifier;
- raw session cookie;
- raw CSRF token;
- API keys;
- bootstrap token.

Safe logs may use gateway request ID, opaque principal/session/external-identity IDs, issuer hostname,
and fixed failure category.

Add canary tests for redaction.

## Documentation

Update:
- docs/architecture/security.md
- docs/architecture/admin-api.md
- docs/contracts/domain-model.md
- docs/contracts/admin-v1-foundation.md
- docs/development/README.md
- docs/development/agent-handoff.md

Document:
- OIDC authorization-code + PKCE flow;
- external identity linking;
- session cookie attributes/lifetimes;
- CSRF design;
- Bearer-vs-cookie precedence;
- role/project/principal revalidation;
- no automatic privilege from IdP claims;
- local test IdP strategy.

Do not modify the dated audit.

## Still Deferred

Do not implement:
- OAuth device flow for Linux CLI;
- React web console;
- IdP group-to-role mapping;
- SCIM;
- multiple OIDC providers;
- SAML;
- full remaining catalog/accounting/queue admin CRUD;
- Responses API;
- embeddings;
- v1 migration.

## Verification Before Commit

- full containerized test suite;
- migration 0011 -> 0012;
- empty DB -> latest;
- ruff/lint;
- git diff --check;
- secret/token log canary checks;
- legacy v1 untouched;
- dated audit unchanged;
- all required local-IdP/live nomnom scenarios completed.

## Handoff

Include:
- implementation commit(s);
- migration revision;
- OIDC library/validation approach;
- external identity model;
- session model and cookie properties;
- CSRF model;
- authentication precedence;
- human provisioning/linking policy;
- local test IdP strategy;
- live state/nonce/PKCE/replay evidence;
- live RBAC/deactivation/revocation evidence;
- real SDK inference result;
- final test count;
- dynamic port/backend;
- issues/risks;
- exactly one recommended next step.

Never include raw OIDC/session/CSRF/API/bootstrap secrets, tokens, prompt/completion bodies, or large logs.

## Commit and Push

Use conventional commits on v2.

Suggested primary commit:
feat(identity): add OIDC human sessions and CSRF protection

Push all completed commits to origin/v2.
Never push directly to main.
Do not ask whether to commit/push.

The task is complete only when all live and automated criteria are met and origin/v2 contains the
implementation and updated handoff.
