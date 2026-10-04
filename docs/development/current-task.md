# AetherGate v2 — Current Task

## Task ID
AGV2-010

## Title
Accounting foundation — immutable pricing, usage, and optional project budgets

## Ownership
Primary: accounting/audit  
Coordinating: scheduler/queueing, contracts, catalog/routing, platform/testing

## Why This Task Exists

The scheduler and shared provider-account quota layers are now live-verified.

This task adds the accounting primitives required by `project_spec.md`:

- immutable price snapshots;
- authoritative usage records;
- optional project budget policy;
- monetary budget reservations;
- append-only ledger entries;
- idempotent settlement.

The commercial model remains deliberately deferred.

A project budget is an optional spending-cap policy. It is **not** a prepaid balance and a positive
monetary balance is **not** required for authorization.

## Compaction Recovery

If context is compacted, summarized, restarted, or you become uncertain about the remaining task:

1. re-read `AGENTS.md`;
2. re-read `project_spec.md`;
3. re-read this file;
4. re-read `docs/development/agent-handoff.md`;
5. inspect `git status` and recent history;
6. continue from repository state.

Do not ask the user to choose whether to commit, push, continue, or stop when this file already
specifies those actions.

## Before You Start

1. Work on branch `v2`.
2. Run `git pull --ff-only origin v2`.
3. Read:
   - `AGENTS.md`
   - `project_spec.md`
   - `docs/development/agent-handoff.md`
   - `docs/development/current-task.md`
   - `docs/contracts/domain-model.md`
   - `docs/contracts/admin-v1-foundation.md`
   - `docs/architecture/scheduler.md`
   - `docs/architecture/provider-model.md`
   - `src/aethergate/domain/value_objects.py`
   - current scheduler quota reservation/settlement code
4. Preserve all AGV2-009/009V scheduler and quota invariants.
5. Do not modify/delete legacy v1 `app/` or `frontend/src/`.
6. Do not commit unrelated local/untracked files.

## Separation of Concerns — Non-Negotiable

Preserve these as distinct concepts:

- authorization / entitlement;
- throughput quota / physical capacity;
- budget policy;
- usage accounting;
- pricing;
- settlement / billing.

Specifically:

- no positive-balance authorization gate;
- no implicit prepaid-wallet model;
- no account balance field used as universal access control;
- no assumption that a ledger debit means an invoice or external payment;
- do not foreclose future prepaid, showback, chargeback, or reseller modes.

## Monetary Representation

Use `Decimal` end to end.

Requirements:

- no binary floating point for prices, budgets, monetary reservations, or ledger amounts;
- PostgreSQL uses an explicit fixed-precision NUMERIC/DECIMAL type;
- API/domain contracts reject floats;
- currency is explicit, normalized uppercase ISO-style three-letter code;
- no FX conversion in this task;
- budget and price must use the same currency to interact;
- all monetary calculations have explicit rounding/quantization behavior documented and tested.

Use a precision sufficient for very small per-token prices and large enterprise totals.

## Pricing Configuration vs Immutable Snapshot

Do not treat mutable pricing configuration as the historical record.

Introduce a mutable pricing configuration entity, for example `PricePolicy` / `RoutePrice`, and keep
`PriceSnapshot` immutable.

### Price configuration

Associate pricing with a `RouteBinding` for this phase.

Support at least:

#### Request pricing
- billing unit: `request`;
- fixed monetary amount per successfully priced request;
- exact pre-dispatch budget reservation is possible without token estimation.

#### Token pricing
- billing unit: `token`;
- input price;
- output price;
- positive integer unit scale, e.g. price per 1,000,000 units;
- pre-dispatch budget reservation requires a trustworthy input-token estimate plus bounded output
  reservation.

Requirements:

- currency;
- enabled/active flag;
- no float prices;
- validation appropriate to billing unit;
- changing a mutable price config never alters an existing snapshot.

Do not add image/audio pricing behavior beyond contract extensibility in this task.

### Price snapshot

At the final admission/dispatch decision, capture the effective price into an immutable
`PriceSnapshot`.

A snapshot must contain enough data to reproduce the calculation later without reading mutable price
configuration.

At minimum capture:

- source pricing-config ID;
- route binding ID;
- public model alias ID;
- billing unit;
- currency;
- unit scale;
- request price and/or input/output prices as applicable;
- captured timestamp.

Once created, application code must not update/delete a snapshot during normal operation.

A request that never reaches dispatch should not create historical priced usage.

## Project Budget Policy

Implement optional project-level budgets.

A project with no active budget policy is **not** monetarily blocked.

