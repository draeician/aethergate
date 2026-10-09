# AetherGate v2 — Current Task

## Task ID
AGV2-022V

## Title
Close accounting live queue-unblock and released-reservation UI proofs

## WIP Marker — FIRST LOCAL ACTION

Immediately after entering the repository, before pull/read/implementation work, create:

`.aethergate-wip`

Safe contents:
- task ID: AGV2-022V
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

AGV2-022 is substantially implemented and pushed:
- accounting management UI;
- Decimal-safe browser handling;
- pricing/snapshot UI;
- budgets/status;
- reservations/usage/ledger/audit;
- signed usage_debit backend correction;
- 504 backend tests;
- 74 frontend tests;
- 25 Playwright tests;
- green SDK/build/lint/OpenAPI/secret checks.

Final review found two narrow **live acceptance** gaps:

1. The budget live proof uses `budget_request_too_large`, which is a terminal failed request, then
   submits a new request after increasing the budget. That does not prove the **same queued
   `budget_window_exhausted` request becomes eligible** after a policy edit.
2. There is no live/browser proof of a **real pre-dispatch released BudgetReservation** rendered with
   `price_snapshot_id = null`, zero reserved/committed amounts, no UsageRecord, and no usage_debit.

Close these only. Do not rebuild AGV2-022 and do not start observability/protocol work yet.

## Recovery

If context is compacted/restarted/uncertain:
1. ensure `.aethergate-wip` exists if task incomplete;
2. re-read AGENTS.md;
3. re-read project_spec.md;
4. re-read this file;
5. re-read docs/development/agent-handoff.md;
6. inspect git status/history;
7. continue from repository state.

## 1. Real queued budget-window exhaustion -> same request unblocks

Use the existing request-priced route and a disposable project/budget so a single request can fit an
empty budget window, but the current window has insufficient remaining headroom.

Preferred deterministic setup:

1. Use an exact request price `P`.
2. Create a budget limit that permits exactly one request (or otherwise less than two requests but >= P).
3. Run one official SDK request to consume/commit `P`.
4. Start a **second official OpenAI Python SDK request** and keep that same call pending while the
   scheduler queues it.
5. Poll the queue/operator API and capture that request's stable `request_id`.
6. Prove:
   - state remains queued;
   - `effective_wait_reason` / persisted wait_reason is `budget_window_exhausted`;
   - wait_limit_metric = budget;
   - it is not failed with `budget_request_too_large`;
   - no second usage/ledger settlement exists yet.
7. Through the browser UI, increase the same budget policy limit (or disable the policy) enough to
   admit the queued request.
8. Prove the **same captured request_id** transitions out of queued and succeeds.
9. Await the original second SDK call and prove it succeeds; do not submit a replacement request as
   the unblock proof.
10. Prove exactly one new UsageRecord and one signed negative `usage_debit` were created for that
    request; no duplicate settlement.
11. UI budget status/headroom reflects the server-returned exact values after completion.

SDK constraints:
- use official `openai` Python SDK;
- inference auth bypass false;
- key through env/stdin/in-memory plumbing, never argv;
- no raw key in logs/output.

If the existing synchronous `runOfficialSdk` helper cannot remain pending while browser actions run,
add a narrow async child-process helper using `spawn`/equivalent. Do not weaken the SDK proof to a
new replacement request.

## 2. Real pre-dispatch release lifecycle

Create a real request that reaches **reserved/pre-dispatch** accounting state, then cancel/reclaim it
before durable upstream dispatch.

This must use the real scheduler/accounting lifecycle, not manual insertion of a BudgetReservation row.

A controlled harness is acceptable if it uses the real database/service transition path and a
provider adapter that proves no upstream dispatch occurred.

Required preconditions before cancellation:
- request has a monetary BudgetReservation;
- request has acquired the associated pre-dispatch price snapshot/reference according to the real
  scheduler path;
- request has not made the upstream inference call.

Then cancel/reclaim through the established scheduler/admin transition.

