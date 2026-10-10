# AetherGate v2 — Current Task

## Task ID
AGV2-023

## Title
Operational observability — queue wait, streaming TTFT, retry rate, and upstream health

## WIP Marker — FIRST LOCAL ACTION

Immediately after entering the repository, before pull/read/implementation work, create:

`.aethergate-wip`

Safe contents:
- task ID: AGV2-023
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
3. every task commit is pushed to origin/v2;
4. origin/v2 is verified to contain the finished work.

## Why This Task Exists

The product/web-console requirement calls for authoritative operational telemetry:
- queue-wait percentiles;
- time-to-first-token (TTFT) percentiles;
- retry rate;
- upstream health;
- budget headroom.

Budget headroom is already implemented and Decimal-safe. The dashboard currently labels the other
three areas "Not yet instrumented by the backend."

The durable scheduler already owns the correct source data for:
- request enqueue time;
- execution attempts;
- endpoint assignment;
- terminal attempt outcomes;
- provider failures;
- ambiguous outcomes.

What is missing is an explicit, safe first-token timestamp plus aggregate read models.

This task adds those metrics without logging/decrypting prompt or completion content and without
introducing a second scheduling/accounting authority.

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
   - docs/development/agent-workstreams.md
   - docs/architecture/scheduler.md
   - docs/architecture/admin-api.md
   - docs/architecture/security.md
   - docs/architecture/v2-overview.md
   - scheduler service/repository/models/tests
   - provider adapter/error mapping
   - queue admin service/router/contracts
   - current DashboardPage and dashboard tests
3. Use the canonical backend test command from AGENTS.md.
4. Treat every failing test as a real defect until root-caused.
5. Rebuild/restart affected containers before live browser verification.
6. Do not modify/delete legacy Python v1 app/.
7. Do not commit unrelated local/untracked files.

## 1. Observability domain boundary

Add a small observability read-model module owned by the platform/observability workstream.

Recommended structure:
- `src/aethergate/observability/service.py`
- `src/aethergate/observability/repository.py`
- thin admin router under `/admin/v1/observability`
- typed admin contracts
- frontend generated client integration.

Rules:
- source-of-truth lifecycle writes remain in scheduler/accounting domains;
- observability reads durable facts and may add safe timestamps/classification metadata needed to make
  those facts queryable;
- no metrics code may mutate admission/quota/accounting decisions;
- routers contain no SQL/business policy.

## 2. Migration 0017 — durable first-token/provider-outcome metadata

A migration is justified for this task.

Add linear migration `0017` with `down_revision = 0016`.

Extend `execution_attempts` with safe non-content metadata:

- `first_token_at TIMESTAMPTZ NULL`
- `upstream_error BOOLEAN NOT NULL DEFAULT FALSE`
- `upstream_status_code INTEGER NULL`

Semantics:
- `first_token_at` = timestamp when the worker receives the first non-empty provider content chunk for
  a streaming attempt;
- `upstream_error=true` only when the terminal failure came from `ProviderError`;
- `upstream_status_code` is the sanitized numeric upstream HTTP status when reliably known;
- secret-resolution/configuration/unsupported-provider failures are gateway/configuration failures,
  not upstream-health failures;
- cancellations are not upstream failures;
- `outcome_unknown` remains ambiguous, not a provider failure.

Add only indexes justified by the aggregate queries. At minimum consider:
- attempt started/finished time for rolling windows;
- endpoint + finished time for upstream-health grouping;
- request created/queued time if required for bounded queue-wait queries.

Do not add content/log columns.

Verify:
- empty database -> latest;
- 0016 -> 0017;
- downgrade 0017 -> 0016 if practical;
- 0001-0016 unchanged.

## 3. Streaming first-token transition

Today streaming requests may remain durably `dispatched` until terminal settlement even though the
state model includes `streaming`.

Add one atomic scheduler repository transition for the first non-empty stream content chunk.

Required behavior:
- fence/attempt ownership checked;
- request: dispatched -> streaming;
- execution attempt: dispatched -> streaming;
- set `first_token_at` exactly once;
- idempotent if already streaming with first_token_at populated;
- stale/late worker cannot overwrite another attempt or terminal state;
- no transition for a chunk with no content (usage-only/finish-only/role-only chunk);
- no prompt/completion content is persisted by this transition.

Call it from `run_stream` when the first non-empty `chunk.content` arrives.

The encrypted StreamEvent behavior remains unchanged.

If a successful stream contains no non-empty content chunk, TTFT remains unavailable/null; do not
fabricate zero.

