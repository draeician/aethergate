# AetherGate v2 — Current Task

## Task ID
AGV2-018

## Title
Queue and operator admin API — inspection, cancellation, reconciliation, pause, and drain

## Ownership
Primary: scheduler/queueing + admin API
Coordinating: identity/RBAC, accounting, catalog/routing, audit, platform/testing

## Why This Task Exists

The inference scheduler is durable and multi-worker safe, and the identity/catalog/accounting control
planes are now exposed through /admin/v1.

The remaining backend control-plane gap before the CLI/web console is queue/operator administration:

- inspect queued and in-flight work without exposing inference content;
- explain why a request is waiting;
- inspect endpoint slot occupancy;
- safely cancel work;
- reconcile outcome_unknown requests;
- temporarily pause/drain endpoint dispatch without abusing catalog is_active.

Existing development-only tools (inspect_queue.py and reconcile.py) already prove parts of this
internally. This task turns the same invariants into the authenticated /admin/v1 product surface.

## Recovery

If context is compacted/restarted/uncertain:
1. re-read AGENTS.md;
2. re-read project_spec.md;
3. re-read this file;
4. re-read docs/development/agent-handoff.md;
5. inspect git status/history;
6. continue from repository state.

current-task.md is authoritative.

## Before You Start

1. Work on branch v2 and pull latest origin/v2.
2. Read:
   - AGENTS.md
   - project_spec.md
   - docs/development/current-task.md
   - docs/development/agent-handoff.md
   - docs/architecture/scheduler.md
   - docs/architecture/admin-api.md
   - docs/architecture/accounting.md
   - src/aethergate/scheduler/service.py
   - src/aethergate/scheduler/repository.py
   - src/aethergate/inspect_queue.py
   - src/aethergate/reconcile.py
   - current scheduler/accounting tests
   - current admin authorization/pagination patterns
3. Preserve all scheduler lease/fencing, quota, budget, accounting, and ambiguous-outcome invariants.
4. Preserve all OIDC/session/CSRF behavior.
5. Preserve catalog is_active semantics.
6. Do not modify/delete legacy v1 app/ or frontend/src/.
7. Do not commit unrelated local/untracked files.

## Core safety rules

- Never expose/decrypt prompt content, completion content, encrypted payloads, encrypted results, or
  stream event bodies through the operator API.
- No admin action may fabricate a succeeded result.
- outcome_unknown is never auto-retried or auto-released.
- Pause/drain is not the same as catalog deactivation.
- No DB transaction spans upstream inference.
- Existing scheduler lock order, leases, fencing, quota, and accounting correctness remain intact.
- CLI and future web console must consume this API rather than editing scheduler tables directly.

## Authorization model

Use:
- admin:queue:read
- admin:queue:write

### Project-scoped queue access

- system_admin: read all requests and cancel any eligible request.
- project_admin: read and cancel requests belonging to its authorized project.
- project_viewer: read requests belonging to its authorized project; no mutation.
- cross-project opaque request IDs are non-enumerating for project-scoped callers.
- project roles may not see requests with project_id = NULL.

### Deployment-only operations

Require system_admin deployment authority plus admin:queue:*:
- endpoint runtime/slot summaries;
- pause/drain/resume endpoint dispatch;
- global provider-quota runtime status;
- outcome_unknown reconciliation.

A project_admin must not gain deployment operator authority merely because its credential contains
admin:queue:*.

## 1. Scheduler operator service boundary

Create a dedicated scheduler/operator administration service, e.g.:
- src/aethergate/scheduler/admin.py

It must:
- accept typed AdminRequestContext;
- authorize internally;
- resolve project/deployment scope centrally;
- use safe scheduler transition primitives rather than duplicating lifecycle logic in routers;
- emit immutable safe audit events for mutations;
- never decrypt inference content.

Routers remain thin.

Refactor existing SchedulingService cancellation/reconciliation internals only as necessary so:
- normal inference paths;
- development reconcile CLI;
- new admin API
reuse the same transition implementation.

Do not maintain two subtly different cancellation/reconciliation state machines.

## 2. Queue request metadata reads

Add safe DTOs and endpoints:

- GET /admin/v1/queue/requests
- GET /admin/v1/queue/requests/{request_id}

