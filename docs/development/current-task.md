# AetherGate v2 — Current Task

## Task ID
AGV2-020

## Title
Web console foundation — OIDC session shell, typed admin client, and live operator dashboard

## WIP Marker — FIRST LOCAL ACTION

Immediately after entering the repository, before pull/read/implementation work, create:

`.aethergate-wip`

Safe contents:
- task ID: AGV2-020
- branch: v2
- UTC start timestamp

The marker is gitignored and must never be staged/committed/pushed.
Keep it present for the entire incomplete task.
If context is compacted/restarted and the task is incomplete, recreate it if missing.
If blocked/incomplete, leave it present.

Remove it only after:
1. all verification is green;
2. handoff is committed;
3. every task commit is pushed to origin/v2;
4. origin/v2 is verified to contain the final work.

## Why This Task Exists

The backend control plane and Linux CLI are now mature:
- identity/RBAC/OIDC/browser sessions;
- catalog/routing;
- accounting/budgets;
- queue/operator controls;
- human CLI/device flow.

The existing React frontend is still a v1-style shell:
- stores a master admin key in sessionStorage;
- sends an x-admin-key header;
- calls legacy /admin/* endpoints;
- presents old revenue/balance semantics that do not match the v2 commercial model.

This task converts that shell into the first real v2 web console.

The first slice is deliberately bounded:
- secure browser OIDC/session integration;
- centralized typed admin API client;
- authenticated application shell;
- live operator dashboard;
- queue/request inspection and operator actions.

Full catalog/accounting/identity CRUD screens follow after this foundation.

## Recovery

If context is compacted/restarted/uncertain:
1. ensure `.aethergate-wip` exists if task incomplete;
2. re-read AGENTS.md;
3. re-read project_spec.md;
4. re-read this file;
5. re-read docs/development/agent-handoff.md;
6. inspect git status/history;
7. continue from repository state.

current-task.md is authoritative.

## Before You Start

1. Work on branch v2 and pull latest origin/v2.
2. Read:
   - AGENTS.md
   - project_spec.md
   - docs/development/current-task.md
   - docs/development/agent-handoff.md
   - docs/architecture/security.md
   - docs/architecture/admin-api.md
   - docs/architecture/scheduler.md
   - frontend package/build/nginx/Vite config
   - frontend/src/App.tsx
   - frontend/src/context/AuthContext.tsx
   - frontend/src/lib/api.ts and types.ts
   - current LoginPage/DashboardPage/Sidebar/Shell
   - server OIDC/browser session routes
   - queue/operator admin contracts
3. Preserve the existing visual shell where useful; do not rewrite the frontend merely for aesthetics.
4. Remove/rework insecure v1 auth/data behavior that conflicts with v2.
5. Do not reintroduce a browser-stored master/API credential.
6. Do not modify/delete legacy Python v1 app/.
7. Do not commit unrelated local/untracked files.

## 1. Browser authentication model

The v2 web console uses the existing human OIDC Authorization Code + PKCE flow and server-managed
browser session.

Required:
- session authentication is the HttpOnly `ag_session` cookie;
- no admin API key, CLI token, OIDC token, session cookie, or master key is stored in localStorage,
  sessionStorage, IndexedDB, frontend config, or JS-readable application state;
- invalid/expired session centrally returns the console to login;
- role changes/principal/project deactivation are reflected on the next API request through existing
  backend revalidation.

Delete the existing `aethergate_admin_key` sessionStorage behavior and old `x-admin-key` client
injection.

## 2. Web OIDC callback completion

The current callback returns JSON + one-time CSRF token. Add a safe web-console completion mode that
does not put the CSRF token into a URL/query/fragment.

Preferred architecture:
- keep the HttpOnly session cookie exactly as today;
- issue a separate JS-readable CSRF cookie/token for the web console, generated from the same fresh
  per-session CSRF secret and validated against the server-side verifier;
- cookie is NOT an authentication credential;
- Secure in prod;
- SameSite=Lax;
- fixed safe Path appropriate to the web console;
- clear it on logout/session invalidation as appropriate;
- never store it in localStorage/sessionStorage.

The callback may redirect with a fixed relative path such as `/auth/callback` when web-console mode
is enabled, so there is no user-controlled open redirect.

Preserve a JSON-compatible callback mode if existing deterministic backend tests rely on it, or update
tests with an explicit safe contract.

Do not expose session cookie values, provider tokens, code verifier, state, nonce, or OIDC tokens.

## 3. CSRF request behavior

Central frontend client behavior:
- all fetches use same-origin credentials;
- mutations send `X-CSRF-Token` from the dedicated CSRF cookie/token;
- GET/HEAD do not require CSRF;
- browser session mutations without/wrong CSRF remain 403;
- Bearer CLI/service behavior remains unchanged.

Add backend/browser tests for:
- valid web mutation;
- missing CSRF;
- wrong CSRF;
- logout clears browser auth state;
- no CSRF/session secret in web storage.

## 4. Login page and callback page

Replace the v1 master-key login UI.

Login page:
- explains company OIDC sign-in;
- button navigates to `/admin/v1/auth/oidc/login`;
- no credential input field.

Callback page:
- completes session bootstrap after backend redirect;
- calls session/whoami endpoint as needed;
- never renders a CSRF/session/OIDC secret;
- redirects to the intended safe console landing page after success;
- renders fixed safe auth failure state on failure.

No arbitrary return URL from untrusted query input.

## 5. Central AuthContext

Replace `adminKey` AuthContext with typed browser-session state:

At minimum:
- status: loading | authenticated | unauthenticated;
- principal_id;
- project_id;
- authentication_kind;
- roles;
- refreshSession();
- logout();

On application boot:
- GET `/admin/v1/auth/session` or `/admin/v1/whoami` with cookies;
- 401 => unauthenticated;
- successful session => authenticated.

No raw auth secret in React state.

## 6. Typed/generated v2 admin client

The frontend must stop hand-maintaining v1 endpoint/types as its source of truth.

Create a reproducible contract-generation workflow from FastAPI OpenAPI.

Acceptable implementation:
- export the backend OpenAPI schema deterministically from the app;
- use a standard TypeScript OpenAPI type/client generator;
- commit generated types/client if that is the chosen workflow;
- add a generation/check script so CI/dev can detect drift.

Requirements:
- frontend API calls target `/admin/v1`;
- generated code is clearly marked and not hand-edited;
- application-specific wrapper owns credentials/CSRF/error handling;
- server pagination stays server-side;
- no generic arbitrary-path admin escape hatch.

At minimum the generated/typed client must cover the resources used in this task:
- auth/session/logout;
- queue requests/summary;
- endpoint runtime;
- pause/drain/resume;
- request cancel;
- outcome_unknown/reconcile;
- quota status;
- project budget status if shown.

## 7. Central API/error handling

Create one frontend request layer.

Required:
- same-origin base URL by default;
- `credentials: "include"`;
- CSRF header for mutations;
- structured AetherGate error parsing;
- centralized 401 -> auth-expired transition;
- 403 distinct from 401;
- no automatic mutation retry;
- AbortController/timeout behavior if practical;
- no logging of sensitive headers/cookies/tokens.

Remove old `x-admin-key` handling and legacy `/admin/*` API calls from routed v2 pages.

## 8. Application routing / role-aware shell

Use React Router.

Required routes for this phase:
- `/login`;
- `/auth/callback`;
- `/` dashboard;
- `/queue`;
- `/queue/:requestId` or an equivalent request-detail surface.

The authenticated shell:
- shows current principal/role context safely;
- logout calls server logout, not just local state deletion;
- nav items are role-aware for obvious deployment-only actions;
- backend remains authoritative; hiding a button is never the authorization boundary.

Old v1 Users/Keys/Endpoints/Models/Logs routes must not remain reachable if they still call legacy
/admin endpoints. They may be temporarily removed from navigation/routing until converted in later
tasks.

## 9. Live operator dashboard

Replace the v1 revenue/user/key dashboard with v2 operational data.

Use live endpoints:
- `GET /admin/v1/queue/summary`;
- `GET /admin/v1/queue/endpoints`;
- `GET /admin/v1/queue/quota-status`;
- project budget status where authorized/useful.

Show at minimum:
- queued total;
- in-flight total;
- outcome_unknown total;
- oldest queued/wait;
- endpoint occupied vs available slots;
- endpoint operational state;
- blocking quota/cooldown information where present;
- budget headroom when a project-scoped user has a current budget.

### Metrics not yet instrumented

The product spec also calls for:
- queue/TTFT percentiles;
- upstream health;
- retry rate.

Do NOT fabricate these from unrelated fields.

If the backend lacks authoritative instrumentation:
- render them as clearly unavailable/not yet instrumented, or omit with an explicit developer note;
- record them as the recommended backend observability follow-up.

Do not silently show fake zeros.

## 10. Queue page

Implement a paginated queue/request table using the v2 server pagination.

At minimum:
- state;
- project/principal where authorized;
- model alias;
- endpoint;
- queued/start/finish timestamps;
- effective_wait_reason;
- next_eligible_at;
- cancellation_requested;
- safe error code;
- outcome_unknown state.

Never display/decrypt:
- prompt/completion content;
- encrypted payload/result;
- stream chunks;
- provider secrets;
- fencing tokens.

Filters:
- state;
- endpoint;
- project where system_admin can use it;
- pagination.

Request details remain safe metadata only.

## 11. Queue/operator actions

Expose actions based on current role/context, but rely on backend authorization.

Required:
- cancel eligible request;
- system_admin pause endpoint;
- system_admin drain endpoint;
- system_admin resume endpoint;
- system_admin reconcile outcome_unknown as failed/cancelled.

UX:
- mutation buttons show pending state;
- destructive/ambiguous actions require explicit confirmation;
- reconcile never offers succeeded;
- refresh affected views after mutation;
- display stable server error messages without stack traces.

Project admin:
- may cancel own-project eligible work;
- must not get deployment pause/drain/reconcile capability.

Project viewer:
- read-only.

## 12. Polling / refresh behavior

For this first phase, bounded polling is acceptable.

Recommended:
- dashboard/queue auto-refresh every 3–5 seconds while visible;
- stop polling when tab is hidden if straightforward;
- prevent overlapping fetches;
- manual refresh control.

Do not introduce WebSockets/SSE for admin telemetry in this task.

## 13. Frontend deployment integration

Make the v2 compose/dev environment able to run the web console without uncommitted OIDC overrides.

Close the recurring development gap noted in AGV2-017/018/019:
- first-class OIDC/device/browser env passthrough in deploy/v2 configuration where appropriate;
- add a v2 web/frontend service or documented supported Vite path;
- web service binds loopback only;
- nginx/Vite proxies `/admin`, `/health`, and `/v1` to the v2 API;
- SPA fallback works;
- session/CSRF cookies work through the proxy;
- do not hard-code the API host port in committed frontend code.

Prefer same-origin browser API calls.

Do not expose PostgreSQL.

## 14. Frontend tests

Add a real frontend test layer.

At minimum:
- unit/component tests for AuthContext/client/error behavior;
- login renders OIDC button, no key input;
- auth boot 401 vs success;
- mutation adds CSRF header;
- 401 centrally expires auth state;
- dashboard renders authoritative queue/endpoint data;
- unavailable metrics are not shown as fake zero;
- queue table renders effective wait reason;
- role-aware operator buttons;
- cancellation/pause/drain/reconcile client calls;
- no localStorage/sessionStorage auth writes.

Add browser E2E tests with Playwright or an equivalent maintained browser runner if the environment can
support it. Browser tests should use the deterministic local OIDC provider, not an Internet IdP.

Do not make browser test success depend on external provider credentials.

## 15. Live nomnom verification — required

Use a fresh deterministic local OIDC IdP, v2 API/worker/Postgres, and the real internal Ollama backend.

Inference auth bypass remains false.

### A. WIP marker
- exists at task start;
- gitignored;
- never tracked/staged.

### B. Web login
Using a real browser:
- unauthenticated visit -> login page;
- no admin/master-key input;
- click Sign in with OIDC;
- complete deterministic IdP login;
- backend sets HttpOnly session;
- frontend becomes authenticated;
- browser storage contains no admin/API/session/OIDC secret;
- CSRF token is not placed in URL/history or local/session storage.

### C. Dashboard
With real queue activity:
- dashboard shows real queue counts;
- endpoint occupied/available values;
- operational state;
- oldest wait;
- quota state/cooldown where configured;
- no fake TTFT/upstream-health/retry values.

### D. Six/two live workload
Set endpoint max_concurrency=2 and submit six real requests.
Observe in browser:
- in-flight never exceeds 2;
- queued work visible;
- effective wait reason displayed;
- counts converge to succeeded as work completes.

### E. Pause/drain/resume
From system_admin browser session:
- pause -> queued work remains held and UI shows paused;
- resume -> dispatch continues;
- drain -> in-flight finishes, queued work held;
- draining_complete reflected;
- resume works.

### F. Cancellation
- project_admin cancels own queued request successfully;
- project_viewer cannot cancel;
- cross-project request not visible;
- system_admin can cancel eligible work.

### G. outcome_unknown
Create controlled outcome_unknown:
- UI lists it;
- project role cannot reconcile;
- system_admin can reconcile failed/cancelled only;
- UI never offers succeeded;
- slot/accounting behavior remains as backend already verified.

### H. Auth expiry/logout
- logout revokes server session and returns to login;
- stale/expired browser session on next API request centrally returns to login;
- role revocation/deactivation takes effect without browser secret changes.

### I. CSRF
- normal UI mutation succeeds;
- direct missing/wrong CSRF mutation => 403;
- no raw session cookie/CSRF token logged.

### J. Regression
- full backend containerized suite (baseline 495);
- frontend npm build;
- frontend lint;
- frontend unit/component/browser tests;
- official OpenAI Python SDK non-stream + stream with bypass false.

## 16. Migration

Prefer no database migration.

Do not modify migrations 0001-0016.

If the chosen CSRF/web-session design genuinely requires schema change:
- add only linear migration 0017;
- justify it clearly;
- preserve browser/CLI session compatibility;
- verify 0016 -> 0017 and empty -> latest.

Do not add a migration merely to mark the phase.

## 17. Documentation

Update:
- docs/architecture/security.md;
- docs/architecture/admin-api.md;
- add/update docs/web-console.md;
- docs/development/README.md;
- docs/development/agent-handoff.md;
- frontend/README.md;
- README.md if needed.

Document:
- browser session + CSRF design;
- no browser master/API token storage;
- OpenAPI client generation workflow;
- auth-expiry handling;
- role-aware UI vs backend authorization;
- dashboard data sources;
- unsupported/uninstrumented metrics;
- dev/web deployment path;
- frontend test commands.

Do not modify the dated architecture audit.

## Still Deferred

Do not implement in this task:
- full users/projects/credential management UI;
- full catalog CRUD UI;
- full accounting CRUD UI;
- CLI write expansion;
- WebSocket/SSE admin telemetry;
- fabricated TTFT/upstream/retry metrics;
- Responses API;
- embeddings;
- v1 data migration;
- production secret backend selection.

## Verification Before Commit

- full backend containerized suite;
- frontend build;
- frontend lint;
- frontend tests;
- browser OIDC E2E;
- CSRF tests;
- queue/operator E2E;
- real SDK regression;
- git diff --check;
- secret/token/content canary scan;
- no browser auth secret in local/session storage;
- no old master-key login reachable;
- migration head unchanged unless justified;
- legacy Python v1 untouched;
- dated audit unchanged.

## Handoff

Include:
- implementation commit(s);
- auth/CSRF browser design;
- generated/typed client workflow;
- routed web pages;
- dashboard data sources;
- explicit unavailable metrics;
- queue/operator UI behavior;
- browser OIDC/CSRF proof;
- six/two browser proof;
- pause/drain/cancel/reconcile browser proof;
- frontend build/lint/test results;
- backend final test count;
- real SDK regression;
- migration/no-migration decision;
- dynamic API/web/IdP ports;
- issues/risks;
- WIP marker lifecycle;
- exactly one recommended next step.

Never include session/CSRF/OIDC/API/device/provider secrets, prompt/completion content, or large logs.

## Commit and Push

Suggested primary commit:
`feat(web): add OIDC operator console foundation`

Push all completed commits to origin/v2.
Never push directly to main.
Do not ask whether to commit/push.

The task is complete only when every stated criterion is green, origin/v2 contains the final
implementation/tests/docs/handoff, and local `.aethergate-wip` has been removed after final push
verification.
