# AetherGate v2 — Current Task

## Task ID
AGV2-014

## Title
Admin identity CRUD and cross-project authorization hardening

## Ownership
Primary: admin API + identity/auth
Coordinating: contracts, audit, platform/testing

## Why This Task Exists

AGV2-013 established admin service-account authentication, RBAC, one-use bootstrap, and the first
protected /admin/v1 endpoints.

Code review found one concrete information-disclosure gap in the minimal HTTP surface:
credential rotate/revoke currently loads the target credential before project authorization, allowing
a project-scoped admin to distinguish an existing credential in another project (403) from a
nonexistent ID (404).

This task fixes that boundary and completes the remaining identity-management CRUD needed before human
OIDC is introduced.

The current deployment is an internal development network, so developer ergonomics may remain simple,
but production-facing authorization and secret-handling invariants must stay strict.

## Compaction Recovery

If context is compacted, summarized, restarted, or uncertain:
1. re-read AGENTS.md;
2. re-read project_spec.md;
3. re-read this file;
4. re-read docs/development/agent-handoff.md;
5. inspect git status and recent commits;
6. continue from repository state.

current-task.md is authoritative.

## Before You Start

1. Work on branch v2 and pull latest origin/v2.
2. Read the canonical files plus docs/architecture/security.md, docs/architecture/admin-api.md,
   docs/contracts/admin-v1-foundation.md, src/aethergate/api/admin.py,
   src/aethergate/identity/admin.py, src/aethergate/identity/rbac.py, and related persistence/tests.
3. Preserve AGV2-012/013 inference/admin audience separation and one-use bootstrap behavior.
4. Do not modify/delete legacy v1 app/ or frontend/src/.
5. Do not commit unrelated local/untracked files.

## 1. Fix cross-project credential enumeration

Current rotate/revoke behavior:
- look up credential by opaque ID;
- return 404 if absent;
- then authorize using its project;
- return 403 if it exists outside the caller's project authority.

That leaks existence across project boundaries.

Required behavior for project-scoped callers:
- an inaccessible credential ID and a nonexistent credential ID are indistinguishable;
- do not reveal target project, principal, prefix, audience, status, or existence;
- system_admin retains normal deployment-wide behavior.

Use an authorization-aware resource resolver/service rather than duplicating ad-hoc checks in routers.

For credential get/rotate/revoke:
- system_admin may resolve any target;
- project_admin/project_viewer may resolve only resources in authorized project scope;
- unauthorized cross-project resource behaves as not-found or another fixed non-enumerating response;
- write vs read permission remains distinct.

Add deterministic and live tests comparing nonexistent vs cross-project IDs.

## 2. Clarify generic credential administration vs admin-credential lifecycle

The current POST /admin/v1/credentials DTO defaults audience to inference, while the service function is
named create_admin_credential and audit action is admin_credential.created.

Choose and document one coherent model.

Preferred:
- /admin/v1/credentials is the administrative API for managing client credentials of either audience;
- caller still needs admin:credentials:write;
- requested credential audience/scopes are validated by the shared identity service;
- audit actions are neutral, e.g. credential.created / credential.rotated / credential.revoked;
- audit metadata records safe audience value;
- no endpoint/service name implies every managed credential is itself an admin credential.

Alternatively, if the endpoint is intentionally admin-credential-only:
- DTO must require/default admin audience appropriately;
- inference credential provisioning needs a separately named path later.

Do not leave the current mixed semantics.

## 3. Complete project CRUD foundation over /admin/v1

Implement thin HTTP CRUD for projects using existing domain/repository contracts.

At minimum:
- POST /admin/v1/projects
- GET /admin/v1/projects
- GET /admin/v1/projects/{project_id}
- PATCH /admin/v1/projects/{project_id}

Requirements:
- system_admin: all projects;
- project_admin: read/update its authorized project only;
- project_viewer: read its authorized project only;
- project-scoped callers must not enumerate other projects;
- project creation requires deployment-level/system_admin authority;
- disabling a project immediately affects inference/admin credential authentication as already designed;
- stable opaque IDs;
- typed DTOs;
- no direct DB policy in routers;
- deterministic ordering and basic pagination contract if list size can grow; implement a simple
  limit/cursor or limit/offset foundation rather than an unbounded list.

Do not implement project deletion in this phase.

## 4. Complete principal CRUD foundation

Implement:
- POST /admin/v1/projects/{project_id}/principals
- GET /admin/v1/projects/{project_id}/principals
- GET /admin/v1/principals/{principal_id}
- PATCH /admin/v1/principals/{principal_id}