## 4. Provider failure classification

When `run_complete` or `run_stream` catches `ProviderError`:

- preserve current safe client error behavior;
- settle the ExecutionAttempt with `upstream_error=true`;
- persist `upstream_status_code` when `ProviderError.status_code` is an integer;
- never persist raw exception text, headers, URLs, bodies, credentials, or provider response content.

For:
- UnsupportedProvider;
- SecretResolutionError;
- internal gateway/config failures;

`upstream_error=false`.

Outcome-unknown remains distinguishable as ambiguous.

## 5. Metric definitions — settle them explicitly

Use one bounded rolling time window selected by the caller.

Default:
- 15 minutes.

Allowed:
- minimum 60 seconds;
- maximum 86,400 seconds.

Return:
- `window_start`;
- `window_end`;
- `window_seconds`;
- sample counts.

### Queue wait

One sample per request that entered the selected observation window and acquired an execution attempt.

Definition:
- earliest ExecutionAttempt.started_at for the request
  minus InferenceRequest.queued_at.

This is **admission/queue delay**.
Do not use mutable `InferenceRequest.started_at` if a later safe retry can overwrite it.

Return:
- sample_count;
- p50_ms;
- p95_ms;
- p99_ms.

No sample => percentile fields null, never fake zero.

### TTFT

Streaming attempts only.

Definition:
- `ExecutionAttempt.first_token_at - ExecutionAttempt.started_at`.

This is **dispatch-to-first-content TTFT**, intentionally separate from queue wait.

Return:
- sample_count;
- p50_ms;
- p95_ms;
- p99_ms.

Do not:
- decrypt StreamEvent content to reconstruct TTFT;
- use terminal completion time;
- include non-stream requests;
- emit zero for no samples.

Document this definition in the API/UI.

### Retry rate

A retry is an additional ExecutionAttempt for the same request.

Because v2 never auto-retries dispatched/ambiguous work, multiple attempts currently represent the
safe pre-dispatch reclaim/re-attempt path.

For requests in the observation cohort return:
- attempted_requests;
- retried_requests (attempt_count > 1);
- retry_attempts = sum(max(attempt_count - 1, 0));
- request_retry_rate = retried_requests / attempted_requests, nullable when denominator = 0.

Do not count:
- quota/budget polling with no execution attempt;
- SDK/client retries that arrive as a brand-new gateway request_id;
- outcome_unknown as an automatic retry.

### Upstream health

Use passive, factual execution evidence grouped by Endpoint.

Do not invent active probes or arbitrary synthetic "99% healthy" claims.

For each endpoint return:
- endpoint_id;
- endpoint name;
- provider_account_id if safe for system_admin;
- window sample count;
- succeeded_attempts;
- upstream_failed_attempts;
- ambiguous_attempts;
- upstream_success_rate =
  succeeded / (succeeded + upstream_failed), nullable when denominator = 0;
- rate_limited_attempts where upstream_status_code = 429;
- last_success_at;
- last_failure_at;
- current quota-group cooldown where already available/related, if clean.

Exclude:
- user cancellation from provider failure;
- pre-dispatch abandoned attempts;
- gateway/config failures from upstream_failed_attempts.

The frontend may show "No samples" when there is no definitive upstream evidence.

Do not make readiness fail solely because one provider endpoint has poor passive health.

## 6. Percentile computation

Use PostgreSQL-authoritative bounded queries.

Requirements:
- do not load unbounded history into Python/React to compute percentiles;
- use `percentile_cont` or an equally correct bounded SQL approach;
- observation queries are read-only;
- project scoping is applied in SQL/service scope, not by filtering rows after reading all projects;
- indexes support the intended rolling-window access path.

Tests must cover exact known samples and percentile results.

## 7. Admin API

Add typed endpoints under:

`/admin/v1/observability`

Recommended:

### GET /admin/v1/observability/summary
Query:
- `window_seconds` optional;
- `project_id` optional only where the caller is authorized.

Response:
- window metadata;
- queue-wait percentiles;
- streaming-TTFT percentiles;
- retry metrics.

Authorization:
- system_admin: deployment aggregate or explicit project;
- project_admin/project_viewer: own project only;
- cross-project project-role query is non-enumerating/denied consistently with existing admin API;
- admin service credentials require the existing `admin:queue:read` permission.

Do not introduce a new RBAC scope solely for these queue-derived read metrics.

### GET /admin/v1/observability/upstreams
System-admin deployment scope only.

Response:
- paginated/safely bounded endpoint passive-health rows described above.