Bounded pagination, default 50 / max 200.

Filters at minimum:
- project_id;
- principal_id;
- api_credential_id;
- model_alias_id;
- endpoint_id;
- state;
- stream;
- wait_reason;
- created/queued time range as practical.

Safe request metadata may include:
- request_id;
- project_id;
- principal_id;
- api_credential_id;
- model_alias_id;
- endpoint_id;
- quota_group_id;
- state;
- stream;
- queued_at;
- started_at;
- finished_at;
- queue_wait_until;
- expires_at;
- cancellation_requested;
- wait_reason;
- wait_limit_id;
- wait_limit_metric;
- next_eligible_at;
- error_code;
- price_snapshot_id;
- reconciled_state;
- reconciled_at;
- reconciled_by;
- worker_id and lease_expires_at if useful.

Do NOT expose:
- payload_encrypted;
- result_encrypted;
- decrypted prompt/completion;
- stream-event content;
- fencing_token;
- provider secret material.

### Effective waiting reason

A queued request must explain temporary endpoint operator holds even if the persisted scheduler
wait_reason currently describes quota/budget state.

Prefer a computed safe field such as effective_wait_reason:
- endpoint_paused when its endpoint operational state is paused;
- endpoint_draining when draining;
- otherwise the persisted wait_reason/capacity reason.

Do not overwrite quota/budget historical wait metadata merely to display an operator hold.

## 3. Queue summary

Implement:
- GET /admin/v1/queue/summary

Return safe operational counts for the caller's authorized scope:
- counts by request state;
- queued total;
- in-flight total (reserved/dispatched/streaming);
- outcome_unknown total;
- oldest queued timestamp;
- oldest wait duration or equivalent;
- counts by effective wait reason where practical.

Authorization:
- system_admin => deployment-wide;
- project roles => their authorized project(s) only.

Repeated reads must be side-effect free.

Do not add expensive unbounded scans; use aggregate SQL.

## 4. Endpoint runtime / slot status

Implement deployment-only:
- GET /admin/v1/queue/endpoints
- GET /admin/v1/queue/endpoints/{endpoint_id}

Return:
- endpoint_id/name;
- catalog is_active;
- operational_state;
- max_concurrency;
- occupied_slots from active physical reservations;
- available_slots = max(0, max_concurrency - occupied_slots);
- draining_complete or equivalent;
- oldest queued work for the endpoint if useful.

Never count a released Reservation as occupied.

If occupied_slots > max_concurrency after an operator reduces configured capacity, report honestly and
available_slots = 0; do not rewrite active reservations.

## 5. Durable endpoint operational state

Catalog Endpoint.is_active remains configuration/lifecycle state.

Add a separate durable scheduling/operator state:
- active
- paused
- draining

Recommended name:
- EndpointOperationalState

Semantics:

### active
Normal scheduler dispatch.

### paused
- no new request may acquire endpoint capacity after the pause transaction commits;
- already reserved/dispatched/streaming work is not killed;
- queued work remains queued;
- resume re-enables future dispatch.

### draining
- also prevents new capacity reservations;
- existing in-flight work continues to terminal settlement;
- draining_complete is true when active physical reservations reach zero;
- state remains draining until an explicit resume (do not require a background state flip).

Pause and drain intentionally share the "no new dispatch" gate; the distinction is operator intent and
the runtime drained indicator.

Do not model pause/drain by setting is_active=false. Catalog deactivation retains its existing
resource-unavailable semantics.

## 6. Migration 0015

Add linear migration 0015:
- add endpoint operational state with default active for all existing endpoints;
- DB CHECK / equivalent allows only active, paused, draining;
- preserve all existing endpoint IDs/configuration;
- 0014 -> 0015 succeeds;
- empty DB -> latest succeeds.

Do not rewrite 0001-0014.

Update domain/entity/repository mappings and typed DTOs as needed.

## 7. Scheduler pause/drain correctness

The worker must not reserve capacity on a paused/draining endpoint.

Correctness requirement:
- after a pause/drain transaction commits, no later capacity reservation may be granted for that
  endpoint until resume.

Handle races safely:
- scheduler must re-check endpoint operational_state under the same endpoint row lock used for
  physical capacity admission, immediately before reserving;