Requirements:
- system_admin deployment-wide;
- project_admin may create/read/update principals in its project;
- project_viewer read-only;
- cross-project principal IDs are non-enumerable to project-scoped callers;
- PrincipalKind remains explicit;
- no human OIDC linkage fields yet unless only neutral placeholders are needed for the next task;
- disabling a principal immediately affects active credentials.

No delete; use inactive state.

## 5. Role-assignment HTTP CRUD

Expose the existing centralized role-assignment service.

Implement at least:
- POST /admin/v1/role-assignments
- GET /admin/v1/role-assignments with authorized filtering
- GET /admin/v1/role-assignments/{id}
- POST /admin/v1/role-assignments/{id}/revoke

Requirements:
- system_admin can grant/revoke system_admin and project roles;
- project_admin may grant/revoke project_admin/project_viewer only within its own project, unless the
  architecture deliberately chooses a stricter rule and documents it;
- project_viewer cannot mutate roles;
- project-scoped caller cannot discover assignments outside its scope;
- no caller may grant a role/scope broader than its own effective authority;
- privilege escalation is prevented centrally in the identity/admin service, not only in router code;
- duplicate active assignment remains idempotent/uniqueness-safe;
- role revocation audit remains immutable and actor-attributed.

Explicitly test:
- project_admin cannot grant system_admin;
- project_admin(A) cannot grant a role for B;
- project_admin cannot use role assignment to escape project scope.

## 6. Harden service-layer authorization boundaries

Do not rely solely on the router passing actor_principal_id.

Administrative mutation services should receive a typed AdminRequestContext or an explicit
authorization decision/token sufficient to prove the actor was authorized.

At minimum harden:
- credential create/rotate/revoke;
- role assignment create/revoke;
- project/principal mutations introduced here.

Goal:
- another internal caller cannot bypass RBAC merely by calling a service method with a forged actor ID;
- router remains thin;
- tests can exercise authorization at the service layer.

Avoid a giant god-service. A dedicated admin identity/service layer is fine.

## 7. Resource-aware authorization semantics

The existing authorize_admin signature accepts resource_type but currently authorization is primarily
project-ID based.

Define a clear resource-scope resolution model:
- project resources authorize against that project;
- principal resources resolve to principal.project_id;
- credential resources resolve to credential.project_id;
- role assignments resolve to their project scope, or deployment for system roles;
- deployment-scoped resources require deployment authority.

Centralize this mapping or pass an already resolved authorization scope from a trusted service.

Do not compare unrelated opaque resource IDs directly to project IDs.

## 8. Audit correctness and idempotency

Audit events should describe actual state transitions.

Review:
- repeated credential revoke;
- repeated role-assignment revoke;
- idempotent duplicate role assignment create.

Preferred semantics:
- one audit event for the state-changing action;
- repeated idempotent no-op does not emit a misleading second revoked/created event;
- or, if attempts are intentionally audited, use a distinct action/result field so state transitions
  are not duplicated.

Rename credential audit actions if generic credential management is chosen in section 2.

Add API/service tests proving no secret/hash/token is present in audit metadata.

## 9. DB/domain invariants

Review migration 0010 and models for RBAC shape.

Add migration 0011 only if required.

At minimum enforce consistently:
- system_admin => deployment scope and no project resource ID;
- project_admin/project_viewer => project scope with project resource ID;
- active equivalent assignment uniqueness;
- valid role/scope enums.

Migration must not rewrite 0010.

If no schema change is needed, document why application/domain invariants plus existing DB constraints
are sufficient.

## 10. Pagination / list safety foundation

The new admin list endpoints must not be unbounded.

Use a simple consistent contract across projects/principals/credentials/role assignments:
- default limit;
- bounded maximum limit;
- stable sort;
- offset or opaque cursor.

This is a foundation, not a full search framework.

Existing credential-list endpoint should be brought into this list contract if practical.

## Real nomnom Verification — Required

Use dynamic Docker/Podman ports. Internal-network convenience is fine for test orchestration, but
authenticate using real admin/inference credentials as appropriate.

### A. Anti-enumeration
With project_admin(A):
- rotate/revoke/get nonexistent credential ID;
- rotate/revoke/get existing credential in B;
- prove externally visible response is indistinguishable in status/code/message where required;
- prove B credential remains untouched.

Repeat for principal and role-assignment IDs introduced here.

