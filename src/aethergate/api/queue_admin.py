"""Queue/operator administration HTTP surface.

Exposes safe queue inspection, cancellation, outcome_unknown reconciliation,
endpoint pause/drain/resume, endpoint runtime/slot status, and provider quota
runtime status over ``/admin/v1/queue``. Routers are thin: authorization,
validation, and the safe scheduler transition primitives live in
:mod:`aethergate.scheduler.admin`, which accepts a typed ``AdminRequestContext``
and authorizes internally.

Request metadata DTOs never carry prompt/completion content, encrypted payload/
result bytes, stream-event bodies, fencing tokens, or provider secret material.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Query

from aethergate.api.admin import (
    DEFAULT_LIMIT,
    AdminContextDep,
    LimitQuery,
    OffsetQuery,
    SessionDep,
)
from aethergate.contracts.admin_v1 import (
    CancelResult,
    EndpointRuntimeRead,
    QueueRequestRead,
    QueueSummaryRead,
    QuotaStatusRead,
    ReconcileRequest,
    ReconcileResult,
)
from aethergate.contracts.common import Page
from aethergate.domain.ids import EndpointId, ProjectId, RequestId
from aethergate.errors import AdminResourceNotFound, QueueTransitionError
from aethergate.scheduler import admin as queue_service

router = APIRouter(prefix="/admin/v1/queue", tags=["admin-queue"])

ProjectIdQuery = Annotated[ProjectId | None, Query()]
EndpointIdQuery = Annotated[EndpointId | None, Query()]
StateQuery = Annotated[str | None, Query()]
StringQuery = Annotated[str | None, Query()]
BoolQuery = Annotated[bool | None, Query()]
DatetimeQuery = Annotated[datetime | None, Query()]


# --- converters ---------------------------------------------------------------


def _to_request_read(view: queue_service.QueueRequestView) -> QueueRequestRead:
    return QueueRequestRead(
        request_id=view.request_id,
        project_id=view.project_id,
        principal_id=view.principal_id,
        api_credential_id=view.api_credential_id,
        model_alias_id=view.model_alias_id,
        endpoint_id=view.endpoint_id,
        quota_group_id=view.quota_group_id,
        state=view.state,
        stream=view.stream,
        queued_at=view.queued_at,
        started_at=view.started_at,
        finished_at=view.finished_at,
        queue_wait_until=view.queue_wait_until,
        expires_at=view.expires_at,
        cancellation_requested=view.cancellation_requested,
        wait_reason=view.wait_reason,
        wait_limit_id=view.wait_limit_id,
        wait_limit_metric=view.wait_limit_metric,
        next_eligible_at=view.next_eligible_at,
        error_code=view.error_code,
        price_snapshot_id=view.price_snapshot_id,
        reconciled_state=view.reconciled_state,
        reconciled_at=view.reconciled_at,
        reconciled_by=view.reconciled_by,
        worker_id=view.worker_id,
        lease_expires_at=view.lease_expires_at,
        effective_wait_reason=view.effective_wait_reason,
    )


def _to_endpoint_read(view: queue_service.EndpointRuntimeView) -> EndpointRuntimeRead:
    return EndpointRuntimeRead(
        endpoint_id=view.endpoint_id,
        name=view.name,
        is_active=view.is_active,
        operational_state=view.operational_state,
        max_concurrency=view.max_concurrency,
        occupied_slots=view.occupied_slots,
        available_slots=view.available_slots,
        draining_complete=view.draining_complete,
        oldest_queued_at=view.oldest_queued_at,
    )


def _to_quota_read(view: queue_service.QuotaStatusView) -> QuotaStatusRead:
    return QuotaStatusRead(
        quota_group_id=view.quota_group_id,
        quota_group_name=view.quota_group_name,
        provider_account_id=view.provider_account_id,
        quota_limit_id=view.quota_limit_id,
        quota_limit_name=view.quota_limit_name,
        metric=view.metric,
        limit_units=view.limit_units,
        window_seconds=view.window_seconds,
        window_start=view.window_start,
        window_end=view.window_end,
        committed_units=view.committed_units,
        reserved_units=view.reserved_units,
        remaining_units=view.remaining_units,
        enabled=view.enabled,
        cooldown_until=view.cooldown_until,
    )


# --- request metadata reads ----------------------------------------------------


@router.get("/requests", response_model=Page[QueueRequestRead])
async def list_requests(
    context: AdminContextDep,
    session: SessionDep,
    project_id: ProjectIdQuery = None,
    principal_id: StringQuery = None,
    api_credential_id: StringQuery = None,
    model_alias_id: StringQuery = None,
    endpoint_id: EndpointIdQuery = None,
    state: StateQuery = None,
    stream: BoolQuery = None,
    wait_reason: StringQuery = None,
    queued_from: DatetimeQuery = None,
    queued_to: DatetimeQuery = None,
    created_from: DatetimeQuery = None,
    created_to: DatetimeQuery = None,
    limit: LimitQuery = DEFAULT_LIMIT,
    offset: OffsetQuery = 0,
) -> Page[QueueRequestRead]:
    items, total = await queue_service.list_requests(
        session,
        context=context,
        project_id=project_id,
        principal_id=principal_id,
        api_credential_id=api_credential_id,
        model_alias_id=model_alias_id,
        endpoint_id=endpoint_id,
        state=state,
        stream=stream,
        wait_reason=wait_reason,
        queued_from=queued_from,
        queued_to=queued_to,
        created_from=created_from,
        created_to=created_to,
        limit=limit,
        offset=offset,
    )
    return Page(
        items=[_to_request_read(v) for v in items], limit=limit, offset=offset, total=total
    )


@router.get("/requests/{request_id}", response_model=QueueRequestRead)
async def get_request(
    request_id: RequestId, context: AdminContextDep, session: SessionDep
) -> QueueRequestRead:
    view = await queue_service.get_request(session, context=context, request_id=request_id)
    return _to_request_read(view)


# --- summary -------------------------------------------------------------------


@router.get("/summary", response_model=QueueSummaryRead)
async def get_summary(
    context: AdminContextDep, session: SessionDep
) -> QueueSummaryRead:
    counts, queued, in_flight, unknown, oldest, oldest_wait = await queue_service.get_summary(
        session, context=context
    )
    return QueueSummaryRead(
        counts_by_state=counts,
        queued_total=queued,
        in_flight_total=in_flight,
        outcome_unknown_total=unknown,
        oldest_queued_at=oldest,
        oldest_wait_seconds=oldest_wait,
    )


# --- endpoint runtime / operational state --------------------------------------


@router.get("/endpoints", response_model=Page[EndpointRuntimeRead])
async def list_endpoints(
    context: AdminContextDep, session: SessionDep
) -> Page[EndpointRuntimeRead]:
    views = await queue_service.list_endpoint_runtime(session, context=context)
    items = [_to_endpoint_read(v) for v in views]
    return Page(items=items, limit=len(items), offset=0, total=len(items))


@router.get("/endpoints/{endpoint_id}", response_model=EndpointRuntimeRead)
async def get_endpoint(
    endpoint_id: EndpointId, context: AdminContextDep, session: SessionDep
) -> EndpointRuntimeRead:
    view = await queue_service.get_endpoint_runtime(
        session, context=context, endpoint_id=endpoint_id
    )
    return _to_endpoint_read(view)


@router.post("/endpoints/{endpoint_id}/pause", response_model=EndpointRuntimeRead)
async def pause_endpoint(
    endpoint_id: EndpointId, context: AdminContextDep, session: SessionDep
) -> EndpointRuntimeRead:
    async with session.begin():
        view = await queue_service.pause_endpoint(
            session, context=context, endpoint_id=endpoint_id
        )
    return _to_endpoint_read(view)


@router.post("/endpoints/{endpoint_id}/drain", response_model=EndpointRuntimeRead)
async def drain_endpoint(
    endpoint_id: EndpointId, context: AdminContextDep, session: SessionDep
) -> EndpointRuntimeRead:
    async with session.begin():
        view = await queue_service.drain_endpoint(
            session, context=context, endpoint_id=endpoint_id
        )
    return _to_endpoint_read(view)


@router.post("/endpoints/{endpoint_id}/resume", response_model=EndpointRuntimeRead)
async def resume_endpoint(
    endpoint_id: EndpointId, context: AdminContextDep, session: SessionDep
) -> EndpointRuntimeRead:
    async with session.begin():
        view = await queue_service.resume_endpoint(
            session, context=context, endpoint_id=endpoint_id
        )
    return _to_endpoint_read(view)


# --- cancellation --------------------------------------------------------------


@router.post("/requests/{request_id}/cancel", response_model=CancelResult)
async def cancel_request(
    request_id: RequestId, context: AdminContextDep, session: SessionDep
) -> CancelResult:
    async with session.begin():
        outcome, state, _project_id = await queue_service.cancel_request(
            session, context=context, request_id=request_id
        )
    if outcome in ("cancelled_now", "cancellation_requested", "already_cancelled"):
        return CancelResult(request_id=request_id, result=outcome, state=state)
    if outcome == "outcome_unknown":
        raise QueueTransitionError("outcome_unknown_requires_reconciliation")
    if outcome == "not_found":
        raise AdminResourceNotFound()
    raise QueueTransitionError("invalid_transition")


# --- outcome_unknown -----------------------------------------------------------


@router.get("/outcome-unknown", response_model=Page[QueueRequestRead])
async def list_outcome_unknown(
    context: AdminContextDep,
    session: SessionDep,
    limit: LimitQuery = DEFAULT_LIMIT,
    offset: OffsetQuery = 0,
) -> Page[QueueRequestRead]:
    items, total = await queue_service.list_outcome_unknown(
        session, context=context, limit=limit, offset=offset
    )
    return Page(
        items=[_to_request_read(v) for v in items], limit=limit, offset=offset, total=total
    )


@router.post("/requests/{request_id}/reconcile", response_model=ReconcileResult)
async def reconcile_request(
    request_id: RequestId,
    body: ReconcileRequest,
    context: AdminContextDep,
    session: SessionDep,
) -> ReconcileResult:
    async with session.begin():
        result, _project_id = await queue_service.reconcile_request(
            session, context=context, request_id=request_id, disposition=body.disposition
        )
    if not result:
        raise QueueTransitionError("reconcile_not_outcome_unknown")
    return ReconcileResult(
        request_id=request_id,
        disposition=body.disposition,
        reconciled_by=str(context.principal_id),
    )


# --- provider quota runtime status ---------------------------------------------


@router.get("/quota-status", response_model=Page[QuotaStatusRead])
async def list_quota_status(
    context: AdminContextDep, session: SessionDep
) -> Page[QuotaStatusRead]:
    views = await queue_service.list_quota_status(session, context=context)
    items = [_to_quota_read(v) for v in views]
    return Page(items=items, limit=len(items), offset=0, total=len(items))


__all__ = ["router"]
