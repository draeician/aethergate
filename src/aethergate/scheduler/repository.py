"""Low-level scheduler persistence operations.

These functions operate on ORM rows and are internal to the scheduler domain.
Callers must manage their own transaction boundaries via ``AsyncSession.begin``.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, or_, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from aethergate.domain.enums import RequestState
from aethergate.domain.ids import (
    ApiCredentialId,
    EndpointId,
    ExecutionAttemptId,
    ModelAliasId,
    PrincipalId,
    ProjectId,
    RequestId,
    ReservationId,
)
from aethergate.errors import SchedulerInvariantError
from aethergate.persistence import models

# Stable, documented advisory-lock key that serializes queue-cap admission
# (count-then-insert) across concurrent API processes. It is a single global
# scope: the queue bound is a global bound, not per-endpoint.
ADMISSION_LOCK_KEY = 0x4147_5144  # "AGQD"


def _new_id() -> str:
    return uuid.uuid4().hex


@dataclass
class QuotaReservationPlan:
    """Locks/evaluation for one request's quota reservation across every limit.

    ``windows`` are FOR UPDATE-locked rows for the current fixed window of each
    limit (stable ID order); the caller may mutate them via :func:`reserve_quota`
    in the same transaction after confirming every other admission resource fits.
    ``blocking_limit`` is the first limit (stable order) that would be exceeded;
    ``next_eligible_at`` is the earliest time every currently-blocking window has
    rolled over to a fresh empty window (None when nothing blocks).
    """

    limits: list[models.QuotaLimit]
    requested_by_limit: dict[str, int]
    windows: dict[str, models.QuotaWindow]
    blocking_limit: models.QuotaLimit | None
    next_eligible_at: datetime | None


def fixed_window_start(now: datetime, window_seconds: int) -> datetime:
    """Return the start of the fixed UTC-epoch-anchored window containing ``now``.

    Windows are ``[start, start + window_seconds)`` anchored at the UTC epoch, so
    boundaries are deterministic across processes and workers. No sliding-window
    semantics are claimed.
    """
    epoch = int(now.timestamp())
    start = (epoch // window_seconds) * window_seconds
    return datetime.fromtimestamp(start, tz=UTC)


async def acquire_admission_lock(session: AsyncSession) -> None:
    """Acquire the transaction-scoped advisory lock guarding queue-cap admission."""
    await session.execute(
        text("SELECT pg_advisory_xact_lock(:key)"), {"key": ADMISSION_LOCK_KEY}
    )


async def count_queued(session: AsyncSession) -> int:
    result = await session.execute(
        select(func.count())
        .select_from(models.InferenceRequest)
        .where(models.InferenceRequest.state == RequestState.QUEUED)
    )
    return int(result.scalar_one())


async def get_request(session: AsyncSession, request_id: str) -> models.InferenceRequest | None:
    return await session.get(models.InferenceRequest, request_id)


async def get_request_for_update(
    session: AsyncSession, request_id: str
) -> models.InferenceRequest | None:
    """Return the request row with an exclusive row lock (for state transitions)."""
    result = await session.execute(
        select(models.InferenceRequest)
        .where(models.InferenceRequest.id == request_id)
        .with_for_update()
    )
    return result.scalar_one_or_none()


async def create_request(
    session: AsyncSession,
    *,
    request_id: RequestId,
    project_id: ProjectId | None,
    principal_id: PrincipalId | None,
    api_credential_id: ApiCredentialId | None,
    model_alias_id: ModelAliasId,
    endpoint_id: EndpointId | None,
    quota_group_id: str | None,
    stream: bool,
    payload_encrypted: bytes,
    queued_at: datetime,
    queue_wait_until: datetime,
    expires_at: datetime,
) -> models.InferenceRequest:
    row = models.InferenceRequest(
        id=str(request_id),
        project_id=str(project_id) if project_id else None,
        principal_id=str(principal_id) if principal_id else None,
        api_credential_id=str(api_credential_id) if api_credential_id else None,
        model_alias_id=str(model_alias_id),
        endpoint_id=str(endpoint_id) if endpoint_id else None,
        quota_group_id=quota_group_id,
        state=RequestState.QUEUED,
        stream=stream,
        payload_encrypted=payload_encrypted,
        queued_at=queued_at,
        queue_wait_until=queue_wait_until,
        expires_at=expires_at,
    )
    session.add(row)
    await session.flush()
    return row


def _eligible_queued_predicate(now: datetime):
    return (
        models.InferenceRequest.state == RequestState.QUEUED,
        models.InferenceRequest.cancellation_requested.is_(False),
        models.InferenceRequest.expires_at > now,
        or_(
            models.InferenceRequest.queue_wait_until.is_(None),
            models.InferenceRequest.queue_wait_until > now,
        ),
        or_(
            models.InferenceRequest.next_eligible_at.is_(None),
            models.InferenceRequest.next_eligible_at <= now,
        ),
    )


async def list_queued_scopes(
    session: AsyncSession, now: datetime
) -> list[tuple[str | None, str | None, str | None, datetime]]:
    """Return distinct scheduling scopes with eligible queued work, oldest first.

    A scheduling scope is ``(endpoint_id, quota_group_id, project_id)``. A null
    endpoint, null quota group, or null project is its own bucket. Including the
    project prevents a budget-blocked project from head-of-line blocking another
    project sharing the same endpoint/quota scope. FIFO is per scope; ordering
    across scopes is by each scope's oldest eligible queued request, so a
    quota-blocked scope never strands unrelated capacity on the same endpoint.
    """
    result = await session.execute(
        select(
            models.InferenceRequest.endpoint_id,
            models.InferenceRequest.quota_group_id,
            models.InferenceRequest.project_id,
            func.min(models.InferenceRequest.created_at),
        )
        .where(*_eligible_queued_predicate(now))
        .group_by(
            models.InferenceRequest.endpoint_id,
            models.InferenceRequest.quota_group_id,
            models.InferenceRequest.project_id,
        )
        .order_by(func.min(models.InferenceRequest.created_at))
    )
    return [(row[0], row[1], row[2], row[3]) for row in result.all()]


async def claim_next_queued_for_scope(
    session: AsyncSession,
    now: datetime,
    endpoint_id: str | None,
    quota_group_id: str | None,
    project_id: str | None,
) -> models.InferenceRequest | None:
    """Claim the oldest eligible queued request for one scheduling scope (FIFO).

    Rows locked by another worker are skipped so concurrent claimers do not block
    one another.
    """
    if endpoint_id is None:
        endpoint_predicate = models.InferenceRequest.endpoint_id.is_(None)
    else:
        endpoint_predicate = models.InferenceRequest.endpoint_id == endpoint_id
    if quota_group_id is None:
        group_predicate = models.InferenceRequest.quota_group_id.is_(None)
    else:
        group_predicate = models.InferenceRequest.quota_group_id == quota_group_id
    if project_id is None:
        project_predicate = models.InferenceRequest.project_id.is_(None)
    else:
        project_predicate = models.InferenceRequest.project_id == project_id
    stmt = (
        select(models.InferenceRequest)
        .where(
            endpoint_predicate,
            group_predicate,
            project_predicate,
            *_eligible_queued_predicate(now),
        )
        .order_by(models.InferenceRequest.created_at.asc(), models.InferenceRequest.id.asc())
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def fail_request_direct(
    session: AsyncSession,
    *,
    request_id: str,
    state: str,
    error_code: str,
    finished_at: datetime,
) -> None:
    """Fail a request that never reached reservation (revalidation failure)."""
    await session.execute(
        update(models.InferenceRequest)
        .where(models.InferenceRequest.id == request_id)
        .values(state=state, error_code=error_code, finished_at=finished_at)
    )


async def lock_endpoint(
    session: AsyncSession, endpoint_id: str
) -> models.Endpoint | None:
    stmt = (
        select(models.Endpoint)
        .where(models.Endpoint.id == endpoint_id)
        .with_for_update()
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def count_active_reservations(session: AsyncSession, endpoint_id: str) -> int:
    result = await session.execute(
        select(func.count())
        .select_from(models.Reservation)
        .where(
            models.Reservation.endpoint_id == endpoint_id,
            models.Reservation.released_at.is_(None),
        )
    )
    return int(result.scalar_one())


async def update_endpoint_operational_state(
    session: AsyncSession, endpoint_id: str, state: str
) -> None:
    """Set an endpoint's durable operational state under its row lock."""
    await session.execute(
        update(models.Endpoint)
        .where(models.Endpoint.id == endpoint_id)
        .values(operational_state=state)
    )


