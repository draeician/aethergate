# AetherGate v2 — Current Task

## Task ID
AGV2-016V

## Title
Complete live browser-session verification for catalog admin API

## Why This Task Exists

AGV2-016 is implemented and pushed. The catalog/routing control plane, migration 0014, API-created
catalog -> real inference, egress validation, route invariants, quota edits, and automated suite are
all complete.

One required live scenario was explicitly deferred in the handoff because the previously running
host-network development IdP was unreachable:

- live browser-session catalog RBAC + CSRF verification.

Close only that verification gap. Do not rebuild AGV2-016 and do not expand scope.

## Recovery

If context is compacted/restarted/uncertain:
1. re-read AGENTS.md;
2. re-read project_spec.md;
3. re-read this file;
4. re-read docs/development/agent-handoff.md;
5. inspect git status/history;
6. continue from repository state.

current-task.md is authoritative.

## Required live verification

Use nomnom and the existing deterministic local OIDC provider.

Do not depend on an already-running stale IdP process. Start a fresh controlled local/internal IdP
instance/container as part of the verification if necessary, and restart the API as needed to clear
the development JWKS cache.

The environment is an isolated internal development network, so simple local orchestration is fine.
Preserve the production-facing auth/CSRF behavior.

### A. Human system_admin catalog read/write

1. Establish a linked USER principal with system_admin.
2. Complete the real OIDC Authorization Code + PKCE flow.
3. Using only the browser session cookie:
   - GET /admin/v1/providers succeeds;
   - GET /admin/v1/model-aliases succeeds.
4. Using the browser session + correct X-CSRF-Token:
   - perform a harmless catalog mutation, preferably create/update a dedicated disposable provider or
     model alias;
   - mutation succeeds.

### B. Browser CSRF

With the same valid human system_admin session:
- mutation without X-CSRF-Token => 403 invalid_csrf_token;
- mutation with wrong X-CSRF-Token => 403 invalid_csrf_token;
- mutation with correct token => succeeds.

### C. Project-scoped human role denied catalog

Create/link a USER principal with project_admin on project A and no deployment system_admin role.

Complete real OIDC login for that principal.

Prove:
- GET /admin/v1/providers => 403;
- GET /admin/v1/model-aliases => 403;
- catalog mutation with correct CSRF => 403.

The denial must come from deployment-scope catalog authorization, not from broken session
authentication.

### D. Bearer regression

Using a valid system_admin Bearer credential:
- catalog read succeeds;
- catalog mutation succeeds without CSRF.

This confirms CSRF remains specific to ambient browser-session authority.

### E. OIDC and inference regression

Re-run:
- OIDC happy path;
- cross-browser transaction-binding rejection;
- logout/session revocation;
- official OpenAI Python SDK non-stream chat with inference auth bypass disabled;
- official SDK streaming chat with inference auth bypass disabled.

## Automated regression

Run the full containerized suite again.

Baseline: 386 passing tests.

The handoff mentioned two pre-existing intermittent concurrency tests under full-suite load. Do not
paper over a real failure. If either flakes:
- rerun the specific test once;
- record whether it is reproducible;
- fix it only if the failure is attributable to AGV2-016 or reveals a real invariant violation.
Do not weaken assertions or add arbitrary sleeps merely to make the suite green.

## Migration

No migration expected.

Migration head must remain 0014.

Verify current DB is at 0014 and empty -> latest migration coverage remains green through the existing
test suite.

## Code changes

No production code change is expected if the live scenario passes.

If live browser-session catalog access reveals a real defect:
- fix the defect narrowly;
- add deterministic regression coverage;
- rerun the full required verification.

Do not expand into pricing/accounting/queue admin work.

## Verification before completion

- live human system_admin catalog read/write;
- live browser CSRF missing/wrong/correct;
- live human project_admin deployment-catalog denial;
- Bearer catalog regression;
- OIDC binding/logout regression;
- real SDK inference non-stream + stream;
- full containerized tests;
- ruff/lint;
- git diff --check;
- secret/token canary scan;
- migration head 0014;
- legacy v1 untouched;
- dated audit unchanged.

## Handoff

Update docs/development/agent-handoff.md so AGV2-016 no longer says scenario H was deferred.

Include:
- how the controlled local IdP was started;
- live human system_admin catalog result;
- live project_admin denial result;
- live CSRF result;
- Bearer result;
- OIDC/inference regression result;
- final test count;
- dynamic API/IdP ports;
- backend/model;
- explicit no-migration decision unless a real fix required one;
- exactly one recommended next step.

Never include raw OIDC/session/CSRF/API/bootstrap secrets, provider secrets, prompt/completion content,
or large logs.

## Commit and Push

If only handoff evidence changes, use a docs/verification commit such as:
`docs(development): complete AGV2-016 browser verification`

If code changes are required, use an appropriate conventional fix commit plus the handoff update.

Push all completed commits to origin/v2.
Never push directly to main.
Do not ask whether to commit/push.

The task is complete only when the missing live browser-session catalog scenario and regressions are
green and origin/v2 contains the updated handoff.
