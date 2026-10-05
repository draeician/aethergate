# AetherGate v2 — Current Task

## Task ID
AGV2-014V

## Title
Close admin CRUD verification gaps and harden bootstrap/RBAC races

## Ownership
Primary: admin API + identity/auth
Coordinating: platform/testing, audit

## Why This Task Exists

AGV2-014 implemented the intended admin identity CRUD and authorization boundaries, with 306 tests.

Review found three remaining gaps before moving to human OIDC:

1. the task explicitly required re-running real OpenAI SDK non-stream/stream inference after the admin changes, but the handoff deferred it;
2. principal-deactivation and immediate role-revocation effects were not both proven live in the AGV2-014 verification;
3. two production-boundary invariants are still weak:
   - bootstrap runtime config accepts arbitrarily short non-placeholder secrets even though the design requires a high-entropy operator secret;
   - concurrent duplicate role-assignment creation can race past the application pre-check and hit the partial unique index as an unhandled IntegrityError instead of resolving idempotently.

Close these only. Do not expand into OIDC or broader admin CRUD.

## Compaction Recovery

If context is compacted/restarted/uncertain:
1. re-read AGENTS.md;
2. re-read project_spec.md;
3. re-read this file;
4. re-read docs/development/agent-handoff.md;
5. inspect git status and recent commits;
6. continue from repository state.

current-task.md is authoritative.

## Before You Start

- Work on branch v2; pull latest origin/v2.
- Read the four canonical files plus:
  - docs/architecture/security.md
  - docs/architecture/admin-api.md
  - src/aethergate/config.py
  - src/aethergate/identity/admin.py
  - src/aethergate/identity/authorization.py
  - role-assignment persistence/model code
  - tests/test_admin_auth.py
  - tests/test_admin_crud.py
- Preserve all AGV2-014 anti-enumeration and service-layer authorization semantics.
- Do not touch legacy v1 app/ or frontend/src/.
- Do not commit unrelated files.

## 1. Bootstrap secret strength guard

The development helper already generates a strong token using secrets.token_urlsafe(32), but Settings currently accepts any non-placeholder non-empty string.

Add a runtime validation guard suitable for production-facing configuration.

Requirements:
- no built-in/default bootstrap token;
- empty/unset still disables bootstrap;
- reject obvious placeholders as today;
- reject trivially short configured bootstrap tokens;
- preferred minimum: at least 32 characters of operator-provided secret material;
- do not claim a length check proves entropy; docs must still require randomly generated high-entropy secrets;
- existing dev helper remains valid;
- token remains SecretStr and never logged.

Add tests for:
- unset accepted;
- generated dev-format token accepted;
- short arbitrary token rejected;
- placeholder rejected;
- token value does not appear in validation/log output.

No migration should be needed.

## 2. Concurrent duplicate role assignment must be idempotent

Current flow:
- check for active equivalent assignment;
- then insert;
- DB partial unique index prevents duplicate rows.

Under concurrent requests, both can pass the pre-check and one can receive IntegrityError.

Required behavior:
- concurrent creation of the same effective active assignment never returns a raw DB/500 error;
- exactly one active assignment row exists;
- callers receive either the same canonical assignment or another documented deterministic idempotent success result;
- exactly one role_assignment.created audit event exists;
- no duplicate history/event is created merely because of the race;
- unrelated role-assignment conflicts still surface correctly.

Preferred implementation:
- use a race-safe insert/fetch pattern or savepoint/IntegrityError translation narrowly around the active-equivalent uniqueness constraint;
- do not swallow unrelated IntegrityError values.

Add a true concurrent DB-gated regression test with at least two sessions/tasks.

## 3. Live principal-deactivation proof

On nomnom with real admin credentials:

- create a project/principal/admin credential;
- verify protected admin access works;
- deactivate the principal through the admin API;
- next protected admin request using that credential must fail immediately;
- reactivate only for cleanup if needed;
- no container restart required.

Record only opaque IDs/status, not raw keys.

## 4. Live role-revocation proof

On nomnom:

- create a project-scoped admin credential with an active role assignment;
- verify an allowed protected action works;
- revoke the role assignment while credential remains otherwise valid;
- next protected request requiring that role must fail immediately;
- credential metadata remains active;
- no stale role authority survives a new HTTP request.

This must exercise the real API/container/DB path, not only an in-process test.

## 5. Real inference regression — required now

The AGV2-014 task required this but it was deferred.

Use scripts/dev/v2 devseed or equivalent dev-safe setup as needed.

With a real inference-audience credential and AETHERGATE_ALLOW_INFERENCE_AUTH_BYPASS=false:

- official OpenAI Python SDK non-stream chat completion succeeds;
- official SDK streaming chat completion succeeds;
- admin credential still fails on /v1/models and /v1/chat/completions;
- inference credential still fails on /admin/v1/whoami;
- persisted request attribution remains correct.

Use the current dynamic AetherGate port and current backend/model; do not assume the prior port.

## 6. Regression

Re-run:
- full containerized test suite;
- admin anti-enumeration tests;
- project/principal CRUD;
- role delegation/escalation defense;
- bootstrap one-use/concurrent behavior;
- queued inference credential revocation;
- scheduler/quota/accounting suites;
- migration head / empty-to-latest checks.

Baseline before this task: 306 passing tests.

## Automated Tests

Add deterministic coverage for at least:
1. short bootstrap token rejected;
2. strong generated bootstrap token accepted;
3. bootstrap secret absent from validation/log output;
4. concurrent duplicate role assignment yields one active assignment;
5. concurrent duplicate assignment yields one created audit event;
6. concurrent duplicate assignment does not expose IntegrityError/500;
7. unrelated role-assignment integrity failure is not silently swallowed;
8. principal deactivation still invalidates admin authentication;
9. role revocation removes authority on next request;
10. existing 306-test baseline remains green or higher.

## Migration

Do not add a migration unless absolutely required.

Do not modify migrations 0001 through 0011.

If no migration is needed, say so explicitly in the handoff.

## Documentation

Update:
- docs/architecture/security.md
- docs/architecture/admin-api.md if concurrency semantics need mention
- docs/development/README.md
- docs/development/agent-handoff.md

Document:
- bootstrap minimum-length guard is only a floor, not an entropy proof;
- dev helper generates strong random bootstrap material;
- duplicate role grant is concurrency-safe/idempotent;
- live principal/role revocation and real SDK inference are now complete.

Do not modify the dated audit.

## Verification Before Commit

- full containerized suite;
- ruff/lint;
- git diff --check;
- secret scan;
- auth/bootstrap log canary checks;
- migration head checks;
- legacy v1 untouched;
- dated audit unchanged;
- all live scenarios above complete.

## Handoff

Include:
- implementation/verification commit(s);
- bootstrap strength rule;
- concurrent role-assignment behavior;
- live principal-deactivation result;
- live role-revocation result;
- real SDK non-stream/stream result;
- audience separation result;
- final test count;
- dynamic AetherGate port;
- current backend/model;
- explicit migration/no-migration decision;
- issues/risks;
- exactly one recommended next step.

Never include raw bootstrap tokens, API keys, Authorization headers, hashes, provider secrets, prompt/completion bodies, or large logs.

## Commit and Push

Use conventional commits on v2.

Suggested primary commit:
fix(admin): close identity verification and RBAC race gaps

Push all completed commits to origin/v2.
Never push directly to main.
Do not ask whether to commit or push.

The task is complete only when all stated live and automated criteria are satisfied and origin/v2 contains the implementation and updated handoff.
