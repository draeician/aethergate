# AetherGate v2 — Current Task

## Task ID
AGV2-012

## Title
Identity phase 1 — scoped inference API credentials and durable authorization context

## Ownership
Primary: identity/auth  
Coordinating: contracts, scheduler/queueing, admin API foundation, platform/testing

## Why This Task Exists

The inference, scheduler, quota, and accounting core is now live-verified and hardened.

The OpenAI data plane still relies on an explicit development-only authentication bypass. Replace that
with the first production-capable identity boundary before exposing the administrative API.

This task implements machine/service authentication for the inference surface only.

Human OIDC login, browser sessions, OAuth device flow, and full admin RBAC are the next identity/admin
phase and remain out of scope here.

## Compaction Recovery

If context is compacted, summarized, restarted, or you become uncertain what remains:

1. re-read `AGENTS.md`;
2. re-read `project_spec.md`;
3. re-read this file;
4. re-read `docs/development/agent-handoff.md`;
5. inspect `git status` and recent history;
6. continue from repository state.

Do not ask the user whether to commit, push, continue, or stop when this task already specifies the
required behavior.

## Before You Start

1. Work on branch `v2`.
2. Run `git pull --ff-only origin v2`.
3. Read:
   - `AGENTS.md`
   - `project_spec.md`
   - `docs/development/agent-handoff.md`
   - `docs/development/current-task.md`
   - `docs/architecture/security.md`
   - `docs/architecture/admin-api.md`
   - `docs/contracts/admin-v1-foundation.md`
   - `src/aethergate/api/deps.py`
   - current Project / Principal / ApiCredential domain + persistence code
4. Preserve all scheduler/quota/accounting invariants through AGV2-011.
5. Do not modify/delete legacy v1 `app/` or `frontend/src/`.
6. Do not commit unrelated local/untracked files.

## Goal

A normal OpenAI client should authenticate to AetherGate using an AetherGate-issued Bearer API key:

```text
Authorization: Bearer agk_...
```

A successful credential lookup must yield the durable request context:

- project ID;
- principal ID;
- API credential ID;
- inference audience/scope.

That context is then persisted on the queued request exactly as the scheduler/accounting stack already
expects.

The raw API key must never be stored in PostgreSQL and must never be recoverable from normal reads,
logs, backups, or admin DTOs.

## Credential Model

Refine `ApiCredential` into a one-way-verifiable client credential.

The current `secret_ref_id` concept is appropriate for retrievable upstream provider secrets, but
not for high-entropy client API keys whose plaintext never needs to be recovered.

Use a model with at least:

- stable opaque ApiCredential ID;
- project ID;
- principal ID;
- name;
- key prefix / display prefix;
- cryptographic verifier/hash;
- audience;
- scopes/permissions;
- created_at;
- expires_at;
- revoked_at;
- is_active;
- optional last_used_at if it can be updated safely without creating a hot write bottleneck.

### Raw key format

Generate AetherGate API keys using a recognizable non-secret prefix such as:

`agk_<public-prefix>_<secret-material>`

Requirements:

- at least 256 bits of CSPRNG secret entropy;
- generated with Python's `secrets` module or equivalent;
- public/display prefix contains no meaningful secret entropy;
- raw key returned exactly once at create/rotate boundary;
- only the one-way verifier/hash and safe prefix are persisted.

### Hashing

Because generated credentials are high-entropy random tokens, a one-way SHA-256 verifier is acceptable
for this credential type.

Requirements:

- hash the full raw key using SHA-256;
- never log the raw key;
- never return the hash in API/admin read DTOs;
- exact lookup by hash is acceptable;
- use constant-time comparison anywhere direct comparison is still performed;
- do not reuse password-hashing assumptions for these generated random keys.

Document why this is safe only for high-entropy generated tokens, not user passwords.

## Audience and Scope Separation

Administrative and inference audiences are separate.

For this phase implement at least:

- audience: `inference`;
- scope: `inference:invoke`.

Design enums/contracts so future scopes can include resource/admin permissions without replacing the
credential model.

Requirements:

- inference endpoint rejects credential with wrong audience;
- inference endpoint rejects credential without `inference:invoke`;
- future admin credentials must not become valid for inference merely because they exist;
- do not create a shared master key.

## Project / Principal Semantics

A credential must resolve to an active project and active principal.

Requirements:

- principal belongs to the credential's project;
- service-account principal is the normal machine-auth principal for this task;
- user principal support may exist in the data model but do not invent human login flows here;
- inactive project => deny;
- inactive principal => deny;
- inactive credential => deny;
- expired credential => deny;
- revoked credential => deny;
- missing/unknown credential => deny.

Never silently fall back to the development identity when a Bearer credential is invalid.

## Bearer Parsing

Implement strict standards-aware Bearer parsing.

Requirements:

- missing header => 401;
- wrong scheme => 401;
- malformed Bearer value => 401;
- multiple/ambiguous credential values => reject;
- leading/trailing junk => reject;
- safe OpenAI-compatible structured error response;
- `WWW-Authenticate: Bearer` where appropriate;
- no raw token echoed in error/log output.

Do not accept API keys from query strings.

For compatibility, `Authorization: Bearer <key>` is the canonical inference mechanism.

## Request Context

Replace the development-only dependency chain with a real authenticated request-context resolver.

Use a typed object instead of a bare tuple if practical, e.g.:

- project_id;
- principal_id;
- api_credential_id;
- audience;
- scopes.

Requirements:

- scheduler/admission receives the authenticated context;
- usage/accounting attribution remains correct;
- context cannot be client-overridden in the JSON body;
- context is safe to log only by opaque IDs, never raw token;
- development bypass may still create the same typed RequestContext in dev mode.

## Development Auth Bypass

Keep the bypass only as a deliberate development/test escape hatch.

Requirements:

- `AETHERGATE_ALLOW_INFERENCE_AUTH_BYPASS=true` remains impossible in production;
- when bypass is enabled and no Authorization header is supplied, use the seeded stable development
  identity;
- when an Authorization header is supplied, authenticate it normally even in development;
- an invalid supplied key must fail; it must never fall through to bypass;
- document bypass as development-only.

The real nomnom smoke test must run with bypass **disabled**.

## Credential Lifecycle Service

Implement identity service/repository operations for at least:

### Create
- create service-account credential;
- generate raw secret;
- persist verifier/hash + safe prefix only;
- return raw key once.

### Read/list
- metadata only;
- no secret/hash.

### Revoke
- sets durable revocation state;
- immediately prevents new requests.

### Rotate
Preferred semantics:
- create a new credential/key;
- revoke the old credential atomically;
- return new raw key once;
- preserve project/principal/scope metadata unless explicit approved changes are supplied.

Do not delete old credential metadata on rotation; retain auditability.

No HTTP admin routes required yet. These are service/repository/contract foundations for the next
/admin/v1 phase.

## Revocation and Queued Work

Security requirement: revocation applies to queued work.

This means credential authorization must be revalidated immediately before scheduler reservation /
dispatch, not only at API enqueue time.

Required behavior:

- credential active when enqueued;
- credential revoked while queued;
- worker reaches request later;
- request must not contact upstream;
- request transitions to an explicit safe terminal authorization failure;
- no quota, endpoint, or budget reservation survives;
- no price snapshot/usage/ledger entry is created as dispatched work;
- already-dispatched work is not retroactively duplicated/retried.

Also revalidate:

- project active state;
- principal active state;
- credential expiry;
- required inference audience/scope.

Avoid copying authorization policy into the router and worker separately; use one identity/auth service.

## Key Enumeration / Timing Safety

Credential verification should not leak whether a safe prefix exists more than necessary.

Requirements:

- primary lookup should be based on the full credential hash or an equivalent fixed-cost indexed
  verifier;