For this phase, a budget policy contains:

- stable opaque ID;
- project ID;
- name;
- currency;
- positive limit amount;
- positive fixed `window_seconds`;
- enabled flag.

Use fixed UTC-epoch-anchored windows, consistent with quota-window semantics.

A project may have more than one enabled budget policy. All applicable same-currency policies must
admit the request.

Do not implement principal-level budgets yet, but do not design the persistence model in a way that
makes adding principal scope impractical.

## Budget Windows and Reservations

Persist authoritative budget-window state with:

- policy ID;
- window start;
- committed amount;
- reserved amount.

Persist a per-request `BudgetReservation` with:

- request ID;
- budget policy ID;
- price snapshot ID;
- window start;
- reserved amount;
- committed amount;
- state;
- timestamps.

Requirements:

`committed + reserved + requested <= budget limit`

must be evaluated transactionally under PostgreSQL locks.

No process-local budget counter.

### Admission behavior

Budget admission must participate in the same scheduler transaction as:

- shared request/token quota;
- endpoint physical capacity;
- request/attempt ownership.

If any required constraint cannot be acquired, acquire none.

A budget-exhausted request remains queued until the next eligible budget window, subject to normal
queue/total deadlines.

A request whose minimum required monetary reservation cannot fit an empty applicable budget window
must fail explicitly rather than wait forever.

Persist non-content wait metadata sufficient to explain:

- budget policy blocking the request;
- next eligible/reset time;
- budget reason distinct from throughput quota and endpoint capacity.

A blocked budget scope must not cause unrelated project/scope work to head-of-line block when shared
capacity is otherwise available.

## Monetary Reservation Calculation

### Request-priced route

Reserve exactly the configured per-request price.

This is the preferred live nomnom budget test path because it does not require token estimation.

### Token-priced route

Reserve:

`estimated input cost + bounded maximum output cost`

Requirements:

- use the same trustworthy provider/model-specific estimator contract already enforced by the
  scheduler;
- use explicit client output bound or configured route default output-token bound;
- if no trustworthy token estimator exists, a budget-enforced token-priced request fails closed;
- never reintroduce a character/word heuristic;
- token pricing may still be recorded post-completion from trustworthy actual usage even when no
  budget policy is configured, because no pre-dispatch monetary reservation is required in that
  case.

Document this distinction.

## Usage Accounting

Create immutable `UsageRecord` rows for trustworthy measured usage.

At minimum record:

- request ID;
- execution-attempt ID;
- project ID;
- principal ID where available;
- API credential ID where available;
- public model alias ID;
- route binding ID;
- provider account ID;
- price snapshot ID;
- billing unit;
- measured input units;
- measured output units;
- request units where applicable;
- calculated monetary amount;
- currency;
- measured/recorded timestamp;
- upstream request ID if safe/available.

Requirements:

- one logical settled usage record per request for this phase;
- uniqueness/idempotency enforced in PostgreSQL;
- no prompt/completion content;
- no secret material;
- usage rows are append-only/immutable in normal operation;
- provider-reported actual usage wins over estimates when trustworthy;
- never fabricate measured token usage from a reservation.

If a dispatched request fails and trustworthy usage is unavailable, do not create a fake measured
UsageRecord.

## Budget Settlement

### Successful request with trustworthy priceable usage

- compute actual monetary amount from the immutable price snapshot;
- create UsageRecord idempotently;
- settle BudgetReservation to actual amount;
- release unused monetary reservation;
- if actual amount exceeds reservation, record actual amount honestly even if budget window becomes
  over limit;
- never truncate/hide overage.

### Known post-dispatch failure/cancellation with no trustworthy usage

For budget-cap safety:

- conservatively commit the reserved monetary amount;
- do **not** create a measured UsageRecord pretending that amount was real provider usage;
- record the conservative settlement reason in budget-reservation metadata/state.

This is budget-policy accounting, not necessarily external billing.

### outcome_unknown

- keep monetary reservation held;
- never release it automatically;
- on explicit reconciliation to failed/cancelled, conservatively commit the held budget reservation
  unless trustworthy usage is supplied through a future explicit reconciliation flow;
- do not invent a usage record.

### Pre-dispatch cancellation/reclaim

- release monetary reservation completely;
- no UsageRecord;
- no usage ledger entry.

## Append-Only Ledger

Implement an append-only monetary ledger foundation.

The ledger is an accounting/event primitive, not a mandatory prepaid balance.

At minimum support immutable entries for:

- priced measured usage debit;
- explicit adjustment credit;
- explicit adjustment debit.

