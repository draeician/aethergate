"""Scheduler phase 1 service: durable queueing, endpoint concurrency, and recovery.

This milestone enforces physical endpoint concurrency only. Provider RPM/TPM,
token reservations, shared-account quotas, project budgets, and retry/cooldown
orchestration are later phases.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from aethergate.accounting import repository as accounting_repository
from aethergate.accounting import service as accounting_service
from aethergate.adapters.base import GenerationParams, Message, Usage
from aethergate.catalog.service import (
    ResolvedRoute,
    resolve_model_alias,
    resolve_model_alias_by_id,
)
from aethergate.config import Settings
from aethergate.domain.entities import RequestContext
from aethergate.domain.enums import (
    BillingUnit,
    EndpointOperationalState,
    QuotaMetric,
    RequestState,
)
from aethergate.domain.ids import (
    ApiCredentialId,
    ExecutionAttemptId,
    ModelAliasId,
    PrincipalId,
    ProjectId,
    RequestId,
    ReservationId,
)
from aethergate.encryption import QueueEncryptor
from aethergate.errors import (
    AmbiguousRoute,
    AuthenticationRequired,
    DestinationDenied,
    DomainError,
    ModelAliasNotFound,
    ProviderError,
    QueueFull,
    ResourceInactive,
    RouteUnresolved,
    SchedulerInvariantError,
    SecretResolutionError,
    UnsupportedProvider,
)
from aethergate.identity import service as identity_service
from aethergate.inference.service import InferenceService, PreparedDispatch
from aethergate.persistence import models
from aethergate.scheduler import repository as scheduler_repository

logger = logging.getLogger(__name__)

TERMINAL_STATES = {
    RequestState.SUCCEEDED,
    RequestState.FAILED,
    RequestState.CANCELLED,
    RequestState.EXPIRED,
    RequestState.OUTCOME_UNKNOWN,
}

# Only ``failed``/``cancelled`` are reconcilable dispositions. ``succeeded`` is
# deliberately excluded: an outcome_unknown request's result was never persisted,
# so an operator cannot reconstruct a trustworthy success without supplying the
# payload, which phase 1 does not support.
RECONCILABLE_STATES = {
    RequestState.FAILED,
    RequestState.CANCELLED,
}

_MAX_ERROR_CODE_LENGTH = 64


def utcnow() -> datetime:
    return datetime.now(UTC)


def _new_id() -> str:
    return uuid.uuid4().hex


def _sanitize_error_code(message: str) -> str:
    return (message or "scheduler_error")[:_MAX_ERROR_CODE_LENGTH]


# --- payload (de)serialization ----------------------------------------------


def serialize_chat_payload(messages: list[Message], params: GenerationParams) -> bytes:
    data = {
        "messages": [{"role": m.role, "content": m.content} for m in messages],
        "params": {
            "temperature": params.temperature,
            "top_p": params.top_p,
            "max_tokens": params.max_tokens,
            "stop": params.stop,
            "frequency_penalty": params.frequency_penalty,
            "presence_penalty": params.presence_penalty,
            "seed": params.seed,
        },
    }
    return json.dumps(data).encode("utf-8")


def deserialize_chat_payload(data: bytes) -> tuple[list[Message], GenerationParams]:
    obj = json.loads(data.decode("utf-8"))
    messages = [
        Message(role=m["role"], content=m["content"]) for m in obj["messages"]
    ]
    p = obj.get("params", {})
    params = GenerationParams(
        temperature=p.get("temperature"),
        top_p=p.get("top_p"),
        max_tokens=p.get("max_tokens"),
        stop=p.get("stop"),
        frequency_penalty=p.get("frequency_penalty"),
        presence_penalty=p.get("presence_penalty"),
        seed=p.get("seed"),
    )
    return messages, params


def serialize_result(
    content: str,
    finish_reason: str | None,
    usage: Usage,
    upstream_request_id: str | None,
) -> bytes:
    data = {
        "content": content,
        "finish_reason": finish_reason,
        "usage": {
            "prompt_tokens": usage.prompt_tokens,
            "completion_tokens": usage.completion_tokens,
            "total_tokens": usage.total_tokens,
        },
        "upstream_request_id": upstream_request_id,
    }
    return json.dumps(data).encode("utf-8")


def deserialize_result(data: bytes) -> dict:
    return json.loads(data.decode("utf-8"))


def serialize_event(
    content: str | None, finish_reason: str | None, usage: Usage | None
) -> bytes:
    data = {
        "content": content,
        "finish_reason": finish_reason,
        "usage": (
            {
                "prompt_tokens": usage.prompt_tokens,
                "completion_tokens": usage.completion_tokens,
                "total_tokens": usage.total_tokens,
            }
            if usage is not None
            else None
        ),
    }
    return json.dumps(data).encode("utf-8")


def deserialize_event(data: bytes) -> dict:
    return json.loads(data.decode("utf-8"))


# --- result dataclasses ------------------------------------------------------


@dataclass(frozen=True)
class EnqueuedRequest:
    request_id: str
    endpoint_id: str | None
    alias_name: str
    queue_wait_until: datetime
    expires_at: datetime


@dataclass(frozen=True)
class TerminalResult:
    state: str
    content: str | None
    finish_reason: str | None
    usage: Usage | None
    upstream_request_id: str | None
    error_code: str | None


@dataclass(frozen=True)
class ClaimedWork:
    request_id: str
    attempt_id: str
    reservation_id: str
    worker_id: str
    fencing_token: int
    endpoint_id: str
    public_alias: str
    stream: bool
    prepared: PreparedDispatch
    quota_group_id: str | None = None


@dataclass
class _QuotaEvaluation:
    """Outcome of evaluating (but not mutating) a request's quota constraints.

    ``outcome`` is one of ``ok``, ``exhausted``, ``cooldown``, ``too_large``,
    ``unbounded_output``, or ``estimator_unavailable``. ``plan`` holds the locked
    windows to mutate later (same transaction) only when ``outcome`` is ``ok`` or
    ``exhausted``.
    """

    outcome: str
    plan: scheduler_repository.QuotaReservationPlan | None = None
    cooldown_until: datetime | None = None


@dataclass
class _BudgetEvaluation:
    """Outcome of evaluating (but not mutating) a request's budget constraints.

    ``outcome`` is one of ``none`` (no applicable price/budget), ``ok``,
    ``exhausted``, ``too_large``, ``unbounded_output``, or
    ``estimator_unavailable``. ``price_policy`` is locked whenever a price policy
    exists (even with no budget policies, so a snapshot is still captured at
    dispatch); ``policies``/``windows`` are locked and ready to reserve in the
    same transaction when ``outcome`` is ``ok``.
    """

    outcome: str
    price_policy: models.PricePolicy | None = None
    policies: list[models.ProjectBudgetPolicy] = field(default_factory=list)
    windows: dict[str, models.BudgetWindow] = field(default_factory=dict)
    required_amount: Decimal | None = None
    input_units: int | None = None
    output_units: int | None = None
    blocking_policy: models.ProjectBudgetPolicy | None = None
    next_eligible_at: datetime | None = None


def _error_hint(exc: BaseException) -> str:
    if isinstance(exc, ProviderError):
        return exc.message
    if isinstance(exc, ModelAliasNotFound):
        return "model_not_found"
    if isinstance(exc, ResourceInactive):
        return "model_unavailable"
    if isinstance(exc, AmbiguousRoute):
        return "ambiguous_route"
    if isinstance(exc, RouteUnresolved):
        return "route_unresolved"
    if isinstance(exc, DestinationDenied):
        return "destination_denied"
    if isinstance(exc, UnsupportedProvider):
        return "unsupported_provider"
    if isinstance(exc, SecretResolutionError):
        return "secret_resolution_failed"
    return "dispatch_error"


# --- shared scheduler transition primitives ---------------------------------
#
# These session-bound functions are the single implementation of the
# cancellation and reconciliation state machines. The inference scheduler, the
# development reconcile CLI, and the operator admin API all call them, so there
# is no second, subtly different lifecycle to drift out of sync.


async def _release_pre_dispatch_accounting(
    session: AsyncSession,
    *,
    request_id: str,
    snapshot_id: str | None,
    now: datetime,
) -> None:
    """Release budget and discard the pre-dispatch snapshot for a request that
    never reached durable dispatch intent (so no orphan snapshot survives)."""
    await accounting_repository.release_budget_reservations(
        session, request_id=request_id, now=now
    )
    if snapshot_id is not None:
        await scheduler_repository.clear_price_snapshot_reference(session, request_id)
        await accounting_repository.discard_price_snapshot(session, snapshot_id=snapshot_id)


async def cancel_request_transition(
    session: AsyncSession, request_id: str, now: datetime
) -> tuple[str, str | None]:
    """Apply safe cancellation within an existing transaction.

    Returns ``(outcome, state)`` where ``outcome`` is one of ``cancelled_now``,
    ``cancellation_requested``, ``already_cancelled``, ``terminal``,
    ``outcome_unknown``, or ``not_found``, and ``state`` is the request's
    resulting (or current) state. Only non-terminal, non-ambiguous states are
    mutated; the caller owns the surrounding transaction and must commit.
    """
    row = await scheduler_repository.get_request_for_update(session, request_id)
    if row is None:
        return "not_found", None
    if row.state == RequestState.CANCELLED:
        return "already_cancelled", RequestState.CANCELLED
    if row.state == RequestState.OUTCOME_UNKNOWN:
        return "outcome_unknown", RequestState.OUTCOME_UNKNOWN
    if row.state in (
        RequestState.SUCCEEDED,
        RequestState.FAILED,
        RequestState.EXPIRED,
    ):
        return "terminal", row.state
    if row.state == RequestState.QUEUED:
        row.state = RequestState.CANCELLED
        row.cancellation_requested = True
        row.finished_at = now
        return "cancelled_now", RequestState.CANCELLED
    if row.state == RequestState.RESERVED:
        row.state = RequestState.CANCELLED
        row.cancellation_requested = True
        row.finished_at = now
        await scheduler_repository.release_active_reservation(session, request_id, now)
        await scheduler_repository.abandon_reserved_attempts(session, request_id, now)
        await scheduler_repository.release_quota_reservations(
            session, request_id=request_id, now=now
        )
        await _release_pre_dispatch_accounting(
            session, request_id=request_id, snapshot_id=row.price_snapshot_id, now=now
        )
        return "cancelled_now", RequestState.CANCELLED
    # dispatched / streaming: flag for the owning worker; do not claim upstream
    # execution has already stopped.
    await scheduler_repository.set_cancellation_requested(session, request_id)
    return "cancellation_requested", row.state


async def reconcile_transition(
    session: AsyncSession,
    *,
    request_id: str,
    disposition: str,
    operator: str,
    now: datetime,
) -> bool:
    """Explicitly reconcile an ``outcome_unknown`` request within a transaction.

    ``disposition`` must be in :data:`RECONCILABLE_STATES` (``failed``/
    ``cancelled``). The held reservation is released and the budget reservation
    is conservatively committed through the established reconciliation path.
    """
    if disposition not in RECONCILABLE_STATES:
        raise ValueError(f"disposition must be one of {sorted(RECONCILABLE_STATES)}")
    result = await scheduler_repository.reconcile_request(
        session,
        request_id=request_id,
        disposition=disposition,
        operator=operator,
        now=now,
    )
    if result:
        await accounting_repository.commit_budget_reservations_conservative(
            session, request_id=request_id, now=now, reason=disposition
        )
    return result


class SchedulingService:
    """Orchestrates admission, durable queueing, dispatch, and recovery."""

    def __init__(
        self,
        *,
        encryptor: QueueEncryptor,
        inference_service: InferenceService,
        session_factory: async_sessionmaker[AsyncSession],
        settings: Settings,
    ) -> None:
        self._encryptor = encryptor
        self._inference = inference_service
        self._session_factory = session_factory
        self._settings = settings

    # --- API-side -----------------------------------------------------------

    async def admit_and_enqueue(
        self,
        *,
        alias_name: str,
        messages: list[Message],
        params: GenerationParams,
        stream: bool,
        context: RequestContext,
    ) -> EnqueuedRequest:
        """Validate/resolve, then enqueue an encrypted request durably."""
        project_id = context.project_id
        principal_id = context.principal_id
        api_credential_id = context.api_credential_id
        async with self._session_factory() as session:
            resolved = await resolve_model_alias(session, alias_name)
            self._inference.validate_destination(resolved)
            request_id, queue_wait_until, expires_at = await self._enqueue(
                session,
                resolved=resolved,
                messages=messages,
                params=params,
                stream=stream,
                project_id=project_id,
                principal_id=principal_id,
                api_credential_id=api_credential_id,
            )
            await session.commit()
        return EnqueuedRequest(
            request_id=request_id,
            endpoint_id=resolved.endpoint.id,
            alias_name=alias_name,
            queue_wait_until=queue_wait_until,
            expires_at=expires_at,
        )

    async def wait_for_terminal(
        self, request_id: str, deadline: datetime
    ) -> TerminalResult:
        """Poll until the request reaches a terminal state or ``deadline``.

        ``deadline`` is the persisted total-lifetime deadline, not a fresh timer,
        so the API's wait cannot drift from the durable scheduler state. On
        deadline, any still-queued/reserved work is durably cancelled so it can
        never dispatch after the client has given up.
        """
        while utcnow() < deadline:
            async with self._session_factory() as session:
                row = await scheduler_repository.get_request(session, request_id)
            if row is None:
                return TerminalResult(
                    RequestState.EXPIRED, None, None, None, None, "not_found"
                )
            if row.state in TERMINAL_STATES:
                content = finish_reason = None
                usage: Usage | None = None
                upstream_request_id = None
                if row.result_encrypted is not None:
                    data = deserialize_result(self._encryptor.decrypt(row.result_encrypted))
                    content = data.get("content")
                    finish_reason = data.get("finish_reason")
                    upstream_request_id = data.get("upstream_request_id")
                    raw_usage = data.get("usage") or {}
                    usage = Usage(**raw_usage)
                return TerminalResult(
                    row.state,
                    content,
                    finish_reason,
                    usage,
                    upstream_request_id,
                    row.error_code,
                )
            await asyncio.sleep(self._settings.scheduler_poll_interval_seconds)
        await self.request_cancellation(request_id)
        return TerminalResult(RequestState.EXPIRED, None, None, None, None, "timeout")

    @property
    def poll_interval(self) -> float:
        return self._settings.scheduler_poll_interval_seconds

    async def request_cancellation(self, request_id: str) -> None:
        """Durably cancel a request, choosing the safe action for its state.

        Queued and reserved (pre-dispatch) requests are terminally cancelled and
        their capacity is released; dispatched/streaming requests are flagged so
        the owning worker settles them cancelled (or outcome_unknown if the
        upstream outcome cannot be established).
        """
        now = utcnow()
        async with self._session_factory() as session:
            async with session.begin():
                await cancel_request_transition(session, request_id, now)

    async def cancel_request(
        self, request_id: str
    ) -> tuple[str, str | None]:
        """Cancel a request and return ``(outcome, state)`` for the admin API.

        The transition primitive is shared with :meth:`request_cancellation` so
        operator and client cancellation cannot diverge.
        """
        now = utcnow()
        async with self._session_factory() as session:
            async with session.begin():
                return await cancel_request_transition(session, request_id, now)

    async def is_cancelled(self, request_id: str) -> bool:
        async with self._session_factory() as session:
            row = await scheduler_repository.get_request(session, request_id)
            return row.cancellation_requested if row is not None else True

    async def get_state(self, request_id: str) -> str | None:
        async with self._session_factory() as session:
            row = await scheduler_repository.get_request(session, request_id)
            return row.state if row is not None else None

    async def stream_events_after(
        self, request_id: str, after_seq: int
    ) -> list[dict]:
        """Return decrypted stream events with seq > ``after_seq``, in order."""
        async with self._session_factory() as session:
            rows = await scheduler_repository.list_stream_events_after(
                session, request_id, after_seq
            )
        return [
            {"seq": r.seq, **deserialize_event(self._encryptor.decrypt(r.event_encrypted))}
            for r in rows
        ]

    # --- reconciliation -----------------------------------------------------

    async def list_outcome_unknown(self) -> list[dict]:
        """Return metadata for outcome_unknown requests (never their content)."""
        async with self._session_factory() as session:
            rows = await scheduler_repository.list_outcome_unknown(session)
        return [
            {
                "request_id": r.id,
                "endpoint_id": r.endpoint_id,
                "worker_id": r.worker_id,
                "state": r.state,
                "queued_at": r.queued_at,
                "started_at": r.started_at,
                "lease_expires_at": r.lease_expires_at,
            }
            for r in rows
        ]

    async def reconcile(self, request_id: str, disposition: str, operator: str) -> bool:
        """Explicitly reconcile an outcome_unknown request to a terminal state."""
        now = utcnow()
        async with self._session_factory() as session:
            async with session.begin():
                return await reconcile_transition(
                    session,
                    request_id=request_id,
                    disposition=disposition,
                    operator=operator,
                    now=now,
                )

    # --- worker-side --------------------------------------------------------

    async def recover(self) -> None:
        """Expire overdue queued work and apply conservative lease recovery."""
        now = utcnow()
        async with self._session_factory() as session:
            async with session.begin():
                expired = await scheduler_repository.expire_overdue_queued(session, now)
                reclaimed = await scheduler_repository.reclaim_expired_reserved(session, now)
                for request_id in reclaimed:
                    row = await scheduler_repository.get_request_for_update(session, request_id)
                    await _release_pre_dispatch_accounting(
                        session,
                        request_id=request_id,
                        snapshot_id=row.price_snapshot_id if row is not None else None,
                        now=now,
                    )
                unknown = await scheduler_repository.mark_outcome_unknown_expired(session, now)
        if expired or reclaimed or unknown:
            logger.info(
                "scheduler recovery: expired=%d reclaimed=%d outcome_unknown=%d",
                expired,
                len(reclaimed),
                unknown,
            )

    async def claim_and_reserve(self, worker_id: str) -> ClaimedWork | str | None:
        """Claim one eligible request (FIFO per scheduling scope) and reserve capacity.

        Iterates over scheduling scopes (endpoint + effective quota group + project)
        that hold eligible queued work, oldest first. A quota-blocked scope is skipped
        so it never strands unrelated capacity on the same endpoint, and a
        saturated endpoint is skipped so it never blocks an unrelated endpoint
        with available capacity. Returns :class:`ClaimedWork`, ``"full"`` when
        every candidate endpoint is saturated, ``"processed"`` when a request was
        resolved to a failure, ``"quota"`` when eligible work is quota-blocked,
        ``"budget"`` when eligible work is budget-blocked, or ``None`` when
        nothing is eligible.
        """
        now = utcnow()
        async with self._session_factory() as session:
            scopes = await scheduler_repository.list_queued_scopes(session, now)

        saw_full = False
        saw_processed = False
        saw_quota = False
        saw_budget = False
        saw_paused = False
        for endpoint_id, quota_group_id, project_id, _oldest in scopes:
            outcome = await self._claim_scope(
                worker_id, endpoint_id, quota_group_id, project_id
            )
            if isinstance(outcome, ClaimedWork):
                return outcome
            if outcome == "full":
                saw_full = True
            elif outcome == "processed":
                saw_processed = True
            elif outcome == "quota":
                saw_quota = True
            elif outcome == "budget":
                saw_budget = True
            elif outcome == "paused":
                saw_paused = True

        if saw_processed:
            return "processed"
        if saw_quota:
            return "quota"
        if saw_budget:
            return "budget"
        if saw_full:
            return "full"
        if saw_paused:
            return "paused"
        return None

    async def _claim_scope(
        self,
        worker_id: str,
        endpoint_id: str | None,
        quota_group_id: str | None,
        project_id: str | None,
    ) -> ClaimedWork | str | None:
        """Claim and reserve the oldest eligible request for one scheduling scope.

        Admission evaluates budget (locking the active price policy and project
        budget windows) before provider quota (locking the quota group/limit/
        window rows) and endpoint physical capacity; only when every required
        admission resource is known to fit are quota reservations, the endpoint
        reservation, the attempt, and the request's reserved state mutated and
        committed together. A request that cannot acquire every resource acquires
        none of them (all-or-nothing).
        """
        now = utcnow()
        lease_expires = now + timedelta(seconds=self._settings.worker_lease_seconds)
        fencing_token = uuid.uuid4().int & ((1 << 53) - 1)

        async with self._session_factory() as session:
            async with session.begin():
                request = await scheduler_repository.claim_next_queued_for_scope(
                    session, now, endpoint_id, quota_group_id, project_id
                )
                if request is None:
                    return None
                request_id = request.id
                model_alias_id = ModelAliasId(request.model_alias_id)

                try:
                    await identity_service.authorize_for_dispatch(
                        session,
                        project_id=(
                            ProjectId(request.project_id) if request.project_id else None
                        ),
                        principal_id=(
                            PrincipalId(request.principal_id)
                            if request.principal_id
                            else None
                        ),
                        api_credential_id=(
                            ApiCredentialId(request.api_credential_id)
                            if request.api_credential_id
                            else None
                        ),
                    )
                except AuthenticationRequired:
                    # Revocation/expiry/inactivation applied while queued must
                    # block dispatch without contacting upstream or reserving
                    # any quota/budget/endpoint capacity.
                    await scheduler_repository.fail_request_direct(
                        session,
                        request_id=request_id,
                        state=RequestState.FAILED,
                        error_code="authorization_failed",
                        finished_at=now,
                    )
                    logger.info("request=%s failed authorization revalidation", request_id)
                    return "processed"

                try:
                    resolved = await resolve_model_alias_by_id(session, model_alias_id)
                    self._inference.validate_destination(resolved)
                except DomainError as exc:
                    await scheduler_repository.fail_request_direct(
                        session,
                        request_id=request_id,
                        state=RequestState.FAILED,
                        error_code=_sanitize_error_code(_error_hint(exc)),
                        finished_at=now,
                    )
                    logger.info("request=%s failed revalidation: %s", request_id, _error_hint(exc))
                    return "processed"

                resolved_endpoint_id = resolved.endpoint.id
                messages, params = deserialize_chat_payload(
                    self._encryptor.decrypt(request.payload_encrypted)
                )
                effective_group_id = (
                    str(resolved.route_binding.quota_group_id)
                    if resolved.route_binding.quota_group_id is not None
                    else None
                )

                budget_eval = await self._evaluate_budget(
                    session,
                    resolved=resolved,
                    project_id=request.project_id,
                    messages=messages,
                    params=params,
                    now=now,
                )
                if budget_eval.outcome == "too_large":
                    await scheduler_repository.fail_request_direct(
                        session,
                        request_id=request_id,
                        state=RequestState.FAILED,
                        error_code="budget_request_too_large",
                        finished_at=now,
                    )
                    return "processed"
                if budget_eval.outcome == "unbounded_output":
                    await scheduler_repository.fail_request_direct(
                        session,
                        request_id=request_id,
                        state=RequestState.FAILED,
                        error_code="budget_unbounded_output",
                        finished_at=now,
                    )
                    return "processed"
                if budget_eval.outcome == "estimator_unavailable":
                    await scheduler_repository.fail_request_direct(
                        session,
                        request_id=request_id,
                        state=RequestState.FAILED,
                        error_code="budget_token_estimator_unavailable",
                        finished_at=now,
                    )
                    return "processed"
                if budget_eval.outcome == "exhausted":
                    assert budget_eval.blocking_policy is not None
                    await scheduler_repository.set_request_wait_metadata(
                        session,
                        request_id=request_id,
                        wait_reason="budget_window_exhausted",
                        wait_limit_id=budget_eval.blocking_policy.id,
                        wait_limit_metric="budget",
                        next_eligible_at=budget_eval.next_eligible_at,
                    )
                    return "budget"

                quota_eval: _QuotaEvaluation | None = None
                if effective_group_id is not None:
                    quota_eval = await self._evaluate_quota(
                        session,
                        resolved=resolved,
                        quota_group_id=effective_group_id,
                        messages=messages,
                        params=params,
                        now=now,
                    )
                    if quota_eval.outcome == "too_large":
                        await scheduler_repository.fail_request_direct(
                            session,
                            request_id=request_id,
                            state=RequestState.FAILED,
                            error_code="quota_request_too_large",
                            finished_at=now,
                        )
                        return "processed"
                    if quota_eval.outcome == "unbounded_output":
                        await scheduler_repository.fail_request_direct(
                            session,
                            request_id=request_id,
                            state=RequestState.FAILED,
                            error_code="quota_unbounded_output",
                            finished_at=now,
                        )
                        return "processed"
                    if quota_eval.outcome == "estimator_unavailable":
                        await scheduler_repository.fail_request_direct(
                            session,
                            request_id=request_id,
                            state=RequestState.FAILED,
                            error_code="quota_token_estimator_unavailable",
                            finished_at=now,
                        )
                        return "processed"
                    if quota_eval.outcome == "cooldown":
                        await scheduler_repository.set_request_wait_metadata(
                            session,
                            request_id=request_id,
                            wait_reason="quota_group_cooldown",
                            next_eligible_at=quota_eval.cooldown_until,
                        )
                        return "quota"
                    if quota_eval.outcome == "exhausted":
                        assert quota_eval.plan is not None
                        assert quota_eval.plan.blocking_limit is not None
                        await scheduler_repository.set_request_wait_metadata(
                            session,
                            request_id=request_id,
                            wait_reason="quota_window_exhausted",
                            wait_limit_id=quota_eval.plan.blocking_limit.id,
                            wait_limit_metric=quota_eval.plan.blocking_limit.metric,
                            next_eligible_at=quota_eval.plan.next_eligible_at,
                        )
                        return "quota"

                endpoint = await scheduler_repository.lock_endpoint(session, resolved_endpoint_id)
                if endpoint is None or not endpoint.is_active:
                    await scheduler_repository.fail_request_direct(
                        session,
                        request_id=request_id,
                        state=RequestState.FAILED,
                        error_code="endpoint_inactive",
                        finished_at=now,
                    )
                    return "processed"

                if endpoint.operational_state == EndpointOperationalState.PAUSED.value:
                    await scheduler_repository.set_request_wait_metadata(
                        session, request_id=request_id, wait_reason="endpoint_paused"
                    )
                    return "paused"

                active = await scheduler_repository.count_active_reservations(
                    session, resolved_endpoint_id
                )

                if endpoint.operational_state == EndpointOperationalState.DRAINING.value:
                    # Draining admits no new reservations: requests wait until the
                    # endpoint drains to zero occupied slots, then fail cleanly so
                    # operators can confirm drain completion.
                    if active > 0:
                        await scheduler_repository.set_request_wait_metadata(
                            session, request_id=request_id, wait_reason="endpoint_draining"
                        )
                        return "full"
                    await scheduler_repository.fail_request_direct(
                        session,
                        request_id=request_id,
                        state=RequestState.FAILED,
                        error_code="endpoint_draining",
                        finished_at=now,
                    )
                    return "processed"

                if active >= endpoint.max_concurrency:
                    await scheduler_repository.set_request_wait_metadata(
                        session, request_id=request_id, wait_reason="endpoint_full"
                    )
                    return "full"

                # Every required resource fits: mutate quota + endpoint + attempt
                # + request reserved state together (committed below).
                if quota_eval is not None and quota_eval.plan is not None:
                    await scheduler_repository.reserve_quota(
                        session, request_id=request_id, plan=quota_eval.plan
                    )

                price_snapshot_id: str | None = None
                if budget_eval.outcome == "ok" and budget_eval.price_policy is not None:
                    price_snapshot_id = _new_id()
                    price = budget_eval.price_policy
                    await accounting_repository.create_price_snapshot(
                        session,
                        snapshot_id=price_snapshot_id,
                        source_price_policy_id=price.id,
                        route_binding_id=str(resolved.route_binding.id),
                        provider_account_id=str(resolved.provider_account.id),
                        model_alias_id=str(model_alias_id),
                        billing_unit=price.billing_unit,
                        currency=price.currency,
                        unit_scale=price.unit_scale,
                        request_price=price.request_price,
                        input_price=price.input_price,
                        output_price=price.output_price,
                        captured_at=now,
                    )
                    request.price_snapshot_id = price_snapshot_id
                    for policy in budget_eval.policies:
                        await accounting_repository.reserve_budget(
                            session,
                            reservation_id=_new_id(),
                            request_id=request_id,
                            budget_policy_id=policy.id,
                            price_snapshot_id=price_snapshot_id,
                            window_start=budget_eval.windows[policy.id].window_start,
                            amount=budget_eval.required_amount,
                        )

                reservation = await scheduler_repository.create_reservation(
                    session,
                    reservation_id=ReservationId(_new_id()),
                    request_id=request_id,
                    endpoint_id=resolved_endpoint_id,
                    acquired_at=now,
                )
                attempt = await scheduler_repository.create_attempt(
                    session,
                    attempt_id=ExecutionAttemptId(_new_id()),
                    request_id=request_id,
                    endpoint_id=resolved_endpoint_id,
                    reservation_id=reservation.id,
                    fencing_token=fencing_token,
                    worker_id=worker_id,
                    lease_expires_at=lease_expires,
                    started_at=now,
                )
                request.state = RequestState.RESERVED
                request.worker_id = worker_id
                request.fencing_token = fencing_token
                request.lease_expires_at = lease_expires
                request.started_at = now
                await scheduler_repository.clear_request_wait_metadata(
                    session, request_id=request_id
                )

                attempt_id = attempt.id
                reservation_id = reservation.id
                public_alias = resolved.alias.name
                stream = request.stream

            async with session.begin():
                dispatch = await scheduler_repository.mark_dispatched(
                    session,
                    request_id=request_id,
                    attempt_id=attempt_id,
                    fencing_token=fencing_token,
                    now=now,
                )
                if dispatch == "expired":
                    await scheduler_repository.release_reservation(
                        session, reservation_id, now
                    )
                    if effective_group_id is not None:
                        await scheduler_repository.release_quota_reservations(
                            session, request_id=request_id, now=now
                        )
                    await _release_pre_dispatch_accounting(
                        session, request_id=request_id, snapshot_id=price_snapshot_id, now=now
                    )
                    return None
                if dispatch == "not_reserved":
                    if effective_group_id is not None:
                        await scheduler_repository.release_quota_reservations(
                            session, request_id=request_id, now=now
                        )
                    await _release_pre_dispatch_accounting(
                        session, request_id=request_id, snapshot_id=price_snapshot_id, now=now
                    )
                    return None
                if effective_group_id is not None:
                    await scheduler_repository.commit_request_quota(
                        session, request_id=request_id, now=now
                    )

        async with self._session_factory() as session:
            prepared = await self._inference.prepare_from_resolved(
                session, resolved, messages, params
            )

        return ClaimedWork(
            request_id=request_id,
            attempt_id=attempt_id,
            reservation_id=reservation_id,
            worker_id=worker_id,
            fencing_token=fencing_token,
            endpoint_id=resolved_endpoint_id,
            public_alias=public_alias,
            stream=stream,
            prepared=prepared,
            quota_group_id=effective_group_id,
        )

    async def _evaluate_quota(
        self,
        session: AsyncSession,
        *,
        resolved: ResolvedRoute,
        quota_group_id: str,
        messages: list[Message],
        params: GenerationParams,
        now: datetime,
    ) -> _QuotaEvaluation:
        """Evaluate (lock, never mutate) a request's shared-quota constraints.

        Returns an outcome describing why the request cannot proceed, or ``ok``
        with a plan whose windows are locked and ready to reserve in the same
        transaction once every other admission resource is known to fit.
        """
        group = await scheduler_repository.get_quota_group_for_update(
            session, group_id=quota_group_id
        )
        if group is None:
            return _QuotaEvaluation(outcome="too_large")
        if group.cooldown_until is not None and group.cooldown_until > now:
            return _QuotaEvaluation(outcome="cooldown", cooldown_until=group.cooldown_until)

        limits = await scheduler_repository.list_quota_limits_for_group(
            session, group_id=quota_group_id
        )
        if not limits:
            return _QuotaEvaluation(outcome="ok")

        has_token = any(limit.metric == QuotaMetric.TOKENS for limit in limits)
        requested: dict[str, int] = {}
        token_units = 0
        if has_token:
            input_estimate = self._inference.estimate_tokens(resolved, messages, params)
            if input_estimate is None:
                return _QuotaEvaluation(outcome="estimator_unavailable")
            output_bound = params.max_tokens
            if output_bound is None:
                output_bound = resolved.route_binding.default_output_tokens
            if output_bound is None:
                return _QuotaEvaluation(outcome="unbounded_output")
            token_units = input_estimate + output_bound
        for limit in limits:
            if limit.metric == QuotaMetric.REQUESTS:
                requested[limit.id] = 1
            elif limit.metric == QuotaMetric.TOKENS:
                requested[limit.id] = token_units
                if token_units > limit.limit_units:
                    return _QuotaEvaluation(outcome="too_large")

        plan = await scheduler_repository.plan_quota_reservation(
            session, limits=limits, requested_by_limit=requested, now=now
        )
        if plan.blocking_limit is not None:
            return _QuotaEvaluation(outcome="exhausted", plan=plan)
        return _QuotaEvaluation(outcome="ok", plan=plan)

    async def _evaluate_budget(
        self,
        session: AsyncSession,
        *,
        resolved: ResolvedRoute,
        project_id: str | None,
        messages: list[Message],
        params: GenerationParams,
        now: datetime,
    ) -> _BudgetEvaluation:
        """Evaluate (lock, never mutate) a request's monetary budget constraints.

        Returns ``none`` when there is no applicable pricing or budget; ``ok`` with
        a locked price policy and locked budget windows otherwise. Token-priced
        routes with an active budget fail closed when the token estimator is
        unavailable or no output bound exists, because a monetary reservation
        cannot be computed without them.
        """
        if project_id is None:
            return _BudgetEvaluation(outcome="none")

        route_binding_id = str(resolved.route_binding.id)
        price_policy = await accounting_repository.get_active_price_policy_for_route(
            session, route_binding_id=route_binding_id
        )
        if price_policy is None:
            return _BudgetEvaluation(outcome="none")

        policies = [
            p
            for p in await accounting_repository.list_enabled_budget_policies_for_project(
                session, project_id=project_id
            )
            if p.currency == price_policy.currency
        ]
        if not policies:
            return _BudgetEvaluation(outcome="ok", price_policy=price_policy)

        input_units: int | None = None
        output_units: int | None = None
        if price_policy.billing_unit == BillingUnit.TOKEN.value:
            input_estimate = self._inference.estimate_tokens(resolved, messages, params)
            if input_estimate is None:
                return _BudgetEvaluation(
                    outcome="estimator_unavailable", price_policy=price_policy
                )
            output_bound = params.max_tokens
            if output_bound is None:
                output_bound = resolved.route_binding.default_output_tokens
            if output_bound is None:
                return _BudgetEvaluation(
                    outcome="unbounded_output", price_policy=price_policy
                )
            input_units = input_estimate
            output_units = output_bound
        required = accounting_service.reservation_amount(
            price_policy, input_units=input_units or 0, output_units=output_units or 0
        )

        windows: dict[str, models.BudgetWindow] = {}
        blocking_policy: models.ProjectBudgetPolicy | None = None
        next_eligible: datetime | None = None
        for policy in policies:
            if required > policy.limit_amount:
                return _BudgetEvaluation(
                    outcome="too_large",
                    price_policy=price_policy,
                    policies=policies,
                    required_amount=required,
                    input_units=input_units,
                    output_units=output_units,
                    blocking_policy=policy,
                )
            window_start = scheduler_repository.fixed_window_start(
                now, policy.window_seconds
            )
            window = await accounting_repository.get_or_create_budget_window(
                session, budget_policy_id=policy.id, window_start=window_start
            )
            windows[policy.id] = window
            if (
                window.committed_amount
                + window.reserved_amount
                + required
                > policy.limit_amount
            ):
                if blocking_policy is None:
                    blocking_policy = policy
                reset = window_start + timedelta(seconds=policy.window_seconds)
                if next_eligible is None or reset > next_eligible:
                    next_eligible = reset

        if blocking_policy is not None:
            return _BudgetEvaluation(
                outcome="exhausted",
                price_policy=price_policy,
                policies=policies,
                windows=windows,
                required_amount=required,
                input_units=input_units,
                output_units=output_units,
                blocking_policy=blocking_policy,
                next_eligible_at=next_eligible,
            )
        return _BudgetEvaluation(
            outcome="ok",
            price_policy=price_policy,
            policies=policies,
            windows=windows,
            required_amount=required,
            input_units=input_units,
            output_units=output_units,
        )

    async def _apply_cooldown(self, claim: ClaimedWork, exc: ProviderError) -> None:
        """Apply a shared-quota-group cooldown from a provider 429 response."""
        if claim.quota_group_id is None:
            return
        retry = exc.retry_after_seconds
        if retry is None:
            retry = self._settings.provider_429_cooldown_seconds
        until = utcnow() + timedelta(seconds=retry)
        async with self._session_factory() as session:
            async with session.begin():
                await scheduler_repository.set_quota_group_cooldown(
                    session, group_id=claim.quota_group_id, cooldown_until=until
                )

    async def run_complete(self, claim: ClaimedWork) -> None:
        """Dispatch a non-streaming completion and settle, renewing its lease."""
        heartbeat = asyncio.create_task(self._heartbeat_loop(claim))
        try:
            result = await claim.prepared.adapter.complete(
                claim.prepared.request, claim.prepared.secret
            )
            if await self.is_cancelled(claim.request_id):
                await self._settle(claim, state=RequestState.CANCELLED)
                return
            result_encrypted = self._encryptor.encrypt(
                serialize_result(
                    result.content,
                    result.finish_reason,
                    result.usage,
                    result.upstream_request_id,
                )
            )
            await self._settle(
                claim,
                state=RequestState.SUCCEEDED,
                result_encrypted=result_encrypted,
                upstream_request_id=result.upstream_request_id,
                usage=result.usage,
            )
        except (ProviderError, UnsupportedProvider, SecretResolutionError) as exc:
            if isinstance(exc, ProviderError):
                await self._apply_cooldown(claim, exc)
            await self._settle(
                claim, state=RequestState.FAILED, error_code=_sanitize_error_code(_error_hint(exc))
            )
        finally:
            heartbeat.cancel()
            try:
                await heartbeat
            except asyncio.CancelledError:
                pass

    async def run_stream(self, claim: ClaimedWork) -> None:
        """Dispatch a streaming completion, persisting encrypted events, then settle."""
        seq = 0
        terminal_state = RequestState.SUCCEEDED
        usage: Usage | None = None
        heartbeat = asyncio.create_task(self._heartbeat_loop(claim))
        try:
            async for chunk in claim.prepared.adapter.stream(
                claim.prepared.request, claim.prepared.secret
            ):
                if await self.is_cancelled(claim.request_id):
                    terminal_state = RequestState.CANCELLED
                    break
                if chunk.usage is not None and chunk.usage.total_tokens > 0:
                    usage = chunk.usage
                seq += 1
                event = serialize_event(chunk.content, chunk.finish_reason, chunk.usage)
                await self._append_event(claim.request_id, seq, event)
            if terminal_state == RequestState.SUCCEEDED and await self.is_cancelled(
                claim.request_id
            ):
                terminal_state = RequestState.CANCELLED
            await self._settle(
                claim, state=terminal_state, result_encrypted=None, usage=usage
            )
        except (ProviderError, UnsupportedProvider, SecretResolutionError) as exc:
            if isinstance(exc, ProviderError):
                await self._apply_cooldown(claim, exc)
            await self._settle(
                claim, state=RequestState.FAILED, error_code=_sanitize_error_code(_error_hint(exc))
            )
        finally:
            heartbeat.cancel()
            try:
                await heartbeat
            except asyncio.CancelledError:
                pass

    async def _heartbeat_loop(self, claim: ClaimedWork) -> None:
        """Renew ownership for in-flight work until execution terminates."""
        interval = self._settings.worker_heartbeat_seconds
        while True:
            await asyncio.sleep(interval)
            renewed = await self._renew_lease(claim)
            if not renewed:
                return

    async def _renew_lease(self, claim: ClaimedWork) -> bool:
        now = utcnow()
        lease_expires = now + timedelta(seconds=self._settings.worker_lease_seconds)
        async with self._session_factory() as session:
            async with session.begin():
                try:
                    return await scheduler_repository.renew_lease(
                        session,
                        request_id=claim.request_id,
                        attempt_id=claim.attempt_id,
                        worker_id=claim.worker_id,
                        fencing_token=claim.fencing_token,
                        lease_expires_at=lease_expires,
                        now=now,
                    )
                except SchedulerInvariantError:
                    logger.exception(
                        "lease renewal invariant broken for request=%s",
                        claim.request_id,
                    )
                    raise

    async def _append_event(self, request_id: str, seq: int, event: bytes) -> None:
        encrypted = self._encryptor.encrypt(event)
        async with self._session_factory() as session:
            async with session.begin():
                await scheduler_repository.append_stream_event(
                    session, request_id=request_id, seq=seq, event_encrypted=encrypted
                )

    async def _settle(
        self,
        claim: ClaimedWork,
        *,
        state: str,
        result_encrypted: bytes | None = None,
        error_code: str | None = None,
        upstream_request_id: str | None = None,
        usage: Usage | None = None,
    ) -> None:
        """Settle to a terminal state, overriding to ``expired`` past lifetime.

        Once the request's total lifetime has expired, the durable state is
        ``expired`` (with ``lifetime_exceeded``) regardless of the worker's local
        outcome, so a result can never be persisted for a request the client has
        already abandoned.
        """
        now = utcnow()
        async with self._session_factory() as session:
            async with session.begin():
                row = await scheduler_repository.get_request(session, claim.request_id)
                if row is not None and row.expires_at <= now:
                    state = RequestState.EXPIRED
                    result_encrypted = None
                    upstream_request_id = None
                    error_code = "lifetime_exceeded"
                    usage = None
                usage_total = (
                    usage.total_tokens
                    if usage is not None and usage.total_tokens > 0
                    else None
                )
                settled = await scheduler_repository.settle(
                    session,
                    request_id=claim.request_id,
                    attempt_id=claim.attempt_id,
                    reservation_id=claim.reservation_id,
                    fencing_token=claim.fencing_token,
                    state=state,
                    finished_at=now,
                    result_encrypted=result_encrypted,
                    error_code=error_code,
                    upstream_request_id=upstream_request_id,
                )
                if settled and claim.quota_group_id is not None:
                    await scheduler_repository.settle_token_quota(
                        session,
                        request_id=claim.request_id,
                        actual_tokens=usage_total,
                        now=now,
                    )
                if settled:
                    await self._settle_accounting(
                        session,
                        claim=claim,
                        state=state,
                        usage=usage,
                        upstream_request_id=upstream_request_id,
                        now=now,
                    )

    async def _settle_accounting(
        self,
        session: AsyncSession,
        *,
        claim: ClaimedWork,
        state: str,
        usage: Usage | None,
        upstream_request_id: str | None,
        now: datetime,
    ) -> None:
        """Settle monetary accounting (usage, ledger, budget) after a terminal state.

        A captured price snapshot is required; without one the request carried no
        pricing and there is nothing to record. A request without a project has no
        billable attribution, so no usage/ledger/budget settlement applies.
        """
        request = await scheduler_repository.get_request(session, claim.request_id)
        snapshot_id = request.price_snapshot_id if request is not None else None
        if snapshot_id is None or request is None or request.project_id is None:
            return

        snapshot = await accounting_repository.get_price_snapshot_for_update(
            session, snapshot_id=snapshot_id
        )
        if snapshot is None:
            return

        if state == RequestState.SUCCEEDED:
            if snapshot.billing_unit == BillingUnit.REQUEST.value:
                amount = accounting_service.request_reservation_amount(snapshot)
                input_units = 0
                output_units = 0
                request_units = 1
            else:
                if usage is None or (usage.prompt_tokens + usage.completion_tokens) <= 0:
                    await accounting_repository.commit_budget_reservations_conservative(
                        session,
                        request_id=claim.request_id,
                        now=now,
                        reason="unknown_usage",
                    )
                    return
                amount = accounting_service.token_amount(
                    snapshot,
                    input_units=usage.prompt_tokens,
                    output_units=usage.completion_tokens,
                )
                input_units = usage.prompt_tokens
                output_units = usage.completion_tokens
                request_units = None

            usage_record = await accounting_repository.create_usage_record(
                session,
                request_id=claim.request_id,
                execution_attempt_id=claim.attempt_id,
                project_id=request.project_id,
                principal_id=request.principal_id,
                api_credential_id=request.api_credential_id,
                model_alias_id=snapshot.model_alias_id,
                route_binding_id=snapshot.route_binding_id,
                provider_account_id=snapshot.provider_account_id,
                price_snapshot_id=snapshot.id,
                billing_unit=snapshot.billing_unit,
                input_units=input_units,
                output_units=output_units,
                request_units=request_units,
                amount=amount,
                currency=snapshot.currency,
                recorded_at=now,
                upstream_request_id=upstream_request_id,
            )
            await accounting_repository.create_ledger_entry(
                session,
                project_id=request.project_id,
                usage_record_id=usage_record.id,
                entry_type="usage_debit",
                amount=amount,
                currency=snapshot.currency,
                created_at=now,
                idempotency_key=f"usage:{usage_record.id}",
            )
            await accounting_repository.settle_budget_reservations_to_actual(
                session, request_id=claim.request_id, actual_amount=amount, now=now
            )
        else:
            await accounting_repository.commit_budget_reservations_conservative(
                session, request_id=claim.request_id, now=now, reason=state
            )

    async def _enqueue(
        self,
        session: AsyncSession,
        *,
        resolved: ResolvedRoute,
        messages: list[Message],
        params: GenerationParams,
        stream: bool,
        project_id: ProjectId,
        principal_id: PrincipalId,
        api_credential_id: ApiCredentialId,
    ) -> tuple[str, datetime, datetime]:
        # Serialize count-then-insert so concurrent admissions cannot exceed the
        # configured queue bound.
        await scheduler_repository.acquire_admission_lock(session)
        queued = await scheduler_repository.count_queued(session)
        if queued >= self._settings.queue_max_requests:
            raise QueueFull("the inference queue is at capacity")

        payload = self._encryptor.encrypt(serialize_chat_payload(messages, params))
        now = utcnow()
        request_id = RequestId(_new_id())
        queue_wait_until = now + timedelta(seconds=self._settings.queue_max_wait_seconds)
        expires_at = now + timedelta(seconds=self._settings.queue_total_lifetime_seconds)
        quota_group_id = (
            str(resolved.route_binding.quota_group_id)
            if resolved.route_binding.quota_group_id is not None
            else None
        )
        await scheduler_repository.create_request(
            session,
            request_id=request_id,
            project_id=project_id,
            principal_id=principal_id,
            api_credential_id=api_credential_id,
            model_alias_id=resolved.alias.id,
            endpoint_id=resolved.endpoint.id,
            quota_group_id=quota_group_id,
            stream=stream,
            payload_encrypted=payload,
            queued_at=now,
            queue_wait_until=queue_wait_until,
            expires_at=expires_at,
        )
        return str(request_id), queue_wait_until, expires_at
