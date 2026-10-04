"""Low-level scheduler persistence operations.

These functions operate on ORM rows and are internal to the scheduler domain.
Callers must manage their own transaction boundaries via ``AsyncSession.begin``.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, or_, select, text, update
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
from aethergate.persistence import models

# Stable, documented advisory-lock key that serializes queue-cap admission
# (count-then-insert) across concurrent API processes. It is a single global
# scope: the queue bound is a global bound, not per-endpoint.
ADMISSION_LOCK_KEY = 0x4147_5144  # "AGQD"


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
    )


async def list_queued_endpoints(
    session: AsyncSession, now: datetime
) -> list[tuple[str | None, datetime]]:
    """Return distinct endpoints with eligible queued work, oldest first.

    FIFO is per endpoint; ordering across endpoints is by each endpoint's oldest
    eligible queued request so a saturated endpoint never starves an unrelated
    one. A null ``endpoint_id`` is grouped as its own bucket.
    """
    result = await session.execute(
        select(
            models.InferenceRequest.endpoint_id,
            func.min(models.InferenceRequest.created_at),
        )
        .where(*_eligible_queued_predicate(now))
        .group_by(models.InferenceRequest.endpoint_id)
        .order_by(func.min(models.InferenceRequest.created_at))
    )
    return [(row[0], row[1]) for row in result.all()]


async def claim_next_queued_for_endpoint(
    session: AsyncSession, now: datetime, endpoint_id: str | None
) -> models.InferenceRequest | None:
    """Claim the oldest eligible queued request for a single endpoint (FIFO).

    Rows locked by another worker are skipped so concurrent claimers do not
    block one another.
    """
    if endpoint_id is None:
        endpoint_predicate = models.InferenceRequest.endpoint_id.is_(None)
    else:
        endpoint_predicate = models.InferenceRequest.endpoint_id == endpoint_id
    stmt = (
        select(models.InferenceRequest)
        .where(endpoint_predicate, *_eligible_queued_predicate(now))
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


async def settle_request(
    session: AsyncSession,
    *,
    request_id: str,
    fencing_token: int,
    state: str,
    result_encrypted: bytes | None,
    finished_at: datetime,
    error_code: str | None = None,
) -> bool:
    """Transition a request to a terminal state iff the fence still matches."""
    result = await session.execute(
        update(models.InferenceRequest)
        .where(
            models.InferenceRequest.id == request_id,
            models.InferenceRequest.fencing_token == fencing_token,
        )
        .values(
            state=state,
            result_encrypted=result_encrypted,
            error_code=error_code,
            finished_at=finished_at,
        )
    )
    return result.rowcount == 1


async def settle_attempt(
    session: AsyncSession,
    *,
    attempt_id: str,
    fencing_token: int,
    state: str,
    finished_at: datetime,
    upstream_request_id: str | None = None,
    error_code: str | None = None,
) -> bool:
    result = await session.execute(
        update(models.ExecutionAttempt)
        .where(
            models.ExecutionAttempt.id == attempt_id,
            models.ExecutionAttempt.fencing_token == fencing_token,
        )
        .values(
            state=state,
            finished_at=finished_at,
            upstream_request_id=upstream_request_id,
            error_code=error_code,
        )
    )
    return result.rowcount == 1


async def release_reservation(session: AsyncSession, reservation_id: str, now: datetime) -> None:
    await session.execute(
        update(models.Reservation)
        .where(models.Reservation.id == reservation_id)
        .values(released_at=now)
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


async def mark_attempt_dispatched(
    session: AsyncSession, *, attempt_id: str, fencing_token: int
) -> bool:
    result = await session.execute(
        update(models.ExecutionAttempt)
        .where(
            models.ExecutionAttempt.id == attempt_id,
            models.ExecutionAttempt.fencing_token == fencing_token,
            models.ExecutionAttempt.state == "reserved",
        )
        .values(state="dispatched")
    )
    return result.rowcount == 1


async def mark_request_dispatched(
    session: AsyncSession, *, request_id: str, fencing_token: int
) -> bool:
    """Transition a request from ``reserved`` to ``dispatched`` iff fence matches.

    The state guard means a request cancelled (or otherwise transitioned) after
    reservation is never re-marked as dispatched.
    """
    result = await session.execute(
        update(models.InferenceRequest)
        .where(
            models.InferenceRequest.id == request_id,
            models.InferenceRequest.fencing_token == fencing_token,
            models.InferenceRequest.state == RequestState.RESERVED,
        )
        .values(state=RequestState.DISPATCHED)
    )
    return result.rowcount == 1


async def renew_lease(
    session: AsyncSession,
    *,
    request_id: str,
    attempt_id: str,
    fencing_token: int,
    lease_expires_at: datetime,
) -> bool:
    """Renew request and attempt ownership for the current fencing token.

    Only a request/attempt still owned by this worker (matching fence, still in
    an active dispatched/streaming state) is renewed. A stale worker whose fence
    changed, or whose work reached a terminal state, cannot renew.
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
        .values(lease_expires_at=lease_expires_at)
    )
    if request_result.rowcount != 1:
        return False
    await session.execute(
        update(models.ExecutionAttempt)
        .where(
            models.ExecutionAttempt.id == attempt_id,
            models.ExecutionAttempt.fencing_token == fencing_token,
            models.ExecutionAttempt.state.in_(["dispatched", "streaming"]),
        )
        .values(lease_expires_at=lease_expires_at)
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
    Returns the freed reservation IDs.
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
            freed.append(reservation.id)
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
        await session.execute(
            update(models.InferenceRequest)
            .where(models.InferenceRequest.id == row.id)
            .values(state=RequestState.OUTCOME_UNKNOWN)
        )
        await session.execute(
            update(models.ExecutionAttempt)
            .where(
                models.ExecutionAttempt.request_id == row.id,
                models.ExecutionAttempt.state.in_(["dispatched", "streaming"]),
            )
            .values(state="outcome_unknown", finished_at=now)
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