Project roles:
- no endpoint/provider-account deployment-health enumeration;
- direct call => 403.

No prompt/completion/provider-secret material in either response.

## 8. OpenAPI / generated client

Add typed admin contracts and regenerate frontend client/types.

Requirements:
- generated files are not hand-edited;
- central frontend client owns calls/errors;
- OpenAPI drift check added to normal verification;
- no arbitrary observability-path escape hatch.

## 9. Dashboard

Replace the three current "Not yet instrumented" placeholders.

### Queue wait
Show:
- p50;
- p95;
- p99;
- sample count.

### Streaming TTFT
Show:
- p50;
- p95;
- p99;
- sample count;
- label clearly says streaming/dispatch-to-first-token.

### Retry rate
Show:
- request retry rate;
- retried request count / attempted request count;
- retry-attempt count.

### Upstream health — system_admin
Show one compact row/card per endpoint:
- success rate or "No samples";
- successes;
- upstream failures;
- ambiguous;
- 429 count;
- last success/failure when present;
- cooldown when applicable.

Project-scoped users:
- see only their authorized queue-wait/TTFT/retry aggregate;
- do not see deployment endpoint health.

No samples:
- render "No samples";
- do not render fake 0ms or fake 100%.

Retain:
- queued/in-flight/outcome_unknown;
- oldest wait;
- slots;
- quota/cooldown;
- Decimal-safe budget headroom.

Add a small window selector:
- 15m;
- 1h;
- 24h.

Polling:
- reuse bounded non-overlapping polling;
- 4-5 seconds is acceptable;
- hidden-tab suppression if existing helper already provides it.

## 10. Request-detail observability

Where safe and useful, add to Queue request detail:
- queue wait for that request;
- execution attempt count;
- first-token latency for streaming attempts when available;
- upstream request ID already present in safe attempt/accounting data only if the existing contract
  explicitly exposes it safely.

Do not add prompt/completion inspection.

This section is optional if it would require a large separate attempt-history API; dashboard metrics are
the required scope.

## 11. Structured logs — do not broaden into a logging rewrite

Do not replace the logging stack in this task.

However, new observability code/log messages must:
- use stable request/attempt/endpoint IDs only;
- never log content or secrets;
- never log provider response bodies/headers;
- keep provider error classification numeric/safe.

A broader structured-log exporter/retention task remains separate if needed.

## 12. Automated backend tests

Add deterministic coverage for at least:

1. first non-empty stream chunk atomically marks request+attempt streaming;
2. first_token_at set exactly once;
3. usage-only/empty chunk does not set first_token_at;
4. stale fence cannot set first_token_at;
5. terminal/late worker cannot overwrite first_token_at/state;
6. ProviderError marks upstream_error and safe status;
7. SecretResolutionError/UnsupportedProvider do not count as upstream failure;
8. queue-wait percentile known dataset;
9. TTFT percentile known dataset;
10. no-sample percentiles are null;
11. retry metrics count additional ExecutionAttempts, not queued polls;
12. outcome_unknown counted ambiguous, not upstream failed;
13. cancellation excluded from upstream failure;
14. 429 counted separately;
15. project metric scoping;
16. upstream-health system_admin-only;
17. no content columns/decryption needed for aggregate queries.

## 13. Frontend tests

Add/extend tests proving:
- dashboard replaces the three placeholders with real metric data;
- no-sample states say "No samples";
- p50/p95/p99 render correctly;
- TTFT label says streaming/dispatch-to-first-token;
- retry denominator/counts render correctly;
- system_admin upstream cards render;
- project roles never request/render deployment upstream health;
- window selector changes API query;
- existing budget headroom remains Decimal-safe;
- 401/403 handling remains centralized.

## 14. Live nomnom verification

Use the real v2 stack, deterministic OIDC provider, and real internal Ollama.
Inference-auth bypass remains false.

### A. WIP/migration
- marker lifecycle correct;
- migrate 0016 -> 0017;
- empty -> latest;
- migration head 0017.

### B. Queue-wait metrics
With endpoint max_concurrency=2:
- submit six real requests with a workload slow enough to queue;
- browser visibly shows queued work;
- after samples exist, observability API/dashboard reports non-null queue-wait percentiles;
- values are internally ordered p50 <= p95 <= p99;
- sample_count reflects admitted requests;
- no fake zero sample.

### C. Streaming TTFT
Use official OpenAI Python SDK streaming request:
- real stream succeeds;
- ExecutionAttempt.first_token_at becomes non-null on first non-empty content;
- request/attempt durable state reaches streaming before terminal settlement when observable;
- dashboard/API TTFT sample appears;
- TTFT > 0 and matches persisted timestamps within normal rounding;
- no encrypted StreamEvent is decrypted for the metric.