Prove in PostgreSQL/admin API:
- request terminal state is cancelled/reclaimed as appropriate;
- BudgetReservation state = released;
- reserved_amount = 0 exactly;
- committed_amount = 0 exactly;
- price_snapshot_id = null;
- the pre-dispatch PriceSnapshot is discarded;
- no UsageRecord for that request;
- no `usage_debit` LedgerEntry for that request;
- budget-window reserved amount is released;
- no upstream dispatch occurred.

Do not manually seed the released reservation.

## 3. Browser UI proof for released reservation

Using a real authorized OIDC browser session, navigate to the accounting Reservations page and locate
the real released reservation created in criterion 2.

The UI must visibly and correctly render:
- Released state;
- exact reserved amount 0;
- exact committed amount 0;
- null snapshot as a safe explanatory value such as "No snapshot / released before dispatch";
- request/policy identifiers safely.

Then use Usage and Ledger pages/API filters to prove there is no measured usage / usage_debit for that
request.

Do not treat null `price_snapshot_id` as an error.

## 4. Deterministic automated coverage

Add or strengthen tests so the two semantics cannot regress:

### Budget unblock
A deterministic scheduler/integration test must prove:
- first request consumes budget;
- second request gets `budget_window_exhausted`;
- budget policy mutation changes admission;
- the **same request ID** is later reserved/dispatched/succeeded;
- exactly-once usage/ledger/budget settlement.

### Released reservation
Existing scheduler/accounting tests already cover much of the release lifecycle.
Strengthen as needed to explicitly assert:
- released BudgetReservation has `price_snapshot_id is None`;
- reserved=0;
- committed=0;
- pre-dispatch snapshot removed;
- no UsageRecord;
- no usage_debit;
- no upstream adapter call.

Add a frontend test if the null-snapshot explanatory rendering is not already covered.

## 5. Handoff truthfulness

Correct the AGV2-022 wording:

- `budget_request_too_large` proves fail-fast behavior for a request that cannot fit even an empty
  budget window.
- `budget_window_exhausted` is the queued temporary-capacity condition.
- Only call the scenario "block/unblock" after the same queued request resumes following the UI policy
  edit.

Record both semantics separately.

## 6. Regression

Re-run:
- full backend containerized suite; baseline **504**;
- frontend unit/component suite; baseline **74**;
- Playwright; baseline **25**;
- npm build;
- npm lint;
- OpenAPI/client drift;
- ruff check src tests;
- git diff --check;
- secret/token/content canary scan;
- official OpenAI Python SDK non-stream + stream with inference bypass false.

No migration expected.

Do not modify migrations 0001-0016.

## 7. No new product scope

Do not implement:
- new accounting UI features beyond what is needed for the two proofs;
- manual ledger adjustments;
- prepaid/wallet semantics;
- invoices/payments;
- observability metrics;
- Responses API;
- embeddings;
- v1 migration.

If either proof exposes a real defect, fix it narrowly and add regression coverage.

## Documentation / handoff

Update docs/development/agent-handoff.md with:

- exact `budget_request_too_large` vs `budget_window_exhausted` distinction;
- first request budget consumption;
- captured second request_id;
- queued `budget_window_exhausted` evidence;
- browser budget policy edit;
- same request_id success after edit;
- official SDK pending-call success;
- exactly-once usage/negative ledger settlement;
- real pre-dispatch release creation method;
- proof no upstream dispatch happened;
- released reservation state/amounts/null snapshot;
- pre-dispatch snapshot discard;
- no usage/debit;
- browser Reservations UI proof;
- final backend/frontend/Playwright counts;
- build/lint/OpenAPI drift;
- migration head 0016/no migration;
- dynamic API/web/IdP ports;
- WIP marker lifecycle;
- exactly one recommended next step.

Never include raw credentials/session/CSRF/OIDC/provider secrets, prompt/completion content, or large
logs.

## Commit and Push

If only tests/docs change:
`test(accounting): close live reservation and budget unblock proofs`

If a real defect is found:
use a narrow conventional fix commit plus tests/docs.

Push all completed commits to origin/v2.
Never push directly to main.
Do not ask whether to commit/push.

The task is complete only when every criterion above is green, origin/v2 contains the final
tests/fixes/docs/handoff, and local `.aethergate-wip` has been removed after final push verification.
