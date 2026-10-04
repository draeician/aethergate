# AetherGate v2 — Current Task

## Task ID
AGV2-006

## Title
Scheduler phase 1 correctness hardening before quota expansion

## Ownership
Primary: scheduler/queueing  
Coordinating: platform/testing, provider adapters, OpenAI API surface

## Why This Task Exists

AGV2-005 proved the primary happy path on nomnom:

- six concurrent requests;
- endpoint `max_concurrency=2`;
- four requests durably queued;
- FIFO dispatch for the single-endpoint test;
- two workers sharing one PostgreSQL scheduler;
- queued work survived worker restart;
- streaming traversed the scheduler.

Before adding RPM/TPM/shared-account quotas or budgets, fix the correctness gaps exposed by review of
the actual phase-1 implementation.

Do not expand scheduler scope until these invariants are solid.

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
   - `src/aethergate/worker.py`
   - `src/aethergate/api/openai_chat.py`
   - scheduler tests and migrations
4. Preserve AGV2-005's successful OpenAI/inference behavior.
5. Do not modify/delete legacy v1 `app/` or `frontend/src/`.
6. Do not commit unrelated local/untracked files.

## Required Corrections

### 1. Healthy in-flight work must renew its lease

Current phase-1 behavior sets a lease once before dispatch but does not renew it while a healthy
request is executing.

That can cause a long-running but healthy inference to be marked `outcome_unknown` merely because it
outlived `worker_lease_seconds`.

Implement active lease renewal / heartbeat for dispatched and streaming work.

Requirements:

- renew both request and attempt ownership for the current fencing token;
- renewal must be conditional on worker identity/fence/state;
- stale workers must not renew after ownership changes;
- heartbeat must stop promptly when execution terminates;
- healthy requests lasting multiple lease periods must never become `outcome_unknown`;
- actual worker death/loss after dispatch must still become `outcome_unknown` conservatively;
- no long-lived DB transaction during provider inference.

Choose heartbeat frequency safely below lease duration and validate settings so an invalid heartbeat /
lease relationship fails configuration.

### 2. Recovery must update request and attempt consistently

When post-dispatch ownership expires:

- request -> `outcome_unknown`;
- corresponding execution attempt -> `outcome_unknown`;
- reservation remains active;
- worker/fence history remains auditable;
- no automatic retry;
- no slot release until explicit reconciliation.

Pre-dispatch reclaim must also be concurrency-safe when multiple workers perform recovery.

Use row locking / conditional updates so concurrent recovery loops cannot produce inconsistent attempt,
request, or reservation state.

### 3. Remove cross-endpoint head-of-line blocking

Current scheduler selects the globally oldest queued request. If that request's endpoint is full, the
worker returns `full`, which can prevent eligible requests for other endpoints from dispatching.

Required scheduling rule for this phase:

- FIFO **per endpoint**;
- a saturated endpoint must not block an unrelated endpoint with available capacity;
- within one endpoint, older eligible queued work dispatches before newer work;
- no silent global reordering claim beyond what is required to avoid unrelated-endpoint starvation.

Implement this correctly under multiple workers.

Document the precise ordering rule in `docs/architecture/scheduler.md`.

### 4. Make queue-cap admission atomic

Current admission performs a count followed by insert. Concurrent API requests can race and exceed
`queue_max_requests`.

Enforce the global queue bound transactionally.

Acceptable approaches include:

- a scheduler-control/admission row locked during count+insert;
- PostgreSQL advisory locking with a clearly documented stable lock key;
- another PostgreSQL-transactional design with equivalent correctness.

Do not use a process-local semaphore/counter.

Add a concurrent test proving the configured cap cannot be exceeded.

### 5. Enforce queue-wait deadline separately from total lifetime

`queue_wait_until` is persisted but currently not enforced by queued-request expiry.

Semantics:

- `queue_max_wait_seconds`: maximum time a request may remain undispatched in the queue;
- `queue_total_lifetime_seconds`: end-to-end maximum lifetime including execution/streaming.

Requirements:

- queued requests crossing `queue_wait_until` expire without contacting upstream;
- requests already dispatched are governed by total lifetime, not queue-wait deadline;
- API waiting deadline must correspond to the persisted request deadline, not a fresh independent
  timer that can drift from DB state;
