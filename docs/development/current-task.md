# AetherGate v2 — Current Task

## Task ID
AGV2-007

## Title
Close residual scheduler invariants before quota expansion

## Ownership
Primary: scheduler/queueing  
Coordinating: contracts, platform/testing, OpenAI API surface

## Why This Task Exists

AGV2-006 materially hardened scheduler phase 1 and passed the required nomnom failure-mode tests.
A code-level review still found a small set of correctness gaps that should be fixed before adding
RPM/TPM/shared-account quotas.

This is a narrow invariant-closure task. Do not expand into new quota features yet.

## Before You Start

1. Work on branch `v2`.
2. Run `git pull --ff-only origin v2`.
3. Read:
   - `AGENTS.md`
   - `project_spec.md`
   - `docs/development/agent-handoff.md`
   - `docs/development/current-task.md`
   - `docs/architecture/scheduler.md`
   - `src/aethergate/scheduler/repository.py`
   - `src/aethergate/scheduler/service.py`
   - `src/aethergate/contracts/admin_v1.py`
   - `src/aethergate/api/openai_chat.py`
   - `src/aethergate/persistence/models.py`
4. Preserve the successful AGV2-004 through AGV2-006 behavior.
5. Do not modify/delete legacy v1 `app/` or `frontend/src/`.
6. Do not commit unrelated local/untracked files.

## Required Fixes

### 1. Lease renewal must be all-or-nothing for request + attempt + worker identity

Current `renew_lease` updates the request row, then updates the execution-attempt row but does not
check whether the attempt update succeeded. It also does not verify the worker identity explicitly.

Fix this invariant:

A heartbeat is successful only if the same current owner still owns both the request and the active
execution attempt.

Requirements:

- match `request_id`;
- match `attempt_id`;
- match `worker_id`;
- match `fencing_token`;
- require active dispatched/streaming state;
- update request and attempt lease expiries in one transaction;
- if either expected row cannot be renewed, the heartbeat operation must fail atomically;
- do not leave one row renewed and the other stale;
- a stale worker must never keep one side alive.

Update `ClaimedWork`/call signatures as needed so worker identity is available to renewal.

Add tests where:
- request fence is stale;
- attempt fence is stale;
- request worker_id differs;
- attempt worker_id differs;
- attempt row is missing/terminal;
- no partial lease extension is committed in any failed case.

### 2. Coupled scheduler state transitions must not partially succeed

Review and harden all transitions that logically require both request + attempt state to move
together.

At minimum inspect:

- reserved -> dispatched;
- heartbeat renewal;
- successful terminal settlement;
- failed/cancelled terminal settlement;
- outcome_unknown recovery.

Current code ignores the result of `mark_attempt_dispatched` and `settle_attempt` after successfully
updating the request.

Required invariant:

- if a transition requires both request and attempt, either both expected rows transition in one DB
  transaction or the transaction fails/rolls back;
- never contact upstream unless dispatch intent is durably recorded on both request and attempt;
- never release a reservation after only one side of terminal settlement succeeded;
- unexpected rowcount/state mismatch must be surfaced as a scheduler invariant error and logged
  without content/secrets;
- do not silently repair by guessing.

Add an explicit internal `SchedulerInvariantError` or equivalent.

Add fault-injection tests that deliberately make the attempt/request state inconsistent before each
transition and prove no partial commit or premature capacity release occurs.

### 3. Enforce total request lifetime independently of the API waiter

AGV2-006 correctly aligned the API wait with persisted `expires_at`, but execution lifetime is
still primarily driven by the API-side waiter calling cancellation.

The scheduler/worker must independently enforce the persisted total-lifetime deadline.

Requirements:

- queued requests: existing queue-wait/total expiry remains correct;
- reserved pre-dispatch requests past total lifetime must not dispatch;
- dispatched/streaming requests reaching `expires_at` must trigger cancellation/termination handling
  even if the API process disappeared;
- worker heartbeat must not keep an already-expired execution alive indefinitely;
- where upstream cancellation is known and successful -> terminal expired/cancelled and release;
- where execution may still be running and termination outcome is ambiguous -> `outcome_unknown`
  and keep reservation;
- no automatic retry;
- API presence is not required for deadline enforcement.

The worker should enforce this using durable state/deadline, not a process-local timer as the source
of truth.

Add tests for:
- API process/waiter absent;
- execution runs beyond `expires_at`;
- stream runs beyond `expires_at`;
- expired request does not keep receiving lease renewal forever;
- conservative outcome if upstream task cannot be proven stopped.

### 4. Endpoint `max_concurrency` must exist in admin v1 contracts

AGV2-006 added domain + DB + devseed validation, but the initial admin DTOs still omit the field.

Update:

- `EndpointCreate`
- `EndpointRead`
- `EndpointUpdate`

Requirements:

- create has an explicit positive default or required value consistent with the domain model;
- read always exposes effective configured physical concurrency;
- update accepts positive integer only;
- 0 and negative values rejected by Pydantic before persistence;
- docs updated so future web/CLI/admin API clients can configure endpoint capacity through the shared
  contract.