Requirements:

- project ID;
- optional usage-record ID;
- currency;
- signed/typed Decimal amount;
- entry type;
- immutable timestamp;
- stable idempotency key / uniqueness rule;
- optional safe reason/reference metadata with no content/secrets.

For a measured UsageRecord, create its usage ledger entry idempotently in the same settlement
transaction.

Do not create a measured-usage ledger debit from an unknown-usage conservative budget commitment.

Do not implement invoice generation, payment processing, prepaid balance deduction, or external
billing exports in this task.

## Idempotent Settlement

Settlement can be retried after worker/API/process interruption without double charging.

Required:

- DB uniqueness/idempotency for UsageRecord;
- DB uniqueness/idempotency for usage LedgerEntry;
- BudgetReservation settles at most once;
- repeated settlement with the same data is a no-op/same result;
- conflicting second settlement is an explicit invariant error, not silent overwrite;
- no double budget commit;
- no duplicate ledger debit.

Add crash/retry fault-injection tests.

## Scheduler Integration / Lock Ordering

Integrate monetary budget evaluation into the existing all-or-nothing admission plan.

Define and document a deterministic lock order across:

- request/scheduling-scope row(s);
- project budget policy/windows;
- provider quota group/limit/windows;
- endpoint physical capacity;
- request/attempt/reservation mutations.

Preserve the current no-open-transaction-during-inference rule.

Prove with concurrent multi-worker tests that:

- budget cannot oversubscribe;
- quota cannot oversubscribe;
- endpoint cannot oversubscribe;
- no partial reservation survives a failed combined admission;
- no deadlock is observed in stress/fault tests.

## Domain / Admin Contract Foundation

Add typed IDs/contracts for at least:

- price configuration;
- budget policy;
- budget reservation.

Refine the existing:

- `PriceSnapshot`;
- `UsageRecord`;
- `LedgerEntry`.

Add admin-v1 DTO foundations for:

- route pricing create/read/update;
- project budget policy create/read/update;
- budget status/headroom read shape;
- usage-record read shape;
- ledger-entry read shape.

No admin HTTP CRUD routes yet.

Do not expose mutable historical accounting fields through update DTOs.

## Persistence / Migration

Do not rewrite migrations `0001` through `0006`.

Add migration `0007` (additional revision only if truly necessary).

Persist:

- mutable route pricing config;
- immutable price snapshots;
- project budget policies;
- budget windows;
- budget reservations;
- usage records;
- ledger entries;
- required request wait/accounting metadata.

Add DB constraints for:

- positive budget/window amounts;
- allowed currencies format;
- nonnegative reserved/committed amounts;
- supported reservation/ledger states/types;
- immutable/idempotent uniqueness;
- unit-scale positivity;
- billing-unit-specific required pricing fields where practical.

Migration:

- live `0006 -> 0007` must succeed;
- empty DB -> latest must succeed;
- no historical accounting values are invented.

## Developer Seed / Inspection

Extend development tooling idempotently.

Allow optional seed configuration for:

- request-based route price;
- token-based route price;
- currency;
- unit scale;
- project budget limit/window.

Do not hard-code nomnom values.

Extend inspection tooling to show non-content accounting state:

- project budget limit;
- reserved;
- committed;
- headroom;
- current window/reset;
- request price snapshot ID;
- usage-record ID;
- blocking budget policy/reason.

Never show prompt/completion content or secrets.

## Real Nomnom Verification — Required

Use the existing dynamic-port Docker/Podman workflow.

Verify current backend/model; do not guess ports.

### A. Request-priced project budget

Use a short test window and simple decimal request price, for example:

- request price = a small fixed Decimal amount;
- budget allows exactly 2 requests in the current window;
- endpoint/quota capacity otherwise allows more.

Send at least 4 requests concurrently using the official OpenAI Python SDK.

Prove:

- exactly 2 dispatch within the budget window;
- remaining requests stay queued due specifically to budget;
- no endpoint/quota capacity is held by budget-blocked work;
- after the next budget window, queued requests dispatch;
- two workers cannot oversubscribe monetary budget;
- each successful request creates exactly one snapshot, usage record, and usage ledger debit.

Use test-only prices; they are not a product pricing decision.

### B. Price immutability

- dispatch a request under price config A;
- change mutable price config to B;
- prove A's snapshot/usage/ledger result remains unchanged;
- later request gets a new snapshot using B.

### C. Actual < reservation

Use deterministic/mock token-price tests where a trustworthy estimator exists.

Prove unused monetary reservation is released.

### D. Actual > reservation

