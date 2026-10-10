# AetherGate v2 — Operational Observability

Living document. Derived from the durable scheduler/accounting model. Distinguish
**settled** / **direction** / **deferred**.

## Purpose

Expose authoritative operational telemetry over durable facts the scheduler
already owns: queue wait, streaming time-to-first-token (TTFT), retry rate, and
passive upstream health. The web console landing view and the `/admin/v1`
management API read these without ever touching prompt/completion content.
(Settled)

## Domain boundary

- New read-model module `aethergate/observability/` (`repository.py` + `service.py`)
  owned by the platform/observability workstream.
- Lifecycle writes remain in the scheduler/accounting domains. Observability
  reads durable facts and may add safe non-content timestamps/classification
  metadata needed to make those facts queryable.
- No observability code mutates admission/quota/budget/accounting decisions.
- The admin router (`aethergate/api/observability_admin.py`) contains no SQL or
  business policy; authorization and validation live in the service.

## Migration 0017 — durable first-token / upstream-outcome metadata

Linear migration `0017` (`down_revision = 0016`) extends `execution_attempts`
with safe, non-content columns:

| Column | Type | Semantics |
|---|---|---|
| `first_token_at` | `TIMESTAMPTZ NULL` | time the worker received the first non-empty provider content chunk for a streaming attempt |
| `upstream_error` | `BOOLEAN NOT NULL DEFAULT FALSE` | true only when the terminal failure was a `ProviderError` |
| `upstream_status_code` | `INTEGER NULL` | sanitized numeric upstream HTTP status when reliably known |

No content/log columns. Historical rows are not backfilled with fabricated
values (`first_token_at` NULL, `upstream_error` false, `upstream_status_code`
NULL). Justified indexes: `execution_attempts.started_at` (TTFT/retry windows),
`(endpoint_id, finished_at)` (passive upstream-health grouping), and
`inference_requests.queued_at` (queue-wait/retry cohorts). Migration head becomes
**0017**; `0001`–`0016` are untouched.

## Streaming first-token transition

`run_stream` calls `scheduler/repository.py::mark_first_token` on the first
non-empty `chunk.content`. It atomically:

- checks fence/attempt ownership (`fencing_token`, active `dispatched`/`streaming`);
- moves the request `dispatched -> streaming`;
- moves the attempt `dispatched -> streaming`;
- sets `first_token_at` exactly once (idempotent: a repeat is a no-op);
- never lets a stale/late worker overwrite another attempt or a terminal state;
- never fires for a content-less (usage-only/finish-only/role-only) chunk.

No prompt/completion content is persisted or logged by this transition; only the
presence of content is inspected. A successful stream with no non-empty content
chunk leaves TTFT unavailable (NULL), never a fabricated zero. Encrypted
StreamEvent behavior is unchanged.

### Provider failure classification

When `run_complete`/`run_stream` catch a `ProviderError`, the attempt settles with
`upstream_error=true` and persists `upstream_status_code` when it is an integer;
raw exception text, headers, URLs, bodies, and credentials are never stored.
`UnsupportedProvider`, `SecretResolutionError`, and internal gateway/config
failures settle `upstream_error=false` (they are **not** upstream-health
failures). Cancellations are not upstream failures. `outcome_unknown` remains
distinguishable as ambiguous.

## Metric definitions (settled)

One bounded rolling window selected by the caller. Default **900 s**; allowed
**60 s .. 86 400 s**. Every response includes `window_start`, `window_end`,
`window_seconds`, and sample counts.

### Queue wait

One sample per request that entered the window and acquired an execution attempt:

```
earliest ExecutionAttempt.started_at − InferenceRequest.queued_at
```

This is admission/queue delay. The mutable `InferenceRequest.started_at` is
**not** used (a later safe reclaim/re-attempt could overwrite it). Returns
`sample_count`, `p50_ms`, `p95_ms`, `p99_ms`; with no samples the percentile
fields are NULL, never zero.

### Streaming TTFT

