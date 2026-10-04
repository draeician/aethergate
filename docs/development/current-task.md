# AetherGate v2 — Current Task

## Task ID
AGV2-005

## Title
Durable scheduler phase 1 — PostgreSQL queue, worker ownership, and endpoint concurrency

## Ownership
Primary: scheduler/queueing  
Coordinating: contracts, provider adapters, catalog/routing, platform/testing, identity/auth

## Before You Start

1. Work on branch `v2`.
2. Run `git pull --ff-only origin v2`.
3. Read:
   - `AGENTS.md`
   - `project_spec.md`
   - `docs/development/agent-handoff.md`
   - `docs/development/current-task.md`
   - `docs/architecture/scheduler.md`
   - `docs/architecture/provider-model.md`
   - `docs/architecture/security.md`
   - `docs/contracts/domain-model.md`
   - `src/aethergate/inference/service.py`
   - `src/aethergate/api/openai_chat.py`
4. Preserve the successful AGV2-004 provider-adapter and OpenAI compatibility behavior.
5. Do not modify/delete legacy v1 `app/` or `frontend/src/`.
6. Do not commit unrelated local/untracked files.

## Goal

Replace direct API-process dispatch with the first durable AetherGate scheduling path.

The required real behavior is:

```text
endpoint max_concurrency = 2

six simultaneous valid requests A B C D E F

A + B -> dispatched
C D E F -> queued durably in PostgreSQL

A completes -> C becomes eligible
B completes -> D becomes eligible
...
```

The API must keep ordinary synchronous Chat Completions semantics: the client waits on the normal
request and eventually receives the normal OpenAI-compatible response. Do not invent a `202`
workflow or custom queue-status SSE events.

This is **scheduler phase 1**. It establishes durable queueing, physical endpoint concurrency,
multi-worker ownership, encrypted queued content, and conservative recovery. Provider RPM/TPM,
project budgets, richer quota windows, and retry orchestration are later scheduler phases.

## Architectural Requirements

- PostgreSQL remains the single scheduling/coordination authority.
- No process-local capacity counters.
- Correctness must hold with multiple worker processes.
- No database transaction remains open while waiting for upstream inference.
- Use row-level locking / `FOR UPDATE SKIP LOCKED` where appropriate, but do not treat it as the
  whole scheduler design.
- Capacity is reserved transactionally before dispatch.
- Persist request/attempt/reservation ownership before contacting upstream.
- Use leases + fencing tokens.
- A lease expiration after dispatch does **not** prove upstream stopped.
- Never release an ambiguous in-flight slot and immediately retry as if safe.
- No hidden LiteLLM retries/fallbacks; keep AGV2-004's one upstream call per execution attempt.
- Queue order for this milestone is strict FIFO among requests competing for the same endpoint,
  subject to eligibility. Document head-of-line implications; do not silently reorder.

## Schema / Migrations

Do not rewrite migrations `0001` or `0002`.

Add versioned migration(s) beginning with `0003`.

### Endpoint capacity

Add explicit physical concurrency configuration to Endpoint:

- `max_concurrency`
- positive integer;
- safe migration/backfill behavior;
- development seed support.

Do not pretend aliases create capacity. Capacity belongs to the physical endpoint/deployment.

### Durable request state

Persist scheduler records sufficient to represent:

- inference request;
- execution attempt;
- endpoint-capacity reservation;
- worker ownership / lease;
- fencing token;
- queued/started/completed timestamps;
- deadline / expiry;
- cancellation request;
- terminal outcome;
- encrypted request payload;
- encrypted result / streaming event payload where required by the implementation.

Use the existing typed IDs.

Do not store prompts/messages/completions in plaintext.

### Queue-content encryption

The security direction already requires temporary queue payload storage to be encrypted,
access-controlled, and expiring.

Implement encryption at the application boundary using a key that is **outside PostgreSQL**.

Requirements:

- required runtime key for scheduler operation;
- generated/persisted only in the gitignored nomnom development environment by developer tooling;
- never committed;
- never logged;
- production mode must fail closed when the key is absent/invalid;
- authenticated encryption (for example AES-GCM or Fernet from a maintained crypto library);
- request payloads, final completion content, and persisted stream content/events must not be
  plaintext database columns;
- metadata needed for scheduling may remain unencrypted if it contains no prompt/completion content.

Document key rotation as deferred unless safely straightforward.

## Worker Process

Add a real v2 execution-worker entrypoint and a Compose worker service using the same image.

The API service must no longer directly call the provider adapter for scheduled Chat Completions.

Worker responsibilities:

1. select an eligible queued request;
2. re-resolve/revalidate the route immediately before reservation/dispatch;
3. obtain endpoint capacity transactionally;
4. create/update reservation and execution-attempt ownership with lease + fencing;
5. commit;
6. transition to dispatch intent durably before contacting upstream;
7. perform inference outside the DB transaction;
8. publish encrypted result/events;
9. settle terminal state and release capacity only when outcome is known;
10. wake/poll for the next eligible request.

Multiple worker containers/processes must not double-dispatch the same request.

The initial wake-up mechanism may be bounded PostgreSQL polling. LISTEN/NOTIFY is optional and is not
a second authority.

## Conservative Lease / Recovery Rules

Implement and test at least:

- lease expires while still safely pre-dispatch/reserved -> may be reclaimed/requeued only if the
  persisted state proves upstream was never contacted;
- lease expires after durable dispatch intent -> transition/conservatively surface
  `outcome_unknown`;
- `outcome_unknown` continues to consume the physical endpoint slot until explicitly reconciled;
- no automatic retry of an ambiguous attempt;
- fencing prevents a stale worker from committing terminal state after ownership changed.

Provide a development/admin-safe reconciliation helper if needed for tests, but do not invent the
full admin API in this task.

## API Waiting / Result Delivery

### Non-streaming

`POST /v1/chat/completions` with `stream=false` must:

1. validate/auth/resolve sufficiently to reject invalid requests before queueing;
2. enqueue the encrypted request durably;
3. wait for terminal result up to configured queue/total deadline;
4. return the same OpenAI-compatible completion shape established in AGV2-004.

If the request expires before dispatch, return a safe documented timeout/service error without
contacting upstream.

### Streaming

Do not regress AGV2-004 streaming.

`stream=true` must also pass through scheduler admission before upstream dispatch.

Because worker and API are separate processes, implement a safe cross-process stream/result channel.
For this phase, a PostgreSQL-backed encrypted event sequence is acceptable.

Requirements:

- no prompt/completion text stored plaintext;
- monotonically ordered per-request event sequence;
- API emits standard Chat Completions SSE only;
- no queue-status events;
- first SSE content begins only after dispatch/streaming starts;
- `[DONE]` termination remains correct;
- client disconnect while still queued cancels the queued request;
- client disconnect during streaming requests upstream cancellation/stream close where feasible;
- never retry after any content was delivered.

Document event-retention/cleanup behavior.

## Queue Bounds / Deadlines

Add explicit settings with safe defaults for:

- maximum queued requests;
- per-request maximum queue wait;
- maximum total request lifetime;
- scheduler poll interval;
- worker lease duration.

A full per-principal occupancy policy is deferred until scoped credential authentication lands, but
the schema/design must leave room for it.

When the global queue is full, reject new work with a safe overload error rather than growing
without bound.

## Development Identity During Temporary Auth Bypass

Do not invent the final OIDC/auth system here.

While AGV2-004's explicit development-only auth bypass exists, scheduler records still need stable
attribution.

Use an explicit development-only seeded Project/Principal/ApiCredential context for bypassed requests,
or an equivalent typed RequestContext that is impossible to activate in production.

Do not make scheduler identity fields meaningless/random per request.

Production mode must still reject inference auth bypass.

## Development Seed

Extend the idempotent dev seed so nomnom can configure:

- public alias;
- upstream model;
- endpoint destination;
- upstream allowlist;
- endpoint `max_concurrency`;
- development request identity/context required by the scheduler.

Do not hard-code nomnom values in normal configuration.

## Real Nomnom Concurrency Test — Required

Use the existing discovered nomnom Ollama backend unless current inspection shows that environment
changed. Do not assume its port/model without verifying.

Run AetherGate through Docker/Podman using the existing dynamic host-port workflow.

Required real test:

1. start clean/current v2 stack and migrations;
2. seed an endpoint with `max_concurrency=2`;
3. use the official OpenAI Python SDK;
4. launch six non-streaming Chat Completions concurrently against the same public alias;
5. make requests long enough to observe overlapping execution;
6. capture scheduler state/timestamps;
7. prove no more than 2 attempts are in dispatched/streaming execution simultaneously;
8. prove at least 4 requests were queued while the first two occupied capacity;
9. prove all six eventually complete successfully;
10. prove FIFO dispatch order for this single-endpoint case;
11. verify no request was double-dispatched;
12. run a second real test with at least one streaming request passing through scheduler admission.

The handoff must include a concise timestamp/state table or equivalent evidence. Do not include
prompt/completion bodies.

## Multi-worker Test — Required