- timeout returned to the client must not leave forgotten queued work that later dispatches.

### 6. Client disconnect/cancellation must reach durable scheduler state

Streaming API generator currently does not reliably request durable cancellation when the client
disconnects. Non-streaming timeout/disconnect also needs explicit behavior.

Requirements:

#### While queued
- disconnect/cancel -> durable cancellation request;
- queued request must not later dispatch;
- cancellation transitions to a terminal state and no capacity reservation is created.

#### Reserved but pre-dispatch
- cancellation may safely cancel/release before upstream contact if ownership/state proves dispatch
  has not begun.

#### Dispatched/streaming
- cancellation requests upstream stream/request closure where supported;
- if upstream cancellation outcome is known, settle cancelled and release capacity;
- if cancellation outcome is ambiguous, use `outcome_unknown` conservatively and keep capacity;
- never replay/retry automatically.

FastAPI/ASGI disconnect handling should be tested; do not rely solely on generator garbage collection.

### 7. Do not synthesize successful stream termination after a scheduler failure

The current stream API can emit a synthetic `finish_reason="stop"` and `[DONE]` even when the
scheduler terminal state is failed/cancelled/expired/outcome_unknown and no finish event was observed.

Correct this.

Requirements:

- successful terminal stream -> standard completion termination;
- pre-first-byte failure may return an appropriate HTTP/OpenAI structured error when still possible;
- failure after stream headers/content started must terminate in the safest compatible way without
  pretending successful model completion;
- do not invent `finish_reason=stop` for failed/cancelled/outcome_unknown executions;
- document the chosen behavior and test official OpenAI SDK handling where practical.

### 8. Endpoint concurrency must be constrained positive at every layer

`Endpoint.max_concurrency` must be >= 1.

Enforce it:

- domain/admin DTO validation;
- persistence/database CHECK constraint via new migration (do not rewrite `0003`);
- dev seed validation;
- tests.

Add migration `0004` or later.

### 9. Worker shutdown must be graceful

The worker currently installs signal handlers that call `loop.stop()` while `run_until_complete()`
is active.

Replace this with cooperative graceful shutdown.

Requirements:

- SIGTERM/SIGINT stops new claiming;
- active execution is not abruptly abandoned by event-loop teardown;
- bounded graceful shutdown behavior is documented;
- if forced shutdown occurs during dispatched work, lease/recovery semantics remain conservative;
- normal Docker/Podman stop does not produce event-loop-stopped runtime errors.

## Explicit Reconciliation Path for outcome_unknown

Add a development/internal reconciliation service/CLI operation sufficient to test phase-1 recovery.

It must require an explicit operator action to:

- inspect an `outcome_unknown` request without content;
- mark it reconciled as failed/cancelled/succeeded only when an operator supplies the disposition;
- release the held reservation only as part of explicit reconciliation;
- record the reconciliation action in durable scheduler metadata/audit-friendly fields.

Do not build the full admin API/UI yet.

Do not allow a generic "release all stuck slots" shortcut.

## Migrations

Do not rewrite `0001`, `0002`, or `0003`.

Add `0004` (and additional revision only if truly needed) for:

- endpoint positive-concurrency CHECK;
- any scheduler-control/admission state required for atomic queue caps;
- reconciliation/audit metadata required by this task.

Upgrade from an existing `0003` nomnom database must succeed without data loss.

Also verify migration from empty database through latest head.

## Real Nomnom Verification — Required

Use the existing Docker/Podman workflow and dynamically allocated host port.

Verify current backend/model rather than assuming it is unchanged.

Run real tests that demonstrate:

### Long-running lease heartbeat
- configure a deliberately short lease suitable for testing;
- run inference that lasts longer than at least two lease periods;
- prove the request remains dispatched/streaming and succeeds;
- prove it never transitions to `outcome_unknown` while worker heartbeat is healthy.

### Worker death after dispatch
- begin a long request;
- prove durable dispatch intent exists;
- terminate the owning worker hard enough that heartbeat stops;
- prove another worker/recovery marks it `outcome_unknown`;
- prove its reservation remains active;
- prove it is not retried;
- explicitly reconcile it and prove the slot is then released.