### D. Upstream health
Using a disposable route/alias:
1. point upstream_model at a deliberately nonexistent model on the real allowed Ollama endpoint;
2. official SDK request fails through the provider path;
3. passive endpoint health increments upstream_failed_attempts and records safe status when available;
4. restore a valid upstream model;
5. official SDK succeeds;
6. health shows both factual failure and success evidence;
7. raw provider error body/URL is never exposed.

Prefer browser catalog edits for the temporary route mutation when practical.

### E. Retry semantics
Prove deterministically/live-harness as practical that a safe pre-dispatch reclaimed request with two
ExecutionAttempts increments retry metrics exactly once.

Do not fake an automatic retry after dispatch; v2 intentionally does not do that.

If producing this safely through the full real worker stack is impractical, use a controlled
integration harness that drives the real scheduler/repository transitions and document that the live
Ollama proof covers queue/TTFT/upstream-health while retry-rate correctness is covered by the real
scheduler integration test.

### F. RBAC
- system_admin sees deployment summary + upstream endpoint health;
- project_admin(A)/project_viewer(A) see only A queue/TTFT/retry aggregate;
- project roles cannot call upstream health;
- B is not enumerable.

### G. SDK/browser regression
- official SDK non-stream + stream remain green with bypass=false;
- dashboard browser view updates from "No samples" to real metrics.

## 15. Migration / compatibility

Migration head becomes **0017**.

Do not modify 0001-0016.

Upgrade existing populated dev database and verify:
- old attempts get null first_token_at;
- old attempts get upstream_error=false;
- old attempts get null upstream_status_code;
- historical rows remain readable.

No backfill of fake TTFT/provider classification.

## 16. Regression gate

Run:
- full backend containerized suite; baseline **506**;
- frontend unit/component suite; baseline **82**;
- FULL Playwright suite; baseline **26**;
- npm build;
- npm lint;
- `npx tsc -b --noEmit`;
- OpenAPI/client generation drift;
- ruff check src tests;
- git diff --check;
- secret/token/content canary scan;
- official OpenAI Python SDK non-stream + stream with inference-auth bypass false.

Every red test must be root-caused. Do not call completion over a failing full suite.

## 17. Documentation

Add/update:
- `docs/architecture/observability.md` (new);
- docs/architecture/scheduler.md;
- docs/architecture/admin-api.md;
- docs/web-console.md;
- frontend/README.md;
- docs/development/README.md;
- docs/development/agent-handoff.md.

Document:
- exact queue-wait definition;
- exact streaming TTFT definition;
- retry-rate definition;
- passive upstream-health definition;
- no-sample semantics;
- RBAC/scoping;
- migration 0017;
- why encrypted content is not read for metrics;
- explicit non-goals.

Do not edit the dated architecture audit.

## Still Deferred

Do not implement in this task:
- Prometheus/OpenTelemetry exporter if not already required by the existing runtime;
- external metrics retention/TSDB;
- active upstream synthetic probes;
- alerting/paging;
- logging-stack rewrite;
- Responses API;
- embeddings;
- v1 migration;
- production deployment hardening unrelated to these metrics.

## Handoff

Include:
- implementation commit(s);
- migration 0017 proof;
- first_token_at/streaming transition semantics;
- provider failure classification;
- queue percentile definition/results;
- TTFT definition/results;
- retry metric definition/results;
- upstream health definition/results;
- RBAC proof;
- dashboard behavior/no-sample behavior;
- six/two queue metric proof;
- real streaming TTFT proof;
- real provider failure+success health proof;
- retry integration proof;
- backend/frontend/Playwright counts;
- build/lint/typecheck/OpenAPI results;
- official SDK version/results;
- dynamic API/web/IdP ports;
- issues/risks;
- WIP marker lifecycle;
- exactly one recommended next step.

Never include raw API/admin/inference/provider/session/CSRF/OIDC/device secrets, prompt/completion
content, encrypted payload/result, provider response bodies, or large logs.

## Commit and Push

Suggested primary commit:
`feat(observability): add queue and upstream metrics`

A separate migration commit is acceptable.

Push all completed commits to origin/v2.
Never push directly to main.
Do not ask whether to commit/push.

The task is complete only when every automated/live criterion is green, migration head is 0017,
origin/v2 contains the final implementation/tests/docs/handoff, and local `.aethergate-wip` has been
removed after final remote verification.
