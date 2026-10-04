# AetherGate v2 — Current Task

## Task ID
AGV2-012V

## Title
Complete inference-identity live verification and harden credential lifecycle invariants

## Ownership
Primary: identity/auth  
Coordinating: scheduler/queueing, contracts, platform/testing

## Why This Task Exists

AGV2-012 implemented scoped inference API credentials and pushed the real auth path successfully.

The code review found two categories of unfinished work:

1. several scenarios explicitly required as live nomnom verification were only covered by deterministic tests;
2. the credential lifecycle service still permits invalid or security-surprising state transitions.

Close these gaps before moving into admin HTTP APIs or human OIDC/session work.

## Compaction Recovery

If context is compacted, summarized, restarted, or you become uncertain:

1. re-read `AGENTS.md`;
2. re-read `project_spec.md`;
3. re-read this file;
4. re-read `docs/development/agent-handoff.md`;
5. inspect `git status` and recent history;
6. continue from repository state.

`docs/development/current-task.md` is the active assignment. The handoff describes the previous completed state.

Do not ask the user whether to commit, push, continue, or stop when this task already specifies those actions.

## Before You Start

1. Work on branch `v2`.
2. Pull latest `origin/v2`.
3. Read:
   - `AGENTS.md`
   - `project_spec.md`
   - `docs/development/current-task.md`
   - `docs/development/agent-handoff.md`
   - `docs/architecture/security.md`
   - `src/aethergate/identity/service.py`
   - `src/aethergate/identity/keys.py`
   - identity persistence/repository code
   - identity/auth tests
4. Preserve all AGV2-012 real Bearer-auth behavior.
5. Do not modify/delete legacy v1 `app/` or `frontend/src/`.
6. Do not commit unrelated local/untracked files.

## Required Lifecycle Hardening

### 1. Validate project/principal relationship at credential creation

Current `create_credential` accepts arbitrary existing IDs and relies on authentication later to reject
a mismatch.

That allows invalid credential records to be created.

Before generating/persisting a credential:

- project must exist;
- project must be active;
- principal must exist;
- principal must be active;
- principal.project_id must equal the requested project_id.

If any condition fails, reject creation with an explicit identity/domain error.

Do not generate/return a raw key for a creation request that will be rejected.

Add tests for:
- nonexistent project;
- nonexistent principal;
- principal from another project;
- inactive project;
- inactive principal.

### 2. Harden rotation semantics

Rotation is for an active credential.

Current code can rotate a revoked credential into a fresh valid credential, which can bypass the
meaning of revocation.

Required:

- old credential must exist;
- old credential must be active;
- old credential must not be revoked;
- old credential must not already be expired at rotation time;
- its project and principal must still be valid/active/matching;
- rotation creates the replacement and revokes the old credential atomically;
- rotating an already revoked/expired/inactive credential fails without creating a new credential;
- raw replacement key is returned only after the transaction can succeed.

Preserve audience/scopes/project/principal unless an explicitly supported rotation field says otherwise.

### 3. Make revocation truly idempotent

Repeated revocation must preserve the original revocation event.

Required:

- first revoke sets `revoked_at` and disables credential;
- subsequent revoke is a no-op for state/timestamp;
- original `revoked_at` must not move forward;
- no reactivation path through revoke/rotate helpers.

Add deterministic tests.

### 4. Credential creation audience/scope coherence

For this phase:

- inference audience credentials may carry `inference:invoke`;
- admin audience exists only as a future contract value and must not silently receive the inference
  default scope.

Avoid creating nonsensical `audience=admin, scopes=(inference:invoke,)` by default.

Choose one safe contract:

Preferred:
- default scopes are derived from audience only for supported audience types;
- inference defaults to `inference:invoke`;
- admin has no default scopes until admin scopes are explicitly introduced.

Explicit incompatible scopes should be rejected rather than silently accepted.

Document the rule.

### 5. Optional last-used behavior

`last_used_at` is optional in the architecture. Do not add a write on every inference request if it
would create a hot-row bottleneck.

Either:

- leave it intentionally unset and document that it is deferred; or
- update it with a throttled/best-effort strategy.

Do not perform an unconditional credential-row update for every authenticated request.

## Required Live Nomnom Verification

Run with:

`AETHERGATE_ALLOW_INFERENCE_AUTH_BYPASS=false`

Use the real dynamic-port Docker/Podman workflow and current backend/model.

The following AGV2-012 scenarios were required live but were previously only deterministic. Complete them now.

### A. Expired credential — live

Create a real expired test credential.

Prove:

- API returns 401;
- no request is enqueued;
- no upstream call occurs.

### B. Revocation while queued — live

Create a valid credential and force its request to remain durably queued using controlled endpoint,
quota, or budget capacity.

Then revoke the credential before capacity becomes available.

Prove from durable state:

- worker pre-dispatch authorization revalidation rejects it;
- request terminates with `authorization_failed`;
- upstream is never contacted for that request;
- no execution attempt;
- no endpoint reservation;
- no live quota reservation;
- no live budget reservation;
- no price snapshot;
- no usage record;
- no ledger debit.

This must be a live containerized multi-process test, not only an in-process unit/integration test.

### C. Inactive project — live

With an otherwise valid key:

- deactivate project after key creation;
- API returns 401 for a new request;
- reactivate only as needed for test cleanup.

### D. Inactive principal — live

Independently:

- deactivate principal;
- API returns 401.

### E. Audience separation — live

Create an admin-audience credential without inference permission.

Prove it cannot invoke `/v1/chat/completions` or `/v1/models`.

### F. Missing inference scope — live

Create an inference-audience credential lacking `inference:invoke`.

Prove both model and completion endpoints reject it.

### G. Dev-bypass behavior — containerized

In a dev/test container configuration:

- bypass true + no Authorization => seeded dev identity may work;
- bypass true + invalid Authorization => 401, never falls through;
- prod config + bypass true => startup/config validation fails.

### H. Rotation lifecycle — live

Run live rotation again after hardening.

Prove:

- active old key rotates successfully;
- old key fails;
- new key succeeds;
- attempting to rotate the now-revoked old credential fails and creates no third credential;
- original revocation timestamp is preserved on repeated revoke.

### I. Regression

Re-run:

- real SDK non-stream;
- real SDK stream;
- full scheduler/quota/accounting suite;
- six-request/two-slot;
- queued authorization regression;
- migration head checks.

## Automated Tests

Add deterministic coverage for at least:

1. create rejects missing project;
2. create rejects missing principal;
3. create rejects cross-project principal;
4. create rejects inactive project;
5. create rejects inactive principal;
6. valid create still returns raw key once;
7. rotate rejects missing credential;
8. rotate rejects revoked credential;
9. rotate rejects inactive credential;
10. rotate rejects expired credential;
11. rotate rejects now-invalid project;
12. rotate rejects now-invalid principal;
13. failed rotation creates no replacement credential;
14. first revoke sets timestamp;
15. repeated revoke preserves timestamp;
16. admin audience does not inherit inference scope;
17. incompatible audience/scope combination rejected;
18. inference default scope remains `inference:invoke`;
19. queued revocation invariant remains green;
20. auth failure produces no enqueue;
21. normal DTOs remain secret/hash-free;
22. existing 250-test baseline remains green or higher.

## Migration

Do not add a migration unless lifecycle hardening actually requires a schema change.

Do not rewrite `0001` through `0009`.

If a migration is required, use the next linear revision and verify existing DB -> latest and empty DB -> latest.

## Logging / Security

Never log or write into the handoff:

- raw API keys;
- Authorization headers;
- credential hashes/verifiers;
- provider secrets;
- queue encryption key.

Live verification evidence must use opaque credential/project/principal IDs or counts only.

## Documentation

Update:

- `docs/architecture/security.md`;
- `docs/contracts/domain-model.md`;
- `docs/contracts/admin-v1-foundation.md` if lifecycle contract behavior changed;
- `docs/development/README.md`;
- `docs/development/agent-handoff.md`.

Replace the AGV2-012 handoff statements that say expiry/queued-revocation/inactive/audience/scope are
only deterministic once their live verification is complete.

Document rotation eligibility and idempotent revocation semantics.

## Still Deferred

Do not implement:

- admin HTTP CRUD;
- OIDC authorization-code flow;
- browser sessions/cookies/CSRF;
- OAuth device flow;
- admin RBAC;
- CLI;
- React UI;
- Responses API;
- embeddings;
- v1 SQLite migration.

## Verification Before Commit

- full containerized test suite;
- ruff/lint;
- migration head verification;
- `git diff --check`;
- secret scan;
- auth-log canary check;
- legacy v1 untouched;
- dated audit unchanged;
- all live scenarios above completed.

## Handoff

Update `docs/development/agent-handoff.md` with:

- implementation/verification commit(s);
- credential create-validation behavior;
- rotation eligibility behavior;
- idempotent revocation behavior;
- audience/scope default rule;
- live expired-key result;
- live queued-revocation result;
- live inactive project/principal results;
- live audience/scope separation result;
- bypass regression result;
- real SDK non-stream/stream result;
- final test count;
- dynamic AetherGate port;
- current backend/model;
- issues/risks;
- exactly one recommended next step.

No raw credentials, hashes, Authorization headers, prompts/completions, secrets, or large logs.

## Commit and Push

Use conventional commits on branch `v2`.

Suggested primary commit:

`fix(identity): harden credential lifecycle and live authorization checks`

A verification/handoff-only follow-up commit is allowed.

Push all completed commits to `origin/v2`.

Never push directly to `main`.

Do not ask the user whether to commit or push.

The task is complete only when all stated live and automated verification criteria are met and
`origin/v2` contains the updated implementation and handoff.
