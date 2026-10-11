"""Observability read models derived from durable scheduler facts.

This module is a pure read layer over existing durable lifecycle rows
(``inference_requests`` and ``execution_attempts``). It never mutates admission,
quota, budget, or scheduling state, and it never reads prompt/completion content,
encrypted payloads, stream-event bodies, or provider secret material.

All percentile computation is performed by PostgreSQL (``percentile_cont``) over
project-scoped, bounded-window queries; no unbounded history is loaded into
Python. Project scoping is applied in SQL, never by post-filtering all rows.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession


@dataclass(frozen=True)
class PercentileResult:
    sample_count: int
    p50_ms: float | None
    p95_ms: float | None
    p99_ms: float | None


@dataclass(frozen=True)
class RetryResult:
    attempted_requests: int
    retried_requests: int
    retry_attempts: int


@dataclass(frozen=True)
class UpstreamHealthRow:
    endpoint_id: str
    endpoint_name: str
    provider_account_id: str | None
    sample_count: int
    succeeded_attempts: int
    upstream_failed_attempts: int
    ambiguous_attempts: int
    rate_limited_attempts: int
    last_success_at: datetime | None
    last_failure_at: datetime | None
    cooldown_until: datetime | None


def _project_filter(
    project_ids: set[str] | None, column: str
) -> tuple[str, dict, object]:
    """Return SQL clause, bound params, and bindparam for optional project scope.

    ``project_ids is None`` means the deployment aggregate (no filter); an empty
    set means the caller may see no projects (matches nothing). The scope is
    applied in SQL via an expanding bind parameter, never by post-filtering.
    """
    if project_ids is None:
        return "", {}, None
    clause = f" AND {column} IN :project_ids"
    return (
        clause,
        {"project_ids": list(project_ids)},
        bindparam("project_ids", expanding=True),
    )


def _run(session_stmt, sql: str, filter_obj: object):
    if filter_obj is not None:
        return session_stmt.bindparams(filter_obj)
    return sql


async def queue_wait_percentiles(
    session: AsyncSession,
    *,
    window_start: datetime,
    window_end: datetime,
    project_ids: set[str] | None,
) -> PercentileResult:
    """Queue-wait percentiles: earliest attempt ``started_at`` minus ``queued_at``.

    One sample per request whose ``queued_at`` falls in the window and that
    acquired at least one execution attempt. Uses the earliest attempt
    ``started_at`` (never the mutable ``inference_requests.started_at``).
    Read-only, bounded, and project-scoped in SQL.
    """
    clause, params, expanding = _project_filter(project_ids, "r.project_id")
    stmt = text(
        f"""
        SELECT
            count(*) AS n,
            percentile_cont(0.5) WITHIN GROUP (
                ORDER BY EXTRACT(EPOCH FROM (a.started_at - r.queued_at)) * 1000.0
            ) AS p50,
            percentile_cont(0.95) WITHIN GROUP (
                ORDER BY EXTRACT(EPOCH FROM (a.started_at - r.queued_at)) * 1000.0
            ) AS p95,
            percentile_cont(0.99) WITHIN GROUP (
                ORDER BY EXTRACT(EPOCH FROM (a.started_at - r.queued_at)) * 1000.0
            ) AS p99
        FROM inference_requests r
        JOIN (
            SELECT request_id, min(started_at) AS started_at
            FROM execution_attempts
            WHERE started_at IS NOT NULL
            GROUP BY request_id
        ) a ON a.request_id = r.id
        WHERE r.queued_at IS NOT NULL
          AND r.queued_at >= :window_start
          AND r.queued_at < :window_end
          {clause}
        """
    )
    if expanding is not None:
        stmt = stmt.bindparams(expanding)
    row = (
        await session.execute(
            stmt,
            {"window_start": window_start, "window_end": window_end, **params},
        )
    ).one()
    count = int(row.n or 0)
    if count == 0:
        return PercentileResult(0, None, None, None)
    return PercentileResult(
        sample_count=count,
        p50_ms=float(row.p50),
        p95_ms=float(row.p95),
        p99_ms=float(row.p99),
    )


async def ttft_percentiles(
    session: AsyncSession,
    *,
    window_start: datetime,
    window_end: datetime,
    project_ids: set[str] | None,
) -> PercentileResult:
    """Streaming dispatch-to-first-token percentiles.

    Streaming attempts only; value is ``first_token_at - started_at`` in
    milliseconds. An attempt with no observed ``first_token_at`` is excluded
    entirely, so no sample yields NULL percentiles (never a fabricated zero).
    Cohort membership is keyed on ``execution_attempts.started_at``.
    """
    clause, params, expanding = _project_filter(project_ids, "r.project_id")
    stmt = text(
        f"""
        SELECT
            count(*) AS n,
            percentile_cont(0.5) WITHIN GROUP (
                ORDER BY EXTRACT(EPOCH FROM (a.first_token_at - a.started_at)) * 1000.0
            ) AS p50,
            percentile_cont(0.95) WITHIN GROUP (
                ORDER BY EXTRACT(EPOCH FROM (a.first_token_at - a.started_at)) * 1000.0
            ) AS p95,
            percentile_cont(0.99) WITHIN GROUP (
                ORDER BY EXTRACT(EPOCH FROM (a.first_token_at - a.started_at)) * 1000.0
            ) AS p99
        FROM execution_attempts a
        JOIN inference_requests r ON r.id = a.request_id
        WHERE r.stream IS TRUE
          AND a.first_token_at IS NOT NULL
          AND a.started_at IS NOT NULL
          AND a.started_at >= :window_start
          AND a.started_at < :window_end
          {clause}
        """
    )
    if expanding is not None:
        stmt = stmt.bindparams(expanding)
    row = (
        await session.execute(
            stmt,
            {"window_start": window_start, "window_end": window_end, **params},
        )
    ).one()
    count = int(row.n or 0)
    if count == 0:
        return PercentileResult(0, None, None, None)
    return PercentileResult(
        sample_count=count,
        p50_ms=float(row.p50),
        p95_ms=float(row.p95),
        p99_ms=float(row.p99),
    )


async def retry_metrics(
    session: AsyncSession,
    *,
    window_start: datetime,
    window_end: datetime,
    project_ids: set[str] | None,
) -> RetryResult:
    """Retry metrics counting additional execution attempts per gateway request.

    Cohort: requests whose ``queued_at`` falls in the window. A retry is an
    additional ``ExecutionAttempt`` for the **same** gateway request id; queued
    polling (no attempt) and SDK/client retries (new request id) are never
    counted. Read-only and project-scoped in SQL.
    """
    clause, params, expanding = _project_filter(project_ids, "r.project_id")
    stmt = text(
        f"""
        SELECT
            count(*) FILTER (WHERE t.attempt_count >= 1) AS attempted,
            count(*) FILTER (WHERE t.attempt_count > 1) AS retried,
            COALESCE(sum(GREATEST(t.attempt_count - 1, 0)), 0) AS retry_attempts
        FROM (
            SELECT r.id AS request_id, count(a.id) AS attempt_count
            FROM inference_requests r
            LEFT JOIN execution_attempts a ON a.request_id = r.id
            WHERE r.queued_at IS NOT NULL
              AND r.queued_at >= :window_start
              AND r.queued_at < :window_end
              {clause}
            GROUP BY r.id
        ) t
        """
    )
    if expanding is not None:
        stmt = stmt.bindparams(expanding)
    row = (
        await session.execute(
            stmt,
            {"window_start": window_start, "window_end": window_end, **params},
        )
    ).one()
    return RetryResult(
        attempted_requests=int(row.attempted or 0),
        retried_requests=int(row.retried or 0),
        retry_attempts=int(row.retry_attempts or 0),
    )


async def upstream_health(
    session: AsyncSession,
    *,
    window_start: datetime,
    window_end: datetime,
) -> list[UpstreamHealthRow]:
    """Passive per-endpoint upstream health from terminal attempt evidence.

    Deployment-scoped and read-only. Cohort membership is keyed on
    ``execution_attempts.finished_at``. ``upstream_failed_attempts`` counts only
    attempts whose terminal failure was a provider failure; ambiguous
    (``outcome_unknown``) attempts are separate; ``rate_limited_attempts`` counts
    a recorded 429 status. No active probe is performed.
    """
    stmt = text(
        """
        SELECT
            e.id AS endpoint_id,
            e.name AS endpoint_name,
            e.provider_account_id AS provider_account_id,
            count(*) AS sample_count,
            count(*) FILTER (WHERE a.state = 'succeeded') AS succeeded_attempts,
            count(*) FILTER (WHERE a.upstream_error IS TRUE) AS upstream_failed_attempts,
            count(*) FILTER (WHERE a.state = 'outcome_unknown') AS ambiguous_attempts,
            count(*) FILTER (WHERE a.upstream_status_code = 429) AS rate_limited_attempts,
            max(a.finished_at) FILTER (WHERE a.state = 'succeeded') AS last_success_at,
            max(a.finished_at) FILTER (WHERE a.upstream_error IS TRUE) AS last_failure_at,
            (
                SELECT max(qg.cooldown_until)
                FROM quota_groups qg
                WHERE qg.provider_account_id = e.provider_account_id
                  AND qg.cooldown_until IS NOT NULL
            ) AS cooldown_until
        FROM execution_attempts a
        JOIN endpoints e ON e.id = a.endpoint_id
        WHERE a.finished_at IS NOT NULL
          AND a.finished_at >= :window_start
          AND a.finished_at < :window_end
        GROUP BY e.id, e.name, e.provider_account_id
        ORDER BY e.name, e.id
        """
    )
    rows = (
        await session.execute(
            stmt, {"window_start": window_start, "window_end": window_end}
        )
    ).all()
    return [
        UpstreamHealthRow(
            endpoint_id=row.endpoint_id,
            endpoint_name=row.endpoint_name,
            provider_account_id=row.provider_account_id,
            sample_count=int(row.sample_count or 0),
            succeeded_attempts=int(row.succeeded_attempts or 0),
            upstream_failed_attempts=int(row.upstream_failed_attempts or 0),
            ambiguous_attempts=int(row.ambiguous_attempts or 0),
            rate_limited_attempts=int(row.rate_limited_attempts or 0),
            last_success_at=row.last_success_at,
            last_failure_at=row.last_failure_at,
            cooldown_until=row.cooldown_until,
        )
        for row in rows
    ]


__all__ = [
    "PercentileResult",
    "RetryResult",
    "UpstreamHealthRow",
    "queue_wait_percentiles",
    "ttft_percentiles",
    "retry_metrics",
    "upstream_health",
]