- a claim already holding the endpoint lock before pause may complete its reservation first; the
  pause waits and then commits;
- after pause commit, subsequent claims cannot reserve.

Filtering paused/draining endpoints out of queue scans is an optimization, not the only correctness
check.

Add a real DB concurrency race test using barriers/events between:
- worker claim/reserve;
- operator pause/drain.

No arbitrary sleeps as the correctness mechanism.

## 8. Pause / drain / resume API

Implement deployment-only mutations:

- POST /admin/v1/queue/endpoints/{endpoint_id}/pause
- POST /admin/v1/queue/endpoints/{endpoint_id}/drain
- POST /admin/v1/queue/endpoints/{endpoint_id}/resume

Requirements:
- system_admin + admin:queue:write;
- project roles => 403;
- missing endpoint => 404;
- idempotent repeated same-state request;
- transitions are durable across API/worker restart;
- response returns current runtime status;
- audit actual state changes only;
- no-op repeat does not create misleading duplicate audit.

Suggested audit actions:
- endpoint_dispatch.paused
- endpoint_dispatch.draining
- endpoint_dispatch.resumed

Browser-session mutation requires CSRF; Bearer mutation does not.

## 9. Safe request cancellation

Implement:
- POST /admin/v1/queue/requests/{request_id}/cancel

Use the existing scheduler cancellation semantics.

Required state behavior:
- queued => terminal cancelled immediately;
- reserved/pre-dispatch => cancelled, release endpoint/quota/budget reservations, discard pre-dispatch
  price snapshot as today;
- dispatched/streaming => set cancellation_requested; do NOT claim upstream execution has already
  stopped; owning worker settles safely;
- already cancelled => idempotent success;
- succeeded/failed/expired => stable conflict/no-op policy, document it;
- outcome_unknown => do not pretend cancel resolves ambiguity; require reconciliation.

Return a typed result that distinguishes:
- cancelled_now;
- cancellation_requested;
- already_cancelled;
or an equally clear contract.

Project authorization:
- project_admin may cancel its own project's request;
- project_viewer denied;
- cross-project request ID => 404 for project-scoped caller.

Audit:
- immediate cancellation vs cancellation request should be distinguishable;
- actor principal/project/resource ID;
- no content.

## 10. outcome_unknown reads and reconciliation

Implement deployment-only:
- GET /admin/v1/queue/outcome-unknown
- POST /admin/v1/queue/requests/{request_id}/reconcile

Reconcile body:
- disposition = failed | cancelled

Do NOT accept:
- succeeded;
- arbitrary operator identity from client input.

Use authenticated context.principal_id as reconciled_by/audit actor.

Preserve existing reconciliation semantics:
- physical reservation released only through the established reconcile path;
- quota/accounting behavior remains conservative;
- held budget reservation commits conservatively with settlement reason;
- no UsageRecord is fabricated without trustworthy usage;
- result content is not invented.

Repeat reconciliation:
- must not double release or double commit accounting;
- return stable conflict/idempotent result according to existing lifecycle semantics.

Project admins may read their own request metadata through the general queue read API, but
reconciliation itself is system_admin-only because it resolves deployment capacity and ambiguous
provider outcome.

## 11. Provider quota runtime status

Expose deployment-only read:
- GET /admin/v1/queue/quota-status

This is runtime inspection, not configuration CRUD.

For each configured quota limit/current persisted window where applicable, expose safe metadata:
- quota_group_id/name;
- provider_account_id;
- quota_limit_id/name;
- metric;
- limit_units;
- window_seconds;
- current window_start/window_end;
- committed_units;
- reserved_units;
- remaining = max(0, limit - committed - reserved);
- enabled;
- group cooldown_until.

Do not create fake QuotaWindow rows just for a read.
If no current window exists, report zero committed/reserved computed state.

Project roles do not see provider-account quota topology in this phase.

Project budget headroom remains served by the accounting API; do not duplicate it here.

## 12. Audit queue/operator mutations

Emit immutable safe events for actual transitions:
- request.cancelled
- request.cancellation_requested
- request.reconciled
- endpoint_dispatch.paused
- endpoint_dispatch.draining
- endpoint_dispatch.resumed

