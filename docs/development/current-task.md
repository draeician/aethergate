# AetherGate v2 — Current Task

## Task ID
AGV2-017

## Title
Accounting admin API — pricing, project budgets, usage, ledger, and audit reads

## Ownership
Primary: accounting + admin API
Coordinating: identity/RBAC, catalog/routing, scheduler, audit, platform/testing

## Why This Task Exists

The accounting engine is already implemented and hardened:

- fixed-point Decimal money;
- route PricePolicy configuration;
- immutable PriceSnapshot capture;
- project budget policies/windows/reservations;
- idempotent UsageRecord settlement;
- append-only LedgerEntry settlement;
- scheduler budget admission and accounting lock order.

What is missing is the product-facing administrative API that allows the future CLI and web console
to configure pricing/budgets and inspect accounting state safely.

This task exposes those primitives through /admin/v1 without changing the commercial model.

Do not introduce invoices, payment processing, prepaid wallets, balance-gated authorization, reseller
logic, or external billing exports.

## Recovery

If context is compacted/restarted/uncertain:

1. re-read AGENTS.md;
2. re-read project_spec.md;
3. re-read this file;
4. re-read docs/development/agent-handoff.md;
5. inspect git status/history;
6. continue from repository state.

docs/development/current-task.md is authoritative.

## Before You Start

1. Work on branch v2 and pull latest origin/v2.
2. Read:
   - AGENTS.md
   - project_spec.md
   - docs/development/current-task.md
   - docs/development/agent-handoff.md
   - docs/architecture/accounting.md
   - docs/architecture/admin-api.md
   - docs/contracts/domain-model.md
   - docs/contracts/admin-v1-foundation.md
   - src/aethergate/accounting/service.py
   - src/aethergate/accounting/repository.py
   - accounting ORM models/migrations
   - scheduler budget admission + lock-order code
   - current admin authorization/pagination patterns
   - src/aethergate/contracts/admin_v1.py
3. Preserve all AGV2-010/011 accounting invariants.
4. Preserve AGV2-016 catalog/routing behavior and migration 0014.
5. Do not modify/delete legacy v1 app/ or frontend/src/.
6. Do not commit unrelated local/untracked files.

## Core accounting semantics that must remain unchanged

These are non-negotiable:

- authorization, throughput quota, physical capacity, budget policy, usage accounting, pricing, and
  settlement remain separate concepts;
- no positive-balance authorization gate;
- no implicit prepaid-wallet model;
- a budget is an optional spending-cap policy, not a balance;
- Decimal/fixed-point money only; never binary float;
- PricePolicy is mutable configuration;
- PriceSnapshot is immutable historical evidence;
- UsageRecord is immutable measured usage;
- LedgerEntry is append-only accounting history;
- no fabricated token usage;
- outcome_unknown keeps conservative accounting semantics;
- settlement remains idempotent;
- existing scheduler/accounting lock order is preserved.

## Authorization model

Use existing typed permissions:

- admin:accounting:read
- admin:accounting:write

### Deployment-scoped resources

The following are deployment/catalog accounting configuration and require:

- system_admin deployment authority;
- matching accounting permission.

Resources:
- route PricePolicy create/read/update/list;
- immutable PriceSnapshot reads across deployment;
- deployment-wide usage/ledger reads if no project filter is supplied.

Project-scoped roles must not mutate route pricing.

### Project-scoped resources

ProjectBudgetPolicy, project budget status, project reservations, project usage, and project ledger
history are project resources.

Required:

- system_admin may read/write any project;
- project_admin may read/write accounting policy for its own project;
- project_viewer may read accounting state for its own project but not mutate it;
- project-scoped callers cannot enumerate another project's accounting data;
- cross-project opaque IDs must follow the existing non-enumeration pattern;
- role/project/principal changes continue to apply immediately through the existing admin context.

Do not create a parallel authorization system.

## 1. Accounting admin service boundary

Create a dedicated accounting administration service/module.

It must:

- accept typed AdminRequestContext;
- authorize internally;
- resolve resource scope centrally;
- validate relationships/invariants;
- translate persistence conflicts/errors to stable admin-domain errors;
- emit safe audit events for mutable configuration;
- expose read-only immutable historical data without mutation helpers.

Routers stay thin.

