# AetherGate v2 — Current Task

## Task ID
AGV2-020V

## Title
Close live browser verification gaps for the web console

## WIP Marker — FIRST LOCAL ACTION

Immediately after entering the repository, before pull/read/implementation work, create:

`.aethergate-wip`

Safe contents:
- task ID: AGV2-020V
- branch: v2
- UTC start timestamp

It is gitignored.
Never stage, commit, or push it.
Keep it present while the task is incomplete.
If context is compacted/restarted and work is incomplete, recreate it if missing.
If blocked/incomplete, leave it present.

Remove it only after:
1. every criterion below is green;
2. handoff is committed;
3. every commit is pushed to origin/v2;
4. origin/v2 is verified to contain the finished work.

## Why This Task Exists

AGV2-020 is substantially implemented and pushed:

- v1 master-key/sessionStorage auth removed;
- OIDC browser session + JS-readable CSRF cookie;
- typed/generated OpenAPI frontend client;
- centralized auth expiry;
- dashboard/queue/operator pages;
- first-class web/dev compose path;
- 500 backend tests;
- 27 frontend unit/component tests;
- 4 Playwright E2E tests;
- backend/frontend build/lint/regressions green.

Final review found that several criteria explicitly required **live browser proof** but were only
covered by deterministic/unit tests or API-only verification.

Close those live verification gaps only.

Do not rebuild AGV2-020 and do not add the next management UI phase yet.

## Recovery

If context is compacted/restarted/uncertain:
1. ensure `.aethergate-wip` exists if task incomplete;
2. re-read AGENTS.md;
3. re-read project_spec.md;
4. re-read this file;
5. re-read docs/development/agent-handoff.md;
6. inspect git status/history;
7. continue from repository state.

## 1. Live outcome_unknown browser reconciliation

Create a **controlled real outcome_unknown** on the isolated nomnom development stack using the
existing scheduler semantics.

Preferred:
- submit a real request;
- ensure durable dispatch intent exists;
- terminate/lose the owning worker after dispatch in a way that produces the normal ambiguous-outcome
  recovery path;
- allow lease recovery to mark the request outcome_unknown.

Do not fabricate a row manually.

Once outcome_unknown exists:

### system_admin browser
Using the real OIDC browser session:
- Outcome Unknown page lists the request;
- request metadata contains no inference content/secrets;
- UI offers only:
  - failed
  - cancelled
- UI must NOT offer succeeded;
- reconcile as failed or cancelled through the browser;
- action succeeds with correct CSRF;
- request leaves outcome_unknown;
- physical slot releases exactly once;
- conservative budget/accounting behavior remains correct;
- no UsageRecord is fabricated without trustworthy usage.

### project role browser
Using real project_admin/project_viewer browser sessions:
- project role cannot use deployment reconcile;
- backend returns 403/non-enumerating behavior as appropriate;
- UI does not expose reconcile controls.

Do not rely only on unit tests for this criterion.

## 2. Live project queue RBAC in browser

Create Projects A and B with real queued work.

### project_admin(A)
Using a real OIDC browser session:
- can see A queue rows;
- cannot see B queue rows;
- direct B request ID is non-enumerating;
- can cancel an eligible A request through the UI;
- cannot pause/drain/resume endpoints;
- cannot reconcile outcome_unknown.

### project_viewer(A)
Using a real OIDC browser session:
- can see A queue rows;
- cannot see B;
- no cancel control;
- direct cancellation attempt is 403;
- no deployment operator controls.

Record browser evidence, not only backend deterministic tests.

## 3. Live role revocation / deactivation

Using a real authenticated browser session:

### role revocation
- authenticate a project_admin or system_admin;
- verify protected UI/API access;
- revoke its RoleAssignment from a separate authorized admin path;
- without changing browser cookies or re-login, perform the next protected action;
- prove authorization changes immediately.

