"""Accounting/budget administration HTTP surface.

Exposes route pricing configuration, immutable price-snapshot reads, project
budget policies, budget status/headroom, budget reservation reads, usage/ledger
reads, and safe audit reads over ``/admin/v1``. Routers are thin: authorization,
validation, and invariant checks live in :mod:`aethergate.accounting.admin`,
which accepts a typed ``AdminRequestContext`` and authorizes internally.

Deployment-scoped resources (route ``PricePolicy`` and immutable ``PriceSnapshot``)
require ``system_admin`` plus the matching ``admin:accounting:*`` permission.
Project-scoped resources (``ProjectBudgetPolicy``, budget status/reservations,
usage, ledger) allow ``system_admin`` on any project and ``project_admin``/
``project_viewer`` on their own project, with cross-project opaque IDs
indistinguishable from nonexistent ones (``404``).
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Query

from aethergate.accounting import admin as accounting_service
from aethergate.api.admin import (
    DEFAULT_LIMIT,
    AdminContextDep,
    LimitQuery,
    OffsetQuery,
    SessionDep,
)
from aethergate.contracts.admin_v1 import (
    AuditEventRead,
    BudgetReservationRead,
    BudgetStatusRead,
    LedgerEntryRead,
    PricePolicyCreate,
    PricePolicyRead,
    PricePolicyUpdate,
    PriceSnapshotRead,
    ProjectBudgetPolicyCreate,
    ProjectBudgetPolicyRead,
    ProjectBudgetPolicyUpdate,
    UsageRecordRead,
)
from aethergate.contracts.common import Page
from aethergate.domain import entities as domain
from aethergate.domain.enums import (
    BillingUnit,
    BudgetReservationState,
    LedgerEntryType,
)
from aethergate.domain.ids import (
    ApiCredentialId,
    AuditEventId,
    BudgetPolicyId,
    BudgetReservationId,
    LedgerEntryId,
    ModelAliasId,
    PricePolicyId,
    PriceSnapshotId,
    PrincipalId,
    ProjectId,
    ProviderAccountId,
    RequestId,
    RouteBindingId,
    UsageRecordId,
)

router = APIRouter(prefix="/admin/v1", tags=["admin-accounting"])

RouteBindingIdQuery = Annotated[RouteBindingId | None, Query()]
ModelAliasIdQuery = Annotated[ModelAliasId | None, Query()]
ProviderAccountIdQuery = Annotated[ProviderAccountId | None, Query()]
PricePolicyIdQuery = Annotated[PricePolicyId | None, Query()]
ProjectIdQuery = Annotated[ProjectId | None, Query()]
RequestIdQuery = Annotated[RequestId | None, Query()]
PrincipalIdQuery = Annotated[PrincipalId | None, Query()]
ApiCredentialIdQuery = Annotated[ApiCredentialId | None, Query()]
BudgetPolicyIdQuery = Annotated[BudgetPolicyId | None, Query()]
UsageRecordIdQuery = Annotated[UsageRecordId | None, Query()]
BillingUnitQuery = Annotated[BillingUnit | None, Query()]
BudgetReservationStateQuery = Annotated[BudgetReservationState | None, Query()]
LedgerEntryTypeQuery = Annotated[LedgerEntryType | None, Query()]
DatetimeQuery = Annotated[datetime | None, Query()]
StringQuery = Annotated[str | None, Query()]
BoolQuery = Annotated[bool | None, Query()]


def _to_price_policy(policy: domain.PricePolicy) -> PricePolicyRead:
    return PricePolicyRead(
        id=policy.id,
        route_binding_id=policy.route_binding_id,
        billing_unit=policy.billing_unit,
        currency=policy.currency,
        unit_scale=policy.unit_scale,
        request_price=policy.request_price,
        input_price=policy.input_price,
        output_price=policy.output_price,
        enabled=policy.enabled,
        name=policy.name,
    )


def _to_price_snapshot(snapshot: domain.PriceSnapshot) -> PriceSnapshotRead:
    return PriceSnapshotRead(
        id=snapshot.id,
        source_price_policy_id=snapshot.source_price_policy_id,
        route_binding_id=snapshot.route_binding_id,
        provider_account_id=snapshot.provider_account_id,
        model_alias_id=snapshot.model_alias_id,
        billing_unit=snapshot.billing_unit,
        currency=snapshot.currency,
        unit_scale=snapshot.unit_scale,
        request_price=snapshot.request_price,
        input_price=snapshot.input_price,
        output_price=snapshot.output_price,
        captured_at=snapshot.captured_at,
    )


def _to_project_budget_policy(
    policy: domain.ProjectBudgetPolicy,
) -> ProjectBudgetPolicyRead:
    return ProjectBudgetPolicyRead(
        id=policy.id,
        project_id=policy.project_id,
        name=policy.name,
        currency=policy.currency,
        limit_amount=policy.limit_amount,
        window_seconds=policy.window_seconds,
        enabled=policy.enabled,
    )


def _to_budget_status(status: accounting_service.BudgetStatus) -> BudgetStatusRead:
    return BudgetStatusRead(
        project_id=status.project_id,
        budget_policy_id=status.budget_policy_id,
        currency=status.currency,
        limit_amount=status.limit_amount,
        committed_amount=status.committed_amount,
        reserved_amount=status.reserved_amount,
        headroom=status.headroom,
        window_start=status.window_start,
        window_end=status.window_end,
        enabled=status.enabled,
    )


def _to_budget_reservation(
    reservation: domain.BudgetReservation,
) -> BudgetReservationRead:
    return BudgetReservationRead(
        id=reservation.id,
        request_id=reservation.request_id,
        budget_policy_id=reservation.budget_policy_id,
        price_snapshot_id=reservation.price_snapshot_id,
        reserved_amount=reservation.reserved_amount,
        committed_amount=reservation.committed_amount,
        state=reservation.state.value,
        settlement_reason=reservation.settlement_reason,
    )


def _to_usage_record(record: domain.UsageRecord) -> UsageRecordRead:
    return UsageRecordRead(
        id=record.id,
        request_id=record.request_id,
        execution_attempt_id=record.execution_attempt_id,
        project_id=record.project_id,
        principal_id=record.principal_id,
        api_credential_id=record.api_credential_id,
        model_alias_id=record.model_alias_id,
        route_binding_id=record.route_binding_id,
        provider_account_id=record.provider_account_id,
        price_snapshot_id=record.price_snapshot_id,
        billing_unit=record.billing_unit,
        input_units=record.input_units,
        output_units=record.output_units,
        request_units=record.request_units,
        amount=record.amount,
        currency=record.currency,
        recorded_at=record.recorded_at,
        upstream_request_id=record.upstream_request_id,
    )


def _to_ledger_entry(entry: domain.LedgerEntry) -> LedgerEntryRead:
    return LedgerEntryRead(
        id=entry.id,
        project_id=entry.project_id,
        usage_record_id=entry.usage_record_id,
        entry_type=entry.entry_type,
        amount=entry.amount,
        currency=entry.currency,
        created_at=entry.created_at,
        idempotency_key=entry.idempotency_key,
        reason=entry.reason,
    )


def _to_audit_event(event: domain.AuditEvent) -> AuditEventRead:
    return AuditEventRead(
        id=event.id,
        actor_principal_id=event.actor_principal_id,
        project_id=event.project_id,
        action=event.action,
        resource_type=event.resource_type,
        resource_id=event.resource_id,
        occurred_at=event.occurred_at,
        metadata=event.metadata,
    )


# --- price policies ------------------------------------------------------------


@router.post("/price-policies", response_model=PricePolicyRead, status_code=201)
async def create_price_policy(
    body: PricePolicyCreate, context: AdminContextDep, session: SessionDep
) -> PricePolicyRead:
    async with session.begin():
        policy = await accounting_service.create_price_policy(
            session,
            context=context,
            route_binding_id=body.route_binding_id,
            billing_unit=body.billing_unit,
            currency=body.currency,
            unit_scale=body.unit_scale,
            request_price=body.request_price,
            input_price=body.input_price,
            output_price=body.output_price,
            enabled=body.enabled,
            name=body.name,
        )
    return _to_price_policy(policy)


@router.get("/price-policies", response_model=Page[PricePolicyRead])
async def list_price_policies(
    context: AdminContextDep,
    session: SessionDep,
    route_binding_id: RouteBindingIdQuery = None,
    enabled: BoolQuery = None,
    billing_unit: BillingUnitQuery = None,
    limit: LimitQuery = DEFAULT_LIMIT,
    offset: OffsetQuery = 0,
) -> Page[PricePolicyRead]:
    items, total = await accounting_service.list_price_policies(
        session,
        context=context,
        route_binding_id=route_binding_id,
        enabled=enabled,
        billing_unit=billing_unit,
        limit=limit,
        offset=offset,
    )
    return Page(
        items=[_to_price_policy(p) for p in items], limit=limit, offset=offset, total=total
    )


@router.get("/price-policies/{policy_id}", response_model=PricePolicyRead)
async def get_price_policy(
    policy_id: PricePolicyId, context: AdminContextDep, session: SessionDep
) -> PricePolicyRead:
    return _to_price_policy(
        await accounting_service.get_price_policy(
            session, context=context, policy_id=policy_id
        )
    )


@router.patch("/price-policies/{policy_id}", response_model=PricePolicyRead)
async def update_price_policy(
    policy_id: PricePolicyId,
    body: PricePolicyUpdate,
    context: AdminContextDep,
    session: SessionDep,
) -> PricePolicyRead:
    fields = body.model_fields_set
    async with session.begin():
        policy = await accounting_service.update_price_policy(
            session,
            context=context,
            policy_id=policy_id,
            billing_unit=body.billing_unit,
            currency=body.currency,
            unit_scale=body.unit_scale,
            request_price=body.request_price,
            clear_request_price="request_price" in fields and body.request_price is None,
            input_price=body.input_price,
            clear_input_price="input_price" in fields and body.input_price is None,
            output_price=body.output_price,
            clear_output_price="output_price" in fields and body.output_price is None,
            enabled=body.enabled,
            name=body.name,
            clear_name="name" in fields and body.name is None,
        )
    return _to_price_policy(policy)


# --- price snapshots -----------------------------------------------------------


@router.get("/price-snapshots", response_model=Page[PriceSnapshotRead])
async def list_price_snapshots(
    context: AdminContextDep,
    session: SessionDep,
    route_binding_id: RouteBindingIdQuery = None,
    model_alias_id: ModelAliasIdQuery = None,
    provider_account_id: ProviderAccountIdQuery = None,
    source_price_policy_id: PricePolicyIdQuery = None,
    limit: LimitQuery = DEFAULT_LIMIT,
    offset: OffsetQuery = 0,
) -> Page[PriceSnapshotRead]:
    items, total = await accounting_service.list_price_snapshots(
        session,
        context=context,
        route_binding_id=route_binding_id,
        model_alias_id=model_alias_id,
        provider_account_id=provider_account_id,
        source_price_policy_id=source_price_policy_id,
        limit=limit,
        offset=offset,
    )
    return Page(
        items=[_to_price_snapshot(s) for s in items], limit=limit, offset=offset, total=total
    )


@router.get("/price-snapshots/{snapshot_id}", response_model=PriceSnapshotRead)
async def get_price_snapshot(
    snapshot_id: PriceSnapshotId, context: AdminContextDep, session: SessionDep
) -> PriceSnapshotRead:
    return _to_price_snapshot(
        await accounting_service.get_price_snapshot(
            session, context=context, snapshot_id=snapshot_id
        )
    )


# --- project budget policies ---------------------------------------------------


@router.post("/project-budget-policies", response_model=ProjectBudgetPolicyRead, status_code=201)
async def create_project_budget_policy(
    body: ProjectBudgetPolicyCreate, context: AdminContextDep, session: SessionDep
) -> ProjectBudgetPolicyRead:
    async with session.begin():
        policy = await accounting_service.create_project_budget_policy(
            session,
            context=context,
            project_id=body.project_id,
            name=body.name,
            currency=body.currency,
            limit_amount=body.limit_amount,
            window_seconds=body.window_seconds,
            enabled=body.enabled,
        )
    return _to_project_budget_policy(policy)


@router.get("/project-budget-policies", response_model=Page[ProjectBudgetPolicyRead])
async def list_project_budget_policies(
    context: AdminContextDep,
    session: SessionDep,
    project_id: ProjectIdQuery = None,
    enabled: BoolQuery = None,
    currency: StringQuery = None,
    limit: LimitQuery = DEFAULT_LIMIT,
    offset: OffsetQuery = 0,
) -> Page[ProjectBudgetPolicyRead]:
    items, total = await accounting_service.list_project_budget_policies(
        session,
        context=context,
        project_id=project_id,
        enabled=enabled,
        currency=currency,
        limit=limit,
        offset=offset,
    )
    return Page(
        items=[_to_project_budget_policy(p) for p in items],
        limit=limit,
        offset=offset,
        total=total,
    )


@router.get("/project-budget-policies/{policy_id}", response_model=ProjectBudgetPolicyRead)
async def get_project_budget_policy(
    policy_id: BudgetPolicyId, context: AdminContextDep, session: SessionDep
) -> ProjectBudgetPolicyRead:
    return _to_project_budget_policy(
        await accounting_service.get_project_budget_policy(
            session, context=context, policy_id=policy_id
        )
    )


@router.patch("/project-budget-policies/{policy_id}", response_model=ProjectBudgetPolicyRead)
async def update_project_budget_policy(
    policy_id: BudgetPolicyId,
    body: ProjectBudgetPolicyUpdate,
    context: AdminContextDep,
    session: SessionDep,
) -> ProjectBudgetPolicyRead:
    async with session.begin():
        policy = await accounting_service.update_project_budget_policy(
            session,
            context=context,
            policy_id=policy_id,
            name=body.name,
            currency=body.currency,
            limit_amount=body.limit_amount,
            window_seconds=body.window_seconds,
            enabled=body.enabled,
        )
    return _to_project_budget_policy(policy)


# --- budget status / headroom --------------------------------------------------


@router.get("/projects/{project_id}/budget-status", response_model=list[BudgetStatusRead])
async def get_project_budget_status(
    project_id: ProjectId, context: AdminContextDep, session: SessionDep
) -> list[BudgetStatusRead]:
    statuses = await accounting_service.get_project_budget_status(
        session, context=context, project_id=project_id
    )
    return [_to_budget_status(s) for s in statuses]


# --- budget reservations -------------------------------------------------------


@router.get("/budget-reservations", response_model=Page[BudgetReservationRead])
async def list_budget_reservations(
    context: AdminContextDep,
    session: SessionDep,
    project_id: ProjectIdQuery = None,
    request_id: RequestIdQuery = None,
    budget_policy_id: BudgetPolicyIdQuery = None,
    state: BudgetReservationStateQuery = None,
    limit: LimitQuery = DEFAULT_LIMIT,
    offset: OffsetQuery = 0,
) -> Page[BudgetReservationRead]:
    items, total = await accounting_service.list_budget_reservations(
        session,
        context=context,
        project_id=project_id,
        request_id=request_id,
        budget_policy_id=budget_policy_id,
        state=state,
        limit=limit,
        offset=offset,
    )
    return Page(
        items=[_to_budget_reservation(r) for r in items],
        limit=limit,
        offset=offset,
        total=total,
    )


@router.get("/budget-reservations/{reservation_id}", response_model=BudgetReservationRead)
async def get_budget_reservation(
    reservation_id: BudgetReservationId, context: AdminContextDep, session: SessionDep
) -> BudgetReservationRead:
    return _to_budget_reservation(
        await accounting_service.get_budget_reservation(
            session, context=context, reservation_id=reservation_id
        )
    )


# --- usage records -------------------------------------------------------------


@router.get("/usage-records", response_model=Page[UsageRecordRead])
async def list_usage_records(
    context: AdminContextDep,
    session: SessionDep,
    project_id: ProjectIdQuery = None,
    request_id: RequestIdQuery = None,
    principal_id: PrincipalIdQuery = None,
    api_credential_id: ApiCredentialIdQuery = None,
    model_alias_id: ModelAliasIdQuery = None,
    route_binding_id: RouteBindingIdQuery = None,
    provider_account_id: ProviderAccountIdQuery = None,
    billing_unit: BillingUnitQuery = None,
    currency: StringQuery = None,
    recorded_from: DatetimeQuery = None,
    recorded_to: DatetimeQuery = None,
    limit: LimitQuery = DEFAULT_LIMIT,
    offset: OffsetQuery = 0,
) -> Page[UsageRecordRead]:
    items, total = await accounting_service.list_usage_records(
        session,
        context=context,
        project_id=project_id,
        request_id=request_id,
        principal_id=principal_id,
        api_credential_id=api_credential_id,
        model_alias_id=model_alias_id,
        route_binding_id=route_binding_id,
        provider_account_id=provider_account_id,
        billing_unit=billing_unit,
        currency=currency,
        recorded_from=recorded_from,
        recorded_to=recorded_to,
        limit=limit,
        offset=offset,
    )
    return Page(
        items=[_to_usage_record(r) for r in items], limit=limit, offset=offset, total=total
    )


@router.get("/usage-records/{record_id}", response_model=UsageRecordRead)
async def get_usage_record(
    record_id: UsageRecordId, context: AdminContextDep, session: SessionDep
) -> UsageRecordRead:
    return _to_usage_record(
        await accounting_service.get_usage_record(
            session, context=context, record_id=record_id
        )
    )


# --- ledger entries ------------------------------------------------------------


@router.get("/ledger-entries", response_model=Page[LedgerEntryRead])
async def list_ledger_entries(
    context: AdminContextDep,
    session: SessionDep,
    project_id: ProjectIdQuery = None,
    usage_record_id: UsageRecordIdQuery = None,
    entry_type: LedgerEntryTypeQuery = None,
    currency: StringQuery = None,
    created_from: DatetimeQuery = None,
    created_to: DatetimeQuery = None,
    limit: LimitQuery = DEFAULT_LIMIT,
    offset: OffsetQuery = 0,
) -> Page[LedgerEntryRead]:
    items, total = await accounting_service.list_ledger_entries(
        session,
        context=context,
        project_id=project_id,
        usage_record_id=usage_record_id,
        entry_type=entry_type,
        currency=currency,
        created_from=created_from,
        created_to=created_to,
        limit=limit,
        offset=offset,
    )
    return Page(
        items=[_to_ledger_entry(e) for e in items], limit=limit, offset=offset, total=total
    )


@router.get("/ledger-entries/{entry_id}", response_model=LedgerEntryRead)
async def get_ledger_entry(
    entry_id: LedgerEntryId, context: AdminContextDep, session: SessionDep
) -> LedgerEntryRead:
    return _to_ledger_entry(
        await accounting_service.get_ledger_entry(
            session, context=context, entry_id=entry_id
        )
    )


# --- audit events --------------------------------------------------------------


@router.get("/audit-events", response_model=Page[AuditEventRead])
async def list_audit_events(
    context: AdminContextDep,
    session: SessionDep,
    actor_principal_id: PrincipalIdQuery = None,
    project_id: ProjectIdQuery = None,
    action: StringQuery = None,
    resource_type: StringQuery = None,
    resource_id: StringQuery = None,
    occurred_from: DatetimeQuery = None,
    occurred_to: DatetimeQuery = None,
    limit: LimitQuery = DEFAULT_LIMIT,
    offset: OffsetQuery = 0,
) -> Page[AuditEventRead]:
    items, total = await accounting_service.list_audit_events(
        session,
        context=context,
        actor_principal_id=actor_principal_id,
        project_id=project_id,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        occurred_from=occurred_from,
        occurred_to=occurred_to,
        limit=limit,
        offset=offset,
    )
    return Page(
        items=[_to_audit_event(e) for e in items], limit=limit, offset=offset, total=total
    )


@router.get("/audit-events/{event_id}", response_model=AuditEventRead)
async def get_audit_event(
    event_id: AuditEventId, context: AdminContextDep, session: SessionDep
) -> AuditEventRead:
    return _to_audit_event(
        await accounting_service.get_audit_event(
            session, context=context, event_id=event_id
        )
    )


__all__ = ["router"]
