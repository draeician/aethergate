# AetherGate v2 — Current Task

## Task ID
AGV2-015V

## Title
Harden OIDC login transaction binding and audit secrecy

## Ownership
Primary: identity/auth
Coordinating: admin API, platform/testing

## Why This Task Exists

AGV2-015 implemented OIDC authorization-code + PKCE login, durable external identity linking,
server-managed browser sessions, CSRF protection, and live nomnom verification with 348 passing tests.

Code review found two security invariants that were claimed but not actually implemented:

1. the OIDC login transaction is not bound to the browser that initiated it;
2. failed-login audit events persist the raw OIDC state value as AuditEvent.resource_id.

The current login flow validates state, nonce, PKCE, and one-time consumption, but /auth/oidc/login
sets no short-lived pre-auth browser binding cookie. Any browser that obtains a valid pending state can
present the callback and receive the authenticated session. That leaves a login-CSRF/session-swapping
path and violates the task requirement that state/nonce/PKCE be bound to the initiating browser.

Fix these before advancing the roadmap.

## Compaction Recovery

If context is compacted, summarized, restarted, or uncertain:
1. re-read AGENTS.md;
2. re-read project_spec.md;
3. re-read this file;
4. re-read docs/development/agent-handoff.md;
5. inspect git status and recent commits;
6. continue from repository state.

current-task.md is authoritative.

## Before You Start

1. Work on branch v2 and pull latest origin/v2.
2. Read:
   - AGENTS.md
   - project_spec.md
   - docs/development/current-task.md
   - docs/development/agent-handoff.md
   - docs/architecture/security.md
   - src/aethergate/api/session_auth.py
   - src/aethergate/identity/session.py
   - src/aethergate/identity/oidc.py
   - migration 0012
   - persistence models/repository for oidc_login_states
   - tests/test_oidc_session.py
3. Preserve all AGV2-015 OIDC, session, CSRF, RBAC, JIT, and Bearer-vs-cookie behavior.
4. Do not modify/delete legacy v1 app/ or frontend/src/.
5. Do not commit unrelated local/untracked files.

## 1. Bind the login transaction to the initiating browser

Add a separate, short-lived, pre-authentication browser-binding secret.

Preferred design:

- /admin/v1/auth/oidc/login generates a random >=256-bit transaction binding value;
- response sets it in a dedicated HttpOnly cookie, separate from the authenticated session cookie;
- PostgreSQL stores only a one-way verifier/hash of that binding value on the OIDC login-state row;
- callback must present both:
  - the provider-returned state; and
  - the matching transaction-binding cookie;
- callback rejects if the binding cookie is missing or wrong;
- successful or failed callback clears the transaction cookie;
- transaction cookie expires with the login-state TTL;
- transaction cookie is never an authenticated session and grants no authority.

Cookie properties:
- HttpOnly;
- SameSite=Lax;
- Secure in production;
- narrow Path, preferably /admin/v1/auth/oidc/callback or /admin/v1/auth/oidc;
- bounded Max-Age equal to login TTL;
- raw binding value never returned in JSON or URL.

Requirements:
- valid state from browser A must fail when callback is sent from browser B without A's binding cookie;
- copying only state/code is insufficient;
- wrong binding fails;
- replay still fails;
- callback cannot silently create a session when binding is absent.

Do not overload the real authenticated ag_session cookie for this.

## 2. Persist only a verifier for browser binding

Add a new field such as browser_binding_hash to OidcLoginState.

Requirements:
- raw binding secret never stored;
- hash uses the same one-way high-entropy-token approach already used for session/CSRF tokens;
- lookup is still by provider state or a hashed state design if you choose to improve it;
- constant-time comparison for binding verification.

Migration:
- do not rewrite 0012;
- add migration 0013 for the binding verifier field and any additional safe login-transaction changes;
- 0012 -> 0013 succeeds;
- empty DB -> latest succeeds.

If you decide to replace raw state storage with state_hash in this task, do it completely and safely.
Do not partially support both models without a documented transition.

## 3. Remove raw OIDC state from audit records

Current _record_login_failed writes:

resource_id = raw state

That violates the security requirement that raw login transaction secrets not be persisted in audit.

Fix audit semantics.

Required:
- no raw state in resource_id;
- no raw state in metadata;
- no nonce, code, PKCE verifier, session cookie, CSRF token, ID/access/refresh token in audit;
- use a safe opaque login-state row ID, fixed resource identifier, or fixed failure category instead;
- pre-auth failure actor remains NULL;
- failure reason may remain a fixed safe category such as token_exchange_failed / invalid_id_token /
  unknown_identity.

Add a direct DB/audit canary test proving raw state and other transaction secrets do not appear in any
AuditEvent field.

## 4. Review login-state persistence claims

Migration 0012 documentation currently says raw state/nonce/PKCE values are not persisted by the
migration while the runtime table explicitly stores state, nonce, code_verifier, and code_challenge.

Clarify documentation so it is not misleading.

For this phase:
- browser binding raw secret must not be persisted;
- authenticated session raw secret and CSRF raw token must not be persisted;
- raw OIDC provider tokens must not be persisted;
- if state/nonce/PKCE verifier remain in the short-lived login-state table, explicitly document that
  they are ephemeral login transaction material with bounded TTL and are never logged/audited;
- if you choose to hash state/nonce or encrypt PKCE material, document the stronger model.

Do not claim values are absent from runtime storage when they are present.

## 5. Expired/consumed login-state cleanup

Add or verify a safe cleanup path for short-lived login transaction rows.

