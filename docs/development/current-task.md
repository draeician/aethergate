# AetherGate v2 — Current Task

## Task ID
AGV2-016

## Title
Catalog and routing admin API with configuration invariants

## Ownership
Primary: catalog/routing + admin API
Coordinating: identity/RBAC, scheduler/quota, provider adapters, audit, platform/testing

## Why This Task Exists

Human and service-account administration are now authenticated and authorized, but provider/routing
configuration still depends on development seed tooling and direct internal setup.

The next product slice is the deployment-wide catalog/routing control plane used by the future web
console and CLI:

- providers;
- provider accounts;
- endpoint/deployments;
- shared quota groups and limits;
- public model aliases;
- route bindings;
- secret-reference metadata.

This task must expose those resources through /admin/v1 without weakening the inference/scheduler
invariants already proven through AGV2-015C.

## Recovery

If context is compacted/restarted/uncertain:
1. re-read AGENTS.md;
2. re-read project_spec.md;
3. re-read this file;
4. re-read docs/development/agent-handoff.md;
5. inspect git status and recent commits;
6. continue from repository state.

current-task.md is authoritative.

## Before You Start

1. Work on branch v2 and pull latest origin/v2.
2. Read the canonical files plus:
   - docs/architecture/admin-api.md
   - docs/architecture/security.md
   - docs/contracts/domain-model.md
   - docs/contracts/admin-v1-foundation.md
   - src/aethergate/catalog/service.py
   - src/aethergate/egress.py
   - src/aethergate/secrets.py
   - catalog persistence/models/repository code
   - scheduler quota/config locking code
   - current admin authorization/pagination patterns
3. Preserve all identity/OIDC/session/CSRF behavior.
4. Do not modify/delete legacy v1 app/ or frontend/src/.
5. Do not commit unrelated local/untracked files.

## Scope and authorization model

Catalog/routing resources are deployment infrastructure, not project-owned resources.

Required:
- system_admin can read/write catalog resources;
- project_admin/project_viewer cannot enumerate or mutate deployment catalog resources merely because
  their admin credential contains admin:catalog:*;
- authorization must require deployment-scoped authority plus the matching catalog permission;
- both Bearer admin credentials and human OIDC browser sessions use the same service layer;
- browser mutations remain CSRF-protected;
- routers remain thin.

Do not force provider infrastructure into an arbitrary project scope.

## 1. Catalog admin service boundary

Create a dedicated catalog administration service/module rather than putting mutation policy in the
router or generic persistence repository.

The service accepts a typed AdminRequestContext and performs authorization internally.

At minimum centralize:
- deployment-scope authorization;
- validation of parent resources;
- endpoint destination validation;
- route consistency validation;
- concurrency/conflict translation;
- safe audit events.

An internal caller must not bypass RBAC by calling the service directly.

## 2. Provider CRUD

Implement:
- POST /admin/v1/providers
- GET /admin/v1/providers
- GET /admin/v1/providers/{provider_id}
- PATCH /admin/v1/providers/{provider_id}

Fields use the established Provider DTOs.

Requirements:
- no delete; disable with is_active;
- stable opaque IDs;
- deterministic paginated list;
- provider-name uniqueness returns a stable 409/domain conflict, never raw IntegrityError/500;
- provider kind is non-empty opaque adapter configuration unless the existing adapter layer already
  has a stronger supported-kind contract; do not invent an enum that would incorrectly restrict
  LiteLLM-backed providers.

## 3. SecretRef metadata API

The production secret backend remains deferred. Do not add plaintext provider secrets to PostgreSQL.

Expose only the metadata reference needed to configure provider accounts.

Add transport-independent DTOs and endpoints:
- POST /admin/v1/secret-refs
- GET /admin/v1/secret-refs
- GET /admin/v1/secret-refs/{id}

At minimum:
- id;
- name;
- created_at.

Rules:
- system_admin only;
- metadata only;
- no raw secret value accepted or returned;
- no environment-variable value exposed;
- EnvSecretResolver remains dev/test convenience, explicitly not production secret storage;
- duplicate names handled deterministically if names are required unique. If DB currently does not
  make them unique, decide and document whether duplicate names are meaningful; prefer unique names
  because EnvSecretResolver maps by name.

Do not implement secret delete/rotate in this task.

## 4. Provider account CRUD

Implement:
- POST /admin/v1/provider-accounts
- GET /admin/v1/provider-accounts
- GET /admin/v1/provider-accounts/{id}
- PATCH /admin/v1/provider-accounts/{id}