- safe prefix is display/debug metadata, not the authentication selector;
- invalid keys return indistinguishable authentication errors;
- no response reveals project/principal existence.

## Migration

Do not rewrite migrations `0001` through `0008`.

Add migration `0009`.

Migrate the v2 identity schema to support the new credential model.

At minimum:

- credential verifier/hash;
- safe prefix;
- audience;
- scopes;
- expires_at;
- revoked_at;
- timestamps as needed;
- appropriate indexes/uniqueness.

The current development credential rows created by earlier tasks must be handled explicitly.

Do not invent recoverable raw keys for existing rows.

Acceptable direction:

- mark/replace old dev-only credentials through the dev-seed workflow;
- migrate old schema metadata safely;
- require newly generated credentials for real auth.

Remove `secret_ref_id` from the client-credential contract if no longer semantically correct, or make
a clearly documented transitional schema change. Do not leave two competing credential-secret models
without explanation.

Migration requirements:

- existing nomnom `0008 -> 0009` succeeds;
- empty DB -> latest succeeds;
- no raw key is created/stored by migration;
- no secret material appears in migration logs.

## Admin Contract Foundations

Update admin-v1 DTO foundations for credential lifecycle.

At minimum:

- ApiCredentialCreate;
- ApiCredentialCreateResult (metadata + one-time raw key);
- ApiCredentialRead;
- ApiCredentialRotateResult;
- ApiCredentialRevoke request/result if needed.

Normal read/list DTOs must never contain:

- raw key;
- key hash/verifier.

Expose safe metadata:

- prefix;
- audience;
- scopes;
- expires_at;
- revoked_at/is_active.

Do not implement the HTTP admin router yet.

## Real Nomnom Verification — Required

Use the normal dynamic-port Docker/Podman workflow.

Verify the current backend/model first; do not assume ports.

Run with:

`AETHERGATE_ALLOW_INFERENCE_AUTH_BYPASS=false`

### A. Real API-key inference

1. create/seed an active project;
2. create a service-account principal;
3. generate an inference credential through the identity service/dev-safe credential tool;
4. capture raw key only in the test process/environment;
5. call AetherGate with the official OpenAI Python SDK using that API key;
6. non-stream completion succeeds;
7. streaming completion succeeds;
8. persisted request/usage attribution matches the credential's project/principal/credential IDs.

Do not write the raw key into the handoff.

### B. Missing/invalid key

Prove:

- missing key => 401;
- random invalid key => 401;
- malformed/wrong Bearer scheme => 401;
- no upstream call is made;
- no scheduler request is enqueued.

### C. Expiry

Create a short-lived/expired test credential.

Prove an expired credential cannot enqueue/dispatch work.

### D. Revocation before request

Revoke credential, then call inference.

Prove immediate rejection.

### E. Revocation while queued

Create a request that remains queued due to controlled endpoint/quota/budget capacity.

Revoke its credential before dispatch.

Prove:

- worker revalidation catches revocation;
- request never contacts upstream;
- it terminates with authorization failure;
- no live quota/budget/endpoint reservation remains;
- no usage/ledger entry created.

### F. Inactive project/principal

Independently deactivate:

- project;
- principal.

Prove both block inference safely.

### G. Audience/scope separation

Create credentials with:

- wrong audience;
- missing `inference:invoke` scope.

Prove both fail even when otherwise active.

### H. Rotation

Rotate an active credential.

Prove:

- old raw key stops working;
- new raw key works;
- old metadata remains as revoked history;
- new secret revealed once;
- no raw key/hash appears in normal reads.

### I. Dev bypass regression

In dev/test mode only:

- bypass enabled + no Authorization => seeded dev identity works;
- bypass enabled + invalid Authorization => 401, no fallback;
- production config + bypass enabled => startup/config validation fails.

### J. Regression

Re-run:

- full scheduler/quota/accounting suite;
- official SDK real non-stream/stream;
- six-request/two-slot;
- budget and shared-quota regressions.

