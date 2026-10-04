# AetherGate v2 — Current Task

## Task ID
AGV2-011

## Title
Harden accounting and budget scheduling invariants

## Ownership
Primary: accounting/audit  
Coordinating: scheduler/queueing, contracts, platform/testing

## Why This Task Exists

AGV2-010 successfully added immutable pricing snapshots, usage records, optional project budgets,
budget reservations, ledger entries, and idempotent settlement, with 202 passing tests and live
nomnom verification.

Code-level review found several accounting/scheduling invariants that the test suite did not fully
cover. Fix these before moving into identity/auth or admin API work.

Do not expand into OIDC, admin CRUD, UI, Responses API, or migration from v1 in this task.

## Compaction Recovery

If context is compacted, summarized, restarted, or you become uncertain what remains:

1. re-read `AGENTS.md`;
2. re-read `project_spec.md`;
3. re-read this file;
4. re-read `docs/development/agent-handoff.md`;
5. inspect `git status` and recent commits;
6. continue from repository state.

Do not ask the user whether to commit, push, continue, or stop when this file already specifies the
required completion behavior.

## Before You Start

1. Work on branch `v2`.
2. Run `git pull --ff-only origin v2`.
3. Read:
   - `AGENTS.md`
   - `project_spec.md`
   - `docs/development/agent-handoff.md`
   - `docs/development/current-task.md`
   - `docs/architecture/accounting.md`
   - `docs/architecture/scheduler.md`
   - `src/aethergate/accounting/repository.py`
   - `src/aethergate/accounting/service.py`
   - `src/aethergate/scheduler/repository.py`
   - `src/aethergate/scheduler/service.py`
   - `tests/test_accounting.py`
4. Preserve all AGV2-009/010 scheduler, quota, lease, fencing, and settlement invariants.
5. Do not modify/delete legacy v1 `app/` or `frontend/src/`.
6. Do not commit unrelated files.

## Required Fixes

### 1. Prevent project-budget head-of-line blocking

Current scheduling scope is still:

`(endpoint_id, quota_group_id)`

That is insufficient after project budgets were added.

Example:

- same endpoint;
- same quota group;
- project A has an exhausted budget;
- project B has budget headroom;
- A's request is older.

Project B must not remain blocked behind A merely because A is the oldest request in the shared
endpoint/quota scope.

Required behavior:

- FIFO within the same effective admission scope;
- a budget-blocked project must not block another project that can dispatch;
- endpoint physical capacity remains shared;
- provider quota remains shared;
- project budget remains project-specific.

For this phase, extend the effective scheduling scope to include project identity or another stable
budget-policy scope key sufficient to avoid cross-project budget HOL blocking.

If no project is present, null project is its own scope.

Requirements:

- persisted scope metadata must remain non-content;
- route/quota/project scope must be revalidated before dispatch;
- configuration changes must not dispatch under stale policy;
- FIFO remains preserved within one effective project+quota+endpoint scope;
- same-project newer work cannot bypass older eligible work.

Add real and deterministic tests.

### 2. Make the documented lock order match the implementation

AGV2-010 handoff/docs state a deterministic lock order beginning with project budget state before
provider quota state, but current scheduler evaluation locks quota before budget.

Choose one canonical order and use it everywhere.

Preferred:

1. queued request / scheduling-scope row;
2. active price policy;
3. project budget policy/windows in stable ID order;
4. provider quota group/limit/windows in stable ID order;
5. endpoint physical capacity;
6. reservation/attempt/request state mutations.

Requirements:

- implementation and documentation must agree;
- all workers use the same ordering;
- accounting/config edit paths that lock the same rows must follow a compatible order;
- add stress/concurrency tests intended to expose deadlock/order regressions;
- do not hold a DB transaction across provider inference.

### 3. Enforce exactly one enabled price policy per route

Current lookup assumes at most one enabled price policy and uses `scalar_one_or_none()`, but the DB
does not enforce that invariant.

Add a database-backed invariant:

- at most one enabled `PricePolicy` per `RouteBinding`;
- disabled historical/edit records may coexist;
- concurrent enables cannot produce two active policies.

Prefer a PostgreSQL partial unique index on `route_binding_id WHERE enabled = true`.

Add service/repository behavior and tests so a conflict is a clear domain/admin validation error
rather than an unexpected scheduler `MultipleResultsFound`.

Do not rewrite migration `0007`; add `0008`.

### 4. Enforce billing-unit-specific price shape

