# AetherGate v2 — Current Task

## Task ID
AGV2-021V

## Title
Close management-UI live lifecycle verification gaps

## WIP Marker — FIRST LOCAL ACTION

Immediately after entering the repository, before pull/read/implementation work, create:

`.aethergate-wip`

Safe contents:
- task ID: AGV2-021V
- branch: v2
- UTC start timestamp

It is gitignored.
Never stage, commit, or push it.
Keep it present while the task is incomplete.
If context is compacted/restarted and the task is incomplete, recreate it if missing.
If blocked/incomplete, leave it present.

Remove it only after:
1. every criterion below is green;
2. handoff is committed;
3. every task commit is pushed to origin/v2;
4. origin/v2 is verified to contain the finished work.

## Why This Task Exists

AGV2-021 is implemented and pushed with:
- identity/project/principal/role/credential management UI;
- provider/provider-account/SecretRef/endpoint/quota/model-alias/route management UI;
- one-time credential reveal;
- browser RBAC coverage;
- 503 backend tests;
- 54 frontend unit/component tests;
- 18 Playwright tests;
- green build/lint/OpenAPI drift/regressions.

Final review found three narrow acceptance gaps in the **live proof**, not in the core UI implementation:

1. The catalog -> inference browser E2E creates the model alias and route binding through the UI, but
   reuses the pre-provisioned Ollama provider/provider-account/endpoint. AGV2-021 required the
   disposable catalog path to be configured through the web UI wherever those resources are exposed.
2. The credential lifecycle proof creates/rotates/revokes through direct admin API calls, not through
   the browser UI as required.
3. `catalog-inference.spec.ts` uses `fetch` against the OpenAI-compatible route. The handoff calls
   that an "official OpenAI Python SDK" proof, but the disposable UI-created alias itself was not
   exercised with the official Python SDK.

Close these only. Do not rebuild AGV2-021 and do not start accounting UI yet.

## Recovery

If context is compacted/restarted/uncertain:
1. ensure `.aethergate-wip` exists if task incomplete;
2. re-read AGENTS.md;
3. re-read project_spec.md;
4. re-read this file;
5. re-read docs/development/agent-handoff.md;
6. inspect git status/history;
7. continue from repository state.

## 1. Full disposable catalog path through the browser UI

Using a real system_admin OIDC browser session on nomnom, create a disposable inference route through
the actual web pages:

1. Provider;
2. ProviderAccount;
3. Endpoint;
4. PublicModelAlias;
5. RouteBinding;
6. QuotaGroup/QuotaLimit only if needed for the route.

Requirements:

### Provider
Create a new provider through `/catalog/providers`.

Use a provider kind/capability combination compatible with the existing adapter path. Do not invent a
new backend provider implementation just for this proof.

### ProviderAccount
Create a new provider account through `/catalog/provider-accounts`.

Secret handling:
- if the local Ollama path needs no provider secret, leave SecretRef unset;
- if a SecretRef is required by the existing adapter contract, create/select only SecretRef metadata
  through the UI and reuse an already-valid external/dev secret backend binding;
- never place a raw provider secret into browser form fields, PostgreSQL metadata, logs, or Git.

### Endpoint
Create a new endpoint through `/catalog/endpoints`:
- provider account = the newly created account;
- base destination = the real allowed nomnom Ollama destination;
- max_concurrency >= 1;
- active.

The backend egress policy must remain authoritative.

### Model alias
Create a disposable client-visible alias through `/catalog/models`.

### Route binding
Create a route binding through `/catalog/routes` using only the newly-created disposable provider
account + endpoint + alias.

No hidden fallback to the pre-existing route is allowed.

Use unique names per run.

If there is no DELETE surface, leave disposable resources disabled/inactive at the end rather than
mutating database rows directly.

## 2. Official Python SDK against the UI-created disposable alias

After the complete disposable catalog path above exists:

- mint/use a valid inference credential without exposing it in process argv;
- call the **official OpenAI Python SDK** against the AetherGate `/v1` surface;
- model = the newly-created disposable alias;
- inference-auth bypass = false;
- non-stream succeeds;
- stream succeeds.

The proof must use the official `openai` Python package, not raw `fetch`, `curl`, or `httpx`.

Safe invocation:
- pass the inference key through environment/stdin/in-memory test plumbing, never shell argv;
- never print the raw key.