Use deterministic/mock usage.

Prove actual priced amount is recorded honestly and can push committed budget over the configured
limit; do not truncate it.

### E. Unknown usage / failure

Deterministic provider failure after dispatch:

- budget reserve conservatively commits;
- no fake measured UsageRecord;
- no measured-usage ledger debit.

### F. outcome_unknown

- kill/expire a worker after durable dispatch;
- monetary reservation remains held;
- explicit failed/cancelled reconciliation conservatively commits it;
- no fake usage record is created.

### G. Current nomnom token estimator

Because the current Ollama model has no trusted pre-dispatch token estimator:

- token-priced route + active project budget must fail closed for monetary reservation;
- request-priced route must continue to work;
- do not weaken estimator rules.

### H. Regressions

Re-run:

- official SDK non-stream + stream;
- six-request/two-slot endpoint test;
- shared request-quota behavior;
- same-endpoint cross-quota behavior;
- worker recovery/fencing;
- 181-test baseline or higher.

## Automated Tests

Add deterministic coverage for at least:

1. Decimal-only money/price/budget contracts;
2. currency validation;
3. request-price validation;
4. token-price validation/unit scale;
5. immutable snapshot behavior;
6. no snapshot for never-dispatched request;
7. project with no budget is not monetarily blocked;
8. request-priced exact reservation;
9. budget exhaustion queues without holding endpoint/quota capacity;
10. two workers cannot oversubscribe budget;
11. simultaneous budget + quota + endpoint admission is all-or-nothing;
12. request too expensive for empty budget fails explicitly;
13. fixed budget-window boundary;
14. token-priced budget fails closed without trustworthy estimator;
15. token-priced budget reservation with trusted estimator;
16. success settlement creates one immutable UsageRecord;
17. success creates one idempotent usage LedgerEntry;
18. actual below reserve releases headroom;
19. actual above reserve recorded honestly;
20. failed unknown usage conservatively commits budget but creates no measured usage;
21. pre-dispatch cancel releases monetary reservation;
22. outcome_unknown keeps monetary reservation;
23. reconciliation conservatively commits held budget;
24. repeated settlement creates no duplicate usage/ledger/budget commit;
25. conflicting repeated settlement raises invariant error;
26. price config change does not alter historical snapshot;
27. budget-blocked scope does not head-of-line block unrelated project/scope;
28. migration 0006 -> 0007;
29. empty DB -> latest;
30. existing scheduler/quota invariant suites remain green;
31. official SDK chat regressions remain green.

## Documentation

Create/update as appropriate:

- `docs/architecture/accounting.md` — new canonical accounting/budget document;
- `docs/architecture/scheduler.md`;
- `docs/contracts/domain-model.md`;
- `docs/contracts/admin-v1-foundation.md`;
- `docs/development/README.md`;
- `project_spec.md` only for clarification, not to select a commercial model;
- `docs/development/agent-handoff.md`.

Document explicitly:

- budgets are optional policy;
- no-budget project remains allowed subject to auth/quota;
- ledger is not automatically a prepaid wallet;
- conservative budget commitment for unknown usage is not the same thing as measured usage/billing;
- pricing snapshots are immutable;
- no FX conversion;
- principal-level budgets deferred.

Do not modify the dated audit.

## Verification Before Commit

- full containerized test suite;
- migration `0006 -> 0007`;
- empty DB -> latest;
- ruff/lint;
- `git diff --check`;
- secret scan;
- legacy `app/` and `frontend/src/` untouched;
- dated audit unchanged;
- all required nomnom budget/accounting verification completed.

## Handoff

Update `docs/development/agent-handoff.md` with concise evidence for:

- implementation commit(s);
- migration revision;
- pricing model and immutable snapshot behavior;
- budget reservation/settlement semantics;
- ledger semantics;
- lock order;
- idempotency behavior;
- real request-priced nomnom budget test;
- unknown/outcome_unknown behavior;
- token-estimator fail-closed result;
- final automated test count;
- dynamic AetherGate port;
- backend/model;
- issues/risks;
- exactly one recommended next step.

No credentials, prompts, completions, encryption keys, or large logs.

## Commit and Push

Use conventional commits on branch `v2`.

Suggested primary commit:

`feat(accounting): add pricing usage and project budgets`

A handoff-only follow-up commit is allowed.

**Push all completed commits to `origin/v2`.**

Never push directly to `main`.

Do not ask the user whether to commit or push. This task explicitly requires both.

The task is complete only when all stated verification criteria are met and `origin/v2` contains the
work and updated handoff.