Do not implement the admin HTTP API yet.

### 5. Reconciliation semantics for `succeeded` must be coherent

Current reconciliation can mark an `outcome_unknown` request as `succeeded` without supplying a
recoverable result payload. That can leave a logically succeeded request with no completion/usage
data.

Choose and document one safe behavior:

Preferred:
- phase-1 reconciliation allows only `failed` or `cancelled` unless the operator supplies a
  validated reconstructed result through an explicit typed/internal path.

If supporting `succeeded`:
- require complete validated result metadata/content;
- encrypt the reconstructed result at rest;
- never create a `succeeded` request that the normal result reader cannot interpret.

Do not invent result content.

Update CLI help/tests accordingly.

### 6. Streaming terminal behavior must be documented precisely

AGV2-006 correctly stopped synthesizing `finish_reason="stop"` for failed streams, but still emits
the SSE terminal marker `[DONE]` for terminal failures.

Determine and document the intended Chat Completions compatibility behavior using the pinned OpenAI
contract and official SDK behavior.

Requirements:

- do not claim a failed upstream generation completed successfully;
- do not synthesize a normal finish reason for failure;
- preserve official SDK interoperability;
- document whether `[DONE]` is a transport termination marker vs successful model completion;
- test the chosen behavior explicitly.

No custom queue-status SSE events.

## Migrations

Do not rewrite `0001` through `0004`.

A new migration is required only if a persisted schema change is necessary for this task.
Do not create a migration just to change Python behavior/contracts.

If a migration is added, verify:
- existing nomnom DB -> latest;
- empty DB -> latest;
- downgrade where practical.

## Real Nomnom Verification

Use the existing Docker/Podman workflow and dynamic API host port.

Required real checks:

1. official OpenAI SDK non-stream still succeeds;
2. official OpenAI SDK stream still succeeds;
3. six-request/two-slot invariant still succeeds;
4. long-running request survives multiple healthy heartbeat periods;
5. force an attempt-side ownership mismatch in a controlled DB test environment and prove no partial
   heartbeat/settlement transition commits;
6. run a request beyond a deliberately short total-lifetime deadline with the API waiter absent or
   disconnected and prove scheduler/worker enforcement still occurs;
7. confirm endpoint capacity can be represented/validated via the admin v1 DTO contract.

Do not alter or restart the external inference backend except normal read-only/probe usage.

## Automated Tests

Add deterministic coverage for at least:

1. request+attempt heartbeat is atomic;
2. worker_id mismatch rejects renewal;
3. attempt mismatch rejects renewal with no partial request update;
4. reserved->dispatched is atomic;
5. terminal settlement is atomic;
6. reservation not released after failed partial settlement;
7. worker enforces total lifetime without API waiter;
8. heartbeat stops extending expired executions;
9. conservative outcome for ambiguous expiry;
10. EndpointCreate max_concurrency validation;
11. EndpointRead exposes max_concurrency;
12. EndpointUpdate rejects zero/negative;
13. reconciliation cannot create unreadable succeeded result;
14. failed stream transport termination behavior;
15. original AGV2-006 recovery tests remain green;
16. six-request/two-slot tests remain green;
17. official SDK scheduled non-stream/stream tests remain green.

## Still Deferred

Do not implement yet:

- provider RPM/RPD;
- TPM/token reservation;
- shared account quota windows;
- project budgets;
- retry/cooldown orchestration;
- `/v1/responses`;
- embeddings;
- full auth/OIDC;
- admin HTTP CRUD API;
- web UI;
- accounting settlement;
- v1 SQLite migration.

## Documentation

Update as needed:

- `docs/architecture/scheduler.md`
- `docs/contracts/admin-v1-foundation.md`
- `docs/contracts/domain-model.md`
- `docs/development/README.md`
- `docs/development/agent-handoff.md`

Do not modify the dated architecture audit.

## Verification Before Commit

- full test suite;
- containerized tests;
- migrations if changed;
- ruff/lint;
- `git diff --check`;
- secret scan;
- legacy `app/` and `frontend/src/` untouched;
- dated audit unchanged;
- required nomnom verification complete.

## Handoff

Update `docs/development/agent-handoff.md` with concise evidence for:

- implementation commit(s);
- atomic heartbeat proof;
- atomic dispatch/settlement proof;
- independent total-lifetime enforcement;
- admin DTO capacity contract;
- reconciliation semantics;
- stream termination decision;
- test count;
- dynamic AetherGate test port;
- backend/model;
- issues/risks;
- exactly one recommended next step.

No prompt/completion bodies, credentials, keys, or large logs.

## Commit and Push

Use conventional commits on branch `v2`.

Suggested primary commit:

`fix(scheduler): close residual ownership and deadline invariants`

A handoff-only follow-up commit is allowed.

**Push all completed commits to `origin/v2`.**

Never push directly to `main`.

The task is complete only when `origin/v2` contains the changes and updated handoff and all required
nomnom checks pass or a genuine blocker is documented with non-sensitive evidence.
