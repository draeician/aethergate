# AetherGate v2 — Current Task

## Task ID
AGV2-021

## Title
Management UI I — identity, credentials, projects, and catalog administration

## WIP Marker — FIRST LOCAL ACTION

Immediately after entering the repository, before pull/read/implementation work, create:

`.aethergate-wip`

Safe contents:
- task ID: AGV2-021
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
3. all task commits are pushed to origin/v2;
4. origin/v2 is verified to contain the finished work.

## Why This Task Exists

AGV2-020/020V established a verified v2 web-console foundation:
- OIDC browser session + CSRF;
- generated OpenAPI types;
- centralized auth-expiry/error handling;
- live operator dashboard;
- queue/operator workflows;
- real browser RBAC/E2E.

The next product gap is management UI for the already-built admin APIs.

This task adds the first management slice:
- projects;
- principals;
- role assignments;
- service/admin/inference credentials;
- providers;
- provider accounts;
- secret-reference metadata;
- endpoints;
- quota groups/limits;
- model aliases;
- route bindings.

Do not reimplement backend authorization or business rules in React. The web console is a typed client
over /admin/v1 and the backend remains authoritative.

Accounting/budget/usage/ledger management UI is the next task after this one.

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
   - docs/architecture/security.md
   - docs/architecture/admin-api.md
   - docs/architecture/provider-model.md
   - docs/contracts/domain-model.md
   - frontend/src/lib/client.ts
   - frontend/src/context/*
   - frontend/src/components/*
   - frontend/src/pages/*
   - generated OpenAPI schema/client workflow
   - identity/catalog admin routers/services/contracts/tests
3. Preserve all AGV2-014/014V identity/RBAC/non-enumeration invariants.
4. Preserve all AGV2-016/016V catalog/routing/egress invariants.
5. Preserve AGV2-020 browser session/CSRF/security model.
6. Do not store long-lived credentials or raw service/inference keys in browser storage.
7. Do not modify/delete legacy Python v1 app/.
8. Do not commit unrelated local/untracked files.

## 1. Feature-module organization

Reorganize new console code into clear feature modules rather than growing one god file.

Recommended:
- frontend/src/features/identity/
- frontend/src/features/catalog/
- shared table/form/dialog components where genuinely reusable.

Do not perform a cosmetic rewrite of unrelated operator pages.

The generated OpenAPI schema remains the contract source of truth.

## 2. Navigation / routing

Add authenticated routes and role-aware navigation for:

### Identity
- /projects
- /projects/:projectId
- /principals
- /principals/:principalId
- /credentials
- /roles

### Catalog
- /catalog/providers
- /catalog/provider-accounts
- /catalog/endpoints
- /catalog/quotas
- /catalog/models
- /catalog/routes

Equivalent nested route structure is acceptable if coherent.

Rules:
- system_admin sees all deployment-management routes;
- project_admin/project_viewer see only project-scoped identity/resource routes actually authorized by
  the backend;
- project roles do not see deployment catalog controls;
- hiding navigation is usability only, never the authorization boundary.

Old v1 routes must remain gone.

## 3. Projects UI

Implement server-paginated project management.

At minimum:
- list;
- show/detail;
- create where authorized;
- update name/active state where backend supports it;
- clear active/inactive status;
- safe error rendering.

System-admin deployment scope follows backend rules.

For project-scoped callers:
- only show project data returned by the authorized backend;
- never infer or enumerate other project IDs client-side.

## 4. Principals UI

Implement:
- paginated list;
- filter by project/kind/active where backend supports it;
- detail;
- create USER/SERVICE principal where authorized;
- update supported mutable fields;
- activate/deactivate where supported.

Requirements:
- distinguish USER vs SERVICE visibly;
- deactivation warning explains that active sessions/credentials become ineffective through backend
  authorization;
- no role assignment is implied by principal creation;
- no email/username claim is treated as the authorization key.

## 5. Role-assignment UI

Implement:
- list active/revoked assignments;
- filter by project/principal/role where supported;
- grant role;
- revoke role.

Enforce UX consistent with backend:
- project_admin can grant only project roles within its own authorized project;
- project_admin cannot grant system_admin;
- project_viewer cannot mutate;
- system_admin can perform deployment-authorized grants.

Never rely on the UI to prevent escalation: backend 403/validation remains authoritative.

Require an explicit confirmation before revocation.

No "edit role in place" if backend semantics are grant/revoke.

## 6. Credential UI

Use the generic v2 credential API, not legacy "API key" endpoints.

Implement:
- list metadata;
- filters by project/principal/audience/active;
- create;
- rotate;
- revoke;
- show safe metadata.

Distinguish:
- inference audience;
- admin audience.

### One-time raw key handling

Create/rotate may return a raw key once.

Required:
- display only in a dedicated one-time reveal modal/panel;
- explain that it cannot be retrieved again;
- provide explicit Copy button;
- never place raw key in URL;
- never write raw key to localStorage/sessionStorage/IndexedDB;
- never include raw key in application logs, analytics, error reports, or persisted React state beyond
  the transient component lifetime needed for the reveal;
- closing/navigating away destroys the in-memory reveal value;
- do not add "show existing key" behavior because backend cannot recover it.

Add canary browser tests proving the raw key does not survive reload/navigation/storage inspection.

Revocation:
- explicit confirmation;
- idempotent backend semantics preserved.

Rotation:
- only active/lifecycle-valid credentials;
- display the replacement key once;
- old credential behavior follows backend truth.

## 7. Secret-reference metadata UI

The current backend exposes SecretRef metadata, not raw secret values.

Implement:
- list;
- create metadata/ref name if supported;
- select SecretRef when configuring ProviderAccount.

Do NOT:
- invent a plaintext provider-secret input that writes raw values into PostgreSQL;
- display raw provider secrets;
- expose internal secret backend material.

Clearly label this as secret-reference metadata.

Production secret backend selection remains deferred.

## 8. Providers UI

Implement deployment-scoped Provider CRUD:
- list;
- detail;
- create;
- update supported fields;
- activate/deactivate where supported.

Show:
- kind;
- name;
- capabilities;
- active state.

System_admin only.

Project roles:
- route hidden;
- direct route/API access still handled by backend 403.

## 9. Provider accounts UI

Implement:
- list/detail;
- create/update;
- provider relationship;
- secret reference;
- active state;
- safe non-secret account metadata.

Never show raw secret/provider API key.

Relationship selectors use existing paginated endpoints and stable opaque IDs.

## 10. Endpoint UI

Implement catalog Endpoint management separately from runtime operator state.

Show both:
- catalog `is_active`;
- queue runtime `operational_state` where system_admin can access it.

Make the distinction explicit:
- inactive = catalog/config unavailable;
- paused/draining = temporary operator scheduling state.

Implement:
- list/detail;
- create/update supported catalog fields;
- provider-account relationship;
- max_concurrency;
- base URL;
- active state.

Destination validation:
- frontend may pre-check obvious invalid input for UX;
- backend egress DestinationPolicy is authoritative;
- render stable destination-denied errors cleanly;
- never add a bypass.

Pause/drain/resume remain the existing queue/operator actions, not Endpoint PATCH hacks.

## 11. Quota groups / limits UI

Implement:
- QuotaGroup list/detail/create/update;
- QuotaLimit list/detail/create/update where supported;
- request/token metric distinction;
- limit_units;
- window_seconds;
- enabled;
- provider-account relationship;
- current runtime status link/display if available from queue quota-status.

Requirements:
- no generic heuristic token estimator in UI;
- do not imply a token quota will admit requests when backend estimator is fail-closed;
- display runtime cooldown/current committed/reserved values when available, but configuration CRUD and
  runtime status remain conceptually separate.

## 12. Model aliases UI

Implement PublicModelAlias management:
- list/detail;
- create/update;
- active state;
- public alias name;
- supported safe metadata.

Do not expose hidden fallback behavior because v2 has none.

Make it clear the alias is the client-visible OpenAI model name.

## 13. Route bindings UI

Implement:
- list/detail;
- create/update;
- activate/deactivate;
- provider account;
- endpoint;
- model alias;
- quota group optional relationship;
- upstream_model;
- default_output_tokens;
- safe routing metadata.

Required invariants are backend-authoritative and errors must render cleanly:
- endpoint.provider_account_id == route.provider_account_id;
- quota_group provider account == route account;
- at most one active route per alias;
- activation conflict => stable 409;
- invalid egress relationships never saved.

No UI fallback chain editor in this phase; current backend has one active route per alias.

## 14. Forms / PATCH semantics

Respect backend PATCH semantics:
- omitted field means unchanged;
- explicit null only where contract allows clearing;
- do not send every field blindly on edit;
- use generated OpenAPI types.

Prevent accidental type corruption:
- integers remain integers;
- Decimal/money values are not part of this task;
- booleans are explicit;
- stable opaque IDs remain strings.

Render structured AetherGate validation/conflict messages without stack traces.

## 15. Pagination / filtering

All list pages:
- use server-side pagination;
- default reasonable page size;
- Next/Prev or equivalent;
- display total where contract supplies it;
- preserve filter state in component state or URL query params if clean.

Do not fetch all pages client-side just to render one table.

## 16. Shared UX requirements

Add reusable:
- loading state;
- empty state;
- structured error banner;
- confirmation dialog;
- one-time secret reveal component;
- resource-status badge;
- simple form field/error components.

Accessibility:
- labels for form controls;
- keyboard-accessible dialogs/buttons;
- no color-only state indication.

No design-system rewrite required.

## 17. Browser RBAC / security tests

Extend Playwright using the deterministic OIDC subject-control fixture.

Live browser roles:
- system_admin;
- project_admin(A);
- project_viewer(A).

Prove:

### system_admin
- can create/update project;
- can create/update provider/catalog resources;
- can create an admin/inference credential where authorized;
- sees one-time raw key only at create/rotate;
- can revoke credential;
- can grant/revoke role.

### project_admin(A)
- can manage permitted principals/credentials/roles in A;
- cannot enumerate B;
- cannot access deployment catalog routes;
- direct catalog API/mutation => 403.

### project_viewer(A)
- read-only project identity surfaces;
- no mutation controls;
- direct mutation => 403.

### raw key leak canary
After credential create/rotate:
- raw canary key visible only in the reveal;
- not present in URL/history;
- not in localStorage/sessionStorage;
- not present after reload;
- not present in non-secret list/detail API responses;
- not logged by browser console in test.

## 18. Live catalog configuration -> real inference

Using the browser as system_admin:

1. create/configure the catalog resources necessary for a disposable alias entirely through the web UI
   where this task exposes them;
2. provider;
3. provider account;
4. endpoint;
5. model alias;
6. route binding;
7. any needed quota relationship;
8. run official OpenAI Python SDK against the new alias with inference auth bypass false;
9. prove inference succeeds;
10. deactivate an appropriate catalog resource in the UI and prove inference fails safely with no
    fallback;
11. restore it and prove inference succeeds again.

If an existing secret reference must be reused because raw provider-secret provisioning is outside
the current API, do so and document that boundary truthfully. Do not invent secret storage.

## 19. Live credential lifecycle

Through browser UI:
- create inference credential for a disposable principal/project;
- copy one-time value once;
- use it with official SDK;
- rotate;
- old key fails;
- new key succeeds;
- revoke;
- revoked key fails immediately.

Never log/commit the raw keys.

For admin credential lifecycle:
- create/rotate/revoke only if the existing backend authorization and live setup makes it clean;
- at minimum verify audience separation remains enforced.

## 20. OpenAPI generation drift

Run:
- backend OpenAPI generation;
- frontend client/type generation.

Add/check a deterministic drift command so generated schema/types match the current backend.

Do not manually patch generated files.

If backend contracts need no change, generated output should remain stable except for intentional
existing endpoint coverage.

## 21. Automated tests

Add frontend unit/component tests for at least:
1. project pagination;
2. principal list/form;
3. role grant/revoke controls;
4. project_viewer read-only behavior;
5. credential one-time reveal lifecycle;
6. raw key not persisted to storage;
7. provider form;
8. provider-account secret-ref selector;
9. endpoint form and operational-state distinction;
10. destination-denied error rendering;
11. quota group/limit forms;
12. model alias form;
13. route-binding form;
14. active-route 409 rendering;
15. PATCH sends only changed/explicitly-cleared fields;
16. structured 400/403/404/409 handling;
17. generated-client drift check.

Extend Playwright for the live RBAC/lifecycle scenarios above.

## 22. Regression

Run:
- full backend containerized suite; baseline **503**;
- frontend unit/component suite; baseline **27**;
- Playwright; baseline **11**;
- npm build;
- npm lint;
- OpenAPI/client generation drift check;
- ruff check src tests;
- git diff --check;
- secret/token/content canary scan;
- official OpenAI Python SDK non-stream + stream with inference bypass false.

No migration expected.

Do not modify migrations 0001-0016 unless a genuine backend defect requires a narrowly justified
linear 0017.

## 23. Documentation

Update:
- docs/web-console.md;
- docs/development/README.md;
- frontend/README.md;
- docs/development/agent-handoff.md;
- architecture/admin/security/catalog docs only where behavior needs clarification.

Document:
- management routes;
- role visibility vs backend enforcement;
- credential one-time reveal security;
- SecretRef metadata boundary;
- endpoint catalog-vs-runtime state distinction;
- pagination/filter behavior;
- OpenAPI generation workflow;
- browser test workflow.

Do not modify the dated architecture audit.

## Still Deferred

Do not implement:
- accounting/budget/usage/ledger management UI;
- production secret backend;
- raw provider-secret storage UI;
- bulk import/diff/apply;
- observability metric instrumentation;
- Responses API;
- embeddings;
- v1 migration;
- broad visual redesign.

## Handoff

Include:
- implementation commit(s);
- routed pages/features;
- RBAC behavior;
- one-time credential reveal behavior;
- SecretRef boundary;
- catalog form/invariant handling;
- live catalog -> SDK inference proof;
- live credential create/rotate/revoke proof;
- backend test count;
- frontend unit test count;
- Playwright count;
- build/lint/OpenAPI drift results;
- migration/no-migration decision;
- dynamic API/web/IdP ports;
- issues/risks;
- WIP marker lifecycle;
- exactly one recommended next step.

Never include raw API/admin/inference/provider/session/CSRF/OIDC/device secrets, prompt/completion
content, or large logs.

## Commit and Push

Suggested primary commit:
`feat(web): add identity and catalog management UI`

Push all completed commits to origin/v2.
Never push directly to main.
Do not ask whether to commit/push.

The task is complete only when every stated automated/live criterion is green, origin/v2 contains the
finished implementation/tests/docs/handoff, and local `.aethergate-wip` has been removed after final
push verification.