### B. Project CRUD
- system_admin creates A and B;
- project_admin(A) reads/updates A;
- project_admin(A) cannot enumerate/read/update B;
- project_viewer(A) reads A but cannot update;
- deactivate/reactivate a test project and prove auth effects.

### C. Principal CRUD
- project_admin(A) creates service principal in A;
- viewer reads it;
- A-scoped caller cannot enumerate B principal;
- deactivation immediately invalidates that principal's credentials.

### D. Role delegation / escalation defense
- system_admin grants project_admin(A);
- project_admin(A) may grant permitted project role in A;
- cannot grant system_admin;
- cannot grant any role for B;
- project_viewer cannot grant/revoke;
- revocation immediately removes authority.

### E. Generic credential lifecycle
If endpoint manages both audiences:
- authorized admin provisions inference key and admin key;
- inference key works only on inference;
- admin key works only on admin;
- rotate/revoke maintain separation;
- list/read never show raw/hash.

### F. Audit
Verify state-changing project/principal/role/credential operations produce actor-attributed safe audit
events without secret material.

### G. Regression
Re-run:
- bootstrap remains one-use after restart;
- admin whoami;
- real SDK inference non-stream/stream;
- queued inference revocation;
- full scheduler/quota/accounting/identity suite;
- migrations.

## Automated Tests

Add deterministic coverage for at least:
1. cross-project credential existence non-enumeration;
2. cross-project principal existence non-enumeration;
3. cross-project role-assignment existence non-enumeration;
4. system_admin project CRUD;
5. project_admin project read/update only;
6. project_viewer read-only;
7. project list pagination/bounds;
8. principal CRUD authorization;
9. principal list pagination/bounds;
10. project deactivation auth impact;
11. principal deactivation auth impact;
12. role-assignment create/list/read/revoke HTTP behavior;
13. project_admin cannot grant system_admin;
14. project_admin cannot grant outside project;
15. viewer cannot mutate roles;
16. service-layer call cannot bypass authorization with forged actor ID;
17. resource-scope resolver maps credential/principal/assignment correctly;
18. credential generic/admin semantic decision enforced;
19. inference/admin audience separation through new credential endpoint;
20. idempotent revoke audit semantics;
21. duplicate assignment audit semantics;
22. credential list bounded/paginated;
23. audit metadata contains no raw key/hash/token;
24. migration 0010 -> 0011 if added;
25. empty DB -> latest;
26. existing 290-test baseline remains green or higher.

## Human OIDC Boundary

Do not implement OIDC yet.

However, the CRUD/RBAC surface created here must be suitable for a human OIDC principal to use in the
next phase without changing authorization semantics.

## Documentation

Update:
- docs/architecture/security.md
- docs/architecture/admin-api.md
- docs/contracts/domain-model.md
- docs/contracts/admin-v1-foundation.md
- docs/development/README.md
- docs/development/agent-handoff.md

Document the anti-enumeration rule, resource-scope resolution, delegation rules, list pagination
contract, and generic credential-management semantics.

Do not modify the dated audit.

## Still Deferred

Do not implement:
- OIDC authorization-code flow;
- browser session/cookies/CSRF;
- OAuth device flow;
- provider/catalog/accounting/queue full admin CRUD beyond what is necessary here;
- CLI;
- React UI;
- Responses API;
- embeddings;
- v1 migration.

## Verification Before Commit

- full containerized test suite;
- migration checks if schema changed;
- ruff/lint;
- git diff --check;
- secret scan;
- authorization/error/log canary checks;
- legacy v1 untouched;
- dated audit unchanged;
- all required live nomnom scenarios complete.

## Handoff

Include:
- implementation commit(s);
- anti-enumeration proof;
- credential-management semantic decision;
- project/principal/role CRUD behavior;
- service-layer authorization model;
- delegation/escalation rules;
- resource-scope resolution;
- pagination contract;
- audit idempotency semantics;
- migration revision or explicit no-migration decision;
- live nomnom evidence;
- final test count;
- dynamic port/backend;
- issues/risks;
- exactly one recommended next step.

Never include raw bootstrap tokens, API keys, Authorization headers, hashes, provider secrets,
prompt/completion bodies, or large logs.

## Commit and Push

Use conventional commits on v2.

Suggested primary commit:
feat(admin): complete identity management and authorization boundaries

Push all completed commits to origin/v2.
Never push directly to main.
Do not ask the user whether to commit/push.

The task is complete only when all stated live and automated verification criteria are met and
origin/v2 contains the implementation and updated handoff.