### principal or project deactivation
- authenticate browser user;
- deactivate principal or project from a separate authorized admin path;
- next browser API call must fail safely and the centralized auth/session flow must return the user to
  an unauthenticated/login state as designed.

Do not claim deterministic test coverage as the live proof.

## 4. Live CSRF negative proof

With a real browser-authenticated system_admin session:

- normal UI mutation with the real `ag_csrf` cookie succeeds;
- issue a same-origin direct mutation using the browser session cookie but intentionally omit
  `X-CSRF-Token` => 403 invalid_csrf_token;
- repeat with an incorrect CSRF header => 403 invalid_csrf_token;
- repeat with the actual CSRF cookie echoed as `X-CSRF-Token` => succeeds.

Use Playwright/browser-context request APIs or page.evaluate fetch in a way that carries the actual
browser cookies.

Never print/log the CSRF or session cookie values.

## 5. Six requests / two slots browser proof

The previous live pass proved the cap never exceeded 2, but did not actually capture the browser UI
showing 2 simultaneous in-flight requests because the local model was too fast.

Repeat with a safely slower real request workload:
- endpoint max_concurrency=2;
- six real requests;
- increase generation length / use a controlled slow adapter only as needed;
- browser dashboard/queue must visibly capture:
  - in-flight == 2 at some point;
  - remaining requests queued;
  - occupied_slots == 2;
  - available_slots == 0;
- never exceed 2;
- all requests eventually settle.

Do not use arbitrary sleeps as the correctness mechanism; polling/observation is fine.

## 6. Web E2E expansion

Extend Playwright coverage so the live/browser behaviors above are automated where practical.

At minimum add stable browser coverage for:
- system_admin pause/resume via UI;
- project_admin own-project cancellation;
- project_viewer read-only;
- CSRF negative mutation using the authenticated browser context;
- auth expiry/revocation transition;
- outcome_unknown reconcile if the fixture can create it deterministically.

If creating outcome_unknown inside every Playwright run is too expensive, keep the live driver
separate but record it explicitly in the handoff.

## 7. Regression

Re-run:
- full backend containerized suite (baseline 500);
- frontend unit/component tests (baseline 27);
- Playwright browser E2E (baseline 4);
- npm build;
- npm lint;
- ruff check;
- git diff --check;
- secret/token/content canary scan;
- official OpenAI Python SDK non-stream + stream with inference bypass false.

No migration expected.

Do not modify migrations 0001-0016.

## 8. No new product scope

Do not implement:
- full identity CRUD UI;
- full catalog CRUD UI;
- full accounting CRUD UI;
- observability metrics instrumentation;
- Responses API;
- embeddings;
- v1 migration.

If a live test exposes a real AGV2-020 defect, fix it narrowly and add deterministic coverage.

## Documentation / handoff

Update docs/development/agent-handoff.md so it truthfully records:

- real outcome_unknown creation method;
- system_admin browser reconcile result;
- project role reconcile denial;
- project_admin/project_viewer live queue RBAC;
- live role revocation/deactivation behavior;
- live missing/wrong/correct CSRF results;
- six/two browser observation including visible in-flight=2;
- final backend test count;
- final frontend unit test count;
- final Playwright test count;
- build/lint results;
- real SDK regression;
- migration head 0016 / no migration;
- dynamic API/web/IdP ports;
- WIP marker lifecycle;
- exactly one recommended next step.

Never include raw session/CSRF/OIDC/API/device/provider secrets, prompt/completion content, or large
logs.

## Commit and Push

If only tests/docs change:
`test(web): close live operator verification gaps`

If a real bug is found:
use a narrow conventional fix commit, plus handoff update.

Push all completed commits to origin/v2.
Never push directly to main.
Do not ask whether to commit/push.

The task is complete only when every live criterion above is green, origin/v2 contains the final
tests/docs/fixes/handoff, and local `.aethergate-wip` has been removed after final push verification.
