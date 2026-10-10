# AetherGate v2 — Current Task

## Task ID
AGV2-022D

## Title
Remove final browser Decimal coercion from budget headroom

## WIP Marker — FIRST LOCAL ACTION

Immediately after entering the repository, before pull/read/implementation work, create:

`.aethergate-wip`

Safe contents:
- task ID: AGV2-022D
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

AGV2-022C closed the required regression gate:
- backend 506 passed;
- frontend 74 passed;
- full Playwright 26 passed, 0 failed, twice;
- budget-policy limit-raise and disable wake semantics are correct.

Final code review found one remaining violation of AGV2-022's phase-wide Decimal rule:

`frontend/src/pages/DashboardPage.tsx` does:

`const headroom = Number(budget.headroom)`

and compares `headroom <= 0`.

That is not acceptable because:
- accounting Decimal values must never pass through JavaScript IEEE-754 `Number`;
- `BudgetStatusRead.headroom` is signed `Money` and may be negative after overage;
- the browser should make only an exact sign/zero classification, not a lossy numeric conversion.

The accounting feature pages are already Decimal-safe. Close this final dashboard leak only.

## Recovery

If context is compacted/restarted/uncertain:
1. ensure `.aethergate-wip` exists if task incomplete;
2. re-read AGENTS.md;
3. re-read project_spec.md;
4. re-read this file;
5. re-read docs/development/agent-handoff.md;
6. inspect git status/history;
7. continue from repository state.

## 1. Add exact signed-Decimal comparison helper

Extend `frontend/src/lib/decimal.ts` with a helper that can classify a signed fixed-point Decimal
string without using:
- `Number()`;
- `parseFloat()`;
- bigint conversion that would discard scale/format semantics;
- any third-party binary floating point path.

Preferred API:
- `isDecimalNonPositive(value: string): boolean`
or
- `compareDecimalToZero(value: string): -1 | 0 | 1`.

Requirements:
- accepts canonical backend signed Decimal strings;
- handles optional leading `-`;
- handles zero forms such as `0`, `0.0`, `0.000000000000`, and negative zero defensively;
- correctly handles arbitrarily awkward valid values within the backend Numeric(24,12) contract;
- rejects or safely handles malformed values rather than silently coercing.

Do not weaken the existing exact-string rules.

## 2. Remove Number(headroom) from DashboardPage

Replace the dashboard budget exhausted check with the exact Decimal helper.

Semantics:
- `blocked = budget.enabled && headroom <= 0`;
- positive exact Decimal => "ok";
- zero => "exhausted";
- negative => "exhausted";
- disabled budget => not marked exhausted even when headroom <= 0.

Keep rendering the exact server-returned headroom string verbatim.

Do not recompute headroom from limit/committed/reserved.

## 3. Decimal canary tests

Extend `frontend/src/lib/decimal.test.ts` with signed comparison cases including at minimum:

- `0`;
- `0.000000000000`;
- `-0`;
- `-0.000000000000`;
- `0.000000000001`;
- `-0.000000000001`;
- `123456789.123456789012`;
- `-123456789.123456789012`.

Also test malformed strings appropriate to the helper's contract.

No expected result may be derived with `Number` or `parseFloat` inside the implementation.

## 4. Dashboard component regression

Extend `DashboardPage.test.tsx` with project-scoped budget-status cases.

Prove:
- exact tiny positive headroom => status "ok";
- zero headroom => "exhausted";
- exact tiny negative headroom => "exhausted";
- very large precise positive/negative values retain their exact rendered text;
- disabled policy is not marked exhausted solely due to non-positive headroom.

Use raw string values that expose IEEE-754 coercion risk.

## 5. Search for remaining runtime money coercions

Inspect the v2 frontend runtime accounting surfaces:
- DashboardPage;
- accounting feature pages;
- shared accounting helpers.

Prove no monetary Decimal field is passed through:
- `Number(...)`;
- `parseFloat(...)`;
- `.toFixed(...)`.

Integer fields such as:
- unit_scale;
- window_seconds;
- token/request counts;
- slot counts;
may still use normal integer conversion where appropriate.

Document the scan result in the handoff.

## 6. Regression gate

Run:
- full backend containerized suite; baseline **506**;
- frontend unit/component suite; baseline **74**;
- FULL Playwright suite; baseline **26**;
- npm build;
- npm lint;
- `npx tsc -b --noEmit`;
- OpenAPI/client drift;
- ruff check src tests;
- git diff --check;
- secret/token/content canary scan;
- official OpenAI Python SDK non-stream + stream with inference bypass false.

No migration expected.

Do not modify migrations 0001-0016.

## 7. Handoff

Update docs/development/agent-handoff.md truthfully.

Record:
- final Decimal coercion found;
- exact helper semantics;
- dashboard behavior for positive/zero/negative headroom;
- runtime frontend coercion scan;
- backend/frontend/Playwright counts;
- build/lint/typecheck/OpenAPI results;
- SDK regression;
- migration head 0016/no migration;
- WIP marker lifecycle;
- accounting phase closed only after this correction;
- exactly one recommended next step: observability metrics (queue/TTFT percentiles, upstream health,
  retry rate).

## No new product scope

Do not implement:
- observability yet;
- new accounting features;
- Responses API;
- embeddings;
- v1 migration;
- broad visual redesign.

## Commit and Push

Suggested commit:
`fix(web): keep budget headroom decimal-safe`

Push all completed commits to origin/v2.
Never push directly to main.
Do not ask whether to commit/push.

The task is complete only when:
- no runtime monetary Decimal coercion remains in the reviewed frontend accounting surfaces;
- backend/frontend/full-Playwright regressions are green;
- origin/v2 contains the final work and handoff;
- local `.aethergate-wip` is removed only after final remote verification.