An internal caller must not bypass RBAC merely by supplying an actor principal ID.

## 2. PricePolicy CRUD

Implement:

- POST /admin/v1/price-policies
- GET /admin/v1/price-policies
- GET /admin/v1/price-policies/{id}
- PATCH /admin/v1/price-policies/{id}

Support filtering by:
- route_binding_id;
- enabled;
- billing_unit where practical.

Requirements:

- route binding must exist;
- deployment-scoped system_admin only;
- request pricing requires request_price and forbids input/output price;
- token pricing requires input_price + output_price and forbids request_price;
- unit_scale > 0;
- non-negative prices;
- valid currency;
- at most one enabled policy per route;
- disabled historical/edit records may coexist;
- creating/enabling a conflicting second policy => stable 409;
- DB unique races translated, never raw IntegrityError/500;
- editing a policy never changes existing PriceSnapshot rows.

### PricePolicy PATCH semantics

A patch may change future pricing configuration.

When billing_unit or price-shape fields change:

- validate the complete resulting policy, not just fields present in the patch;
- do not allow an intermediate invalid shape;
- omitted means unchanged;
- explicit null may clear a price only if the resulting billing-unit shape remains valid;
- no float acceptance.

Admin price mutation must lock consistently with scheduler admission.

Use the existing canonical lock order: price-policy locking must not introduce a reverse-order deadlock
against scheduler admission.

## 3. Immutable PriceSnapshot reads

Add read DTO if needed and implement:

- GET /admin/v1/price-snapshots
- GET /admin/v1/price-snapshots/{id}

Requirements:

- read-only; no POST/PATCH/DELETE;
- stable pagination;
- filter by route_binding_id, model_alias_id, provider_account_id, source_price_policy_id where useful;
- system_admin deployment-wide read;
- if project-scoped access is later desired, do not infer project ownership directly from a
  PriceSnapshot because it is route/catalog history; keep this phase deployment-scoped;
- no prompt/completion/provider secret content.

Prove historical snapshot values remain unchanged after price-policy edits.

## 4. ProjectBudgetPolicy CRUD

Implement:

- POST /admin/v1/project-budget-policies
- GET /admin/v1/project-budget-policies
- GET /admin/v1/project-budget-policies/{id}
- PATCH /admin/v1/project-budget-policies/{id}

List filters:
- project_id;
- enabled;
- currency where useful.

Authorization:
- system_admin: any project;
- project_admin: own project read/write;
- project_viewer: own project read only.

Creation:
- project must exist;
- project scope must be authorized;
- name non-empty;
- valid currency;
- positive limit_amount;
- positive window_seconds.

### Budget policy mutation safety

Do not casually mutate semantics that already have historical windows/reservations.

For this phase:

- name: mutable;
- limit_amount: mutable for future admission;
- enabled: mutable;
- currency: immutable after creation;
- window_seconds: immutable after creation.

If changing currency/window semantics is needed later, create a replacement policy.

Reason:
- existing BudgetWindow and BudgetReservation history is keyed to the policy and existing fixed-window
  semantics;
- in-place currency/window mutation can create ambiguous historical meaning.

The API should return a stable validation error if a PATCH attempts to change immutable fields.

Do not rewrite historical windows/reservations when limit/name/enabled changes.

Budget policy mutation must use a lock compatible with scheduler budget admission and preserve the
existing canonical lock order.

## 5. Budget status / headroom reads

Implement a project accounting status surface.

Preferred:

- GET /admin/v1/projects/{project_id}/budget-status

Return one BudgetStatusRead per applicable budget policy/current window.

At minimum expose:

- project_id;
- budget_policy_id;
- currency;
- limit_amount;
- committed_amount;
- reserved_amount;
- headroom = limit - committed - reserved;
- current window_start/window_end;
- policy enabled state if contract needs it.

Requirements:

- no side-effectful creation of accounting history merely for a read if avoidable;
- if no BudgetWindow exists yet for the current fixed window, report zero committed/reserved without
  fabricating reservation history;
- negative headroom is allowed after honest overage;
- system_admin any project;
- project_admin/viewer own project.

## 6. BudgetReservation reads

Implement read-only:

- GET /admin/v1/budget-reservations
- GET /admin/v1/budget-reservations/{id}