Current domain/DB contracts allow enabled price policies such as:

- request billing with `request_price = NULL`;
- token billing with missing input/output prices.

The accounting helpers currently turn missing values into zero, which can silently create unintended
free pricing.

Fix this.

Required invariants:

#### Request billing
- `request_price` is required;
- token input/output prices must be null or explicitly rejected as incompatible;
- `unit_scale` may remain 1/default.

#### Token billing
- `input_price` and `output_price` are both required;
- `request_price` must be null;
- `unit_scale >= 1`.

Zero price is allowed when explicitly configured as Decimal zero. Missing price is not equivalent to
zero.

Enforce this in:

- domain models;
- admin-v1 DTOs;
- persistence/service validation;
- database CHECK constraints in migration `0008`.

Add tests proving missing vs explicit zero are distinct.

### 5. Detect conflicting idempotent settlement replays

The task requirement was:

- identical replay => no-op/same canonical result;
- conflicting replay => explicit invariant error.

Current low-level `create_usage_record` / `create_ledger_entry` uses
`ON CONFLICT DO NOTHING` and returns the existing row without visibly checking that the replay data
matches the canonical row.

Harden this.

For UsageRecord:

- same request ID + same canonical settlement fields => idempotent same result;
- same request ID + conflicting amount/currency/snapshot/route/attempt/usage fields => explicit
  accounting invariant error.

For LedgerEntry:

- same idempotency key + same canonical fields => idempotent same result;
- same key + conflicting project/usage/type/amount/currency => explicit invariant error.

Do not compare volatile timestamps in a way that breaks legitimate replay semantics; define the
canonical equality set explicitly.

Add `AccountingInvariantError` or equivalent, internal-only.

Add direct repository/service tests and crash/retry scheduler tests.

### 6. No orphan historical price snapshot for pre-dispatch failure

Price snapshots are described as the immutable historical capture of the price used for dispatched
work.

Current claim flow creates the snapshot during reservation, then marks durable dispatch intent in a
separate transaction. A cancellation/expiry/invariant failure between those phases can leave a price
snapshot attached to a request that never dispatched.

Fix the lifecycle.

Required invariant:

- a request that never reaches durable dispatch intent must not retain a historical active price
  snapshot as if pricing was used;
- no UsageRecord or usage ledger entry exists pre-dispatch;
- budget reservations still must be priced deterministically before dispatch.

Choose a clean design, for example:

- distinguish a pending price capture from immutable dispatched snapshot; or
- defer/finalize snapshot activation at durable dispatch; or
- another design that preserves immutable historical snapshots without orphaning them.

Do not simply delete snapshots that may already be referenced by dispatched historical usage.

Add race tests for:

- cancellation after resource reservation but before durable dispatch;
- expiry between reservation and dispatch;
- dispatch invariant failure;
- normal dispatch still creates exactly one immutable snapshot.

### 7. Price-policy mutation must not race historical capture

A concurrent admin/service price-policy edit must not cause a request to reserve under one price and
snapshot another.

Required:

- the active price-policy row used for budget evaluation remains consistently locked/captured through
  the final dispatch pricing decision;
- reservation amount and immutable snapshot derive from the same canonical values/version;
- concurrent policy edits either occur before the request's pricing decision or after it, never
  partially through it.

Add a deterministic concurrency test.

## Migration

Do not rewrite `0001` through `0007`.

Add migration `0008` for at least:

- unique enabled price policy per route;
- billing-unit-specific price-policy CHECK constraints;
- any persisted scheduling-scope/snapshot-lifecycle fields required by the chosen design.

Migration requirements:

- live `0007 -> 0008` succeeds;
- empty DB -> latest succeeds;
- detect invalid existing price-policy rows before adding constraints;
- do not silently reinterpret missing prices as zero;
- fail migration with a clear diagnostic if existing data violates a new invariant and cannot be
  safely repaired without guessing.

## Real Nomnom Verification — Required

Use the existing dynamic-port Docker/Podman workflow.

Verify the current backend/model rather than assuming it.

### A. Cross-project budget no-HOL

Create two development projects sharing:

- the same physical endpoint;
- the same provider account;
- the same effective provider quota group;
- the same request-priced route pricing.

Configure:

- project A budget exhausted;
- project B budget available.

Queue older A work, then B work.

Prove:

- A remains budget-blocked;
- B dispatches through the same endpoint/quota resources;
- B does not wait for A's budget reset;
- endpoint and quota limits remain respected;
- FIFO remains correct within project A.