At minimum:
- old consumed/expired OIDC login-state rows can be deleted by a service/repository cleanup function;
- cleanup never touches active transactions;
- callback correctness does not depend on cleanup running;
- no background scheduler is required yet.

A dev/test callable cleanup helper is sufficient for this task.

## 6. Session fixation regression

Re-test:
- successful callback creates a fresh ag_session value;
- a pre-existing ag_session cookie is replaced by the new session cookie;
- the pre-auth transaction cookie is cleared;
- a failed callback creates no new browser session.

Do not require revoking every previously authenticated browser session merely because a new login is
performed unless the architecture explicitly chooses single-session semantics.

## Real nomnom Verification — Required

Use the local/internal deterministic OIDC provider and dynamic AetherGate port.

### A. Cross-browser login binding

Browser A:
- GET /auth/oidc/login;
- receives the transaction-binding cookie;
- IdP returns valid code+state.

Browser B:
- sends the callback with A's valid code+state but without A's binding cookie;
- callback must fail;
- zero authenticated browser sessions created.

Then Browser A:
- uses its binding cookie with a fresh valid login transaction;
- callback succeeds.

### B. Wrong binding

- valid state/code;
- wrong transaction-binding cookie;
- callback fails;
- no authenticated session.

### C. Binding cookie properties

Prove:
- HttpOnly;
- SameSite=Lax;
- Secure in prod-mode configuration;
- narrow Path;
- Max-Age bounded to login TTL;
- raw binding value absent from body/redirect URL.

### D. Binding one-time lifecycle

- success clears transaction cookie;
- failed callback clears transaction cookie where response handling permits;
- replayed callback fails even if old binding value is manually replayed.

### E. Audit secrecy

Force token-exchange failure and unknown-identity failure.

Prove PostgreSQL audit rows contain none of:
- raw state;
- code;
- nonce;
- PKCE verifier;
- binding cookie;
- authenticated session cookie;
- CSRF token.

### F. Existing OIDC regressions

Re-run:
- happy-path linked USER login;
- state mismatch;
- nonce failure;
- bad PKCE;
- callback replay;
- unknown identity/JIT behavior;
- RBAC continuity;
- project/principal deactivation;
- CSRF;
- logout;
- Bearer-vs-cookie precedence.

### G. Real inference regression

With inference auth bypass disabled:
- official OpenAI SDK non-stream succeeds;
- official SDK stream succeeds;
- inference/admin/browser audience separation remains correct.

## Automated Tests

Add deterministic coverage for at least:

1. login endpoint sets transaction-binding cookie;
2. raw binding value not persisted;
3. stored binding verifier matches raw cookie;
4. missing binding cookie rejected;
5. wrong binding rejected;
6. valid state from another browser rejected;
7. correct binding succeeds;
8. callback replay still rejected;
9. transaction cookie cleared on success;
10. transaction cookie clear behavior on failure;
11. prod transaction cookie Secure;
12. transaction cookie HttpOnly/SameSite/Path/Max-Age;
13. pre-existing authenticated session cookie replaced with fresh session on successful login;
14. failed callback creates no BrowserSession;
15. failed-login audit contains no raw state;
16. audit contains no code/nonce/PKCE/binding/session/CSRF secret;
17. expired/consumed login cleanup removes only stale rows;
18. migration 0012 -> 0013;
19. empty DB -> latest;
20. existing 348-test baseline remains green or higher;
21. official SDK inference regressions remain green.

## Security / logging

Never log or place in audit:
- raw transaction binding;
- raw state;
- nonce;
- PKCE verifier;
- authorization code;
- ID/access/refresh token;
- raw session cookie;
- raw CSRF token;
- API keys;
- bootstrap token.

Safe identifiers:
- opaque OidcLoginState ID;
- opaque BrowserSession ID;
- principal ID;
- fixed failure category;
- configured issuer hostname.

## Documentation

Update:
- docs/architecture/security.md
- docs/architecture/admin-api.md
- docs/contracts/domain-model.md
- docs/development/README.md
- docs/development/agent-handoff.md
- migration 0012 commentary if needed for truthful runtime-storage wording

Document:
- pre-auth transaction cookie/browser binding;
- one-way binding verifier;
- callback requires state + binding;
- audit never stores raw state;
- ephemeral login-state storage policy;
- cleanup semantics.

Do not modify the dated audit.

## Still Deferred

Do not implement:
- Linux CLI OAuth device flow;
- React web console;
- multiple OIDC providers;
- IdP group-to-role mapping;
- SCIM;
- SAML;
- full remaining catalog/accounting/queue admin CRUD;
- Responses API;
- embeddings;
- v1 migration.

## Verification Before Commit

- full containerized test suite;
- migration 0012 -> 0013;
- empty DB -> latest;
- ruff/lint;
- git diff --check;
- secret/state/token audit/log canary checks;
- legacy v1 untouched;
- dated audit unchanged;
- all live nomnom scenarios complete.

## Handoff

Include:
- implementation commit(s);
- migration revision;
- browser-binding design;
- transaction-cookie properties;
- audit secrecy behavior;
- login-state persistence/cleanup policy;
- live cross-browser rejection proof;
- live happy-path proof;
- real SDK inference result;
- final test count;
- dynamic port/backend;
- issues/risks;
- exactly one recommended next step.

Never include raw OIDC/session/CSRF/API/bootstrap secrets, tokens, prompt/completion bodies, or large logs.

## Commit and Push

Use conventional commits on v2.

Suggested primary commit:
fix(identity): bind OIDC login transactions to browser

Push all completed commits to origin/v2.
Never push directly to main.
Do not ask whether to commit/push.

The task is complete only when all stated live and automated criteria are met and origin/v2 contains
the implementation and updated handoff.