async def create_reservation(
    session: AsyncSession,
    *,
    reservation_id: ReservationId,
    request_id: str,
    endpoint_id: str,
    acquired_at: datetime,
) -> models.Reservation:
    row = models.Reservation(
        id=str(reservation_id),
        request_id=request_id,
        endpoint_id=endpoint_id,
        requested_units=1,
        granted_units=1,
        acquired_at=acquired_at,
    )
    session.add(row)
    await session.flush()
    return row


async def create_attempt(
    session: AsyncSession,
    *,
    attempt_id: ExecutionAttemptId,
    request_id: str,
    endpoint_id: str,
    reservation_id: str,
    fencing_token: int,
    worker_id: str,
    lease_expires_at: datetime,
    started_at: datetime,
) -> models.ExecutionAttempt:
    row = models.ExecutionAttempt(
        id=str(attempt_id),
        request_id=request_id,
        endpoint_id=endpoint_id,
        reservation_id=reservation_id,
        state="reserved",
        worker_id=worker_id,
        fencing_token=fencing_token,
        lease_expires_at=lease_expires_at,
        started_at=started_at,
    )
    session.add(row)
    await session.flush()
    return row


async def settle(
    session: AsyncSession,
    *,
    request_id: str,
    attempt_id: str,
    reservation_id: str,
    fencing_token: int,
    state: str,
    finished_at: datetime,
    result_encrypted: bytes | None = None,
    error_code: str | None = None,
    upstream_request_id: str | None = None,
) -> bool:
    """Atomically settle request+attempt to a terminal state and release capacity.

    Both rows must be active (``dispatched``/``streaming``) and still owned by
    ``fencing_token``. A request that is no longer active (for example recovery
    already marked it ``outcome_unknown``) is a no-op returning ``False``, so a
    late worker can never overwrite a conservative terminal state. The physical
    reservation is released only after both rows transitioned; if the attempt
    fails to transition, a :class:`SchedulerInvariantError` is raised so the
    transaction rolls back rather than leaving a half-settled request.
    """
    request_result = await session.execute(
        update(models.InferenceRequest)
        .where(
            models.InferenceRequest.id == request_id,
            models.InferenceRequest.fencing_token == fencing_token,
            models.InferenceRequest.state.in_(
                [RequestState.DISPATCHED, RequestState.STREAMING]
            ),
        )
        .values(
            state=state,
            result_encrypted=result_encrypted,
            error_code=error_code,
            finished_at=finished_at,
        )
    )
    if request_result.rowcount != 1:
        return False

    attempt_result = await session.execute(
        update(models.ExecutionAttempt)
        .where(
            models.ExecutionAttempt.id == attempt_id,
            models.ExecutionAttempt.fencing_token == fencing_token,
            models.ExecutionAttempt.state.in_(["dispatched", "streaming"]),
        )
        .values(
            state=state,
            finished_at=finished_at,
            upstream_request_id=upstream_request_id,
            error_code=error_code,
        )
    )
    if attempt_result.rowcount != 1:
        raise SchedulerInvariantError(
            f"request {request_id} settled to {state} but attempt "
            f"{attempt_id} did not transition"
        )
    await release_reservation(session, reservation_id, finished_at)
    return True