For request events use project_id when present so project audit readers can see appropriate events.
Endpoint operational events are deployment-scoped.

Never include:
- prompt/completion content;
- encrypted payload/result;
- stream chunks;
- API/OIDC/session/CSRF/provider secrets;
- fencing tokens.

## 13. Error translation

Stable admin errors for:
- request/endpoint not found;
- cross-project non-enumeration;
- invalid lifecycle transition;
- reconcile non-outcome_unknown;
- invalid reconcile disposition;
- unauthorized deployment operation.

Do not expose SQL, stack traces, raw provider errors, or internal encryption data.

## 14. Existing development tools

inspect_queue.py and reconcile.py may remain for development/offline operations, but:
- normal product administration uses /admin/v1;
- reconcile.py should reuse the same scheduler transition primitive where practical;
- do not make the CLI module an HTTP client yet; Linux product CLI is the next workstream.

Document the distinction.

## 15. What is intentionally NOT in this task

Do not implement:
- force-kill of upstream inference;
- fabricated success reconciliation;
- automatic retry of outcome_unknown;
- priority/fairness queue reordering;
- queue item manual reordering;
- provider retry orchestration not already present;
- TTFT/latency percentile metrics if the required timestamp instrumentation is not already reliable;
- upstream health probing subsystem;
- Linux product CLI;
- web console;
- Responses API;
- embeddings;
- v1 migration.

Those can be separate tasks. This milestone provides the reliable operator control surface they need.

## Real nomnom Verification — Required

Use dynamic API ports, the real internal Ollama backend, and inference auth bypass false.

### A. Queue inspection / explanation

With endpoint max_concurrency=2:
- submit six concurrent real requests;
- observe queued + in-flight states through /admin/v1/queue/requests;
- queue summary counts are coherent;
- endpoint runtime reports occupied <= 2 and correct available slots;
- request payload/completion content is absent from all operator DTOs.

### B. Project queue RBAC

Projects A and B:
- system_admin sees both;
- project_admin(A) sees A requests only;
- project_viewer(A) sees A requests read-only;
- A caller querying B request ID => 404;
- project_admin(A) cannot use deployment endpoint/quota operator endpoints.

### C. Pause live behavior

- pause endpoint while workers are running;
- after pause commits, no new reservation/dispatch begins;
- already in-flight request(s) complete normally;
- queued requests remain queued;
- queue read explains endpoint_paused;
- restart API/worker and prove pause persists;
- resume;
- queued work begins dispatching again.

### D. Drain live behavior

- create in-flight + queued work;
- drain endpoint;
- no new reservations after drain commit;
- current in-flight work completes;
- runtime reports draining_complete once occupied_slots reaches zero;
- queued work remains held;
- resume dispatches it.

### E. Pause/claim race

Use a controlled concurrency harness:
- worker claim/reservation races operator pause;
- prove lock semantics: either reservation commits before pause, or pause wins and claim does not
  reserve afterward;
- never observe a capacity reservation created after the pause commit boundary.

### F. Queued cancellation

Cancel queued request:
- immediately cancelled;
- never contacts upstream;
- no endpoint/quota/budget reservation survives;
- no usage/ledger record.

### G. Reserved pre-dispatch cancellation

Exercise real reserved state:
- cancel;
- endpoint/quota/budget reservations released;
- pre-dispatch price snapshot discarded;
- no upstream call;
- queue API shows cancelled.

### H. In-flight cancellation

For a controlled slow real/test adapter request:
- cancel after durable dispatch intent;
- response clearly says cancellation_requested rather than claiming upstream was killed;
- worker settles according to established safe semantics;
- no duplicate settlement.

### I. outcome_unknown reconciliation

Create a real controlled ambiguous outcome via worker lease loss/death after durable dispatch intent.

Prove:
- request becomes outcome_unknown;
- physical slot stays occupied before reconciliation;
- outcome_unknown API shows safe metadata only;
- project_admin cannot reconcile;
- system_admin reconcile failed/cancelled succeeds;
- slot releases only after reconciliation;
- budget reservation commits conservatively;
- no fabricated UsageRecord;
- repeated reconcile does not double-release/double-commit;
- succeeded disposition rejected.

