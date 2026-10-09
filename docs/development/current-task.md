# AetherGate v2 — Current Task

## Task ID
AGV2-022

## Title
Management UI II — pricing, budgets, usage, ledger, reservations, snapshots, and audit

## WIP Marker — FIRST LOCAL ACTION

Immediately after entering the repository, before pull/read/implementation work, create:

`.aethergate-wip`

Safe contents:
- task ID: AGV2-022
- branch: v2
- UTC start timestamp

The marker is gitignored.
Never stage, commit, or push it.
Keep it present for the entire incomplete task.
If context is compacted/restarted and the task is incomplete, recreate it if missing.
If blocked/incomplete, leave it present.

Remove it only after:
1. every criterion below is green;
2. handoff is committed;
3. every task commit is pushed to origin/v2;
4. origin/v2 is verified to contain the finished work.

## Why This Task Exists

The v2 backend already exposes a complete accounting administration surface:
- route PricePolicy CRUD;
- immutable PriceSnapshot reads;
- project budget policy CRUD;
- current budget/headroom status;
- BudgetReservation reads;
- immutable UsageRecord reads;
- append-only LedgerEntry reads;
- AuditEvent reads.

The web console now has a verified OIDC/CSRF foundation, operator UI, and identity/catalog management
patterns. This task completes the accounting-management surface in the web console.

This is a UI/control-plane task. Do not change the settled accounting/commercial model.

## Recovery

If context is compacted/restarted/uncertain:
1. ensure `.aethergate-wip` exists if task incomplete;
2. re-read AGENTS.md;
3. re-read project_spec.md;
4. re-read this file;
5. re-read docs/development/agent-handoff.md;
6. inspect git status/history;
7. continue from repository state.

current-task.md is authoritative.

## Before You Start

