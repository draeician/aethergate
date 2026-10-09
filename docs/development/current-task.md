# AetherGate v2 — Current Task

## Task ID
AGV2-022C

## Title
Close accounting regression gate and budget-policy disable wake-up

## WIP Marker — FIRST LOCAL ACTION

Immediately after entering the repository, before pull/read/implementation work, create:

`.aethergate-wip`

Safe contents:
- task ID: AGV2-022C
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

AGV2-022V successfully closed its two accounting live-proof gaps and surfaced/fixed two real defects:
- queued budget-window work now wakes when a budget limit is raised;
- nginx no longer cuts long-held synchronous inference at 60 seconds.

The task-specific accounting browser suite is green, but the required full Playwright regression is not:
- 24 passed;
- 2 failed.

Both failures are understood and narrow:
1. management project-create assertion assumes the newly-created project is on page 1, but the list is
   oldest-first and test data has accumulated beyond one page.
2. the management credential lifecycle runs multiple real slow-model SDK calls inside Playwright's
   default 60-second timeout.

Final review also found one adjacent scheduler correctness hole:
- disabling an enabled ProjectBudgetPolicy changes admission immediately;
- the evaluator correctly ignores disabled policies;
- but queued requests blocked by that policy still retain future `next_eligible_at` metadata and are
  not re-evaluated until the old window reset.
- The AGV2-022V fix currently wakes requests only when `limit_amount` increases.

Close these three items only. Do not start observability or protocol expansion yet.

## Recovery

If context is compacted/restarted/uncertain:
1. ensure `.aethergate-wip` exists if task incomplete;
2. re-read AGENTS.md;
3. re-read project_spec.md;
4. re-read this file;
5. re-read docs/development/agent-handoff.md;
6. inspect git status/history;
7. continue from repository state.

## 1. Wake budget-blocked queued requests when a policy is disabled

In `accounting/admin.py::update_project_budget_policy`, wake requests blocked by a policy when the
mutation makes that policy less restrictive.

Required wake conditions:
- `new_limit > existing.limit_amount`; OR
- `existing.enabled is True` and `new_enabled is False`.

Use the existing scoped repository primitive:
`clear_budget_wait_metadata_for_policy`.

Do not clear wait metadata for:
- unchanged policy;
- name-only edit;
- limit decrease;
- disabled -> enabled;
unless another established semantic explicitly requires it.

The clear primitive must remain scoped to:
- state = queued;
- wait_reason = budget_window_exhausted;
- wait_limit_id = this policy.

Worker re-evaluation remains authoritative; if another budget policy still blocks, the worker will
reapply correct wait metadata.

## 2. Deterministic disable-unblock regression

Add a deterministic accounting/scheduler test:

1. request price = P;
2. enabled budget limit allows first request but not second;
3. first request succeeds and commits P;
4. second request becomes queued with `budget_window_exhausted`;
5. disable the same policy through the real accounting admin/service update path if practical;
6. prove its wait metadata is cleared;
7. prove the SAME request_id becomes eligible and succeeds;
8. exactly one new UsageRecord and one signed negative usage_debit;
9. no duplicate settlement.

Prefer exercising `update_project_budget_policy` itself so the regression covers the actual product
mutation path rather than directly calling the repository wake helper.

Preserve all authorization and audit semantics.

## 3. Fix management project-list pagination E2E

In `frontend/e2e/management-rbac.spec.ts`:
- after creating a uniquely named project through the UI, do not assume it is on page 1;
- use the existing robust `goToLastPage` helper or an equally stable UI pagination approach;
- assert the newly-created project through the browser UI;
- do not fetch all projects and substitute a direct API assertion for the UI proof.

The test must remain robust as disposable project count grows well beyond 20.

If provider list accumulation can create the same failure mode, make that assertion pagination-safe too
rather than waiting for the next run to fail.

## 4. Fix management credential lifecycle timeout

The credential lifecycle test uses multiple official SDK calls against slow Ollama generation.

Add an explicit test timeout consistent with other live SDK proofs, e.g.:
`test.setTimeout(240_000)`
or the scoped equivalent.

Do not:
- remove SDK calls;
- replace them with raw fetch;
- weaken assertions;
- increase global timeout for every cheap browser test unless necessary.

Keep official OpenAI Python SDK, bypass=false, old/new/revoked key assertions unchanged.

## 5. Full Playwright suite must be green

This is the gating criterion that AGV2-022V did not satisfy.

Run the complete browser suite, not just accounting.spec.ts.

Expected after fixes:
- zero failed;
- zero unexpected flaky failures.

Record the exact count.

Do not call the task complete if any Playwright test fails, even if it is described as pre-existing or
unrelated.

A retry-only pass is not enough for a deterministic pagination defect; fix the test.

## 6. Re-verify AGV2-022V accounting proofs

Re-run:
- same-request `budget_window_exhausted` unblock live browser/SDK proof;
- released pre-dispatch reservation browser proof.

These must remain green after the scheduler wake semantics change.

No need to rebuild or expand the accounting UI.

## 7. Regression

Run:
- full backend containerized suite; baseline **505**;
- frontend unit/component suite; baseline **74**;
- FULL Playwright suite;
- npm build;
- npm lint;
- OpenAPI/client drift;
- ruff check src tests;
- git diff --check;
- secret/token/content canary scan;
- official OpenAI Python SDK non-stream + stream with inference bypass false.

No migration expected.

Do not modify migrations 0001-0016.

## 8. Handoff correction

Update docs/development/agent-handoff.md.

Do not leave AGV2-022V described as fully complete while recording a failed required regression gate.

After this task passes, record:
- AGV2-022/022V/022C accounting phase closed;
- budget limit raise wake semantics;
- budget policy disable wake semantics;
- deterministic same-request proofs;
- project pagination E2E fix;
- lifecycle timeout fix;
- full Playwright exact green count;
- backend/frontend counts;
- accounting live proofs still green;
- build/lint/OpenAPI/SDK results;
- migration head 0016/no migration;
- WIP marker lifecycle;
- exactly one recommended next step.

## 9. No new product scope

Do not implement:
- observability metrics;
- Responses API;
- embeddings;
- accounting feature expansion;
- v1 migration;
- broad frontend redesign.

## Commit and Push

Suggested commit:
`fix(web): close accounting regression gate`

A separate narrow backend commit for disable wake semantics is acceptable.

Push all completed commits to origin/v2.
Never push directly to main.
Do not ask whether to commit/push.

The task is complete only when:
- backend/frontend regressions are green;
- the FULL Playwright suite is green with zero failures;
- accounting same-request and released-reservation live proofs remain green;
- origin/v2 contains the final work and handoff;
- local `.aethergate-wip` is removed only after final remote verification.