Filters:
- project_id;
- request_id;
- budget_policy_id;
- state.

Authorization:
- system_admin all;
- project roles only their own project.

The current DTO has price_snapshot_id as required, but released pre-dispatch reservations may detach
their snapshot and store NULL.

Fix the contract so read DTO matches persistence truth:

- price_snapshot_id must be nullable when the persisted reservation is detached/released.

Do not mutate reservations through admin HTTP in this task.

## 7. UsageRecord reads

Implement immutable read-only:

- GET /admin/v1/usage-records
- GET /admin/v1/usage-records/{id}

Filters at minimum:
- project_id;
- request_id;
- principal_id;
- api_credential_id;
- model_alias_id;
- route_binding_id;
- provider_account_id;
- billing_unit;
- currency;
- recorded_at time range.

Requirements:

- bounded pagination;
- stable ordering;
- no content/prompt/completion fields;
- no secret material;
- normal reads expose immutable recorded settlement data only;
- system_admin all;
- project_admin/viewer only their project;
- cross-project ID lookup is non-enumerating for project-scoped caller.

Do not add update/delete routes.

## 8. LedgerEntry reads

Implement immutable read-only:

- GET /admin/v1/ledger-entries
- GET /admin/v1/ledger-entries/{id}

Filters:
- project_id;
- usage_record_id;
- entry_type;
- currency;
- created_at time range.

Authorization:
- system_admin all;
- project_admin/viewer own project.

Requirements:

- append-only history;
- no update/delete;
- signed Decimal amount preserved exactly;
- adjustment entries may have no usage_record_id;
- usage_debit links to UsageRecord;
- no assumption that ledger equals invoice/payment/prepaid balance.

### Manual adjustment writes

Do NOT expose arbitrary adjustment-credit/debit creation in AGV2-017.

The ledger model supports adjustments, but operator adjustment policy, approval/audit requirements, and
commercial semantics are not yet settled.

Keep adjustment HTTP writes deferred.

## 9. Accounting audit reads

Expose immutable safe administrative audit events:

- GET /admin/v1/audit-events
- GET /admin/v1/audit-events/{id}

Use existing admin:audit:read permission.

Filters at minimum:
- actor_principal_id;
- project_id;
- action;
- resource_type;
- resource_id;
- occurred_at time range.

Authorization:
- system_admin may read all audit events;
- project_admin/project_viewer may read only events explicitly scoped to their authorized project;
- deployment-scoped events are not exposed to project roles;
- an event with project_id NULL is deployment-scoped for authorization purposes.

No mutation/delete.

Never expose secret-bearing metadata; existing audit safety guarantees remain.

If the existing AuditEvent DTO does not exist, add one with safe typed fields.

## 10. Pagination / filter semantics

All new list endpoints must use the existing Page[T] contract:

- default limit 50;
- max 200;
- offset >= 0;
- deterministic stable ordering.

Time filters use explicit UTC timestamps.

Do not create a generic query language.

Invalid filter combinations return stable 400 errors, not DB exceptions.

## 11. Read consistency and no accidental writes

Accounting GET endpoints must not:

- create usage rows;
- create ledger entries;
- settle reservations;
- mutate snapshots;
- create fake historical budget reservations;
- alter accounting timestamps.

Budget-status reads may compute current-window zero state if no persisted window exists.

Add tests that repeated reads do not change accounting row counts or monetary values.

## 12. Configuration concurrency

Price and budget configuration writes race with scheduler admission.

Required:

- price edits serialize with active-price capture using the existing price-policy lock;
- budget policy edits serialize with scheduler budget policy evaluation using the same policy-row lock
  order;
- no transaction spans upstream inference;
- no deadlock-prone reverse lock order introduced;
- concurrent create/enable of price policies never yields two enabled policies;
- concurrent budget limit edits resolve deterministically and future admissions see one committed
  value.

Use targeted DB concurrency tests.

## 13. Accounting error translation

Stable errors for:

- accounting resource not found;
- unauthorized/cross-project resource;
- price-policy shape validation;
- one-enabled-price-policy conflict;
- immutable budget currency/window update;
- invalid Decimal/currency;
- invalid time-range filter;
- parent route/project mismatch.

Do not expose:
- SQL;
- constraint names;
- stack traces;
- provider secrets;
- inference content.