Run at least two scheduler worker processes/containers against the same PostgreSQL database.

Prove with deterministic integration tests and the real nomnom test where practical:

- no double dispatch;
- shared `max_concurrency=2` is enforced across workers;
- killing/restarting one worker while requests are only queued does not lose them;
- pre-dispatch lease recovery is safe;
- post-dispatch ambiguous ownership is not automatically retried/released.

Do not claim multi-worker safety based only on unit mocks.

## Observability

Add scheduler-safe metadata/logging/metrics foundation sufficient to inspect:

- request ID;
- queue entered timestamp;
- dispatch timestamp;
- terminal timestamp;
- queue wait duration;
- endpoint ID;
- current state;
- reservation/attempt ID;
- worker ID/fencing generation.

Never log prompt/completion content or secret values.

A small development inspection command is acceptable until the admin API/UI exists.

## Automated Tests

Add deterministic offline/integration coverage for at least:

1. FIFO queue order;
2. endpoint `max_concurrency`;
3. six requests / two slots;
4. two workers sharing one endpoint limit;
5. atomic reservation under concurrent claims;
6. queue-full rejection;
7. queue expiry before dispatch;
8. encrypted request payload at rest;
9. encrypted completion/stream content at rest;
10. no plaintext canary prompt in relevant PostgreSQL content columns;
11. fencing rejects stale worker completion;
12. safe reclaim before dispatch;
13. post-dispatch lease expiry -> `outcome_unknown`;
14. `outcome_unknown` keeps capacity reserved;
15. queued client cancellation;
16. streaming event order;
17. scheduled non-stream OpenAI SDK interoperability;
18. scheduled streaming OpenAI SDK interoperability;
19. restart persistence of queued work;
20. worker process starts independently from API.

Keep ordinary tests offline; the required nomnom smoke/load test is separate.

## Compose / Developer Workflow

Update `deploy/v2/compose.yaml` and `scripts/dev/v2` so:

- API + PostgreSQL + scheduler worker(s) can be started together;
- PostgreSQL remains un-published;
- API host port remains dynamically allocated on loopback;
- at least two workers can be run for the multi-worker verification;
- migration/test helpers still work;
- developer tooling can inspect queue state without exposing content.

Do not hard-code a host port.

## Documentation

Update:

- `docs/architecture/scheduler.md`
- `docs/architecture/v2-overview.md`
- `docs/contracts/domain-model.md`
- `docs/development/README.md`

Clearly document that scheduler phase 1 enforces **physical endpoint concurrency only**.

Explicitly defer, rather than fake:

- provider RPM windows;
- TPM/token reservation;
- shared provider-account quota windows;
- project budgets;
- retries/cooldown;
- fairness modes beyond FIFO.

Those become later phases and must eventually be reserved together with endpoint capacity.

Do not modify the dated audit.

## Out of Scope

Do not implement in this task:

- `/v1/responses`;
- embeddings;
- full API-key/OIDC/session system;
- admin CRUD API;
- React changes;
- accounting/ledger settlement;
- provider RPM/TPM enforcement;
- retry/fallback orchestration;
- v1 SQLite migration;
- production secret backend.

## Verification

Before committing:

- full unit/contract/integration suite;
- containerized tests;
- migration from empty DB through latest revision;
- ruff/lint;
- `git diff --check`;
- repository secret scan;
- confirm legacy `app/` and `frontend/src/` untouched;
- confirm dated audit unchanged;
- complete the required real six-request/two-slot nomnom test;
- complete the required scheduled streaming test;
- complete multi-worker verification.

## Handoff

Update `docs/development/agent-handoff.md` with:

- branch / starting commit / implementation commits;
- migration revisions;
- scheduler schema and worker topology;
- queue encryption approach;
- actual dynamically assigned AetherGate host port;
- actual verified backend/model (non-sensitive);
- six-request/two-slot evidence;
- multi-worker evidence;
- streaming-through-scheduler result;
- worker recovery/fencing evidence;
- exact summarized test results;
- deferred scheduler phases;
- issues/risks;
- exactly one recommended next step.

No credentials, prompt text, completion text, encryption keys, or large logs.

## Commit and Push

Use conventional commits on branch `v2`.

Suggested primary message:

`feat(scheduler): add durable endpoint-concurrency queue`

A separate handoff-only follow-up commit is allowed.

**Push all completed commits to `origin/v2`.**

Never push directly to `main`.

The task is complete only when `origin/v2` contains the work and updated handoff, and the real
six-request/two-slot nomnom test has passed or the handoff records a genuine environmental blocker
with evidence.