### J. Quota runtime status

With a request quota:
- status shows configured limit/window;
- committed/reserved values track real admission;
- cooldown metadata remains visible when set;
- read creates no fake window history.

### K. Browser RBAC/CSRF

With deterministic local OIDC provider:
- human system_admin can pause/resume and reconcile with correct CSRF;
- missing/wrong CSRF => 403;
- human project_admin can cancel own queued request with correct CSRF;
- project_viewer mutation => 403;
- Bearer system_admin operations remain CSRF-exempt.

### L. Regression

Re-run:
- six-request/two-slot scheduler regression;
- dead-worker/outcome_unknown regression;
- quota/accounting settlement;
- catalog route invariants;
- OIDC browser binding/logout;
- official OpenAI Python SDK non-stream + stream inference.

## Automated Tests

Add deterministic coverage for at least:

1. service-layer queue authorization cannot be bypassed;
2. project queue list filtering;
3. cross-project request ID non-enumeration;
4. queue request DTO contains no content/encrypted fields/fencing token;
5. queue summary counts/stable scope;
6. endpoint runtime occupied/available calculation;
7. operational state default active;
8. pause prevents new reserve;
9. draining prevents new reserve;
10. existing in-flight work not killed by pause/drain;
11. resume restores dispatch;
12. operational state survives service restart/DB reload;
13. pause-vs-claim true concurrency race;
14. repeated pause/drain/resume idempotency/audit behavior;
15. queued cancellation;
16. reserved cancellation releases physical/quota/accounting state;
17. dispatched cancellation only requests cancellation;
18. already-cancelled idempotency;
19. terminal succeeded/failed/expired cancellation contract;
20. outcome_unknown cannot use cancel as fake reconciliation;
21. outcome_unknown list safe metadata;
22. reconcile only failed/cancelled;
23. reconciliation uses authenticated principal ID;
24. project role cannot reconcile;
25. reconciliation releases held slot exactly once;
26. reconciliation conservative budget settlement exactly once;
27. reconciliation creates no fabricated usage;
28. quota runtime zero-state read without row creation;
29. quota runtime committed/reserved/cooldown values;
30. browser CSRF on operator mutation;
31. Bearer operator mutation CSRF-exempt;
32. audit events safe/no content/secrets;
33. migration 0014 -> 0015;
34. empty DB -> latest;
35. existing 402-test baseline remains green or higher;
36. official SDK inference regressions remain green.

## Migration

Add migration 0015 for Endpoint operational state.

Do not rewrite migrations 0001-0014.

Verify:
- 0014 -> 0015;
- empty DB -> latest;
- existing endpoint rows become active;
- invalid operational state rejected;
- downgrade is explicit/reasonable.

## Documentation

Update:
- docs/architecture/scheduler.md
- docs/architecture/admin-api.md
- docs/contracts/domain-model.md
- docs/contracts/admin-v1-foundation.md
- docs/development/README.md
- docs/development/agent-handoff.md

Document:
- safe queue metadata contract;
- project/deployment queue authorization;
- endpoint operational state vs catalog is_active;
- pause/drain/resume semantics;
- cancellation truthfulness;
- outcome_unknown reconciliation;
- quota runtime status;
- audit behavior;
- migration 0015.

Do not modify the dated architecture audit.

## Handoff

Include:
- implementation commit(s);
- migration revision;
- endpoints added;
- queue DTO safe-field list;
- authorization/scoping;
- pause/drain lock semantics;
- cancellation state semantics;
- reconciliation/accounting semantics;
- live six/two proof;
- live pause/drain + restart proof;
- live outcome_unknown proof;
- browser CSRF proof;
- final test count;
- dynamic API/IdP ports;
- backend/model;
- issues/risks;
- exactly one recommended next step.

Never include API/bootstrap/OIDC/session/CSRF/provider secrets, prompt/completion content, encrypted
payload/result bytes, stream chunks, or large logs.

## Commit and Push

Suggested primary commit:
`feat(admin): add queue and operator control plane`

Push all completed commits to origin/v2.
Never push directly to main.
Do not ask whether to commit/push.

The task is complete only when every stated automated/live criterion is green and origin/v2 contains
the implementation and updated handoff.
