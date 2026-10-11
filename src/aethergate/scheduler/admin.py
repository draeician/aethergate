"""Scheduler/operator administration service (queue control plane).

The operator surface reads and manages the durable inference queue without ever
decrypting or exposing inference content. Every read returns only safe
non-content scheduler metadata; every mutation flows through the same
session-bound transition primitives as the inference scheduler and the
development ``reconcile.py`` CLI, so there is no second lifecycle to drift.

Authorization is centralized and follows the control-plane conventions:

- ``admin:queue:read`` — request metadata reads, queue summary.
- ``admin:queue:write`` — cancellation, reconciliation, endpoint pause/drain/
  resume.

Project-scoped roles read/cancel only their own projects' requests; a
cross-project opaque request ID is indistinguishable from a nonexistent one
(``404``). Deployment-scoped operations (endpoint runtime, pause/drain/resume,
outcome_unknown reconciliation, provider quota status) require ``system_admin``
deployment authority; a project_admin credential carrying ``admin:queue:*``
never gains deployment operator authority.

Mutation services accept a typed :class:`AdminRequestContext` and authorize
internally; routers remain thin.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from aethergate.domain import entities as domain
from aethergate.domain.enums import (
    AdminAuthenticationKind,
    CredentialScope,
    EndpointOperationalState,
    RequestState,
)
from aethergate.domain.ids import (
    AuditEventId,
    EndpointId,
    PrincipalId,
    ProjectId,
    RequestId,
)
from aethergate.errors import (
    AdminAuthorizationError,
    AdminResourceNotFound,
    AdminValidationError,
    QueueTransitionError,
)
from aethergate.identity import rbac
from aethergate.identity.authorization import (
    RESOURCE_DEPLOYMENT,
    RESOURCE_PROJECT,
    authorize_admin,
    authorized_project_ids,
)
from aethergate.persistence import models, repository
from aethergate.scheduler import repository as sched_repo
from aethergate.scheduler.service import (
    RECONCILABLE_STATES,
    cancel_request_transition,
    reconcile_transition,
)

# Audit action names (stable strings; safe metadata only).
AUDIT_REQUEST_CANCELLED = "request.cancelled"
AUDIT_REQUEST_CANCELLATION_REQUESTED = "request.cancellation_requested"
AUDIT_REQUEST_RECONCILED = "request.reconciled"
AUDIT_ENDPOINT_PAUSED = "endpoint_dispatch.paused"
AUDIT_ENDPOINT_DRAINING = "endpoint_dispatch.draining"
AUDIT_ENDPOINT_RESUMED = "endpoint_dispatch.resumed"

# Cancellation outcome keywords returned by the shared transition primitive.
_CANCELLED_NOW = "cancelled_now"
_CANCELLATION_REQUESTED = "cancellation_requested"
_ALREADY_CANCELLED = "already_cancelled"

_IN_FLIGHT_STATES = frozenset(
    {RequestState.RESERVED, RequestState.DISPATCHED, RequestState.STREAMING}
)


def utcnow() -> datetime:
    return datetime.now(UTC)


def _new_id() -> str:
    return uuid.uuid4().hex


# ---------------------------------------------------------------------------
# Authorization helpers
# ---------------------------------------------------------------------------


def _authorize_deployment(context: domain.AdminRequestContext, permission: CredentialScope) -> None:
    authorize_admin(context, permission, RESOURCE_DEPLOYMENT, None)


def _authorize_project_list(
    context: domain.AdminRequestContext, permission: CredentialScope
) -> None:
    """Authorize a project-scoped *list* read; visible projects derived separately."""
    if (
        context.authentication_kind is AdminAuthenticationKind.SERVICE_CREDENTIAL
        and permission not in context.scopes
    ):
        raise AdminAuthorizationError()
    if not any(
        rbac.role_grants_permission(a.role, permission) for a in context.assignments
    ):
        raise AdminAuthorizationError()


def _project_list_scope(
    context: domain.AdminRequestContext, project_id: ProjectId | None
) -> tuple[set[ProjectId] | None, bool]:
    authorized = authorized_project_ids(context)
    if project_id is not None:
        if authorized is not None and project_id not in authorized:
            return None, False
        return {project_id}, True
    return authorized, True


async def _resolve_request(
    session: AsyncSession,
    context: domain.AdminRequestContext,
    request_id: RequestId,
    permission: CredentialScope,
) -> models.InferenceRequest:
    row = await sched_repo.get_request(session, str(request_id))
    if row is None:
        raise AdminResourceNotFound()
    authorized = authorized_project_ids(context)
    if authorized is not None:
        # Project-scoped caller: only its own projects' requests are visible; a
        # deployment-scoped (NULL project) request is hidden.
        if row.project_id is None or ProjectId(row.project_id) not in authorized:
            raise AdminResourceNotFound()
        authorize_admin(context, permission, RESOURCE_PROJECT, ProjectId(row.project_id))
    else:
        authorize_admin(context, permission, RESOURCE_DEPLOYMENT, None)
    return row


# ---------------------------------------------------------------------------
# Read views (safe, non-content)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class QueueRequestView:
    request_id: str
    project_id: str | None
    principal_id: str | None
    api_credential_id: str | None
    model_alias_id: str
    endpoint_id: str | None
    quota_group_id: str | None
    state: str
    stream: bool
    queued_at: datetime | None
    started_at: datetime | None
    finished_at: datetime | None
    queue_wait_until: datetime | None
    expires_at: datetime | None
    cancellation_requested: bool
    wait_reason: str | None
    wait_limit_id: str | None
    wait_limit_metric: str | None
    next_eligible_at: datetime | None
    error_code: str | None
    price_snapshot_id: str | None
    reconciled_state: str | None
    reconciled_at: datetime | None
    reconciled_by: str | None
    worker_id: str | None
    lease_expires_at: datetime | None
    effective_wait_reason: str | None


@dataclass(frozen=True)
class EndpointRuntimeView:
    endpoint_id: str
    name: str
    is_active: bool
    operational_state: str
    max_concurrency: int
    occupied_slots: int
    available_slots: int
    draining_complete: bool
    oldest_queued_at: datetime | None


@dataclass(frozen=True)
class QuotaStatusView:
    quota_group_id: str
    quota_group_name: str
    provider_account_id: str
    quota_limit_id: str
    quota_limit_name: str | None
    metric: str
    limit_units: int
    window_seconds: int
    window_start: datetime
    window_end: datetime
    committed_units: int
    reserved_units: int
    remaining_units: int
    enabled: bool
    cooldown_until: datetime | None


def _effective_wait_reason(
    state: str, wait_reason: str | None, endpoint_operational_state: str | None
) -> str | None:
    if state != RequestState.QUEUED:
        return None
    if endpoint_operational_state == EndpointOperationalState.PAUSED.value:
        return "endpoint_paused"
    if endpoint_operational_state == EndpointOperationalState.DRAINING.value:
        return "endpoint_draining"
    return wait_reason


def _to_request_view(
    row: models.InferenceRequest, endpoint_operational_state: str | None
) -> QueueRequestView:
    return QueueRequestView(
        request_id=row.id,
        project_id=row.project_id,
        principal_id=row.principal_id,
        api_credential_id=row.api_credential_id,
        model_alias_id=row.model_alias_id,
        endpoint_id=row.endpoint_id,
        quota_group_id=row.quota_group_id,
        state=row.state,
        stream=row.stream,
        queued_at=row.queued_at,
        started_at=row.started_at,
        finished_at=row.finished_at,
        queue_wait_until=row.queue_wait_until,
        expires_at=row.expires_at,
        cancellation_requested=row.cancellation_requested,
        wait_reason=row.wait_reason,
        wait_limit_id=row.wait_limit_id,
        wait_limit_metric=row.wait_limit_metric,
        next_eligible_at=row.next_eligible_at,
        error_code=row.error_code,
        price_snapshot_id=row.price_snapshot_id,
        reconciled_state=row.reconciled_state,
        reconciled_at=row.reconciled_at,
        reconciled_by=row.reconciled_by,
        worker_id=row.worker_id,
        lease_expires_at=row.lease_expires_at,
        effective_wait_reason=_effective_wait_reason(
            row.state, row.wait_reason, endpoint_operational_state
        ),
    )


async def _endpoint_states_for(
    session: AsyncSession, endpoint_ids: set[str]
) -> dict[str, str]:
    return await sched_repo.list_endpoint_operational_states(session, endpoint_ids)


# ---------------------------------------------------------------------------
# Request metadata reads
# ---------------------------------------------------------------------------


async def list_requests(
    session: AsyncSession,
    *,
    context: domain.AdminRequestContext,
    project_id: ProjectId | None = None,
    principal_id: PrincipalId | None = None,
    api_credential_id: str | None = None,
    model_alias_id: str | None = None,
    endpoint_id: EndpointId | None = None,
    state: str | None = None,
    stream: bool | None = None,
    wait_reason: str | None = None,
    queued_from: datetime | None = None,
    queued_to: datetime | None = None,
    created_from: datetime | None = None,
    created_to: datetime | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[QueueRequestView], int]:
    _authorize_project_list(context, CredentialScope.ADMIN_QUEUE_READ)
    scope, visible = _project_list_scope(context, project_id)
    if not visible:
        return [], 0
    rows = await sched_repo.list_requests_paged(
        session,
        limit=limit,
        offset=offset,
        project_ids=scope,
        principal_id=str(principal_id) if principal_id is not None else None,
        api_credential_id=api_credential_id,
        model_alias_id=model_alias_id,
        endpoint_id=str(endpoint_id) if endpoint_id is not None else None,
        state=state,
        stream=stream,
        wait_reason=wait_reason,
        queued_from=queued_from,
        queued_to=queued_to,
        created_from=created_from,
        created_to=created_to,
    )
    total = await sched_repo.count_requests(
        session,
        project_ids=scope,
        principal_id=str(principal_id) if principal_id is not None else None,
        api_credential_id=api_credential_id,
        model_alias_id=model_alias_id,
        endpoint_id=str(endpoint_id) if endpoint_id is not None else None,
        state=state,
        stream=stream,
        wait_reason=wait_reason,
        queued_from=queued_from,
        queued_to=queued_to,
        created_from=created_from,
        created_to=created_to,
    )
    states = await _endpoint_states_for(
        session, {r.endpoint_id for r in rows if r.endpoint_id is not None}
    )
    views = [_to_request_view(r, states.get(r.endpoint_id)) for r in rows]
    return views, total


async def get_request(
    session: AsyncSession,
    *,
    context: domain.AdminRequestContext,
    request_id: RequestId,
) -> QueueRequestView:
    row = await _resolve_request(session, context, request_id, CredentialScope.ADMIN_QUEUE_READ)
    states = await _endpoint_states_for(
        session, {row.endpoint_id} if row.endpoint_id is not None else set()
    )
    return _to_request_view(row, states.get(row.endpoint_id))


async def get_summary(
    session: AsyncSession, *, context: domain.AdminRequestContext
) -> tuple[dict[str, int], int, int, int, datetime | None, int | None]:
    _authorize_project_list(context, CredentialScope.ADMIN_QUEUE_READ)
    scope = authorized_project_ids(context)
    counts = await sched_repo.count_requests_by_state(session, project_ids=scope)
    queued_total = counts.get(RequestState.QUEUED, 0)
    in_flight_total = sum(counts.get(s, 0) for s in _IN_FLIGHT_STATES)
    outcome_unknown_total = counts.get(RequestState.OUTCOME_UNKNOWN, 0)
    oldest = await sched_repo.oldest_queued_at(session, project_ids=scope)
    oldest_wait = int((utcnow() - oldest).total_seconds()) if oldest is not None else None
    return counts, queued_total, in_flight_total, outcome_unknown_total, oldest, oldest_wait


# ---------------------------------------------------------------------------
# Endpoint runtime reads (deployment-only)
# ---------------------------------------------------------------------------


async def list_endpoint_runtime(
    session: AsyncSession, *, context: domain.AdminRequestContext
) -> list[EndpointRuntimeView]:
    _authorize_deployment(context, CredentialScope.ADMIN_QUEUE_READ)
    endpoints = await sched_repo.list_all_endpoints(session)
    occupied = await sched_repo.count_active_reservations_by_endpoint(session)
    views: list[EndpointRuntimeView] = []
    for ep in endpoints:
        occ = occupied.get(ep.id, 0)
        oldest = await sched_repo.oldest_queued_for_endpoint(session, ep.id)
        views.append(
            EndpointRuntimeView(
                endpoint_id=ep.id,
                name=ep.name,
                is_active=ep.is_active,
                operational_state=ep.operational_state,
                max_concurrency=ep.max_concurrency,
                occupied_slots=occ,
                available_slots=max(0, ep.max_concurrency - occ),
                draining_complete=(
                    ep.operational_state == EndpointOperationalState.DRAINING.value and occ == 0
                ),
                oldest_queued_at=oldest,
            )
        )
    return views


async def get_endpoint_runtime(
    session: AsyncSession, *, context: domain.AdminRequestContext, endpoint_id: EndpointId
) -> EndpointRuntimeView:
    _authorize_deployment(context, CredentialScope.ADMIN_QUEUE_READ)
    ep = await sched_repo.get_endpoint_row(session, str(endpoint_id))
    if ep is None:
        raise AdminResourceNotFound()
    occ = await sched_repo.count_active_reservations(session, ep.id)
    oldest = await sched_repo.oldest_queued_for_endpoint(session, ep.id)
    return EndpointRuntimeView(
        endpoint_id=ep.id,
        name=ep.name,
        is_active=ep.is_active,
        operational_state=ep.operational_state,
        max_concurrency=ep.max_concurrency,
        occupied_slots=occ,
        available_slots=max(0, ep.max_concurrency - occ),
        draining_complete=(
            ep.operational_state == EndpointOperationalState.DRAINING.value and occ == 0
        ),
        oldest_queued_at=oldest,
    )


# ---------------------------------------------------------------------------
# outcome_unknown reads (deployment-only)
# ---------------------------------------------------------------------------


async def list_outcome_unknown(
    session: AsyncSession,
    *,
    context: domain.AdminRequestContext,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[QueueRequestView], int]:
    _authorize_deployment(context, CredentialScope.ADMIN_QUEUE_READ)
    rows = await sched_repo.list_outcome_unknown_paged(session, limit=limit, offset=offset)
    total = await sched_repo.count_outcome_unknown(session)
    states = await _endpoint_states_for(
        session, {r.endpoint_id for r in rows if r.endpoint_id is not None}
    )
    views = [_to_request_view(r, states.get(r.endpoint_id)) for r in rows]
    return views, total


# ---------------------------------------------------------------------------
# Provider quota runtime status (deployment-only, read-only)
# ---------------------------------------------------------------------------


async def list_quota_status(
    session: AsyncSession, *, context: domain.AdminRequestContext
) -> list[QuotaStatusView]:
    _authorize_deployment(context, CredentialScope.ADMIN_QUEUE_READ)
    rows = await sched_repo.list_quota_runtime_rows(session)
    limit_ids = {r[4] for r in rows}
    windows = await sched_repo.list_quota_windows_for_limits(session, limit_ids)
    now = utcnow()
    views: list[QuotaStatusView] = []
    for row in rows:
        (
            group_id,
            group_name,
            provider_account_id,
            cooldown_until,
            limit_id,
            limit_name,
            metric,
            limit_units,
            window_seconds,
            enabled,
        ) = row
        window_start = sched_repo.fixed_window_start(now, window_seconds)
        window_end = window_start + timedelta(seconds=window_seconds)
        committed = 0
        reserved = 0
        for w in windows.get(limit_id, []):
            if w.window_start == window_start:
                committed = w.committed_units
                reserved = w.reserved_units
                break
        remaining = max(0, limit_units - committed - reserved)
        views.append(
            QuotaStatusView(
                quota_group_id=group_id,
                quota_group_name=group_name,
                provider_account_id=provider_account_id,
                quota_limit_id=limit_id,
                quota_limit_name=limit_name,
                metric=metric,
                limit_units=limit_units,
                window_seconds=window_seconds,
                window_start=window_start,
                window_end=window_end,
                committed_units=committed,
                reserved_units=reserved,
                remaining_units=remaining,
                enabled=enabled,
                cooldown_until=cooldown_until,
            )
        )
    return views


# ---------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------


async def _write_audit(
    session: AsyncSession,
    *,
    actor_principal_id: PrincipalId,
    action: str,
    resource_type: str,
    resource_id: str,
    project_id: ProjectId | None = None,
) -> None:
    await repository.create_audit_event(
        session,
        domain.AuditEvent(
            id=AuditEventId(_new_id()),
            actor_principal_id=actor_principal_id,
            project_id=project_id,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            occurred_at=utcnow(),
            metadata={},
        ),
    )


# ---------------------------------------------------------------------------
# Cancellation
# ---------------------------------------------------------------------------


async def cancel_request(
    session: AsyncSession,
    *,
    context: domain.AdminRequestContext,
    request_id: RequestId,
) -> tuple[str, str, str | None]:
    row = await _resolve_request(session, context, request_id, CredentialScope.ADMIN_QUEUE_WRITE)
    project_id = ProjectId(row.project_id) if row.project_id is not None else None
    now = utcnow()
    outcome, state = await cancel_request_transition(session, str(request_id), now)
    if outcome in (_CANCELLED_NOW, _CANCELLATION_REQUESTED):
        action = (
            AUDIT_REQUEST_CANCELLED
            if outcome == _CANCELLED_NOW
            else AUDIT_REQUEST_CANCELLATION_REQUESTED
        )
        await _write_audit(
            session,
            actor_principal_id=context.principal_id,
            action=action,
            resource_type="request",
            resource_id=str(request_id),
            project_id=project_id,
        )
    return outcome, state, str(project_id) if project_id is not None else None


# ---------------------------------------------------------------------------
# Reconciliation (deployment-only)
# ---------------------------------------------------------------------------


async def reconcile_request(
    session: AsyncSession,
    *,
    context: domain.AdminRequestContext,
    request_id: RequestId,
    disposition: str,
) -> tuple[bool, str | None]:
    _authorize_deployment(context, CredentialScope.ADMIN_QUEUE_WRITE)
    row = await sched_repo.get_request(session, str(request_id))
    if row is None:
        raise AdminResourceNotFound()
    if disposition not in RECONCILABLE_STATES:
        raise AdminValidationError(
            f"disposition must be one of {sorted(RECONCILABLE_STATES)}"
        )
    if row.state != RequestState.OUTCOME_UNKNOWN:
        raise QueueTransitionError("reconcile_not_outcome_unknown")
    project_id = ProjectId(row.project_id) if row.project_id is not None else None
    now = utcnow()
    result = await reconcile_transition(
        session,
        request_id=str(request_id),
        disposition=disposition,
        operator=str(context.principal_id),
        now=now,
    )
    if result:
        await _write_audit(
            session,
            actor_principal_id=context.principal_id,
            action=AUDIT_REQUEST_RECONCILED,
            resource_type="request",
            resource_id=str(request_id),
            project_id=project_id,
        )
    return result, str(project_id) if project_id is not None else None


# ---------------------------------------------------------------------------
# Endpoint pause / drain / resume (deployment-only)
# ---------------------------------------------------------------------------


async def _set_endpoint_operational_state(
    session: AsyncSession,
    *,
    context: domain.AdminRequestContext,
    endpoint_id: EndpointId,
    target: EndpointOperationalState,
) -> EndpointRuntimeView:
    _authorize_deployment(context, CredentialScope.ADMIN_QUEUE_WRITE)
    endpoint = await sched_repo.lock_endpoint(session, str(endpoint_id))
    if endpoint is None:
        raise AdminResourceNotFound()
    if endpoint.operational_state != target.value:
        await sched_repo.update_endpoint_operational_state(
            session, str(endpoint_id), target.value
        )
        endpoint.operational_state = target.value
        action = {
            EndpointOperationalState.PAUSED: AUDIT_ENDPOINT_PAUSED,
            EndpointOperationalState.DRAINING: AUDIT_ENDPOINT_DRAINING,
            EndpointOperationalState.ACTIVE: AUDIT_ENDPOINT_RESUMED,
        }[target]
        await _write_audit(
            session,
            actor_principal_id=context.principal_id,
            action=action,
            resource_type="endpoint",
            resource_id=str(endpoint_id),
        )
    occ = await sched_repo.count_active_reservations(session, str(endpoint_id))
    oldest = await sched_repo.oldest_queued_for_endpoint(session, str(endpoint_id))
    return EndpointRuntimeView(
        endpoint_id=endpoint.id,
        name=endpoint.name,
        is_active=endpoint.is_active,
        operational_state=endpoint.operational_state,
        max_concurrency=endpoint.max_concurrency,
        occupied_slots=occ,
        available_slots=max(0, endpoint.max_concurrency - occ),
        draining_complete=(
            endpoint.operational_state == EndpointOperationalState.DRAINING.value and occ == 0
        ),
        oldest_queued_at=oldest,
    )


async def pause_endpoint(
    session: AsyncSession, *, context: domain.AdminRequestContext, endpoint_id: EndpointId
) -> EndpointRuntimeView:
    return await _set_endpoint_operational_state(
        session,
        context=context,
        endpoint_id=endpoint_id,
        target=EndpointOperationalState.PAUSED,
    )


async def drain_endpoint(
    session: AsyncSession, *, context: domain.AdminRequestContext, endpoint_id: EndpointId
) -> EndpointRuntimeView:
    return await _set_endpoint_operational_state(
        session,
        context=context,
        endpoint_id=endpoint_id,
        target=EndpointOperationalState.DRAINING,
    )


async def resume_endpoint(
    session: AsyncSession, *, context: domain.AdminRequestContext, endpoint_id: EndpointId
) -> EndpointRuntimeView:
    return await _set_endpoint_operational_state(
        session,
        context=context,
        endpoint_id=endpoint_id,
        target=EndpointOperationalState.ACTIVE,
    )


__all__ = [
    "QueueRequestView",
    "EndpointRuntimeView",
    "QuotaStatusView",
    "list_requests",
    "get_request",
    "get_summary",
    "list_endpoint_runtime",
    "get_endpoint_runtime",
    "list_outcome_unknown",
    "list_quota_status",
    "cancel_request",
    "reconcile_request",
    "pause_endpoint",
    "drain_endpoint",
    "resume_endpoint",
    "utcnow",
]
