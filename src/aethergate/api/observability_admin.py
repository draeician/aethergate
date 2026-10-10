"""Operational observability HTTP surface.

Thin routers over :mod:`aethergate.observability.service`. Two endpoints:

- ``GET /admin/v1/observability/summary`` — queue-wait, streaming-TTFT, and
  retry metrics for the caller's authorized scope.
- ``GET /admin/v1/observability/upstreams`` — passive per-endpoint upstream
  health, ``system_admin`` deployment scope only.

No prompt/completion/provider-secret content is ever returned. Authorization and
validation live in the service; routers contain no SQL or business policy.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query

from aethergate.api.admin import AdminContextDep, SessionDep
from aethergate.contracts.admin_v1 import (
    ObservabilitySummaryRead,
    ObservabilityWindowRead,
    PercentileMetricsRead,
    RetryMetricsRead,
    UpstreamHealthRead,
)
from aethergate.contracts.common import Page
from aethergate.domain.ids import ProjectId
from aethergate.observability import service as observability

router = APIRouter(prefix="/admin/v1/observability", tags=["admin-observability"])

WindowSecondsQuery = Annotated[int | None, Query(ge=1)]
ProjectIdQuery = Annotated[ProjectId | None, Query()]


def _window_read(window: observability.Window) -> ObservabilityWindowRead:
    return ObservabilityWindowRead(
        window_start=window.window_start,
        window_end=window.window_end,
        window_seconds=window.window_seconds,
    )


def _percentile_read(p: observability.PercentileView) -> PercentileMetricsRead:
    return PercentileMetricsRead(
        sample_count=p.sample_count,
        p50_ms=p.p50_ms,
        p95_ms=p.p95_ms,
        p99_ms=p.p99_ms,
    )


@router.get("/summary", response_model=ObservabilitySummaryRead)
async def get_summary(
    context: AdminContextDep,
    session: SessionDep,
    window_seconds: WindowSecondsQuery = None,
    project_id: ProjectIdQuery = None,
) -> ObservabilitySummaryRead:
    view = await observability.get_summary(
        session,
        context=context,
        window_seconds=window_seconds,
        project_id=project_id,
    )
    return ObservabilitySummaryRead(
        window=_window_read(view.window),
        queue_wait=_percentile_read(view.queue_wait),
        ttft=_percentile_read(view.ttft),
        retry=RetryMetricsRead(
            attempted_requests=view.retry.attempted_requests,
            retried_requests=view.retry.retried_requests,
            retry_attempts=view.retry.retry_attempts,
            request_retry_rate=view.retry.request_retry_rate,
        ),
    )


@router.get("/upstreams", response_model=Page[UpstreamHealthRead])
async def list_upstreams(
    context: AdminContextDep,
    session: SessionDep,
    window_seconds: WindowSecondsQuery = None,
) -> Page[UpstreamHealthRead]:
    window, views = await observability.list_upstream_health(
        session, context=context, window_seconds=window_seconds
    )
    items = [
        UpstreamHealthRead(
            endpoint_id=v.endpoint_id,
            endpoint_name=v.endpoint_name,
            provider_account_id=v.provider_account_id,
            sample_count=v.sample_count,
            succeeded_attempts=v.succeeded_attempts,
            upstream_failed_attempts=v.upstream_failed_attempts,
            ambiguous_attempts=v.ambiguous_attempts,
            rate_limited_attempts=v.rate_limited_attempts,
            upstream_success_rate=v.upstream_success_rate,
            last_success_at=v.last_success_at,
            last_failure_at=v.last_failure_at,
            cooldown_until=v.cooldown_until,
        )
        for v in views
    ]
    return Page(items=items, limit=len(items), offset=0, total=len(items))


__all__ = ["router"]