## 14. Audit mutable accounting configuration

Emit safe immutable audit events for actual config state changes:

- price_policy.created
- price_policy.updated
- project_budget_policy.created
- project_budget_policy.updated

Include:
- actor principal ID;
- project_id for project budget events;
- resource type/id;
- safe non-secret metadata only.

No-op PATCH => no misleading update event.

Usage/ledger/snapshot reads do not create audit noise unless the architecture already has an explicit
read-audit policy; do not invent one here.

## 15. Optional summary endpoint

If clean and small, add:

- GET /admin/v1/projects/{project_id}/accounting-summary

It may aggregate:
- current budget headroom;
- usage amount for a requested time range;
- ledger total by currency.

Only implement this if it is derived from authoritative rows with exact Decimal math and does not
distort multi-currency semantics.

Do not convert currencies or collapse different currencies into one number.

This endpoint is optional; do not delay the task for it.

## Real nomnom Verification — Required

Use dynamic Docker/Podman ports and the existing internal Ollama route.

### A. Price policy via admin API

Starting from a catalog configured through /admin/v1:

1. create a request-priced PricePolicy through /admin/v1;
2. read/list it;
3. run real inference;
4. prove an immutable PriceSnapshot captures the configured price;
5. PATCH the live PricePolicy price;
6. run another inference;
7. prove old snapshot retains old price and new snapshot captures new price.

### B. One-enabled-price-policy invariant

- create disabled alternate policy for same route;
- attempt to enable while another enabled => 409;
- concurrent enable/create attempts => exactly one enabled policy;
- inference never encounters MultipleResultsFound/ambiguous pricing.

### C. Project budget live behavior

Through /admin/v1:

1. create project budget policy with request-priced route;
2. GET budget status;
3. run request that fits => succeeds and status reflects committed amount;
4. run request that exhausts current window => waits/blocks with budget reason;
5. PATCH limit_amount or enabled state;
6. prove future admission reflects new policy;
7. historical reservations/windows remain unchanged.

### D. Budget scope RBAC

With project A and B:

- system_admin can read/write both;
- project_admin(A) can create/update/read A budget;
- project_viewer(A) can read but not write;
- A roles cannot enumerate B budget policy/status/reservations/usage/ledger.

### E. Usage + ledger

Run real successful request-priced inference.

Prove via admin API:
- exactly one UsageRecord;
- exactly one usage_debit LedgerEntry;
- Decimal amount equals immutable snapshot pricing;
- project/principal/credential/model/route/account attribution matches request;
- normal reads expose no prompt/completion/provider secret.

### F. Idempotent settlement regression

Exercise existing deterministic settlement retry or an equivalent controlled replay.

Prove:
- no duplicate usage row;
- no duplicate usage ledger debit;
- no double budget commit.

Do not invent a second live charge just for the test if deterministic integration already proves this;
a targeted DB/container integration is acceptable.

### G. Budget reservation read semantics

Exercise:
- active/reserved state if practical;
- committed state after success;
- released pre-dispatch state.

Prove released reservation can return price_snapshot_id = null.

### H. Audit reads

Prove:
- system_admin sees deployment price-policy audit and project budget audit;
- project_admin sees project-scoped budget audit for its project;
- project role cannot see deployment price-policy audit or another project's audit;
- audit metadata contains no secrets/tokens/content.

### I. Browser session / CSRF

Using the deterministic local IdP:

- human system_admin can create/update price policy with correct CSRF;
- project_admin can mutate own budget with correct CSRF;
- missing/wrong CSRF => 403;
- project_viewer mutation => 403;
- Bearer admin mutations remain CSRF-exempt.

### J. Existing regressions

Re-run:
- catalog admin API + route invariants;
- OIDC happy path and cross-browser binding;
- official OpenAI SDK non-stream;
- official OpenAI SDK stream;
- scheduler/quota/accounting/identity full suite.

Inference auth bypass must be false for real SDK verification.

## Automated Tests

Add deterministic coverage for at least:

1. accounting service-layer authorization cannot be bypassed;
2. price policy create/read/list/update;
3. request-price shape validation;
4. token-price shape validation;
5. Decimal rejects float;
6. one enabled price policy per route;
7. concurrent enabled-policy race => one winner;
8. PricePolicy PATCH validates resulting full shape;
9. old PriceSnapshot immutable after policy edit;
10. PriceSnapshot read-only pagination/filtering;
11. budget policy create/read/list/update;
12. project_admin own-project budget write;
13. project_viewer budget read-only;
14. cross-project budget non-enumeration;
15. budget currency immutable after create;
16. budget window_seconds immutable after create;
17. budget limit/name/enabled update;
18. budget status zero-state without history mutation;
19. budget status committed/reserved/headroom exact Decimal math;
20. BudgetReservation price_snapshot_id nullable on released reservation;
21. usage list/read filters;
22. cross-project usage non-enumeration;
23. ledger list/read filters;
24. cross-project ledger non-enumeration;
25. signed Decimal ledger values preserved;
26. repeated GETs do not mutate accounting state;
27. audit event list/read scope filtering;
28. project role cannot read deployment audit;
29. config no-op PATCH emits no duplicate audit;
30. accounting audit contains no secrets/content;
31. browser-session CSRF behavior;
32. Bearer accounting mutation CSRF-exempt;
33. price edit scheduler lock-order regression;
34. budget edit scheduler lock-order regression;
35. migration remains valid at current head or new migration if genuinely needed;
36. existing 386-test baseline remains green or higher;
37. real SDK inference regressions remain green.

## Migration

Prefer no new migration if existing schema is sufficient.

Do not modify migrations 0001–0014.

If a schema/index/backstop is genuinely required:
- add linear migration 0015;
- verify 0014 -> 0015;
- verify empty DB -> latest;
- preserve historical accounting rows;
- document why the schema change was necessary.

Do not add a migration merely to mark the milestone.

## Documentation

Update:

- docs/architecture/accounting.md
- docs/architecture/admin-api.md
- docs/contracts/domain-model.md
- docs/contracts/admin-v1-foundation.md
- docs/development/README.md
- docs/development/agent-handoff.md

Document:

- deployment vs project accounting authorization;
- price-policy CRUD and immutable snapshot reads;
- budget mutation safety / immutable currency and window_seconds;
- budget-status semantics;
- usage/ledger read-only semantics;
- audit read scope;
- no prepaid/invoice/payment assumption;
- pagination/filtering;
- migration/no-migration decision.

Do not modify the dated architecture audit.

## Still Deferred

Do not implement:

- invoice generation;
- payment processing;
- prepaid-wallet deductions;
- balance-gated authorization;
- reseller/commercial pricing model;
- FX conversion;
- manual ledger adjustment HTTP writes;
- principal-level budgets;
- external billing exports;
- queue/operator API;
- Linux CLI/device flow;
- React web console;
- import/diff/apply;
- Responses API;
- embeddings;
- v1 migration.

## Verification Before Commit

- full containerized suite;
- migration head verification;
- 0014 -> next migration if one is added;
- empty DB -> latest if migration is added;
- ruff/lint;
- git diff --check;
- secret/token/content canary scans;
- OIDC/CSRF regressions;
- catalog/routing regressions;
- real SDK non-stream/stream;
- legacy v1 untouched;
- dated audit unchanged;
- all required live nomnom scenarios complete.

## Handoff

Update docs/development/agent-handoff.md with:

- implementation commit(s);
- endpoints added;
- deployment/project accounting authorization behavior;
- price-policy mutation/locking semantics;
- budget-policy mutation safety;
- immutable snapshot proof;
- budget-status semantics;
- usage/ledger/audit read behavior;
- live pricing-change snapshot proof;
- live budget-block/unblock proof;
- live usage/ledger proof;
- browser CSRF/RBAC proof;
- migration or explicit no-migration decision;
- final test count;
- dynamic API/IdP ports;
- backend/model;
- issues/risks;
- exactly one recommended next step.

Never include API keys, bootstrap/OIDC/session/CSRF tokens, provider secrets, prompts/completions, or
large logs.

## Commit and Push

Suggested primary commit:

`feat(admin): add accounting and budget control plane`

A verification/handoff-only follow-up commit is allowed.

Push all completed commits to origin/v2.
Never push directly to main.
Do not ask whether to commit/push.

The task is complete only when every stated automated/live criterion is green and origin/v2 contains
the implementation and updated handoff.
