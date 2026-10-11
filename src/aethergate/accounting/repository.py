"""Low-level accounting persistence operations.

These functions operate on ORM rows and are internal to the accounting domain.
Callers manage their own transaction boundaries via ``AsyncSession.begin``. All
monetary amounts are ``Decimal`` and quantized by the callers to the fixed money
precision; the persistence layer stores ``Numeric(24, 12)`` verbatim.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from aethergate.errors import AccountingInvariantError
from aethergate.persistence import models


def _new_id() -> str:
    return uuid.uuid4().hex


# ---------------------------------------------------------------------------
# Pricing configuration and snapshots
# ---------------------------------------------------------------------------


async def get_active_price_policy_for_route(
    session: AsyncSession, *, route_binding_id: str, for_update: bool = True
) -> models.PricePolicy | None:
    """Return the single enabled price policy for a route (or ``None``).

    A route may carry at most one enabled price policy for this phase; the
    caller supplies the route binding id resolved by the scheduler. Locking the
    policy row (``for_update``) serializes edits against admission.
    """
    stmt = select(models.PricePolicy).where(
        models.PricePolicy.route_binding_id == route_binding_id,
        models.PricePolicy.enabled.is_(True),
    )
    if for_update:
        stmt = stmt.with_for_update()
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def get_price_policy_for_update(
    session: AsyncSession, *, price_policy_id: str
) -> models.PricePolicy | None:
    result = await session.execute(
        select(models.PricePolicy)
        .where(models.PricePolicy.id == price_policy_id)
        .with_for_update()
    )
    return result.scalar_one_or_none()


async def get_price_snapshot_for_update(
    session: AsyncSession, *, snapshot_id: str
) -> models.PriceSnapshot | None:
    result = await session.execute(
        select(models.PriceSnapshot)
        .where(models.PriceSnapshot.id == snapshot_id)
        .with_for_update()
    )
    return result.scalar_one_or_none()


async def create_price_snapshot(
    session: AsyncSession,
    *,
    snapshot_id: str,
    source_price_policy_id: str,
    route_binding_id: str,
    provider_account_id: str,
    model_alias_id: str,
    billing_unit: str,
    currency: str,
    unit_scale: int,
    request_price: Decimal | None,
    input_price: Decimal | None,
    output_price: Decimal | None,
    captured_at: datetime,
) -> models.PriceSnapshot:
    row = models.PriceSnapshot(
        id=snapshot_id,
        source_price_policy_id=source_price_policy_id,
        route_binding_id=route_binding_id,
        provider_account_id=provider_account_id,
        model_alias_id=model_alias_id,
        billing_unit=billing_unit,
        currency=currency,
        unit_scale=unit_scale,
        request_price=request_price,
        input_price=input_price,
        output_price=output_price,
        captured_at=captured_at,
    )
    session.add(row)
    await session.flush()
    return row


# ---------------------------------------------------------------------------
# Budget policy / windows / reservations
# ---------------------------------------------------------------------------


async def list_enabled_budget_policies_for_project(
    session: AsyncSession, *, project_id: str, for_update: bool = True
) -> list[models.ProjectBudgetPolicy]:
    stmt = select(models.ProjectBudgetPolicy).where(
        models.ProjectBudgetPolicy.project_id == project_id,
        models.ProjectBudgetPolicy.enabled.is_(True),
    )
    if for_update:
        stmt = stmt.with_for_update()
    stmt = stmt.order_by(models.ProjectBudgetPolicy.id.asc())
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def get_or_create_budget_window(
    session: AsyncSession, *, budget_policy_id: str, window_start: datetime
) -> models.BudgetWindow:
    row = (
        await session.execute(
            select(models.BudgetWindow)
            .where(
                models.BudgetWindow.budget_policy_id == budget_policy_id,
                models.BudgetWindow.window_start == window_start,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if row is not None:
        return row
    await session.execute(
        pg_insert(models.BudgetWindow)
        .values(id=_new_id(), budget_policy_id=budget_policy_id, window_start=window_start)
        .on_conflict_do_nothing(index_elements=["budget_policy_id", "window_start"])
    )
    return (
        await session.execute(
            select(models.BudgetWindow)
            .where(
                models.BudgetWindow.budget_policy_id == budget_policy_id,
                models.BudgetWindow.window_start == window_start,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalar_one()


async def reserve_budget(
    session: AsyncSession,
    *,
    reservation_id: str,
    request_id: str,
    budget_policy_id: str,
    price_snapshot_id: str,
    window_start: datetime,
    amount: Decimal,
) -> models.BudgetReservation:
    """Reserve a monetary amount against one budget window (window already locked)."""
    window = await get_or_create_budget_window(
        session, budget_policy_id=budget_policy_id, window_start=window_start
    )
    window.reserved_amount += amount
    row = models.BudgetReservation(
        id=reservation_id,
        request_id=request_id,
        budget_policy_id=budget_policy_id,
        price_snapshot_id=price_snapshot_id,
        window_start=window_start,
        reserved_amount=amount,
        committed_amount=Decimal("0"),
        state="reserved",
    )
    session.add(row)
    await session.flush()
    return row


async def list_budget_reservations_for_request(
    session: AsyncSession, *, request_id: str, for_update: bool = True
) -> list[models.BudgetReservation]:
    stmt = select(models.BudgetReservation).where(
        models.BudgetReservation.request_id == request_id
    )
    if for_update:
        stmt = stmt.with_for_update()
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def settle_budget_reservations_to_actual(
    session: AsyncSession, *, request_id: str, actual_amount: Decimal, now: datetime
) -> None:
    """Commit reserved budget to the honest actual amount (releasing the unused)."""
    reservations = await list_budget_reservations_for_request(session, request_id=request_id)
    for reservation in reservations:
        if reservation.state != "reserved":
            continue
        window = await get_or_create_budget_window(
            session,
            budget_policy_id=reservation.budget_policy_id,
            window_start=reservation.window_start,
        )
        window.reserved_amount -= reservation.reserved_amount
        window.committed_amount += actual_amount
        reservation.committed_amount = actual_amount
        reservation.reserved_amount = Decimal("0")
        reservation.state = "committed"
        reservation.committed_at = now


async def commit_budget_reservations_conservative(
    session: AsyncSession, *, request_id: str, now: datetime, reason: str
) -> None:
    """Conservatively commit still-reserved budget (unknown usage), never release.

    This is budget-policy accounting, not a measured-usage debit: no ``UsageRecord``
    is created for it.
    """
    reservations = await list_budget_reservations_for_request(session, request_id=request_id)
    for reservation in reservations:
        if reservation.state != "reserved":
            continue
        window = await get_or_create_budget_window(
            session,
            budget_policy_id=reservation.budget_policy_id,
            window_start=reservation.window_start,
        )
        window.reserved_amount -= reservation.reserved_amount
        window.committed_amount += reservation.reserved_amount
        reservation.committed_amount = reservation.reserved_amount
        reservation.reserved_amount = Decimal("0")
        reservation.state = "committed"
        reservation.settlement_reason = reason
        reservation.committed_at = now


async def release_budget_reservations(
    session: AsyncSession, *, request_id: str, now: datetime
) -> None:
    """Release still-reserved budget for a request that never dispatched.

    Detaches the released reservation from its pre-dispatch price snapshot so the
    caller may discard the snapshot without violating the foreign key.
    """
    reservations = await list_budget_reservations_for_request(session, request_id=request_id)
    for reservation in reservations:
        if reservation.state != "reserved":
            continue
        window = await get_or_create_budget_window(
            session,
            budget_policy_id=reservation.budget_policy_id,
            window_start=reservation.window_start,
        )
        window.reserved_amount -= reservation.reserved_amount
        reservation.reserved_amount = Decimal("0")
        reservation.state = "released"
        reservation.released_at = now
        reservation.price_snapshot_id = None


async def discard_price_snapshot(
    session: AsyncSession, *, snapshot_id: str
) -> None:
    """Delete a pre-dispatch price snapshot that never backed dispatched usage.

    Only safe for a snapshot whose owning request never reached durable dispatch
    intent (no usage record and no dispatched historical usage reference it).
    """
    await session.execute(
        delete(models.PriceSnapshot).where(models.PriceSnapshot.id == snapshot_id)
    )


# ---------------------------------------------------------------------------
# Usage records and ledger entries (idempotent)
# ---------------------------------------------------------------------------


async def get_usage_record_for_request(
    session: AsyncSession, *, request_id: str
) -> models.UsageRecord | None:
    result = await session.execute(
        select(models.UsageRecord).where(models.UsageRecord.request_id == request_id)
    )
    return result.scalar_one_or_none()


async def create_usage_record(
    session: AsyncSession,
    *,
    request_id: str,
    execution_attempt_id: str,
    project_id: str | None,
    principal_id: str | None,
    api_credential_id: str | None,
    model_alias_id: str,
    route_binding_id: str,
    provider_account_id: str,
    price_snapshot_id: str,
    billing_unit: str,
    input_units: int,
    output_units: int,
    request_units: int | None,
    amount: Decimal,
    currency: str,
    recorded_at: datetime,
    upstream_request_id: str | None,
) -> models.UsageRecord:
    """Idempotently insert a usage record (unique per request), returning the
    canonical row (created now or pre-existing). Never overwrites an existing row.
    """
    await session.execute(
        pg_insert(models.UsageRecord)
        .values(
            id=_new_id(),
            request_id=request_id,
            execution_attempt_id=execution_attempt_id,
            project_id=project_id,
            principal_id=principal_id,
            api_credential_id=api_credential_id,
            model_alias_id=model_alias_id,
            route_binding_id=route_binding_id,
            provider_account_id=provider_account_id,
            price_snapshot_id=price_snapshot_id,
            billing_unit=billing_unit,
            input_units=input_units,
            output_units=output_units,
            request_units=request_units,
            amount=amount,
            currency=currency,
            recorded_at=recorded_at,
            upstream_request_id=upstream_request_id,
        )
        .on_conflict_do_nothing(index_elements=["request_id"])
    )
    result = await session.execute(
        select(models.UsageRecord).where(models.UsageRecord.request_id == request_id)
    )
    row = result.scalar_one()
    _assert_usage_record_matches(
        row,
        execution_attempt_id=execution_attempt_id,
        project_id=project_id,
        principal_id=principal_id,
        api_credential_id=api_credential_id,
        model_alias_id=model_alias_id,
        route_binding_id=route_binding_id,
        provider_account_id=provider_account_id,
        price_snapshot_id=price_snapshot_id,
        billing_unit=billing_unit,
        input_units=input_units,
        output_units=output_units,
        request_units=request_units,
        amount=amount,
        currency=currency,
    )
    return row


def _assert_usage_record_matches(
    row: models.UsageRecord,
    *,
    execution_attempt_id: str,
    project_id: str | None,
    principal_id: str | None,
    api_credential_id: str | None,
    model_alias_id: str,
    route_binding_id: str,
    provider_account_id: str,
    price_snapshot_id: str,
    billing_unit: str,
    input_units: int,
    output_units: int,
    request_units: int | None,
    amount: Decimal,
    currency: str,
) -> None:
    """Reject an idempotent replay whose canonical settlement fields differ."""
    mismatches = [
        field
        for field, actual, expected in (
            ("execution_attempt_id", row.execution_attempt_id, execution_attempt_id),
            ("project_id", row.project_id, project_id),
            ("principal_id", row.principal_id, principal_id),
            ("api_credential_id", row.api_credential_id, api_credential_id),
            ("model_alias_id", row.model_alias_id, model_alias_id),
            ("route_binding_id", row.route_binding_id, route_binding_id),
            ("provider_account_id", row.provider_account_id, provider_account_id),
            ("price_snapshot_id", row.price_snapshot_id, price_snapshot_id),
            ("billing_unit", row.billing_unit, billing_unit),
            ("input_units", row.input_units, input_units),
            ("output_units", row.output_units, output_units),
            ("request_units", row.request_units, request_units),
            ("amount", row.amount, amount),
            ("currency", row.currency, currency),
        )
        if actual != expected
    ]
    if mismatches:
        raise AccountingInvariantError(
            f"usage record {row.id} replay conflicts on {', '.join(mismatches)}"
        )


async def get_ledger_entry_by_idempotency_key(
    session: AsyncSession, *, idempotency_key: str
) -> models.LedgerEntry | None:
    result = await session.execute(
        select(models.LedgerEntry).where(
            models.LedgerEntry.idempotency_key == idempotency_key
        )
    )
    return result.scalar_one_or_none()


async def create_ledger_entry(
    session: AsyncSession,
    *,
    project_id: str | None,
    usage_record_id: str | None,
    entry_type: str,
    amount: Decimal,
    currency: str,
    created_at: datetime,
    idempotency_key: str,
    reason: str | None = None,
) -> models.LedgerEntry:
    """Idempotently insert a ledger entry (unique idempotency key), returning the
    canonical row (created now or pre-existing). Never overwrites an existing row.
    """
    await session.execute(
        pg_insert(models.LedgerEntry)
        .values(
            id=_new_id(),
            project_id=project_id,
            usage_record_id=usage_record_id,
            entry_type=entry_type,
            amount=amount,
            currency=currency,
            created_at=created_at,
            idempotency_key=idempotency_key,
            reason=reason,
        )
        .on_conflict_do_nothing(index_elements=["idempotency_key"])
    )
    result = await session.execute(
        select(models.LedgerEntry).where(
            models.LedgerEntry.idempotency_key == idempotency_key
        )
    )
    row = result.scalar_one()
    _assert_ledger_entry_matches(
        row,
        project_id=project_id,
        usage_record_id=usage_record_id,
        entry_type=entry_type,
        amount=amount,
        currency=currency,
    )
    return row


def _assert_ledger_entry_matches(
    row: models.LedgerEntry,
    *,
    project_id: str | None,
    usage_record_id: str | None,
    entry_type: str,
    amount: Decimal,
    currency: str,
) -> None:
    """Reject an idempotent replay whose canonical settlement fields differ."""
    mismatches = [
        field
        for field, actual, expected in (
            ("project_id", row.project_id, project_id),
            ("usage_record_id", row.usage_record_id, usage_record_id),
            ("entry_type", row.entry_type, entry_type),
            ("amount", row.amount, amount),
            ("currency", row.currency, currency),
        )
        if actual != expected
    ]
    if mismatches:
        raise AccountingInvariantError(
            f"ledger entry {row.id} replay conflicts on {', '.join(mismatches)}"
        )