Then through the browser UI:
- deactivate the disposable RouteBinding (or another appropriate disposable catalog resource);
- official SDK against the alias fails safely with `model_unavailable` / established error;
- no hidden fallback occurs;
- restore/activate;
- official SDK succeeds again.

Record the exact official SDK version in the handoff.

## 3. Credential lifecycle entirely through browser UI

Using a real system_admin browser session and a disposable project/principal:

### Create
Through `/credentials`:
- select project;
- click New credential;
- select principal;
- audience = inference;
- create;
- capture the one-time raw key only from the RevealSecret UI in test memory.

Prove:
- no raw key in URL/history;
- no localStorage/sessionStorage;
- no console log;
- closing reveal removes it from DOM;
- reload does not recover it.

### Use
Use the official OpenAI Python SDK with the newly created key:
- bypass false;
- known live alias;
- request succeeds.

### Rotate
Through the browser UI:
- click Rotate on that credential;
- confirm;
- capture the new one-time key from RevealSecret;
- old key fails immediately;
- new key succeeds via official SDK.

### Revoke
Through the browser UI:
- click Revoke on the rotated/new credential;
- confirm;
- new key fails immediately.

Do not substitute direct `adminJson` create/rotate/revoke calls for the lifecycle actions being proven.

Direct API calls may still be used to provision unrelated fixtures or inspect postconditions.

## 4. Automated browser coverage

Update/add Playwright coverage so the repository proves these paths.

Preferred:
- expand `catalog-inference.spec.ts` to create provider/account/endpoint/alias/route through UI;
- expand management credential lifecycle test to perform create/rotate/revoke through UI.

For official Python SDK calls inside/alongside Playwright:
- use a safe helper/driver that invokes the existing virtualenv Python/OpenAI SDK;
- pass raw keys through environment or stdin, never argv;
- redact output;
- helper must fail with a clear assertion when SDK request fails.

If spawning Python directly from Playwright is brittle in the container, keep the browser actions in
Playwright and add a deterministic live verification driver/script that consumes only temporary
environment values. Do not commit secrets.

## 5. Handoff truthfulness

Correct any wording that overstates prior evidence.

Specifically distinguish:
- raw OpenAI-compatible HTTP/fetch proof;
- official OpenAI Python SDK proof.

The final handoff may call the disposable path an official SDK proof only after criterion 2 is green.

## 6. Regression

Re-run:
- full backend containerized suite; baseline **503**;
- frontend unit/component suite; baseline **54**;
- Playwright; baseline **18**;
- npm build;
- npm lint;
- OpenAPI/client drift check;
- ruff check src tests;
- git diff --check;
- secret/token/content canary scan;
- official OpenAI Python SDK non-stream + stream with inference-auth bypass false.

No migration expected.

Do not modify migrations 0001-0016.

## 7. No new product scope

Do not implement:
- accounting/budget/usage/ledger UI;
- new provider adapters;
- raw provider-secret storage;
- bulk import/diff/apply;
- observability metrics;
- Responses API;
- embeddings;
- v1 migration.

If the live verification exposes an actual management UI bug, fix it narrowly and add deterministic
coverage.

## Documentation / handoff

Update docs/development/agent-handoff.md with:

- disposable provider name/id proof created through UI;
- disposable provider-account proof created through UI;
- disposable endpoint proof created through UI;
- disposable alias/route proof created through UI;
- official OpenAI Python SDK version;
- official SDK non-stream + stream against the disposable alias;
- route/resource deactivation -> fail closed -> restore proof;
- credential create through UI;
- credential rotate through UI;
- credential revoke through UI;
- old/new/revoked key SDK outcomes;
- one-time reveal leak canary;
- final backend/frontend/Playwright counts;
- build/lint/OpenAPI drift results;
- migration head 0016 / no migration;
- dynamic API/web/IdP ports;
- WIP marker lifecycle;
- exactly one recommended next step: accounting/budget/usage/ledger management UI.

Never include raw inference/admin/provider/session/CSRF/OIDC/device secrets, prompt/completion content,
or large logs.

## Commit and Push

If only tests/docs change:
`test(web): close management lifecycle verification gaps`

If a real product bug is found:
use a narrow conventional fix commit plus test/docs follow-up.

Push all completed commits to origin/v2.
Never push directly to main.
Do not ask whether to commit/push.

The task is complete only when every criterion above is green, origin/v2 contains the final
tests/fixes/docs/handoff, and local `.aethergate-wip` has been removed after final push verification.
