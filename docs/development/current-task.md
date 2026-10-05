# AetherGate v2 — Current Task

## Task ID
AGV2-013

## Title
Identity phase 2 — admin service authentication, RBAC, and one-use bootstrap

## Ownership
Primary: identity/auth
Coordinating: admin API, contracts, audit, platform/testing

## Why This Task Exists

The inference data plane now has production-capable scoped API-key authentication.

Before exposing normal /admin/v1 CRUD, the control plane needs its own secure identity and authorization boundary.

This task establishes:
- admin-audience service-account credentials;
- explicit administrative permissions;
- durable role/resource assignments;
- one-use bootstrap initialization;
- named actor attribution for administrative actions.

Human OIDC/browser/device-flow integration remains the next identity phase, but it must plug into the same principal/RBAC model created here.

## Compaction Recovery

If context is compacted, summarized, restarted, or you become uncertain:
1. re-read AGENTS.md;
2. re-read project_spec.md;
3. re-read this file;
4. re-read docs/development/agent-handoff.md;
5. inspect git status and recent commits;
6. continue from repository state.

docs/development/current-task.md is authoritative for the active task.

Do not ask the user whether to commit, push, continue, or stop when this task already specifies those actions.

## Before You Start

1. Work on branch v2.
2. Pull latest origin/v2.
3. Read AGENTS.md, project_spec.md, this file, the handoff, docs/architecture/security.md, docs/architecture/admin-api.md, docs/contracts/admin-v1-foundation.md, and current identity service/repository/domain code.
4. Preserve all AGV2-012/012V inference-auth invariants.
5. Do not modify/delete legacy v1 app/ or frontend/src/.
6. Do not commit unrelated local/untracked files.

## Administrative Credential Audience

Extend the credential/scope model with explicit typed admin permissions at least equivalent to:
- admin:credentials:read
- admin:credentials:write
- admin:projects:read
- admin:projects:write
- admin:principals:read
- admin:principals:write
- admin:catalog:read
- admin:catalog:write
- admin:accounting:read
- admin:accounting:write
- admin:queue:read
- admin:queue:write
- admin:audit:read

Requirements:
- admin-audience credentials may carry only admin permissions;
- inference-audience credentials may carry only inference permissions;
- audience/scope mismatch fails at create/rotate and authentication;
- inference credentials never authorize admin APIs;
- admin credentials never authorize inference APIs merely by existing.

## RBAC Model

Add durable role and role-assignment primitives.

At minimum support:
- system_admin: deployment-wide administrative access;
- project_admin: administrative access restricted to one project;
- project_viewer: read-only administrative access restricted to one project.

Persist role assignments with stable ID, principal ID, role, resource scope type, optional resource ID, created_at, created_by principal ID when available, revoked_at and active state.

Requirements:
- system_admin scope is deployment-wide;
- project_admin and project_viewer require a project resource scope;
- assignments are revocable;
- duplicate active equivalent assignments are prevented;
- authorization policy is centralized, not scattered as router role checks;
- design permits later custom roles without requiring a rewrite, but custom-role CRUD is out of scope.

## Authorization Service

Implement one centralized authorization service that can answer:
authorize_admin(context, permission, resource_type, resource_id)

Requirements:
- credential authentication and RBAC authorization are separate steps;
- principal/project/credential active, revoked, and expired checks remain enforced;
- active role assignments checked on every admin request;
- project-scoped role cannot access another project;
- system_admin can access all projects;
- viewer cannot perform write operations;
- authorization failures do not leak whether a target resource exists;
- routers stay thin.

Return a typed admin request context containing principal ID, API credential ID, credential audience/scopes, and effective role/assignment metadata sufficient for authorization.

## Admin Bearer Authentication

Implement an admin authentication dependency separate from inference auth.

Canonical service-account mechanism:
Authorization: Bearer agk_...