1. Work on branch v2 and pull latest origin/v2.
2. Read:
   - AGENTS.md
   - project_spec.md
   - docs/development/current-task.md
   - docs/development/agent-handoff.md
   - docs/architecture/accounting.md
   - docs/architecture/admin-api.md
   - docs/architecture/security.md
   - src/aethergate/api/accounting_admin.py
   - src/aethergate/accounting/admin.py
   - accounting admin contracts in src/aethergate/contracts/admin_v1.py
   - frontend generated OpenAPI workflow
   - frontend/src/lib/client.ts
   - frontend/src/lib/roles.ts
   - frontend/src/components/ui/*
   - existing identity/catalog/operator pages/tests/E2E
3. Preserve all AGV2-010/011/017/017V accounting invariants.
4. Preserve all AGV2-020/021 browser auth, CSRF, pagination, RBAC, and error-handling patterns.
5. Do not modify/delete legacy Python v1 app/.
6. Do not commit unrelated local/untracked files.

## Core accounting rules — must remain true

- Money is fixed-point Decimal, never binary float.
- Backend precision/rounding remains authoritative.
- PriceSnapshot, UsageRecord, and LedgerEntry are immutable historical records.
- BudgetReservation is historical/read-only through admin HTTP.
- PricePolicy is mutable configuration; PriceSnapshot captures immutable dispatch-time pricing.
- Project budgets are optional policy controls, not prepaid balances.
- Budget currency and window_seconds are immutable after creation.
- No manual ledger adjustment HTTP writes in this phase.
- No invoices, payments, prepaid-wallet deductions, reseller/commercial semantics, or FX.
- Reads must not fabricate accounting rows.
- The web console never computes an authoritative balance that the backend did not provide.

## 1. Feature-module organization

Add a coherent accounting feature area, for example:

- frontend/src/features/accounting/

Prefer small pages/components:
- PricingPage
- PriceSnapshotsPage
- BudgetsPage
- BudgetReservationsPage
- UsagePage
- LedgerPage
- AuditPage

Reuse existing shared:
- Pagination
- ErrorBanner
- StatusBadge
- Modal
- ConfirmDialog
- Form primitives

Do not turn client.ts or App.tsx into accounting god-files.

## 2. Navigation / routing

Add authenticated routes:

### Accounting configuration
- /accounting/pricing
- /accounting/snapshots
- /accounting/budgets

### Accounting history / observability
- /accounting/reservations
- /accounting/usage
- /accounting/ledger
- /audit

Equivalent nested structure is acceptable if coherent.

Role-aware nav:
- system_admin: all accounting + audit pages;
- project_admin: own-project budgets/reservations/usage/ledger/audit;
- project_viewer: own-project read-only budgets/status/reservations/usage/ledger/audit;
- project roles must not see deployment pricing/snapshot controls.

Backend remains the authorization boundary.

## 3. Typed client coverage

Extend the central generated-type wrapper for:

- list/get/create/update PricePolicy;
- list/get PriceSnapshot;
- list/get/create/update ProjectBudgetPolicy;
- project budget status;
- list/get BudgetReservation;
- list/get UsageRecord;
- list/get LedgerEntry;
- list/get AuditEvent.

Use generated OpenAPI types.

No arbitrary accounting-path passthrough helper.

Run schema/client generation and drift checks after any backend contract change.

## 4. Decimal-safe browser representation

This is critical.

JavaScript must not convert accounting Decimal values to IEEE-754 Number for editing, arithmetic, or
formatting.

Rules:
- keep monetary Decimal fields as their generated wire representation, normally strings;
- form state for money/prices remains string;
- validate decimal syntax without Number()/parseFloat();
- send exact decimal strings accepted by the generated/backend contract;
- render exact values from the server without lossy conversion;
- do not calculate authoritative totals/headroom client-side when the backend already returns them.

For simple non-money integers such as window_seconds, unit_scale, request/input/output unit counts,
normal integer handling is fine.

Add canary tests using values that binary floating point would visibly alter.

## 5. PricePolicy UI — deployment scope

Implement system_admin-only pricing management.

At minimum:
- server-paginated list;
- detail/edit;
- create;
- filters for route binding, enabled, billing unit where supported.

Show:
- route binding;
- billing_unit;
- currency;
- unit_scale;
- request_price;
- input_price;
- output_price;
- enabled;
- name.

Pricing shapes:

### request billing
- request_price is the active monetary field;
- token-price fields must follow backend validation semantics.

### token billing
- input_price/output_price + unit_scale according to backend contract;
- do not invent pricing formulas client-side.

PATCH:
- send only changed fields;
- explicit null only where clearing is valid;
- no-op edit should not emit a mutation request if practical.

Conflicts:
- one enabled PricePolicy per RouteBinding;
- render stable 409 conflict clearly;
- do not auto-disable the previous policy unless the user explicitly performs the required mutation.

## 6. Immutable PriceSnapshot UI

System_admin-only read surface.

Implement:
- paginated list;
- detail;
- filters for route binding/model alias/provider account/source policy where supported.

Show exact immutable snapshot values:
- source policy;
- route/account/alias;
- billing unit;
- currency;
- scale/prices;
- captured_at.

No edit/delete controls.

Link from a PricePolicy/UsageRecord where useful.

Explain that historical requests keep their captured snapshot even when PricePolicy changes.

## 7. Project budget policy UI

Implement project-scoped budget management.

Authorization UX:
- system_admin: any project read/write;
- project_admin: own project read/write;
- project_viewer: own project read-only;
- cross-project data never inferred/client-enumerated.

At minimum:
- list;
- create;
- edit mutable fields;
- filters by project/enabled/currency where supported.

Create fields:
- project;
- name;
- currency;
- limit_amount;
- window_seconds;
- enabled.

Edit:
- name;
- limit_amount;
- enabled.

Do NOT allow editing:
- currency;
- window_seconds.

If shown in edit UI, render those as immutable/read-only with explanation:
"Create a replacement budget policy to change currency or window."

All Decimal values remain string-safe.

## 8. Budget status / headroom

On budgets/project detail, display live current status from:

`GET /admin/v1/projects/{project_id}/budget-status`

For each policy show:
- currency;
- limit_amount;
- committed_amount;
- reserved_amount;
- headroom;
- window_start;
- window_end;
- enabled.

Use the server-returned headroom. Do not recompute authoritative headroom in JS.

If no current persisted budget window exists, show the backend zero-state cleanly.

Do not imply headroom is a prepaid account balance.

## 9. BudgetReservation history

Read-only paginated surface.

Filters where supported:
- project;
- request;
- budget policy;
- state.

Show:
- reservation id;
- request id;
- policy id;
- price_snapshot_id, including nullable value;
- reserved_amount;
- committed_amount;
- state;
- settlement_reason.

Important:
- a released pre-dispatch reservation may have `price_snapshot_id = null`;
- render null as "No snapshot / released before dispatch" or equivalent;
- never treat null as data corruption.

No mutation controls.

## 10. UsageRecord history

Immutable read-only paginated surface.

Support backend filters where useful:
- project;
- request;
- principal;
- credential;
- model alias;
- route;
- provider account;
- billing unit;
- currency;
- time range.

Show safe accounting attribution:
- IDs/relationships;
- billing unit;
- input_units;
- output_units;
- request_units;
- exact amount;
- currency;
- recorded_at;
- upstream_request_id if already part of the safe contract.

Never display:
- prompt/completion content;
- encrypted payload/result;
- provider secret material.

Link to:
- queue request metadata;
- PriceSnapshot;
- related LedgerEntry where useful.

No edit/delete.

## 11. Ledger history

Immutable append-only read-only surface.

Filters:
- project;
- usage_record_id;
- entry_type;
- currency;
- time range.

Show:
- signed exact Decimal amount;
- currency;
- entry type;
- usage record link when present;
- timestamp;
- safe reason/idempotency metadata already present in contract.

Do not:
- provide manual adjustment write controls;
- calculate a universal wallet/balance;
- label ledger as prepaid unless a future settled model says so.

If showing a project summary, make it explicitly historical ledger activity, not an authorization balance.

## 12. Audit UI

Implement read-only audit event list/detail.

Authorization:
- system_admin sees deployment + project events;
- project roles see only events explicitly scoped to their own project;
- deployment-scoped `project_id = null` events remain hidden from project roles.

Filters where supported:
- actor principal;
- project;
- action;
- resource type;
- resource id;
- time range.

Show safe metadata only.

Never render raw session/API/OIDC/provider secrets or inference content.

No mutation controls.

## 13. Cross-linking

Add useful safe links between:
- RouteBinding -> PricePolicy;
- PricePolicy -> PriceSnapshots;
- Project -> Budgets/status;
- BudgetReservation -> Request;
- UsageRecord -> Request/PriceSnapshot/Ledger;
- LedgerEntry -> UsageRecord;
- AuditEvent -> resource when a routed safe page exists.

Do not create brittle client-side data joins that require fetching every page.

## 14. Filtering / pagination

All list pages:
- server-side pagination;
- bounded page sizes;
- stable filters;
- reset page on filter change;
- no "fetch all rows then filter in browser" for authoritative filtering.

If a selector needs catalog/project labels:
- bounded relationship fetches are acceptable;
- use IDs safely when an option is outside the loaded selector page.

The current accumulated E2E data means tests must not assume a newly-created resource appears on page 1.

Reuse robust last-page/filter patterns from AGV2-021V where appropriate.

## 15. Error / empty / loading UX

Use existing structured error handling.

Clearly distinguish:
- 400 validation;
- 401 session expiry;
- 403 authorization;
- 404 non-enumeration/not found;
- 409 pricing/config conflict.

No stack traces or raw SQL/internal exceptions in the browser.

Read-only history pages must have sane empty states.

## 16. Browser RBAC E2E

Extend deterministic OIDC Playwright coverage.

### system_admin
Prove:
- creates and edits PricePolicy;
- reads immutable PriceSnapshot;
- creates/edits a project budget;
- reads reservations/usage/ledger/audit.

### project_admin(A)
Prove:
- reads A budget status/history;
- creates/edits A budget policy;
- cannot see B accounting history;
- cannot access deployment PricePolicy/PriceSnapshot surfaces;
- direct deployment accounting API => 403.

### project_viewer(A)
Prove:
- can read A budget status/reservations/usage/ledger/audit;
- no budget mutation controls;
- direct budget mutation => 403;
- cannot see B.

### audit scope
Prove:
- project role sees A project-scoped audit events;
- project role does not see deployment-scoped/null-project audit events;
- system_admin sees both where filters permit.

## 17. Live pricing snapshot proof through UI

Use the browser + real Ollama path + official Python SDK.

1. Select a disposable active RouteBinding.
2. Through UI create/enable request-priced or token-priced PricePolicy using exact Decimal strings.
3. Run official SDK request with inference bypass false.
4. UI lists the resulting immutable PriceSnapshot/UsageRecord/LedgerEntry.
5. Record the old snapshot exact price.
6. Edit PricePolicy through UI to a different exact price.
7. Prove old PriceSnapshot remains unchanged.
8. Run a new official SDK request.
9. New PriceSnapshot uses the new price.
10. Historical UsageRecord/LedgerEntry remains unchanged.

Do not inspect prompt content.

If an enabled policy already exists on the route, either use a disposable route from prior fixture setup or
explicitly disable/replace it through the UI without violating the one-enabled-policy invariant.

## 18. Live budget block/unblock proof through UI

Using a disposable project and browser-authorized budget editor:

1. create budget policy through UI with a small exact Decimal limit;
2. show budget status/headroom in UI;
3. run a request that fits;
4. verify committed/reserved/headroom changes from the server;
5. run another request that exceeds remaining budget and confirm it is blocked/queued according to the
   established backend behavior;
6. increase limit or disable policy through UI;
7. prove blocked work becomes eligible as established by backend scheduler semantics;
8. historical usage/ledger/reservation records remain unchanged.

Do not describe this as prepaid balance behavior.

## 19. Released reservation null-snapshot UI proof

Exercise or reuse a real pre-dispatch released BudgetReservation.

Browser must show:
- state = released;
- price_snapshot_id = null rendered safely;
- reserved_amount = 0;
- committed_amount = 0;
- no linked UsageRecord/usage_debit for that request.

Do not manually fabricate the row.

## 20. Exact Decimal browser canary

Add deterministic tests and one live check with awkward values, for example:
- 0.000000000123
- 123456789.123456789012
or other values valid under Numeric(24,12).

Prove:
- form input preserves exact text;
- network JSON preserves exact decimal representation expected by backend;
- rendered read model is exact;
- no Number()/parseFloat()-style rounding.

Use values that fit backend constraints.

## 21. Automated frontend tests

Add unit/component coverage for at least:
1. PricePolicy request shape form;
2. token price shape form;
3. one-enabled conflict rendering;
4. PATCH only changed fields;
5. Decimal string preservation;
6. PriceSnapshot read-only;
7. budget create;
8. budget immutable currency/window edit behavior;
9. budget status exact values;
10. project_viewer read-only;
11. reservation null snapshot;
12. usage exact amount;
13. ledger signed amount;
14. audit scope/rendering;
15. server pagination/filter query behavior;
16. 400/403/404/409 structured errors.

## 22. OpenAPI / generated client

Run:
- backend OpenAPI generation;
- frontend type/client generation;
- drift check.

Do not manually edit generated schema files.

If no backend contract changes are needed, generated output should remain stable except intentional client coverage.

## 23. Regression

Run:
- full backend containerized suite; baseline **503**;
- frontend unit/component suite; baseline **54**;
- Playwright; baseline **18**;
- npm build;
- npm lint;
- OpenAPI/client drift;
- ruff check src tests;
- git diff --check;
- secret/token/content canary scan;
- official OpenAI Python SDK non-stream + stream with inference-auth bypass false.

No migration expected.

Do not modify migrations 0001-0016 unless a genuine narrowly-scoped backend defect requires a linear
0017.

## 24. No new product scope

Do not implement:
- manual ledger adjustment HTTP writes;
- invoices/payments;
- prepaid wallet or balance-gated authorization;
- reseller/commercial pricing;
- FX;
- principal-level budgets;
- external billing exports;
- observability metric instrumentation;
- Responses API;
- embeddings;
- v1 migration;
- broad visual redesign.

If verification exposes a real backend/accounting defect, fix it narrowly and preserve all existing
accounting invariants.

## Documentation

Update:
- docs/web-console.md;
- docs/development/README.md;
- frontend/README.md;
- docs/development/agent-handoff.md;
- architecture/accounting/admin docs only if behavior clarification is required.

Document:
- accounting routes/pages;
- Decimal-safe browser handling;
- pricing vs immutable snapshots;
- budget policy vs headroom;
- immutable usage/ledger/reservations;
- audit scope;
- RBAC;
- explicit deferred commercial concepts.

Do not modify the dated architecture audit.

## Handoff

Include:
- implementation commit(s);
- accounting routes/pages;
- Decimal-safe strategy;
- RBAC behavior;
- PricePolicy/PriceSnapshot UI behavior;
- budget policy/status behavior;
- reservation null-snapshot behavior;
- usage/ledger/audit behavior;
- live pricing old/new snapshot proof;
- live budget block/unblock proof;
- released reservation UI proof;
- exact Decimal canary;
- official SDK version/results;
- backend/frontend/Playwright counts;
- build/lint/OpenAPI drift;
- migration/no-migration decision;
- dynamic API/web/IdP ports;
- issues/risks;
- WIP marker lifecycle;
- exactly one recommended next step.

Never include raw API/admin/inference/provider/session/CSRF/OIDC/device secrets, prompt/completion
content, or large logs.

## Commit and Push

Suggested primary commit:
`feat(web): add accounting management UI`

Push all completed commits to origin/v2.
Never push directly to main.
Do not ask whether to commit/push.

The task is complete only when every stated automated/live criterion is green, origin/v2 contains the
finished implementation/tests/docs/handoff, and local `.aethergate-wip` has been removed after final
push verification.