Do not kill/reconfigure the external inference backend.

### Cross-endpoint scheduling
Create two independently configured endpoint records using the available backend if necessary:

- endpoint A max_concurrency=1;
- endpoint B max_concurrency=1;
- hold A busy and queue another request for A;
- enqueue an eligible request for B;
- prove B dispatches without waiting for A's queued request;
- prove FIFO remains correct within A.

### Queue cap race
Using concurrent API admissions against a small configured queue maximum:

- prove committed queued count never exceeds the configured maximum;
- excess clients receive the expected overload error.

### Queue wait expiry
- hold endpoint capacity busy;
- enqueue a request with short queue-wait deadline;
- prove it expires without an upstream attempt;
- prove it never dispatches afterward.

### Client disconnect
At minimum verify a queued streaming request disconnected before dispatch becomes cancelled and never
hits upstream.

If practical, also verify cancellation during active streaming.

## Automated Tests

Add deterministic coverage for at least:

1. healthy lease heartbeat across multiple lease periods;
2. stale fence cannot renew lease;
3. dead-worker post-dispatch expiry -> request + attempt outcome_unknown;
4. outcome_unknown reservation remains held;
5. explicit reconciliation releases exactly the intended reservation;
6. concurrent recovery workers are idempotent/safe;
7. per-endpoint FIFO;
8. saturated endpoint does not block unrelated endpoint;
9. concurrent queue-cap admission cannot exceed configured cap;
10. queue_wait_until expiry before dispatch;
11. API timeout/cancel does not leave later-dispatchable queued work;
12. queued client disconnect cancellation;
13. active-stream cancellation behavior;
14. failed stream does not synthesize successful stop;
15. max_concurrency=0/-1 rejected by DTO/domain/dev seed;
16. database CHECK rejects invalid endpoint capacity;
17. migration 0003 -> 0004;
18. empty DB -> latest migration;
19. graceful SIGTERM worker shutdown;
20. original six-request/two-slot invariant still passes;
21. scheduled official OpenAI SDK non-stream remains green;
22. scheduled official OpenAI SDK streaming remains green.

## Preserve Existing Scope Boundaries

Still deferred:

- provider RPM/RPD windows;
- provider/account TPM/token reservations;
- shared quota groups beyond endpoint concurrency;
- project budgets;
- retry/cooldown orchestration;
- fairness policies beyond FIFO per endpoint;
- full API-key/OIDC auth;
- `/v1/responses`;
- embeddings;
- admin CRUD API;
- React/UI;
- accounting settlement;
- v1 SQLite migration.

Do not add these in this task.

## Documentation

Update:

- `docs/architecture/scheduler.md`
- `docs/development/README.md`
- `docs/contracts/domain-model.md` if schema concepts changed
- `docs/development/agent-handoff.md`

Correct any handoff starting-commit metadata from the prior task if necessary, but do not rewrite
historical commits.

## Verification Before Commit

- full test suite;
- containerized tests;
- migration 0003 -> latest;
- empty DB -> latest;
- ruff/lint;
- `git diff --check`;
- secret scan;
- legacy `app/` and `frontend/src/` untouched;
- dated audit unchanged;
- all required nomnom failure/concurrency scenarios completed.

## Handoff

Update `docs/development/agent-handoff.md` with concise evidence for:

- implementation commit(s);
- lease heartbeat behavior;
- dead-worker outcome_unknown test;
- explicit reconciliation;
- cross-endpoint no-HOL test;
- atomic queue-cap race;
- queue-wait expiry;
- disconnect/cancellation behavior;
- graceful worker stop;
- latest migration;
- test counts;
- actual dynamic AetherGate test port;
- current backend/model;
- issues/risks;
- exactly one recommended next step.

Do not include prompts, completions, secrets, encryption keys, or large logs.

## Commit and Push

Use conventional commits on branch `v2`.

Suggested primary commit:

`fix(scheduler): harden leases admission and cancellation`

A handoff-only follow-up commit is allowed.

**Push all completed commits to `origin/v2`.**

Never push directly to `main`.

The task is complete only when the remote `origin/v2` contains the changes and updated handoff, and
the required nomnom failure-mode tests pass or a genuine environmental blocker is documented with
non-sensitive evidence.