Requirements:
- provider must exist;
- optional secret_ref_id must resolve to existing metadata;
- provider/account active state remains explicit;
- no secret material on reads/audit/logs;
- PATCH must distinguish omitted from explicit null for external_account_id and secret_ref_id;
- provider_id is immutable in this phase unless there is a strong, tested reason otherwise.

## 5. Endpoint CRUD + egress validation

Implement:
- POST /admin/v1/endpoints
- GET /admin/v1/endpoints
- GET /admin/v1/endpoints/{id}
- PATCH /admin/v1/endpoints/{id}

Requirements:
- parent provider account must exist;
- max_concurrency >= 1;
- create and any base_destination update must pass the same DestinationPolicy used before dispatch;
- URL userinfo remains forbidden;
- metadata/link-local/loopback/reserved destinations remain denied according to the existing egress
  policy;
- explicitly allow authorized private-LAN destinations when the host is allowlisted, consistent with
  the current architecture;
- a rejected destination is a stable 4xx admin validation error;
- endpoint provider_account_id is immutable in this phase;
- changing max_concurrency safely serializes with scheduler endpoint reservation locking; no
  transaction crosses inference.

Do not silently persist a destination that the dispatch path would reject.

## 6. Quota group CRUD

Implement:
- POST /admin/v1/quota-groups
- GET /admin/v1/quota-groups
- GET /admin/v1/quota-groups/{id}
- PATCH /admin/v1/quota-groups/{id}

Requirements:
- provider_account_id exists and is immutable;
- current provider cooldown metadata is runtime scheduler state, not editable through normal group
  PATCH;
- pagination/stable ordering;
- duplicate name conflict stable;
- existing active windows/reservations are not rewritten by metadata edits.

## 7. Quota limit CRUD

Implement:
- POST /admin/v1/quota-limits
- GET /admin/v1/quota-limits, filterable by quota_group_id
- GET /admin/v1/quota-limits/{id}
- PATCH /admin/v1/quota-limits/{id}

Requirements:
- positive limit/window;
- metric immutable after creation unless changing it can be proven safe; preferred immutable;
- quota_group_id immutable;
- enabled can be toggled;
- editing a limit must not mutate historical quota reservations/windows;
- scheduler sees the new config on future admission;
- config mutation locking is compatible with scheduler lock order and does not introduce a known
  deadlock path.

## 8. Model alias CRUD

Implement:
- POST /admin/v1/model-aliases
- GET /admin/v1/model-aliases
- GET /admin/v1/model-aliases/{id}
- PATCH /admin/v1/model-aliases/{id}

Requirements:
- stable public alias name is data, not a path segment;
- alias names remain unique with deterministic 409 on conflict;
- capabilities typed;
- deactivate/reactivate via is_active;
- /v1/models reflects active alias changes immediately for authenticated inference clients.

No delete.

## 9. Route binding CRUD

Implement:
- POST /admin/v1/route-bindings
- GET /admin/v1/route-bindings, filterable by model_alias_id
- GET /admin/v1/route-bindings/{id}
- PATCH /admin/v1/route-bindings/{id}

Use established RouteBinding DTOs, but fix semantics where needed.

Required consistency:
- model alias exists;
- endpoint exists;
- provider account exists;
- endpoint.provider_account_id MUST equal route.provider_account_id;
- optional quota group exists;
- quota_group.provider_account_id MUST equal route.provider_account_id;
- upstream_model is explicit provider-facing configuration and is never inferred from alias name;
- default_output_tokens positive when present;
- PATCH distinguishes omitted vs explicit null for upstream_model/quota_group_id/default_output_tokens
  where clearing is supported.

### One active route per alias

The current runtime resolver rejects multiple active routes as AmbiguousRoute because no routing
selection policy exists.

Admin configuration must therefore prevent creating that ambiguous state.

Add a PostgreSQL partial unique index:
- unique model_alias_id WHERE is_active = true.

Requirements:
- creating a second active route for an alias => deterministic 409/domain conflict;
- an inactive alternate route may exist;
- activating an inactive alternate while another active route exists => deterministic conflict;
- deactivate old then activate new is supported;
- concurrent attempts to activate two routes result in exactly one active winner and a stable loser,
  never an ambiguous runtime state.

Add migration 0014. Do not rewrite migrations 0001–0013.

Migration must fail with a clear diagnostic if an existing DB already contains multiple active routes
for one alias; do not silently choose a winner.

## 10. Route/account consistency hardening

RouteBinding duplicates provider_account_id alongside endpoint_id for scheduling/accounting.