Streaming attempts only:

```
ExecutionAttempt.first_token_at − ExecutionAttempt.started_at
```

This is **dispatch-to-first-token** TTFT, intentionally separate from queue wait.
It never decrypts StreamEvent content, never uses terminal completion time,
never includes non-stream requests, and emits NULL (not zero) with no samples.

### Retry rate

A retry is an additional `ExecutionAttempt` for the **same** gateway request.
Because v2 never auto-retries dispatched/ambiguous work, multiple attempts
currently represent the safe pre-dispatch reclaim/re-attempt path. For requests
in the observation cohort:

- `attempted_requests` — requests with ≥ 1 attempt;
- `retried_requests` — requests with `attempt_count > 1`;
- `retry_attempts = sum(max(attempt_count − 1, 0))`;
- `request_retry_rate = retried_requests / attempted_requests` (NULL when the
  denominator is 0).

Quota/budget polling (no attempt) and SDK/client retries (a brand-new gateway
request id) are **not** counted; `outcome_unknown` is not an automatic retry.

### Passive upstream health

Passive, factual execution evidence grouped by `Endpoint`; no active probes and
no synthetic "healthy %". Per endpoint: `endpoint_id`, `endpoint_name`,
`provider_account_id`, window sample count, `succeeded_attempts`,
`upstream_failed_attempts`, `ambiguous_attempts`,
`upstream_success_rate = succeeded / (succeeded + upstream_failed)` (NULL when
the denominator is 0), `rate_limited_attempts` (recorded 429),
`last_success_at`, `last_failure_at`, and the related quota-group
`cooldown_until` when present. User cancellations, pre-dispatch abandoned
attempts, and gateway/config failures are excluded from
`upstream_failed_attempts`. The frontend renders "No samples" when there is no
definitive upstream evidence; readiness never fails solely because one endpoint
has poor passive health.

## Percentile computation

Percentiles are computed by PostgreSQL (`percentile_cont`) in bounded,
read-only, project-scoped SQL. Unbounded history is never loaded into
Python/React; project scoping is applied in SQL, never by post-filtering all
rows. The migration-0017 indexes support the intended rolling-window access
paths.

## Admin API

Two typed endpoints under `/admin/v1/observability`:

### `GET /admin/v1/observability/summary`

Query: `window_seconds` (optional), `project_id` (optional, only where
authorized). Response: window metadata, queue-wait percentiles, streaming-TTFT
percentiles, retry metrics.

Authorization uses the existing `admin:queue:read` permission (no new RBAC scope
for queue-derived read metrics): `system_admin` reads the deployment aggregate or
an explicit project; `project_admin`/`project_viewer` read only their own
project; a cross-project project-role query is non-enumerating (`404`).

### `GET /admin/v1/observability/upstreams`

`system_admin` deployment scope only. Paginated/safely bounded passive-health
rows. Project roles receive `403` and can never enumerate endpoint/provider
account deployment health. No prompt/completion/provider-secret material appears
in either response.

## Generated client / dashboard

Typed admin contracts are the source of truth; `frontend/src/generated/` is
regenerated (`scripts/gen_openapi.py` + `npm run generate:client`), never
hand-edited. The dashboard replaces the three "Not yet instrumented"
placeholders with real queue-wait, streaming-TTFT, and retry metrics, adds
system-admin passive upstream-health cards and a 15m/1h/24h window selector, and
renders "No samples" (never fake `0ms` or `100%`) when there is no sample. The
existing queued/in-flight/outcome_unknown, oldest wait, slots, quota/cooldown,
and Decimal-safe budget headroom are retained; polling reuses the bounded
hidden-tab-aware helper.

## Non-goals (deferred)

Prometheus/OpenTelemetry exporters, external metrics retention/TSDB, active
upstream synthetic probes, alerting/paging, and any logging-stack rewrite remain
deferred. New observability code logs only stable request/attempt/endpoint IDs,
never content, secrets, or provider response bodies/headers.