### B. Single enabled price policy

Attempt concurrent enable/create of two active price policies for the same route.

Prove exactly one can be active and the loser receives a deterministic validation/conflict error.

### C. Price-shape validation

Prove in the running/containerized service layer:

- request price omitted => rejected;
- request price explicitly 0 => accepted;
- token input or output price omitted => rejected;
- explicit token zero prices => accepted when otherwise valid.

### D. Pre-dispatch snapshot lifecycle

Force a request into the reservation/pre-dispatch race and cancel/expire it before durable dispatch.

Prove:

- no historical active price snapshot remains associated as dispatched pricing;
- no usage record;
- no usage ledger debit;
- budget reservation is safely released.

Then run normal dispatch and prove exactly one immutable snapshot is retained.

### E. Idempotent conflict handling

Run a normal priced request, then replay settlement:

- identical replay => same canonical usage/ledger state, no duplicates;
- conflicting replay => explicit invariant error, no mutation.

### F. Regression

Re-run:

- official OpenAI SDK non-stream and stream;
- request-priced budget live test;
- six-request/two-slot endpoint test;
- shared/cross-quota tests;
- outcome_unknown budget reconciliation;
- full automated suite.

## Automated Tests

Add deterministic coverage for at least:

1. project A budget exhaustion does not HOL-block project B on same endpoint/quota;
2. FIFO preserved within project;
3. cross-project shared endpoint concurrency remains correct;
4. cross-project shared provider quota remains correct;
5. implementation lock order matches documented order;
6. stress test does not deadlock under concurrent budget/quota claims;
7. only one enabled price policy per route;
8. concurrent enable conflict;
9. request billing requires request_price;
10. explicit request price zero accepted;
11. token billing requires both input and output prices;
12. token explicit zero prices accepted;
13. incompatible price fields rejected;
14. identical UsageRecord replay idempotent;
15. conflicting UsageRecord replay raises invariant error;
16. identical LedgerEntry replay idempotent;
17. conflicting LedgerEntry replay raises invariant error;
18. pre-dispatch cancellation does not leave historical snapshot;
19. pre-dispatch expiry does not leave historical snapshot;
20. dispatch invariant failure does not leave historical snapshot;
21. normal dispatch creates exactly one immutable snapshot;
22. concurrent price edit cannot split reservation and snapshot values;
23. migration 0007 -> 0008;
24. empty DB -> latest;
25. existing accounting/quota/scheduler test suites remain green;
26. official SDK regressions remain green.

## Still Deferred

Do not implement:

- OIDC/auth/RBAC;
- scoped production API-key authentication;
- admin HTTP CRUD;
- Linux CLI;
- React UI;
- principal-level budgets;
- FX;
- invoices/payments/prepaid balance deduction;
- retry/fallback orchestration;
- `/v1/responses`;
- embeddings;
- v1 SQLite migration.

## Documentation

Update:

- `docs/architecture/accounting.md`;
- `docs/architecture/scheduler.md`;
- `docs/contracts/domain-model.md`;
- `docs/contracts/admin-v1-foundation.md`;
- `docs/development/README.md`;
- `docs/development/agent-handoff.md`.

Do not modify the dated architecture audit.

## Verification Before Commit

- full containerized test suite;
- migration `0007 -> 0008`;
- empty DB -> latest;
- ruff/lint;
- `git diff --check`;
- secret scan;
- legacy `app/` and `frontend/src/` untouched;
- dated audit unchanged;
- all required nomnom verification complete.

## Handoff

Update `docs/development/agent-handoff.md` with concise evidence for:

- implementation commit(s);
- migration revision;
- budget scheduling scope and cross-project no-HOL proof;
- canonical lock order;
- unique active-price-policy enforcement;
- billing-unit price-shape rules;
- idempotency conflict behavior;
- pre-dispatch price-snapshot lifecycle;
- concurrent price-edit behavior;
- final test count;
- dynamic AetherGate port;
- backend/model;
- issues/risks;
- exactly one recommended next step.

No credentials, prompts, completions, encryption keys, or large logs.

## Commit and Push

Use conventional commits on branch `v2`.

Suggested primary commit:

`fix(accounting): harden budget scheduling and price invariants`

A handoff-only follow-up commit is allowed.

**Push all completed commits to `origin/v2`.**

Never push directly to `main`.

Do not ask the user whether to commit or push. This task explicitly requires both.

The task is complete only when all stated verification criteria are met and `origin/v2` contains the
work and updated handoff.