That duplication must be a validated invariant.

At minimum enforce in domain/service/repository paths:
endpoint.provider_account_id == route.provider_account_id.

Also enforce quota-group account consistency.

If practical and clean, add a DB-level backstop. Do not create a brittle cross-table trigger merely to
claim DB enforcement; strong service validation plus deterministic migration/tests is acceptable if
documented.

Direct devseed/catalog setup must use the same validation path or preserve the same invariant.

## 11. PATCH omitted-vs-null semantics

Several established update DTOs currently use optional nullable fields.

For:
- provider_account.external_account_id;
- provider_account.secret_ref_id;
- route_binding.upstream_model;
- route_binding.quota_group_id;
- route_binding.default_output_tokens;
and any similar nullable field introduced here:

Required:
- omitted => leave unchanged;
- explicit null => clear when clearing is allowed;
- non-null => validate and set.

Use Pydantic model_fields_set or an equivalent typed patch representation.
Do not collapse omitted and null.

Add direct HTTP tests.

## 12. Pagination and filtering

Use the existing Page[T] contract:
- default limit 50;
- maximum 200;
- offset >= 0;
- stable deterministic sort.

All list endpoints in this task must be bounded.

Support parent filters where useful:
- provider accounts by provider_id;
- endpoints by provider_account_id;
- quota groups by provider_account_id;
- quota limits by quota_group_id;
- route bindings by model_alias_id / provider_account_id as practical.

Do not implement a generic search language.

## 13. Audit

Emit immutable safe audit events for actual catalog state changes:
- provider created/updated;
- secret-ref metadata created;
- provider account created/updated;
- endpoint created/updated;
- quota group created/updated;
- quota limit created/updated;
- model alias created/updated;
- route binding created/updated.

Requirements:
- actor principal ID;
- stable resource type/ID;
- safe metadata only;
- no raw provider secret;
- no environment-secret value;
- no Authorization/session/CSRF/OIDC secrets;
- idempotent no-op PATCH should not emit a misleading change event.

## 14. Error translation

Introduce stable admin-domain errors for:
- not found;
- validation/invariant failure;
- uniqueness conflict;
- active-route conflict;
- destination denied;
- parent mismatch.

Do not expose SQL text, constraint names, raw DB errors, provider secret names beyond metadata already
authorized for read, or Python trace details.

Conflict races must be translated, not only pre-checked.

## 15. Development seed compatibility

scripts/dev/v2 devseed currently creates catalog configuration directly through internal repository
helpers.

Refactor only as necessary so devseed still works and cannot create route/account inconsistencies or
multiple active routes.

Do not require the development seed tool to authenticate through HTTP.

## Real nomnom Verification — Required

Use dynamic Docker/Podman ports and the existing internal Ollama backend.

### A. Build catalog through admin API

Starting from a clean/bootstrap deployment:
1. authenticate as system_admin;
2. create provider;
3. create provider account;
4. create endpoint pointing to the allowlisted Ollama host;
5. create quota group;
6. create a request quota limit;
7. create model alias;
8. create active route binding to the real upstream model.

No direct DB inserts for these catalog resources in this scenario.

Prove GET/list/read DTOs round-trip correctly and pagination is bounded.

### B. Real inference from API-created catalog

With inference auth bypass disabled:
- create inference credential through existing admin API;
- official OpenAI Python SDK /v1/models includes the active alias;
- non-stream chat succeeds;
- stream chat succeeds;
- persisted route/account/endpoint attribution is correct.

### C. Active-state behavior

Prove live:
- deactivate model alias => absent from /v1/models and inference fails safely;
- reactivate => works;
- deactivate endpoint/provider account/provider independently => inference fails safely without
  hidden fallback;
- reactivate as needed => works again.

### D. Egress validation

Through admin API:
- non-allowlisted destination rejected;
- URL userinfo rejected;
- metadata/link-local destination rejected;
- configured allowlisted internal Ollama destination accepted.

Do not alter global egress policy merely to make the test pass.

### E. Route ambiguity prevention

- create an inactive second route for the same alias;
- attempt to activate it while first route is active => 409;
- concurrent activation attempts on competing inactive routes => exactly one active winner;
- runtime resolver never observes >1 active route.

### F. Route/account mismatch

Create a second provider account/endpoint.
Prove:
- route using endpoint from account A but provider_account_id B is rejected;
- quota group from account A attached to route for B is rejected;
- no invalid row is persisted.

### G. Quota edit live effect