## Automated Tests

Add deterministic coverage for at least:

1. key generation entropy/format;
2. raw key hashes to persisted verifier;
3. raw key never persisted;
4. normal DTO never exposes hash/raw key;
5. valid inference credential resolves typed RequestContext;
6. missing Authorization;
7. malformed Bearer;
8. wrong scheme;
9. invalid key;
10. inactive credential;
11. revoked credential;
12. expired credential;
13. inactive project;
14. inactive principal;
15. principal/project mismatch rejected;
16. wrong audience rejected;
17. missing inference scope rejected;
18. dev bypass absent-header behavior;
19. invalid supplied key never falls through to bypass;
20. production forbids bypass;
21. create returns raw key once;
22. rotate revokes old and creates new;
23. old rotated key fails;
24. revoke is durable/idempotent;
25. queued revocation blocks pre-dispatch;
26. queued auth failure leaves no quota reservation;
27. queued auth failure leaves no budget reservation/snapshot;
28. queued auth failure leaves no endpoint reservation/attempt;
29. attribution reaches InferenceRequest;
30. attribution reaches UsageRecord;
31. migration 0008 -> 0009;
32. empty DB -> latest;
33. existing 220-test baseline remains green;
34. official SDK chat regressions remain green.

## Logging / Security

Never log:

- raw API key;
- Authorization header;
- credential hash;
- provider secrets;
- queue encryption key.

Safe logs may include:

- gateway request ID;
- opaque credential ID;
- opaque principal/project IDs;
- safe public key prefix.

Add canary tests proving auth failures do not emit raw credential material.

## Documentation

Update:

- `docs/architecture/security.md`;
- `docs/contracts/domain-model.md`;
- `docs/contracts/admin-v1-foundation.md`;
- `docs/architecture/scheduler.md` for pre-dispatch revalidation;
- `docs/development/README.md`;
- `docs/development/agent-handoff.md`.

Document explicitly:

- generated high-entropy API keys use one-way SHA-256 verification;
- this is not a password hashing scheme;
- inference and admin audiences remain separate;
- dev bypass is never production auth;
- queued work is authorization-revalidated before dispatch;
- OIDC/browser/device-flow/admin RBAC remain next-phase work.

Do not modify the dated audit.

## Still Deferred

Do not implement in this task:

- OIDC authorization-code flow;
- browser sessions/cookies/CSRF;
- OAuth device flow;
- full admin RBAC;
- admin HTTP CRUD routes;
- CLI;
- React UI;
- principal-level budgets;
- retry/fallback orchestration;
- `/v1/responses`;
- embeddings;
- v1 SQLite migration.

## Verification Before Commit

- full containerized test suite;
- migration `0008 -> 0009`;
- empty DB -> latest;
- ruff/lint;
- `git diff --check`;
- secret scan;
- auth-log canary check;
- legacy `app/` and `frontend/src/` untouched;
- dated audit unchanged;
- all required nomnom auth verification complete with bypass disabled.

## Handoff

Update `docs/development/agent-handoff.md` with concise evidence for:

- implementation commit(s);
- migration revision;
- API key format/verifier design without secret values;
- identity/request-context model;
- audience/scope behavior;
- queued revocation behavior;
- real SDK auth result;
- rotation/revocation result;
- final test count;
- dynamic AetherGate port;
- backend/model;
- issues/risks;
- exactly one recommended next step.

Never include a raw API key, Authorization header, verifier/hash, credential secret, or large logs.

## Commit and Push

Use conventional commits on branch `v2`.

Suggested primary commit:

`feat(identity): add scoped inference API credentials`

A handoff-only follow-up commit is allowed.

**Push all completed commits to `origin/v2`.**

Never push directly to `main`.

Do not ask the user whether to commit or push. This task explicitly requires both.

The task is complete only when all stated verification criteria are met and `origin/v2` contains the
work and updated handoff.
