"""Accounting administration service.

The accounting control plane exposes route pricing configuration, immutable
price-snapshot reads, project budget policies, budget status/headroom, budget
reservation reads, usage/ledger reads, and safe audit reads over ``/admin/v1``.

Authorization flows through the same centralized RBAC engine as the rest of the
control plane:

- Route ``PricePolicy`` and immutable ``PriceSnapshot`` are deployment/catalog
  infrastructure: they require ``system_admin`` deployment authority plus the
  matching ``admin:accounting:*`` permission. Project-scoped roles never mutate
  or read route pricing across the deployment.
- ``ProjectBudgetPolicy``, budget status, budget reservations, usage records,
  and ledger entries are project resources: ``system_admin`` reads/writes any
  project, ``project_admin`` reads/writes its own project, ``project_viewer``
  reads its own project, and a cross-project opaque ID is indistinguishable from
  a nonexistent one (``404``).
- Audit reads use ``admin:audit:read``; a project-scoped caller sees only events
  scoped to its own projects (deployment-scoped events are hidden).

Mutation services accept a typed :class:`AdminRequestContext` and authorize
internally, so an internal caller cannot bypass RBAC by supplying a forged actor
ID. Routers remain thin.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from aethergate.domain import entities as domain
from aethergate.domain.enums import (
    AdminAuthenticationKind,
    BillingUnit,
    BudgetReservationState,
    CredentialScope,
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
from aethergate.domain.value_objects import quantize_money
from aethergate.errors import (
    AdminAuthorizationError,
    AdminResourceNotFound,
    AdminValidationError,
)
from aethergate.identity import rbac
from aethergate.identity.authorization import (
    RESOURCE_DEPLOYMENT,
    RESOURCE_PROJECT,
    authorize_admin,
    authorized_project_ids,
    resolve_admin_resource,
)
from aethergate.persistence import repository
from aethergate.scheduler import repository as sched_repo

# Audit action names (stable strings; safe metadata only).
AUDIT_PRICE_POLICY_CREATED = "price_policy.created"
AUDIT_PRICE_POLICY_UPDATED = "price_policy.updated"
AUDIT_PROJECT_BUDGET_POLICY_CREATED = "project_budget_policy.created"
AUDIT_PROJECT_BUDGET_POLICY_UPDATED = "project_budget_policy.updated"


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
    """Authorize a project-scoped *list* read: hold ``permission`` in at least
    one scope. The actual visible project set is derived separately via
    ``authorized_project_ids``."""
    if (
        context.authentication_kind is AdminAuthenticationKind.SERVICE_CREDENTIAL
        and permission not in context.scopes
    ):
        raise AdminAuthorizationError()
    if not any(
        rbac.role_grants_permission(a.role, permission) for a in context.assignments
    ):
        raise AdminAuthorizationError()


def _require_project_scope(
    context: domain.AdminRequestContext, project_id: ProjectId, permission: CredentialScope
) -> None:
    """Enforce a project resource is within the caller's authority.

    A cross-project project ID is indistinguishable from a nonexistent one
    (``AdminResourceNotFound``); an in-scope project the caller lacks a specific
    permission for raises ``AdminAuthorizationError``.
    """
    authorized = authorized_project_ids(context)
    if authorized is not None and project_id not in authorized:
        raise AdminResourceNotFound()
    authorize_admin(context, permission, RESOURCE_PROJECT, project_id)


def _project_list_scope(
    context: domain.AdminRequestContext, project_id: ProjectId | None
) -> tuple[set[ProjectId] | None, bool]:
    """Return ``(project_ids, visible)`` for a project-scoped list.

    ``project_ids`` is the repository scope (``None`` = all, set = restricted).
    ``visible`` is False when a caller-supplied project filter is outside the
    caller's authority (caller should return an empty page).
    """
    authorized = authorized_project_ids(context)
    if project_id is not None:
        if authorized is not None and project_id not in authorized:
            return None, False
        return {project_id}, True
    return authorized, True


def _validation_message(exc: ValidationError) -> str:
    errors = exc.errors()
    if errors:
        loc = ".".join(str(x) for x in errors[0].get("loc", ()))
        message = errors[0].get("msg", "invalid value")
        return f"{loc}: {message}" if loc else str(message)
    return "invalid accounting configuration"


def _build_price_policy(
    *,
    policy_id: PricePolicyId | None,
    route_binding_id: RouteBindingId,
    billing_unit: BillingUnit,
    currency: str,
    unit_scale: int,
    request_price: Decimal | None,
    input_price: Decimal | None,
    output_price: Decimal | None,
    enabled: bool,
    name: str | None,
) -> domain.PricePolicy:
    try:
        return domain.PricePolicy(
            id=policy_id or PricePolicyId(_new_id()),
            route_binding_id=route_binding_id,
            billing_unit=billing_unit,
            currency=currency,
            unit_scale=unit_scale,
            request_price=request_price,
            input_price=input_price,
            output_price=output_price,
            enabled=enabled,
            name=name,
        )
    except ValidationError as exc:
        raise AdminValidationError(_validation_message(exc)) from exc


async def _write_audit(
    session: AsyncSession,
    *,
    actor_principal_id: PrincipalId,
    action: str,
    resource_type: str,
    resource_id: str,
    project_id: ProjectId | None = None,
    metadata: dict | None = None,
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
            metadata=metadata or {},
        ),
    )


# ---------------------------------------------------------------------------
# PricePolicy CRUD (deployment-scoped)
# ---------------------------------------------------------------------------


async def create_price_policy(
    session: AsyncSession,
    *,
    context: domain.AdminRequestContext,
    route_binding_id: RouteBindingId,
    billing_unit: BillingUnit,
    currency: str,
    unit_scale: int = 1,
    request_price: Decimal | None = None,
    input_price: Decimal | None = None,
    output_price: Decimal | None = None,
    enabled: bool = True,
    name: str | None = None,
) -> domain.PricePolicy:
    _authorize_deployment(context, CredentialScope.ADMIN_ACCOUNTING_WRITE)
    if await repository.get_route_binding(session, route_binding_id) is None:
        raise AdminResourceNotFound()
    policy = _build_price_policy(
        policy_id=None,
        route_binding_id=route_binding_id,
        billing_unit=billing_unit,
        currency=currency,
        unit_scale=unit_scale,
        request_price=request_price,
        input_price=input_price,
        output_price=output_price,
        enabled=enabled,
        name=name,
    )
    created = await repository.create_price_policy(session, policy)
    await _write_audit(
        session,
        actor_principal_id=context.principal_id,
        action=AUDIT_PRICE_POLICY_CREATED,
        resource_type="price_policy",
        resource_id=str(created.id),
        metadata={"route_binding_id": str(created.route_binding_id)},
    )
    return created


async def update_price_policy(
    session: AsyncSession,
    *,
    context: domain.AdminRequestContext,
    policy_id: PricePolicyId,
    billing_unit: BillingUnit | None = None,
    currency: str | None = None,
    unit_scale: int | None = None,
    request_price: Decimal | None = None,
    clear_request_price: bool = False,
    input_price: Decimal | None = None,
    clear_input_price: bool = False,
    output_price: Decimal | None = None,
    clear_output_price: bool = False,
    enabled: bool | None = None,
    name: str | None = None,
    clear_name: bool = False,
) -> domain.PricePolicy:
    _authorize_deployment(context, CredentialScope.ADMIN_ACCOUNTING_WRITE)
    existing = await repository.get_price_policy(session, policy_id)
    if existing is None:
        raise AdminResourceNotFound()

    new_billing_unit = billing_unit if billing_unit is not None else existing.billing_unit
    new_currency = currency if currency is not None else existing.currency
    new_unit_scale = unit_scale if unit_scale is not None else existing.unit_scale
    new_request_price = (
        None
        if clear_request_price
        else (request_price if request_price is not None else existing.request_price)
    )
    new_input_price = (
        None
        if clear_input_price
        else (input_price if input_price is not None else existing.input_price)
    )
    new_output_price = (
        None
        if clear_output_price
        else (output_price if output_price is not None else existing.output_price)
    )
    new_enabled = enabled if enabled is not None else existing.enabled
    new_name = None if clear_name else (name if name is not None else existing.name)

    # Validate the complete resulting policy (not just the patch fields).
    _build_price_policy(
        policy_id=existing.id,
        route_binding_id=existing.route_binding_id,
        billing_unit=new_billing_unit,
        currency=new_currency,
        unit_scale=new_unit_scale,
        request_price=new_request_price,
        input_price=new_input_price,
        output_price=new_output_price,
        enabled=new_enabled,
        name=new_name,
    )

    updated = await repository.update_price_policy(
        session,
        policy_id,
        billing_unit=new_billing_unit.value,
        currency=new_currency,
        unit_scale=new_unit_scale,
        request_price=new_request_price,
        input_price=new_input_price,
        output_price=new_output_price,
        enabled=new_enabled,
        name=new_name,
    )
    assert updated is not None
    changed = (
        new_billing_unit != existing.billing_unit
        or new_currency != existing.currency
        or new_unit_scale != existing.unit_scale
        or new_request_price != existing.request_price
        or new_input_price != existing.input_price
        or new_output_price != existing.output_price
        or new_enabled != existing.enabled
        or new_name != existing.name
    )
    if changed:
        await _write_audit(
            session,
            actor_principal_id=context.principal_id,
            action=AUDIT_PRICE_POLICY_UPDATED,
            resource_type="price_policy",
            resource_id=str(updated.id),
            metadata={"route_binding_id": str(updated.route_binding_id)},
        )
    return updated


async def list_price_policies(
    session: AsyncSession,
    *,
    context: domain.AdminRequestContext,
    route_binding_id: RouteBindingId | None = None,
    enabled: bool | None = None,
    billing_unit: BillingUnit | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[domain.PricePolicy], int]:
    _authorize_deployment(context, CredentialScope.ADMIN_ACCOUNTING_READ)
    items = await repository.list_price_policies(
        session,
        route_binding_id=route_binding_id,
        enabled=enabled,
        billing_unit=billing_unit,
        limit=limit,
        offset=offset,
    )
    total = await repository.count_price_policies(
        session,
        route_binding_id=route_binding_id,
        enabled=enabled,
        billing_unit=billing_unit,
    )
    return items, total


async def get_price_policy(
    session: AsyncSession, *, context: domain.AdminRequestContext, policy_id: PricePolicyId
) -> domain.PricePolicy:
    _authorize_deployment(context, CredentialScope.ADMIN_ACCOUNTING_READ)
    policy = await repository.get_price_policy(session, policy_id)
    if policy is None:
        raise AdminResourceNotFound()
    return policy


# ---------------------------------------------------------------------------
# Immutable PriceSnapshot reads (deployment-scoped)
# ---------------------------------------------------------------------------


async def list_price_snapshots(
    session: AsyncSession,
    *,
    context: domain.AdminRequestContext,
    route_binding_id: RouteBindingId | None = None,
    model_alias_id: ModelAliasId | None = None,
    provider_account_id: ProviderAccountId | None = None,
    source_price_policy_id: PricePolicyId | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[domain.PriceSnapshot], int]:
    _authorize_deployment(context, CredentialScope.ADMIN_ACCOUNTING_READ)
    items = await repository.list_price_snapshots(
        session,
        route_binding_id=route_binding_id,
        model_alias_id=model_alias_id,
        provider_account_id=provider_account_id,
        source_price_policy_id=source_price_policy_id,
        limit=limit,
        offset=offset,
    )
    total = await repository.count_price_snapshots(
        session,
        route_binding_id=route_binding_id,
        model_alias_id=model_alias_id,
        provider_account_id=provider_account_id,
        source_price_policy_id=source_price_policy_id,
    )
    return items, total


async def get_price_snapshot(
    session: AsyncSession, *, context: domain.AdminRequestContext, snapshot_id: PriceSnapshotId
) -> domain.PriceSnapshot:
    _authorize_deployment(context, CredentialScope.ADMIN_ACCOUNTING_READ)
    snapshot = await repository.get_price_snapshot(session, snapshot_id)
    if snapshot is None:
        raise AdminResourceNotFound()
    return snapshot


# ---------------------------------------------------------------------------
# ProjectBudgetPolicy CRUD (project-scoped)
# ---------------------------------------------------------------------------


async def create_project_budget_policy(
    session: AsyncSession,
    *,
    context: domain.AdminRequestContext,
    project_id: ProjectId,
    name: str,
    currency: str,
    limit_amount: Decimal,
    window_seconds: int,
    enabled: bool = True,
) -> domain.ProjectBudgetPolicy:
    _require_project_scope(context, project_id, CredentialScope.ADMIN_ACCOUNTING_WRITE)
    if await repository.get_project(session, project_id) is None:
        raise AdminResourceNotFound()
    if not name.strip():
        raise AdminValidationError("budget policy name must not be empty")
    try:
        policy = domain.ProjectBudgetPolicy(
            id=BudgetPolicyId(_new_id()),
            project_id=project_id,
            name=name,
            currency=currency,
            limit_amount=limit_amount,
            window_seconds=window_seconds,
            enabled=enabled,
        )
    except ValidationError as exc:
        raise AdminValidationError(_validation_message(exc)) from exc
    created = await repository.create_project_budget_policy(session, policy)
    await _write_audit(
        session,
        actor_principal_id=context.principal_id,
        action=AUDIT_PROJECT_BUDGET_POLICY_CREATED,
        resource_type="project_budget_policy",
        resource_id=str(created.id),
        project_id=created.project_id,
    )
    return created


async def update_project_budget_policy(
    session: AsyncSession,
    *,
    context: domain.AdminRequestContext,
    policy_id: BudgetPolicyId,
    name: str | None = None,
    currency: str | None = None,
    limit_amount: Decimal | None = None,
    window_seconds: int | None = None,
    enabled: bool | None = None,
) -> domain.ProjectBudgetPolicy:
    existing = await repository.get_project_budget_policy_for_update(session, policy_id)
    if existing is None:
        raise AdminResourceNotFound()
    _require_project_scope(context, existing.project_id, CredentialScope.ADMIN_ACCOUNTING_WRITE)

    if currency is not None and currency != existing.currency:
        raise AdminValidationError("budget policy currency is immutable after creation")
    if window_seconds is not None and window_seconds != existing.window_seconds:
        raise AdminValidationError("budget policy window_seconds is immutable after creation")
    if name is not None and not name.strip():
        raise AdminValidationError("budget policy name must not be empty")

    new_name = name if name is not None else existing.name
    new_limit = limit_amount if limit_amount is not None else existing.limit_amount
    new_enabled = enabled if enabled is not None else existing.enabled

    updated = await repository.update_project_budget_policy(
        session,
        policy_id,
        name=new_name,
        limit_amount=new_limit,
        enabled=new_enabled,
    )
    assert updated is not None
    changed = (
        new_name != existing.name
        or new_limit != existing.limit_amount
        or new_enabled != existing.enabled
    )
    if changed:
        await _write_audit(
            session,
            actor_principal_id=context.principal_id,
            action=AUDIT_PROJECT_BUDGET_POLICY_UPDATED,
            resource_type="project_budget_policy",
            resource_id=str(updated.id),
            project_id=updated.project_id,
        )
    return updated


async def list_project_budget_policies(
    session: AsyncSession,
    *,
    context: domain.AdminRequestContext,
    project_id: ProjectId | None = None,
    enabled: bool | None = None,
    currency: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[domain.ProjectBudgetPolicy], int]:
    _authorize_project_list(context, CredentialScope.ADMIN_ACCOUNTING_READ)
    scope, visible = _project_list_scope(context, project_id)
    if not visible:
        return [], 0
    items = await repository.list_project_budget_policies(
        session,
        project_ids=scope,
        enabled=enabled,
        currency=currency,
        limit=limit,
        offset=offset,
    )
    total = await repository.count_project_budget_policies(
        session,
        project_ids=scope,
        enabled=enabled,
        currency=currency,
    )
    return items, total


async def get_project_budget_policy(
    session: AsyncSession,
    *,
    context: domain.AdminRequestContext,
    policy_id: BudgetPolicyId,
) -> domain.ProjectBudgetPolicy:
    policy = await repository.get_project_budget_policy(session, policy_id)
    if policy is None:
        raise AdminResourceNotFound()
    _require_project_scope(context, policy.project_id, CredentialScope.ADMIN_ACCOUNTING_READ)
    return policy


# ---------------------------------------------------------------------------
# Budget status / headroom
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BudgetStatus:
    """A read-only projection of one budget policy's current-window status.

    Carries everything the router needs to build a ``BudgetStatusRead`` without
    the router recomputing headroom or touching the window row directly. Not a
    persisted entity; derived on demand from authoritative rows.
    """

    project_id: ProjectId
    budget_policy_id: BudgetPolicyId
    currency: str
    limit_amount: Decimal
    committed_amount: Decimal
    reserved_amount: Decimal
    headroom: Decimal
    window_start: datetime
    window_end: datetime
    enabled: bool


async def get_project_budget_status(
    session: AsyncSession, *, context: domain.AdminRequestContext, project_id: ProjectId
) -> list[BudgetStatus]:
    """Return per-policy current-window budget status for a project.

    Read-only: computes the current fixed window for each policy and reports the
    persisted committed/reserved amounts (or zero when no window exists yet).
    Never fabricates reservation/window history, and never creates a window row
    merely for a read.
    """
    project = await resolve_admin_resource(
        session, context, CredentialScope.ADMIN_ACCOUNTING_READ, RESOURCE_PROJECT, str(project_id)
    )
    assert isinstance(project, domain.Project)
    policies = await repository.list_project_budget_policies(
        session, project_ids={project_id}, limit=None
    )
    now = utcnow()
    statuses: list[BudgetStatus] = []
    for policy in policies:
        window_start = sched_repo.fixed_window_start(now, policy.window_seconds)
        window = await repository.get_budget_window(
            session, budget_policy_id=policy.id, window_start=window_start
        )
        committed = window.committed_amount if window is not None else Decimal("0")
        reserved = window.reserved_amount if window is not None else Decimal("0")
        headroom = quantize_money(policy.limit_amount - committed - reserved)
        statuses.append(
            BudgetStatus(
                project_id=project_id,
                budget_policy_id=policy.id,
                currency=policy.currency,
                limit_amount=policy.limit_amount,
                committed_amount=committed,
                reserved_amount=reserved,
                headroom=headroom,
                window_start=window_start,
                window_end=window_start + timedelta(seconds=policy.window_seconds),
                enabled=policy.enabled,
            )
        )
    return statuses


# ---------------------------------------------------------------------------
# BudgetReservation reads (project-scoped)
# ---------------------------------------------------------------------------


async def list_budget_reservations(
    session: AsyncSession,
    *,
    context: domain.AdminRequestContext,
    project_id: ProjectId | None = None,
    request_id: RequestId | None = None,
    budget_policy_id: BudgetPolicyId | None = None,
    state: BudgetReservationState | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[domain.BudgetReservation], int]:
    _authorize_project_list(context, CredentialScope.ADMIN_ACCOUNTING_READ)
    scope, visible = _project_list_scope(context, project_id)
    if not visible:
        return [], 0
    items = await repository.list_budget_reservations(
        session,
        project_ids=scope,
        request_id=request_id,
        budget_policy_id=budget_policy_id,
        state=state,
        limit=limit,
        offset=offset,
    )
    total = await repository.count_budget_reservations(
        session,
        project_ids=scope,
        request_id=request_id,
        budget_policy_id=budget_policy_id,
        state=state,
    )
    return items, total


async def get_budget_reservation(
    session: AsyncSession,
    *,
    context: domain.AdminRequestContext,
    reservation_id: BudgetReservationId,
) -> domain.BudgetReservation:
    reservation = await repository.get_budget_reservation(session, reservation_id)
    if reservation is None:
        raise AdminResourceNotFound()
    policy = await repository.get_project_budget_policy(session, reservation.budget_policy_id)
    if policy is None:
        raise AdminResourceNotFound()
    _require_project_scope(context, policy.project_id, CredentialScope.ADMIN_ACCOUNTING_READ)
    return reservation


# ---------------------------------------------------------------------------
# UsageRecord reads (project-scoped)
# ---------------------------------------------------------------------------


async def list_usage_records(
    session: AsyncSession,
    *,
    context: domain.AdminRequestContext,
    project_id: ProjectId | None = None,
    request_id: RequestId | None = None,
    principal_id: PrincipalId | None = None,
    api_credential_id: ApiCredentialId | None = None,
    model_alias_id: ModelAliasId | None = None,
    route_binding_id: RouteBindingId | None = None,
    provider_account_id: ProviderAccountId | None = None,
    billing_unit: BillingUnit | None = None,
    currency: str | None = None,
    recorded_from: datetime | None = None,
    recorded_to: datetime | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[domain.UsageRecord], int]:
    _authorize_project_list(context, CredentialScope.ADMIN_ACCOUNTING_READ)
    _validate_time_range(recorded_from, recorded_to)
    scope, visible = _project_list_scope(context, project_id)
    if not visible:
        return [], 0
    items = await repository.list_usage_records(
        session,
        project_ids=scope,
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
    total = await repository.count_usage_records(
        session,
        project_ids=scope,
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
    )
    return items, total


async def get_usage_record(
    session: AsyncSession, *, context: domain.AdminRequestContext, record_id: UsageRecordId
) -> domain.UsageRecord:
    record = await repository.get_usage_record(session, record_id)
    if record is None:
        raise AdminResourceNotFound()
    _require_project_scope(context, record.project_id, CredentialScope.ADMIN_ACCOUNTING_READ)
    return record


# ---------------------------------------------------------------------------
# LedgerEntry reads (project-scoped)
# ---------------------------------------------------------------------------


async def list_ledger_entries(
    session: AsyncSession,
    *,
    context: domain.AdminRequestContext,
    project_id: ProjectId | None = None,
    usage_record_id: UsageRecordId | None = None,
    entry_type: LedgerEntryType | None = None,
    currency: str | None = None,
    created_from: datetime | None = None,
    created_to: datetime | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[domain.LedgerEntry], int]:
    _authorize_project_list(context, CredentialScope.ADMIN_ACCOUNTING_READ)
    _validate_time_range(created_from, created_to)
    scope, visible = _project_list_scope(context, project_id)
    if not visible:
        return [], 0
    items = await repository.list_ledger_entries(
        session,
        project_ids=scope,
        usage_record_id=usage_record_id,
        entry_type=entry_type,
        currency=currency,
        created_from=created_from,
        created_to=created_to,
        limit=limit,
        offset=offset,
    )
    total = await repository.count_ledger_entries(
        session,
        project_ids=scope,
        usage_record_id=usage_record_id,
        entry_type=entry_type,
        currency=currency,
        created_from=created_from,
        created_to=created_to,
    )
    return items, total


async def get_ledger_entry(
    session: AsyncSession, *, context: domain.AdminRequestContext, entry_id: LedgerEntryId
) -> domain.LedgerEntry:
    entry = await repository.get_ledger_entry(session, entry_id)
    if entry is None:
        raise AdminResourceNotFound()
    _require_project_scope(context, entry.project_id, CredentialScope.ADMIN_ACCOUNTING_READ)
    return entry


# ---------------------------------------------------------------------------
# Audit event reads (special scoping)
# ---------------------------------------------------------------------------


def _validate_time_range(start: datetime | None, end: datetime | None) -> None:
    if start is not None and end is not None and start > end:
        raise AdminValidationError("time range start must not be after end")


async def list_audit_events(
    session: AsyncSession,
    *,
    context: domain.AdminRequestContext,
    actor_principal_id: PrincipalId | None = None,
    project_id: ProjectId | None = None,
    action: str | None = None,
    resource_type: str | None = None,
    resource_id: str | None = None,
    occurred_from: datetime | None = None,
    occurred_to: datetime | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[domain.AuditEvent], int]:
    _authorize_project_list(context, CredentialScope.ADMIN_AUDIT_READ)
    _validate_time_range(occurred_from, occurred_to)
    scope, visible = _project_list_scope(context, project_id)
    if not visible:
        return [], 0
    items = await repository.list_audit_events_paged(
        session,
        project_ids=scope,
        actor_principal_id=actor_principal_id,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        occurred_from=occurred_from,
        occurred_to=occurred_to,
        limit=limit,
        offset=offset,
    )
    total = await repository.count_audit_events(
        session,
        project_ids=scope,
        actor_principal_id=actor_principal_id,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        occurred_from=occurred_from,
        occurred_to=occurred_to,
    )
    return items, total


async def get_audit_event(
    session: AsyncSession, *, context: domain.AdminRequestContext, event_id: AuditEventId
) -> domain.AuditEvent:
    event = await repository.get_audit_event(session, event_id)
    if event is None:
        raise AdminResourceNotFound()
    if event.project_id is None:
        # Deployment-scoped event: only a deployment-wide caller may read it. A
        # project-scoped caller is indistinguishable from nonexistent (404).
        if authorized_project_ids(context) is not None:
            raise AdminResourceNotFound()
        _authorize_deployment(context, CredentialScope.ADMIN_AUDIT_READ)
    else:
        _require_project_scope(context, event.project_id, CredentialScope.ADMIN_AUDIT_READ)
    return event


__all__ = [
    "create_price_policy",
    "update_price_policy",
    "list_price_policies",
    "get_price_policy",
    "list_price_snapshots",
    "get_price_snapshot",
    "create_project_budget_policy",
    "update_project_budget_policy",
    "list_project_budget_policies",
    "get_project_budget_policy",
    "get_project_budget_status",
    "BudgetStatus",
    "list_budget_reservations",
    "get_budget_reservation",
    "list_usage_records",
    "get_usage_record",
    "list_ledger_entries",
    "get_ledger_entry",
    "list_audit_events",
    "get_audit_event",
    "utcnow",
]