Using an API-created request quota:
- prove configured limit affects admission;
- PATCH limit/window or enabled state;
- prove future admission reflects the new configuration;
- historical reservation/window rows remain coherent.

Keep the scenario short; do not duplicate the entire scheduler quota campaign.

### H. RBAC/CSRF

Prove:
- human system_admin browser session can read and mutate catalog with valid CSRF;
- missing CSRF on browser mutation => 403;
- project_admin cannot read/mutate deployment catalog;
- admin Bearer system_admin can mutate without CSRF.

### I. Regression

Re-run:
- OIDC happy path and cross-browser binding regression;
- real inference SDK non-stream/stream;
- scheduler/quota/accounting/identity full suite;
- migration tests.

## Automated Tests

Add deterministic coverage for at least:

1. service-layer deployment authorization;
2. project_admin denied catalog list/read/write;
3. provider CRUD + pagination;
4. provider duplicate name conflict;
5. secret-ref metadata only/no secret value;
6. provider-account parent validation;
7. provider-account omitted-vs-null patch;
8. endpoint parent validation;
9. endpoint destination allowlist validation;
10. endpoint max_concurrency validation/update;
11. quota-group parent validation;
12. quota-group CRUD/pagination;
13. quota-limit validation and update;
14. quota config edit does not mutate historical rows;
15. model-alias CRUD and unique name conflict;
16. /v1/models active-state reflection;
17. route parent validation;
18. endpoint/provider-account mismatch rejected;
19. quota-group/provider-account mismatch rejected;
20. route omitted-vs-null patch behavior;
21. only one active route per alias;
22. inactive alternate route allowed;
23. concurrent route activation exactly one winner;
24. active-route conflict translated to stable 409;
25. list filters bounded and stable;
26. no-op PATCH does not duplicate audit;
27. audit contains no secret material;
28. browser CSRF required for mutation;
29. Bearer admin mutation CSRF-exempt;
30. migration 0013 -> 0014;
31. empty DB -> latest;
32. existing 364-test baseline remains green or higher;
33. official SDK inference regressions remain green.

## Migration

Add migration 0014 for at least the partial unique active-route index and any clean DB backstops
chosen above.

Do not rewrite 0001–0013.

Verify:
- live 0013 -> 0014;
- empty DB -> latest;
- explicit diagnostic on pre-existing ambiguous active routes;
- existing valid catalog rows preserved.

## Documentation

Update:
- docs/architecture/admin-api.md
- docs/contracts/domain-model.md
- docs/contracts/admin-v1-foundation.md
- docs/development/README.md
- docs/development/agent-handoff.md
- add/update a catalog-routing architecture doc if the repository has an appropriate living document.

Document:
- catalog is deployment-scoped;
- SecretRef is metadata only and production secret backend remains deferred;
- endpoint egress validation;
- one-active-route invariant;
- route endpoint/account/quota consistency;
- PATCH null semantics;
- audit behavior;
- pagination/filter contract.

Do not modify the dated architecture audit.

## Still Deferred

Do not implement:
- plaintext/DB provider-secret storage;
- Vault/KMS production secret backend choice;
- pricing/budget admin HTTP CRUD;
- usage/ledger admin reads;
- queue/operator admin API;
- OAuth device flow;
- React web console;
- import/diff/apply;
- multi-route balancing/failover policy;
- Responses API;
- embeddings;
- v1 migration.

## Verification Before Commit

- full containerized suite;
- migration 0013 -> 0014;
- empty DB -> latest;
- ruff/lint;
- git diff --check;
- secret/token canary scans;
- OIDC/identity regressions;
- legacy v1 untouched;
- dated audit unchanged;
- all required live nomnom scenarios complete.

## Handoff

Include:
- implementation commit(s);
- migration revision;
- admin endpoints added;
- deployment-scope authorization behavior;
- egress validation behavior;
- one-active-route enforcement proof;
- route/account/quota consistency proof;
- PATCH omitted-vs-null semantics;
- secret-ref metadata decision;
- audit behavior;
- live API-created catalog -> real SDK inference proof;
- quota-edit live proof;
- final test count;
- dynamic port/backend/model;
- issues/risks;
- exactly one recommended next step.

Never include raw provider secrets, API keys, bootstrap/OIDC/session/CSRF tokens, prompt/completion
content, or large logs.

## Commit and Push

Suggested primary commit:
`feat(admin): add catalog and routing control plane`

Commit and push all completed work to origin/v2.
Never push directly to main.
Do not ask whether to commit/push.

The task is complete only when every stated automated/live criterion is green and origin/v2 contains
the implementation and updated handoff.