Requirements:
- admin audience required;
- protected endpoints require appropriate admin permission;
- inference credential is never accepted;
- strict Bearer parsing remains shared;
- no query-string credentials;
- safe structured admin error envelope;
- raw key never logged.

## One-Use Bootstrap

Add explicitly configured bootstrap secret such as AETHERGATE_BOOTSTRAP_TOKEN.

Requirements:
- SecretStr;
- no default value;
- empty/obvious placeholder rejected;
- never logged;
- bootstrap token is not a normal long-lived admin credential.

Persist DB-authoritative bootstrap completion state with at least:
- completed flag;
- completed_at;
- initial admin principal ID;
- initial admin credential ID;
- safe metadata only.

Implement a narrow endpoint:
POST /admin/v1/bootstrap

On first successful call, transactionally:
1. verify configured bootstrap secret;
2. verify bootstrap not already completed;
3. establish required initial administrative project/company context;
4. create a service-account principal;
5. grant system_admin role;
6. create admin-audience credential;
7. mark bootstrap completed;
8. return generated raw admin credential exactly once.

Requirements:
- concurrent bootstrap calls cannot both succeed;
- after completion the bootstrap token can never be used again;
- restart does not reopen bootstrap;
- raw bootstrap/admin secrets are never stored in PostgreSQL;
- bootstrap token never acts as an ongoing master key;
- after bootstrap operators should be able to remove the environment token.

If the configured bootstrap secret is compared directly from environment, secrets.compare_digest is acceptable.

## Administrative Audit Events

Create immutable audit events for at least:
- bootstrap completion;
- role assignment create;
- role assignment revoke;
- admin credential create;
- admin credential rotate;
- admin credential revoke.

Record actor principal ID when available, action, resource type, resource ID, occurred_at, and safe metadata only.

Never record raw keys, hashes, Authorization headers, bootstrap token, prompts, or provider secrets.

Bootstrap has no authenticated actor yet; represent this explicitly rather than inventing one.

## Admin Credential Lifecycle

Extend identity services for admin credentials.

Requirements:
- creation requires authorized admin actor;
- rotate/revoke require authorized actor;
- same lifecycle protections as inference credentials;
- normal reads/lists are metadata only;
- raw key returned once;
- no generic self-service privilege escalation;
- project/system scope must be consistent with RBAC.

## Minimal Admin HTTP Surface

Do not build full admin CRUD yet.

Implement only enough protected HTTP surface to prove the model:
- POST /admin/v1/bootstrap
- GET /admin/v1/whoami
- one real protected read/write identity action if useful, preferably credential list/create/revoke.

All future admin CRUD must reuse these dependencies/services.

## Migration

Do not rewrite migrations 0001 through 0009.

Add migration 0010.

Persist:
- role assignments;
- bootstrap state;
- any credential constraints required for admin permissions;
- uniqueness preventing duplicate active equivalent assignments.

Requirements:
- existing nomnom 0009 -> 0010 succeeds;
- empty DB -> latest succeeds;
- no bootstrap/admin credential secret is invented;
- old inference credentials remain valid.

## Real Nomnom Verification — Required

Use dynamic Docker/Podman ports.

### A. Bootstrap
From bootstrap-not-completed state prove:
- missing bootstrap token fails;
- wrong token fails;
- correct token succeeds once;
- response returns one-time admin credential;
- second use fails;
- API/container restart keeps bootstrap closed;
- DB contains only completion state, role assignment, and credential metadata;
- raw bootstrap/admin secrets are absent from DB.

### B. Concurrent bootstrap
Issue concurrent correct bootstrap calls. Exactly one succeeds.

### C. Admin whoami
Generated admin credential succeeds on GET /admin/v1/whoami and returns safe principal/role/audience metadata.
Inference credential is rejected.

### D. Project RBAC
With projects A and B prove:
- system_admin can administer both;
- project_admin(A) can perform allowed read/write for A;
- project_admin(A) cannot access B;
- project_viewer(A) can read A but not write;
- project_viewer(A) cannot access B.

