# AetherGate v2 — Current Task

## Task ID
AGV2-017V

## Title
Close accounting concurrency and released-reservation verification gaps

## Why This Task Exists

AGV2-017 is implemented and pushed with 399 passing tests. The accounting admin API, live price
snapshot behavior, project budgets, usage/ledger/audit reads, browser CSRF, and real SDK inference are
all complete.

Final review found two narrow acceptance gaps:

1. AGV2-017 required a true concurrent enabled-price-policy create/enable race test, but the new
   accounting-admin test suite only covers sequential conflict behavior.
2. AGV2-017 required a live released pre-dispatch BudgetReservation proof with
   price_snapshot_id = null, but the live handoff only demonstrates a committed reservation. The
   nullable released case is covered deterministically, not live.

Close only these gaps. Do not rebuild AGV2-017 and do not expand into queue/operator work yet.

## Recovery

If context is compacted/restarted/uncertain:
1. re-read AGENTS.md;
2. re-read project_spec.md;
3. re-read this file;
4. re-read docs/development/agent-handoff.md;
5. inspect git status/history;
6. continue from repository state.

current-task.md is authoritative.

## 1. True concurrent PricePolicy race test

Add a DB-gated concurrency regression using independent AsyncSession/transaction boundaries.

Cover at least one of these equivalent races, preferably both if small:

### A. Concurrent create
- same RouteBinding;
- no enabled policy initially;
- two tasks concurrently create enabled PricePolicy rows.

Expected:
- exactly one succeeds;
- exactly one receives the typed PricePolicyConflictError / HTTP 409-equivalent domain result;
- exactly one enabled PricePolicy exists after both transactions settle;
- no raw IntegrityError escapes;
- no transaction corruption leaks into subsequent requests.

### B. Concurrent enable
- same RouteBinding;
- two disabled alternate policies;
- no enabled policy initially;
- two tasks concurrently enable different policies.

Expected:
- exactly one active winner;
- loser receives PricePolicyConflictError;
- runtime price lookup never sees >1 enabled policy.

Use a real synchronization barrier/event so this is a genuine race rather than sequential calls.

Do not weaken the DB partial unique index or serialize the whole application merely to make the test
pass.

If the current implementation already passes, no production code change is needed.
If it exposes a real transaction/error-translation defect, fix it narrowly.

## 2. Live released pre-dispatch budget reservation

Use nomnom and the existing request-priced route/budget setup.

Create a real request that acquires a monetary budget reservation and price snapshot but is then
cancelled/reclaimed before durable upstream dispatch, using the safest existing scheduler mechanism.

Prove from PostgreSQL and the admin API:

- BudgetReservation.state == released;
- reserved_amount == 0;
- committed_amount == 0;
- price_snapshot_id == null;
- the pre-dispatch PriceSnapshot was discarded;
- no UsageRecord exists for the request;
- no usage_debit LedgerEntry exists;
- no upstream dispatch occurred;
- budget window reserved amount is released correctly.

GET /admin/v1/budget-reservations or GET /admin/v1/budget-reservations/{id} must serialize the null
price_snapshot_id correctly.

Do not create the released row manually for the live proof. It must come from the real scheduler /
accounting lifecycle.

## 3. Settlement idempotency confirmation

AGV2-017's live handoff labeled repeated reads as the idempotent-settlement proof. Repeated reads are
not settlement replay.

Do not invent a second real upstream charge. Instead rerun the existing deterministic/containerized
settlement-retry regression that proves:

- same canonical settlement replay does not duplicate UsageRecord;
- does not duplicate usage_debit LedgerEntry;
- does not double-commit budget;
- conflicting replay raises AccountingInvariantError.

Record the exact existing test(s) or integration scenario in the handoff.

If that regression does not exist as claimed by the accounting architecture, add the narrow test now.

## Regression

Re-run:

- full containerized test suite;
- accounting admin tests;
- scheduler/accounting tests;
- catalog admin route/pricing regressions;
- OIDC browser auth/CSRF tests.

Baseline: 399 passing tests.

Run official OpenAI SDK non-stream and stream inference with
AETHERGATE_ALLOW_INFERENCE_AUTH_BYPASS=false to confirm the accounting verification setup did not
damage the normal path.

## Migration

No migration expected.

Migration head must remain 0014.

Do not modify migrations 0001-0014.

## Documentation / handoff

Update docs/development/agent-handoff.md so it truthfully records:

- true concurrent price-policy race result;
- live released pre-dispatch reservation lifecycle;
- null price_snapshot_id API proof;
- absence of usage/ledger/upstream dispatch for that released request;
- exact settlement-retry regression used;
- final test count;
- dynamic API port/backend/model;
- explicit no-migration decision;
- exactly one recommended next step: queue/operator admin API.

Do not claim repeated GETs are settlement-idempotency proof.

Never include raw API/bootstrap/OIDC/session/CSRF/provider secrets or inference content.

## Verification Before Commit

- concurrency regression green;
- live released reservation proof green;
- settlement replay regression green;
- full containerized suite green;
- ruff/lint;
- git diff --check;
- secret/token/content canary scan;
- migration head 0014;
- legacy v1 untouched;
- dated audit unchanged.

## Commit and Push

If code/tests change:
`test(accounting): close concurrency and release lifecycle gaps`

A handoff-only follow-up commit is allowed.

Push all completed commits to origin/v2.
Never push directly to main.
Do not ask whether to commit/push.

The task is complete only when every criterion above is green and origin/v2 contains the updated
implementation/tests/handoff.
