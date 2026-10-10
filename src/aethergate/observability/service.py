"""Observability administration service.

Read-only operational telemetry derived from durable scheduler facts. This
service authorizes internally against a typed
:class:`~aethergate.domain.entities.AdminRequestContext` and returns safe,
non-content read models; routers stay thin and contain no SQL or business policy.

Two authorization shapes apply:

- **Project-scoped summary** (``admin:queue:read`` via the existing permission):
  ``system_admin`` reads the deployment aggregate or an explicit project;
  ``project_admin``/``project_viewer`` read only their own project. A project
  role requesting another project is non-enumerating (``404``).
- **Deployment upstream health**: requires ``system_admin`` deployment authority
  **and** ``admin:queue:read``. Project roles are denied (``403``) and can never
  enumerate endpoint/provider-account deployment health.

No prompt/completion content, encrypted payload/result, stream-event body, or
provider secret material is read or returned.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from aethergate.domain import entities as domain
from aethergate.domain.enums import AdminAuthenticationKind, CredentialScope
from aethergate.domain.ids import ProjectId
from aethergate.errors import AdminAuthorizationError, AdminResourceNotFound, AdminValidationError
from aethergate.identity.authorization import (
    RESOURCE_DEPLOYMENT,
    RESOURCE_PROJECT,
    authorize_admin,
    authorized_project_ids,
)
from aethergate.observability import repository

DEFAULT_WINDOW_SECONDS = 900
MIN_WINDOW_SECONDS = 60
MAX_WINDOW_SECONDS = 86_400


@dataclass(frozen=True)
class Window:
    window_start: datetime
    window_end: datetime
    window_seconds: int


@dataclass(frozen=True)
class PercentileView:
    sample_count: int
    p50_ms: float | None
    p95_ms: float | None
    p99_ms: float | None


@dataclass(frozen=True)
class RetryView:
    attempted_requests: int
    retried_requests: int
    retry_attempts: int
    request_retry_rate: float | None


@dataclass(frozen=True)
class SummaryView:
    window: Window
    queue_wait: PercentileView
    ttft: PercentileView
    retry: RetryView


@dataclass(frozen=True)
class UpstreamHealthView:
    endpoint_id: str
    endpoint_name: str
    provider_account_id: str | None
    sample_count: int
    succeeded_attempts: int
    upstream_failed_attempts: int
    ambiguous_attempts: int
    rate_limited_attempts: int
    upstream_success_rate: float | None
    last_success_at: datetime | None
    last_failure_at: datetime | None
    cooldown_until: datetime | None


def utcnow() -> datetime:
    return datetime.now(UTC)


def resolve_window(window_seconds: int | None) -> Window:
    seconds = DEFAULT_WINDOW_SECONDS if window_seconds is None else window_seconds
    if seconds < MIN_WINDOW_SECONDS or seconds > MAX_WINDOW_SECONDS:
        raise AdminValidationError(
            f"window_seconds must be between {MIN_WINDOW_SECONDS} and "
            f"{MAX_WINDOW_SECONDS}"
        )
    end = utcnow()
    return Window(
        window_start=end - timedelta(seconds=seconds),
        window_end=end,
        window_seconds=seconds,
    )


def _authorize_queue_read(context: domain.AdminRequestContext) -> None:
    """Authorize the existing queue-derived read permission (service + role)."""
    if (
        context.authentication_kind is AdminAuthenticationKind.SERVICE_CREDENTIAL
        and CredentialScope.ADMIN_QUEUE_READ not in context.scopes
    ):
        raise AdminAuthorizationError()
    from aethergate.identity import rbac

    if not any(
        rbac.role_grants_permission(a.role, CredentialScope.ADMIN_QUEUE_READ)
        for a in context.assignments
    ):
        raise AdminAuthorizationError()


def _summary_scope(
    context: domain.AdminRequestContext, project_id: ProjectId | None
) -> set[str] | None:
    """Resolve the project scope for a summary read.

    Returns ``None`` for the full deployment aggregate (``system_admin`` with no
    explicit project), otherwise the concrete set of project IDs to include. A
    project role requesting another project is indistinguishable from a
    nonexistent one (``404``); a project role with no explicit project is limited
    to its own authorized projects.
    """
    _authorize_queue_read(context)
    authorized = authorized_project_ids(context)
    if authorized is None:
        # system_admin: deployment aggregate unless an explicit project is asked.
        return {str(project_id)} if project_id is not None else None
    if project_id is not None:
        if project_id not in authorized:
            raise AdminResourceNotFound()
        return {str(project_id)}
    return {str(p) for p in authorized}


async def get_summary(
    session: AsyncSession,
    *,
    context: domain.AdminRequestContext,
    window_seconds: int | None = None,
    project_id: ProjectId | None = None,
) -> SummaryView:
    scope = _summary_scope(context, project_id)
    window = resolve_window(window_seconds)
    qw = await repository.queue_wait_percentiles(
        session,
        window_start=window.window_start,
        window_end=window.window_end,
        project_ids=scope,
    )
    ttft = await repository.ttft_percentiles(
        session,
        window_start=window.window_start,
        window_end=window.window_end,
        project_ids=scope,
    )
    retry = await repository.retry_metrics(
        session,
        window_start=window.window_start,
        window_end=window.window_end,
        project_ids=scope,
    )
    request_retry_rate = (
        retry.retried_requests / retry.attempted_requests
        if retry.attempted_requests > 0
        else None
    )
    return SummaryView(
        window=window,
        queue_wait=PercentileView(
            sample_count=qw.sample_count,
            p50_ms=qw.p50_ms,
            p95_ms=qw.p95_ms,
            p99_ms=qw.p99_ms,
        ),
        ttft=PercentileView(
            sample_count=ttft.sample_count,
            p50_ms=ttft.p50_ms,
            p95_ms=ttft.p95_ms,
            p99_ms=ttft.p99_ms,
        ),
        retry=RetryView(
            attempted_requests=retry.attempted_requests,
            retried_requests=retry.retried_requests,
            retry_attempts=retry.retry_attempts,
            request_retry_rate=request_retry_rate,
        ),
    )


async def list_upstream_health(
    session: AsyncSession,
    *,
    context: domain.AdminRequestContext,
    window_seconds: int | None = None,
) -> tuple[Window, list[UpstreamHealthView]]:
    authorize_admin(
        context, CredentialScope.ADMIN_QUEUE_READ, RESOURCE_DEPLOYMENT, None
    )
    window = resolve_window(window_seconds)
    rows = await repository.upstream_health(
        session,
        window_start=window.window_start,
        window_end=window.window_end,
    )
    views: list[UpstreamHealthView] = []
    for row in rows:
        denominator = row.succeeded_attempts + row.upstream_failed_attempts
        success_rate = (
            row.succeeded_attempts / denominator if denominator > 0 else None
        )
        views.append(
            UpstreamHealthView(
                endpoint_id=row.endpoint_id,
                endpoint_name=row.endpoint_name,
                provider_account_id=row.provider_account_id,
                sample_count=row.sample_count,
                succeeded_attempts=row.succeeded_attempts,
                upstream_failed_attempts=row.upstream_failed_attempts,
                ambiguous_attempts=row.ambiguous_attempts,
                rate_limited_attempts=row.rate_limited_attempts,
                upstream_success_rate=success_rate,
                last_success_at=row.last_success_at,
                last_failure_at=row.last_failure_at,
                cooldown_until=row.cooldown_until,
            )
        )
    return window, views


__all__ = [
    "DEFAULT_WINDOW_SECONDS",
    "MIN_WINDOW_SECONDS",
    "MAX_WINDOW_SECONDS",
    "Window",
    "PercentileView",
    "RetryView",
    "SummaryView",
    "UpstreamHealthView",
    "resolve_window",
    "get_summary",
    "list_upstream_health",
    "utcnow",
    "RESOURCE_PROJECT",
]