async def release_reservation(session: AsyncSession, reservation_id: str, now: datetime) -> None:
    await session.execute(
        update(models.Reservation)
        .where(models.Reservation.id == reservation_id)
        .values(released_at=now)
    )


async def clear_price_snapshot_reference(session: AsyncSession, request_id: str) -> None:
    """Detach a request's pre-dispatch price snapshot reference before deletion."""
    await session.execute(
        update(models.InferenceRequest)
        .where(models.InferenceRequest.id == request_id)
        .values(price_snapshot_id=None)
    )


async def release_active_reservation(session: AsyncSession, request_id: str, now: datetime) -> int:
    result = await session.execute(
        update(models.Reservation)
        .where(
            models.Reservation.request_id == request_id,
            models.Reservation.released_at.is_(None),
        )
        .values(released_at=now)
    )
    return result.rowcount


async def abandon_reserved_attempts(session: AsyncSession, request_id: str, now: datetime) -> int:
    result = await session.execute(
        update(models.ExecutionAttempt)
        .where(
            models.ExecutionAttempt.request_id == request_id,
            models.ExecutionAttempt.state == "reserved",
        )
        .values(state="abandoned", finished_at=now)
    )
    return result.rowcount


async def mark_dispatched(
    session: AsyncSession,
    *,
    request_id: str,
    attempt_id: str,
    fencing_token: int,
    now: datetime,
) -> str:
    """Atomically advance a reserved request+attempt to dispatched, or refuse.

    Dispatch intent is made durable on the request and its attempt together.
    Returns:

    - ``"dispatched"`` when both rows advanced to dispatched;
    - ``"expired"`` when the request was still reserved but past its total
      lifetime, in which case it is terminally expired and its attempt
      abandoned (the caller must release the reservation);
    - ``"not_reserved"`` when the request is no longer reserved (cancelled or
      otherwise transitioned), meaning a competing path already released its
      reservation.

    Raises :class:`SchedulerInvariantError` if the request advanced but the
    attempt did not (coupled invariant broken).
    """
    request_row = (
        await session.execute(
            select(models.InferenceRequest)
            .where(models.InferenceRequest.id == request_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if request_row is None or request_row.state != RequestState.RESERVED:
        return "not_reserved"

    if request_row.expires_at <= now:
        await session.execute(
            update(models.InferenceRequest)
            .where(models.InferenceRequest.id == request_id)
            .values(state=RequestState.EXPIRED, finished_at=now)
        )
        await session.execute(
            update(models.ExecutionAttempt)
            .where(
                models.ExecutionAttempt.id == attempt_id,
                models.ExecutionAttempt.state == "reserved",
            )
            .values(state="abandoned", finished_at=now)
        )
        return "expired"

    request_result = await session.execute(
        update(models.InferenceRequest)
        .where(
            models.InferenceRequest.id == request_id,
            models.InferenceRequest.fencing_token == fencing_token,
            models.InferenceRequest.state == RequestState.RESERVED,
        )
        .values(state=RequestState.DISPATCHED)
    )
    if request_result.rowcount != 1:
        return "not_reserved"

    attempt_result = await session.execute(
        update(models.ExecutionAttempt)
        .where(
            models.ExecutionAttempt.id == attempt_id,
            models.ExecutionAttempt.fencing_token == fencing_token,
            models.ExecutionAttempt.state == "reserved",
        )
        .values(state="dispatched")
    )
    if attempt_result.rowcount != 1:
        raise SchedulerInvariantError(
            f"request {request_id} dispatched but attempt {attempt_id} "
            f"did not transition"
        )
    return "dispatched"


async def renew_lease(
    session: AsyncSession,
    *,
    request_id: str,
    attempt_id: str,
    worker_id: str,
    fencing_token: int,
    lease_expires_at: datetime,
    now: datetime,
) -> bool:
    """Renew request+attempt ownership all-or-nothing.

    Succeeds only when both rows are active (``dispatched``/``streaming``), still
    owned by this worker (matching fence and ``worker_id``), and the request's
    total lifetime has not already expired. The new lease is clamped so it never
    outlives ``expires_at``, so a heartbeat cannot keep an execution alive past
    its lifetime. Returns ``False`` (no change) when any precondition fails, and
    raises :class:`SchedulerInvariantError` if the request renewed but the attempt
    did not, rolling back the partial update.
    """
    expires_at = (
        await session.execute(
            select(models.InferenceRequest.expires_at).where(
                models.InferenceRequest.id == request_id
            )
        )
    ).scalar_one_or_none()
    if expires_at is None or expires_at <= now:
        return False
    if lease_expires_at > expires_at:
        lease_expires_at = expires_at

    request_result = await session.execute(
        update(models.InferenceRequest)
        .where(
            models.InferenceRequest.id == request_id,
            models.InferenceRequest.fencing_token == fencing_token,
            models.InferenceRequest.worker_id == worker_id,
            models.InferenceRequest.state.in_(
                [RequestState.DISPATCHED, RequestState.STREAMING]
            ),
        )
        .values(lease_expires_at=lease_expires_at)
    )
    if request_result.rowcount != 1:
        return False

    attempt_result = await session.execute(
        update(models.ExecutionAttempt)
        .where(
            models.ExecutionAttempt.id == attempt_id,
            models.ExecutionAttempt.fencing_token == fencing_token,
            models.ExecutionAttempt.worker_id == worker_id,
            models.ExecutionAttempt.state.in_(["dispatched", "streaming"]),
        )
        .values(lease_expires_at=lease_expires_at)
    )
    if attempt_result.rowcount != 1:
        raise SchedulerInvariantError(
            f"request {request_id} lease renewed but attempt {attempt_id} "
            f"did not transition"
        )
    return True


async def set_cancellation_requested(session: AsyncSession, request_id: str) -> bool:
    """Flag cancellation for any non-terminal, cancelable state."""
    result = await session.execute(
        update(models.InferenceRequest)
        .where(
            models.InferenceRequest.id == request_id,
            models.InferenceRequest.state.in_(
                [
                    RequestState.QUEUED,
                    RequestState.RESERVED,
                    RequestState.DISPATCHED,
                    RequestState.STREAMING,
                ]
            ),
        )
        .values(cancellation_requested=True)
    )
    return result.rowcount == 1


async def expire_overdue_queued(session: AsyncSession, now: datetime) -> int:
    """Expire queued requests past their queue-wait (or total-lifetime) deadline.

    A request still ``queued`` has never contacted upstream; expiring it is safe.
    """
    result = await session.execute(
        update(models.InferenceRequest)
        .where(
            models.InferenceRequest.state == RequestState.QUEUED,
            or_(
                models.InferenceRequest.expires_at <= now,
                models.InferenceRequest.queue_wait_until.is_not(None)
                & (models.InferenceRequest.queue_wait_until <= now),
            ),
        )
        .values(state=RequestState.EXPIRED, finished_at=now)
    )
    return result.rowcount


async def reclaim_expired_reserved(session: AsyncSession, now: datetime) -> list[str]:
    """Reclaim reserved requests whose lease expired before dispatch intent.

    Only state ``reserved`` (dispatch intent not yet durable) is safely
    reclaimable. Requests already past their queue-wait deadline expire instead
    of being requeued. Row locks make concurrent recovery loops idempotent.
    Returns the reclaimed request IDs.
    """
    rows = (
        await session.execute(
            select(models.InferenceRequest)
            .where(
                models.InferenceRequest.state == RequestState.RESERVED,
                models.InferenceRequest.lease_expires_at < now,
            )
            .with_for_update(skip_locked=True)
        )
    ).scalars().all()

    freed: list[str] = []
    for row in rows:
        overdue = (
            row.queue_wait_until is not None and row.queue_wait_until <= now
        )
        new_state = RequestState.EXPIRED if overdue else RequestState.QUEUED
        values = {"state": new_state, "finished_at": now} if overdue else {
            "state": new_state,
            "worker_id": None,
            "fencing_token": None,
            "lease_expires_at": None,
            "started_at": None,
            "wait_reason": None,
            "wait_limit_id": None,
            "wait_limit_metric": None,
            "next_eligible_at": None,
        }
        await session.execute(
            update(models.InferenceRequest)
            .where(models.InferenceRequest.id == row.id)
            .values(**values)
        )
        # Find and release the active reservation for this request.
        reservations = (
            await session.execute(
                select(models.Reservation).where(
                    models.Reservation.request_id == row.id,
                    models.Reservation.released_at.is_(None),
                )
            )
        ).scalars().all()
        for reservation in reservations:
            await release_reservation(session, reservation.id, now)
        # Abandon any reserved attempts for this request.
        await session.execute(
            update(models.ExecutionAttempt)
            .where(
                models.ExecutionAttempt.request_id == row.id,
                models.ExecutionAttempt.state == "reserved",
            )
            .values(state="abandoned", finished_at=now)
        )
        # Reclaim pre-dispatch quota reservation: the request never dispatched,
        # so its reserved request/token units are returned to the window.
        await release_quota_reservations(session, request_id=row.id, now=now)
        freed.append(row.id)
    return freed


async def mark_outcome_unknown_expired(session: AsyncSession, now: datetime) -> int:
    """Mark dispatched/streaming requests whose lease expired as outcome_unknown.

    Request and its active execution attempt transition together; the reservation
    is kept (physical slot held) for explicit reconciliation. Row locks make
    concurrent recovery loops idempotent. Never auto-retried.
    """
    rows = (
        await session.execute(
            select(models.InferenceRequest)
            .where(
                models.InferenceRequest.state.in_(
                    [RequestState.DISPATCHED, RequestState.STREAMING]
                ),
                models.InferenceRequest.lease_expires_at < now,
            )
            .with_for_update(skip_locked=True)
        )
    ).scalars().all()

    count = 0
    for row in rows:
        request_result = await session.execute(
            update(models.InferenceRequest)
            .where(
                models.InferenceRequest.id == row.id,
                models.InferenceRequest.state.in_(
                    [RequestState.DISPATCHED, RequestState.STREAMING]
                ),
            )
            .values(state=RequestState.OUTCOME_UNKNOWN)
        )
        if request_result.rowcount != 1:
            raise SchedulerInvariantError(
                f"request {row.id} not marked outcome_unknown: "
                f"unexpected state during recovery"
            )
        attempt_result = await session.execute(
            update(models.ExecutionAttempt)
            .where(
                models.ExecutionAttempt.request_id == row.id,
                models.ExecutionAttempt.state.in_(["dispatched", "streaming"]),
            )
            .values(state="outcome_unknown", finished_at=now)
        )
        if attempt_result.rowcount != 1:
            raise SchedulerInvariantError(
                f"request {row.id} marked outcome_unknown but its active "
                f"attempt did not transition"
            )
        count += 1
    return count


async def list_outcome_unknown(
    session: AsyncSession, *, limit: int = 100
) -> list[models.InferenceRequest]:
    result = await session.execute(
        select(models.InferenceRequest)
        .where(models.InferenceRequest.state == RequestState.OUTCOME_UNKNOWN)
        .order_by(models.InferenceRequest.created_at.asc())
        .limit(limit)
    )
    return list(result.scalars().all())


async def reconcile_request(
    session: AsyncSession,
    *,
    request_id: str,
    disposition: str,
    operator: str,
    now: datetime,
) -> bool:
    """Explicitly reconcile an ``outcome_unknown`` request.

    Sets the request and its active attempt to ``disposition``, records the
    operator action durably, and releases the held reservation as part of the
    same reconciliation. Only ``outcome_unknown`` requests are reconcilable.
    """
    row = (
        await session.execute(
            select(models.InferenceRequest)
            .where(
                models.InferenceRequest.id == request_id,
                models.InferenceRequest.state == RequestState.OUTCOME_UNKNOWN,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    if row is None:
        return False

    await session.execute(
        update(models.InferenceRequest)
        .where(models.InferenceRequest.id == request_id)
        .values(
            state=disposition,
            finished_at=now,
            reconciled_state=disposition,
            reconciled_at=now,
            reconciled_by=operator,
        )
    )
    await session.execute(
        update(models.ExecutionAttempt)
        .where(
            models.ExecutionAttempt.request_id == request_id,
            models.ExecutionAttempt.state == "outcome_unknown",
        )
        .values(state=disposition, finished_at=now)
    )
    await release_active_reservation(session, request_id, now)
    # Conservative: reconcile never invents capacity. Still-reserved token units
    # are committed (reserved -> committed) rather than released, because the
    # upstream outcome is unknowable and may already have consumed them.
    await settle_token_quota(session, request_id=request_id, actual_tokens=None, now=now)
    return True


async def append_stream_event(
    session: AsyncSession,
    *,
    request_id: str,
    seq: int,
    event_encrypted: bytes,
) -> models.StreamEvent:
    row = models.StreamEvent(
        request_id=request_id, seq=seq, event_encrypted=event_encrypted
    )
    session.add(row)
    await session.flush()
    return row


async def list_stream_events_after(
    session: AsyncSession, request_id: str, after_seq: int
) -> list[models.StreamEvent]:
    result = await session.execute(
        select(models.StreamEvent)
        .where(
            models.StreamEvent.request_id == request_id,
            models.StreamEvent.seq > after_seq,
        )
        .order_by(models.StreamEvent.seq.asc())
    )
    return list(result.scalars().all())


async def _get_or_create_quota_window(
    session: AsyncSession, *, quota_limit_id: str, window_start: datetime
) -> models.QuotaWindow:
    """Lock an existing quota window row, or idempotently create and lock one.

    Creation is guarded by the unique ``(quota_limit_id, window_start)``
    constraint so concurrent workers converge on a single row; the returned row
    is always ``FOR UPDATE`` locked.
    """
    row = (
        await session.execute(
            select(models.QuotaWindow)
            .where(
                models.QuotaWindow.quota_limit_id == quota_limit_id,
                models.QuotaWindow.window_start == window_start,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if row is not None:
        return row
    await session.execute(
        pg_insert(models.QuotaWindow)
        .values(id=_new_id(), quota_limit_id=quota_limit_id, window_start=window_start)
        .on_conflict_do_nothing(index_elements=["quota_limit_id", "window_start"])
    )
    return (
        await session.execute(
            select(models.QuotaWindow)
            .where(
                models.QuotaWindow.quota_limit_id == quota_limit_id,
                models.QuotaWindow.window_start == window_start,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalar_one()


async def list_quota_limits_for_group(
    session: AsyncSession, *, group_id: str, enabled_only: bool = True
) -> list[models.QuotaLimit]:
    stmt = select(models.QuotaLimit).where(
        models.QuotaLimit.quota_group_id == group_id
    )
    if enabled_only:
        stmt = stmt.where(models.QuotaLimit.enabled.is_(True))
    stmt = stmt.order_by(models.QuotaLimit.id.asc())
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def get_quota_group_for_update(
    session: AsyncSession, *, group_id: str
) -> models.QuotaGroup | None:
    return (
        await session.execute(
            select(models.QuotaGroup)
            .where(models.QuotaGroup.id == group_id)
            .with_for_update()
        )
    ).scalar_one_or_none()


async def plan_quota_reservation(
    session: AsyncSession,
    *,
    limits: list[models.QuotaLimit],
    requested_by_limit: dict[str, int],
    now: datetime,
) -> QuotaReservationPlan:
    """Lock and evaluate every quota window for a request, without mutating.

    Locks the current fixed window of each limit (stable ID order), computes the
    first blocking limit and the earliest reset across blocking windows. The
    caller must call :func:`reserve_quota` (same transaction) once every other
    admission resource is known to fit, or roll back to reserve nothing.
    """
    ordered = sorted(limits, key=lambda limit: limit.id)
    windows: dict[str, models.QuotaWindow] = {}
    for limit in ordered:
        windows[limit.id] = await _get_or_create_quota_window(
            session,
            quota_limit_id=limit.id,
            window_start=fixed_window_start(now, limit.window_seconds),
        )
    blocking: models.QuotaLimit | None = None
    next_eligible: datetime | None = None
    for limit in ordered:
        units = requested_by_limit[limit.id]
        window = windows[limit.id]
        if window.committed_units + window.reserved_units + units > limit.limit_units:
            if blocking is None:
                blocking = limit
            reset = window.window_start + timedelta(seconds=limit.window_seconds)
            if next_eligible is None or reset > next_eligible:
                next_eligible = reset
    return QuotaReservationPlan(
        limits=ordered,
        requested_by_limit=requested_by_limit,
        windows=windows,
        blocking_limit=blocking,
        next_eligible_at=next_eligible,
    )


async def reserve_quota(
    session: AsyncSession, *, request_id: str, plan: QuotaReservationPlan
) -> None:
    """Reserve quota across every limit using a pre-evaluated, locked plan.

    The plan's windows are already FOR UPDATE-locked in this transaction and its
    capacity was already confirmed to fit; this only applies the mutation.
    """
    for limit in plan.limits:
        units = plan.requested_by_limit[limit.id]
        window = plan.windows[limit.id]
        window.reserved_units += units
        session.add(
            models.QuotaReservation(
                id=_new_id(),
                request_id=request_id,
                quota_limit_id=limit.id,
                window_start=window.window_start,
                metric=limit.metric,
                reserved_units=units,
                committed_units=0,
                state="reserved",
            )
        )


async def commit_request_quota(
    session: AsyncSession, *, request_id: str, now: datetime
) -> None:
    """Commit request-metric reservations at durable dispatch (reserved -> committed)."""
    rows = (
        await session.execute(
            select(models.QuotaReservation)
            .where(
                models.QuotaReservation.request_id == request_id,
                models.QuotaReservation.metric == "requests",
                models.QuotaReservation.state == "reserved",
            )
            .with_for_update()
        )
    ).scalars().all()
    for reservation in rows:
        window = await _get_or_create_quota_window(
            session,
            quota_limit_id=reservation.quota_limit_id,
            window_start=reservation.window_start,
        )
        window.reserved_units -= reservation.reserved_units
        window.committed_units += reservation.reserved_units
        reservation.committed_units = reservation.reserved_units
        reservation.reserved_units = 0
        reservation.state = "committed"
        reservation.committed_at = now


async def settle_token_quota(
    session: AsyncSession,
    *,
    request_id: str,
    actual_tokens: int | None,
    now: datetime,
) -> None:
    """Commit token-metric reservations at settlement.

    ``actual_tokens`` records real usage when it is trustworthy; when it is
    None (failure/cancellation after dispatch) the reserved amount is committed
    conservatively rather than released, so no capacity is invented.
    """
    rows = (
        await session.execute(
            select(models.QuotaReservation)
            .where(
                models.QuotaReservation.request_id == request_id,
                models.QuotaReservation.metric == "tokens",
                models.QuotaReservation.state == "reserved",
            )
            .with_for_update()
        )
    ).scalars().all()
    for reservation in rows:
        commit = actual_tokens if actual_tokens is not None else reservation.reserved_units
        window = await _get_or_create_quota_window(
            session,
            quota_limit_id=reservation.quota_limit_id,
            window_start=reservation.window_start,
        )
        window.reserved_units -= reservation.reserved_units
        window.committed_units += commit
        reservation.committed_units = commit
        reservation.reserved_units = 0
        reservation.state = "committed"
        reservation.committed_at = now


async def release_quota_reservations(
    session: AsyncSession, *, request_id: str, now: datetime
) -> None:
    """Release all still-reserved quota for a request (pre-dispatch reclaim)."""
    rows = (
        await session.execute(
            select(models.QuotaReservation)
            .where(
                models.QuotaReservation.request_id == request_id,
                models.QuotaReservation.state == "reserved",
            )
            .with_for_update()
        )
    ).scalars().all()
    for reservation in rows:
        window = await _get_or_create_quota_window(
            session,
            quota_limit_id=reservation.quota_limit_id,
            window_start=reservation.window_start,
        )
        window.reserved_units -= reservation.reserved_units
        reservation.reserved_units = 0
        reservation.state = "released"
        reservation.released_at = now


async def set_quota_group_cooldown(
    session: AsyncSession, *, group_id: str, cooldown_until: datetime
) -> None:
    """Set a group cooldown monotonically: never shorten an existing one.

    Locks the group row and keeps ``max(existing, new)`` so a concurrent or later
    429 with a shorter Retry-After cannot shorten an already-longer cooldown.
    """
    group = await get_quota_group_for_update(session, group_id=group_id)
    if group is None:
        return
    if group.cooldown_until is not None and group.cooldown_until > cooldown_until:
        return
    group.cooldown_until = cooldown_until


async def set_request_wait_metadata(
    session: AsyncSession,
    *,
    request_id: str,
    wait_reason: str,
    wait_limit_id: str | None = None,
    wait_limit_metric: str | None = None,
    next_eligible_at: datetime | None = None,
) -> None:
    """Persist non-content metadata explaining why a queued request is blocked."""
    await session.execute(
        update(models.InferenceRequest)
        .where(models.InferenceRequest.id == request_id)
        .values(
            wait_reason=wait_reason,
            wait_limit_id=wait_limit_id,
            wait_limit_metric=wait_limit_metric,
            next_eligible_at=next_eligible_at,
        )
    )


async def clear_request_wait_metadata(session: AsyncSession, request_id: str) -> None:
    """Clear transient wait metadata once a request is no longer blocked.

    The persisted scheduling scope (``quota_group_id``) is not cleared: it is the
    request's effective scope, not transient wait state.
    """
    await session.execute(
        update(models.InferenceRequest)
        .where(models.InferenceRequest.id == request_id)
        .values(
            wait_reason=None,
            wait_limit_id=None,
            wait_limit_metric=None,
            next_eligible_at=None,
        )
    )


# ---------------------------------------------------------------------------
# Queue / operator control-plane reads (no content, no secrets)
# ---------------------------------------------------------------------------


def _queue_request_filters(
    *,
    project_ids: set[str] | None,
    principal_id: str | None,
    api_credential_id: str | None,
    model_alias_id: str | None,
    endpoint_id: str | None,
    state: str | None,
    stream: bool | None,
    wait_reason: str | None,
    queued_from: datetime | None,
    queued_to: datetime | None,
    created_from: datetime | None,
    created_to: datetime | None,
) -> list:
    preds = []
    if project_ids is not None:
        preds.append(models.InferenceRequest.project_id.in_(project_ids))
    if principal_id is not None:
        preds.append(models.InferenceRequest.principal_id == principal_id)
    if api_credential_id is not None:
        preds.append(models.InferenceRequest.api_credential_id == api_credential_id)
    if model_alias_id is not None:
        preds.append(models.InferenceRequest.model_alias_id == model_alias_id)
    if endpoint_id is not None:
        preds.append(models.InferenceRequest.endpoint_id == endpoint_id)
    if state is not None:
        preds.append(models.InferenceRequest.state == state)
    if stream is not None:
        preds.append(models.InferenceRequest.stream == stream)
    if wait_reason is not None:
        preds.append(models.InferenceRequest.wait_reason == wait_reason)
    if queued_from is not None:
        preds.append(models.InferenceRequest.queued_at >= queued_from)
    if queued_to is not None:
        preds.append(models.InferenceRequest.queued_at <= queued_to)
    if created_from is not None:
        preds.append(models.InferenceRequest.created_at >= created_from)
    if created_to is not None:
        preds.append(models.InferenceRequest.created_at <= created_to)
    return preds


async def list_requests_paged(
    session: AsyncSession, *, limit: int, offset: int, **filters
) -> list[models.InferenceRequest]:
    preds = _queue_request_filters(**filters)
    stmt = (
        select(models.InferenceRequest)
        .where(*preds)
        .order_by(models.InferenceRequest.created_at.asc(), models.InferenceRequest.id.asc())
        .limit(limit)
        .offset(offset)
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def count_requests(session: AsyncSession, **filters) -> int:
    preds = _queue_request_filters(**filters)
    stmt = select(func.count()).select_from(models.InferenceRequest).where(*preds)
    return int((await session.execute(stmt)).scalar_one())


async def count_requests_by_state(
    session: AsyncSession, *, project_ids: set[str] | None = None
) -> dict[str, int]:
    stmt = select(models.InferenceRequest.state, func.count())
    if project_ids is not None:
        stmt = stmt.where(models.InferenceRequest.project_id.in_(project_ids))
    stmt = stmt.group_by(models.InferenceRequest.state)
    result = await session.execute(stmt)
    return {row[0]: int(row[1]) for row in result.all()}


async def oldest_queued_at(
    session: AsyncSession, *, project_ids: set[str] | None = None
) -> datetime | None:
    stmt = select(func.min(models.InferenceRequest.queued_at)).where(
        models.InferenceRequest.state == RequestState.QUEUED
    )
    if project_ids is not None:
        stmt = stmt.where(models.InferenceRequest.project_id.in_(project_ids))
    return (await session.execute(stmt)).scalar_one_or_none()


async def get_endpoint_row(
    session: AsyncSession, endpoint_id: str
) -> models.Endpoint | None:
    return await session.get(models.Endpoint, endpoint_id)


async def list_all_endpoints(session: AsyncSession) -> list[models.Endpoint]:
    result = await session.execute(
        select(models.Endpoint).order_by(models.Endpoint.name.asc(), models.Endpoint.id.asc())
    )
    return list(result.scalars().all())


async def count_active_reservations_by_endpoint(session: AsyncSession) -> dict[str, int]:
    stmt = (
        select(models.Reservation.endpoint_id, func.count())
        .where(models.Reservation.released_at.is_(None))
        .group_by(models.Reservation.endpoint_id)
    )
    result = await session.execute(stmt)
    return {row[0]: int(row[1]) for row in result.all()}


async def oldest_queued_for_endpoint(
    session: AsyncSession, endpoint_id: str
) -> datetime | None:
    stmt = select(func.min(models.InferenceRequest.queued_at)).where(
        models.InferenceRequest.state == RequestState.QUEUED,
        models.InferenceRequest.endpoint_id == endpoint_id,
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def list_endpoint_operational_states(
    session: AsyncSession, endpoint_ids: set[str]
) -> dict[str, str]:
    if not endpoint_ids:
        return {}
    stmt = select(models.Endpoint.id, models.Endpoint.operational_state).where(
        models.Endpoint.id.in_(list(endpoint_ids))
    )
    result = await session.execute(stmt)
    return {row[0]: row[1] for row in result.all()}


async def list_outcome_unknown_paged(
    session: AsyncSession, *, limit: int, offset: int
) -> list[models.InferenceRequest]:
    stmt = (
        select(models.InferenceRequest)
        .where(models.InferenceRequest.state == RequestState.OUTCOME_UNKNOWN)
        .order_by(models.InferenceRequest.created_at.asc(), models.InferenceRequest.id.asc())
        .limit(limit)
        .offset(offset)
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def count_outcome_unknown(session: AsyncSession) -> int:
    stmt = (
        select(func.count())
        .select_from(models.InferenceRequest)
        .where(models.InferenceRequest.state == RequestState.OUTCOME_UNKNOWN)
    )
    return int((await session.execute(stmt)).scalar_one())


async def list_quota_runtime_rows(session: AsyncSession) -> list:
    stmt = (
        select(
            models.QuotaGroup.id,
            models.QuotaGroup.name,
            models.QuotaGroup.provider_account_id,
            models.QuotaGroup.cooldown_until,
            models.QuotaLimit.id,
            models.QuotaLimit.name,
            models.QuotaLimit.metric,
            models.QuotaLimit.limit_units,
            models.QuotaLimit.window_seconds,
            models.QuotaLimit.enabled,
        )
        .join(models.QuotaLimit, models.QuotaLimit.quota_group_id == models.QuotaGroup.id)
        .order_by(
            models.QuotaGroup.name.asc(),
            models.QuotaLimit.metric.asc(),
            models.QuotaLimit.id.asc(),
        )
    )
    result = await session.execute(stmt)
    return result.all()


async def list_quota_windows_for_limits(
    session: AsyncSession, limit_ids: set[str]
) -> dict[str, list[models.QuotaWindow]]:
    if not limit_ids:
        return {}
    stmt = select(models.QuotaWindow).where(
        models.QuotaWindow.quota_limit_id.in_(list(limit_ids))
    )
    result = await session.execute(stmt)
    grouped: dict[str, list[models.QuotaWindow]] = {}
    for row in result.scalars().all():
        grouped.setdefault(row.quota_limit_id, []).append(row)
    return grouped