### E. Role revocation
Revoke a role assignment while credential remains active; next protected admin request fails.

### F. Credential lifecycle
Authorized admin can create/list/rotate/revoke an admin credential; old/ revoked keys fail; normal reads contain no secret/hash; audit events exist.

### G. Audience separation
Inference key cannot call admin endpoints.
Admin key cannot call /v1/chat/completions or /v1/models.

### H. Regression
Re-run real inference SDK non-stream/stream with inference credential, queued revocation behavior, scheduler/quota/accounting full suite, and migration tests.

## Automated Tests

Cover at least:
1. typed admin scopes and audience coherence;
2. system_admin deployment-wide authorization;
3. project_admin isolation;
4. project_viewer read-only behavior;
5. inactive/revoked role assignment denied;
6. duplicate active assignment prevented;
7. admin request context resolution;
8. inference credential rejected by admin auth;
9. admin credential rejected by inference auth;
10. missing/expired/revoked admin credential;
11. bootstrap missing/wrong/correct token behavior;
12. bootstrap second use rejected;
13. concurrent bootstrap exactly one success;
14. bootstrap completion survives recreated sessions/restart semantics;
15. bootstrap/admin raw secrets never persisted;
16. whoami response contains safe metadata only;
17. role revocation immediately blocks protected action;
18. admin credential create/rotate/revoke authorization;
19. audit events for bootstrap, roles, credentials;
20. no raw key/bootstrap token in logs/audit;
21. migration 0009 -> 0010;
22. empty DB -> latest;
23. existing 268-test baseline remains green or higher.

## Human Identity Boundary

Do not implement OIDC/browser/device flow yet.

Principal + RoleAssignment must be designed so later human OIDC principals receive the same RBAC assignments without changing authorization semantics.

Do not encode service-account == admin role.

## Documentation

Update docs/architecture/security.md, docs/architecture/admin-api.md, docs/contracts/domain-model.md, docs/contracts/admin-v1-foundation.md, docs/development/README.md, and docs/development/agent-handoff.md.

Document:
- admin vs inference audience separation;
- role/permission mapping;
- bootstrap lifecycle;
- bootstrap is one-use initialization only, never master-key auth;
- role revocation;
- audit events;
- human OIDC/device flow remains deferred.

Do not modify the dated architecture audit.

## Still Deferred

Do not implement:
- OIDC authorization-code flow;
- browser sessions/cookies/CSRF;
- OAuth device flow;
- full admin resource CRUD;
- CLI;
- React UI;
- Responses API;
- embeddings;
- v1 SQLite migration.

## Verification Before Commit

- full containerized test suite;
- migration 0009 -> 0010;
- empty DB -> latest;
- ruff/lint;
- git diff --check;
- secret scan;
- bootstrap/admin auth log canary checks;
- legacy v1 untouched;
- dated audit unchanged;
- all live nomnom scenarios above complete.

## Handoff

Update docs/development/agent-handoff.md with:
- implementation commit(s);
- migration revision;
- role/permission model;
- bootstrap design and one-use proof;
- admin credential audience/scope model;
- role-revocation proof;
- admin/inference audience-separation proof;
- audit behavior;
- real nomnom results;
- final test count;
- dynamic AetherGate port;
- backend/model;
- issues/risks;
- exactly one recommended next step.

Never include raw bootstrap tokens, API keys, Authorization headers, hashes, provider secrets, prompt/completion bodies, or large logs.

## Commit and Push

Use conventional commits on branch v2.

Suggested primary commit:
feat(identity): add admin RBAC and one-use bootstrap

A handoff-only follow-up commit is allowed.

Push all completed commits to origin/v2.
Never push directly to main.
Do not ask the user whether to commit or push.

The task is complete only when all stated live and automated verification criteria are met and origin/v2 contains the implementation and updated handoff.
