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
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from aethergate.adapters.base import GenerationParams, Message, Usage
from aethergate.catalog.service import (
    ResolvedRoute,
    resolve_model_alias,
    resolve_model_alias_by_id,
)
from aethergate.config import Settings
from aethergate.domain.enums import QuotaMetric, RequestState
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
from aethergate.inference.service import InferenceService, PreparedDispatch
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
        context: tuple[ProjectId, PrincipalId, ApiCredentialId],
    ) -> EnqueuedRequest:
        """Validate/resolve, then enqueue an encrypted request durably."""
        project_id, principal_id, api_credential_id = context
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
                row = await scheduler_repository.get_request_for_update(session, request_id)
                if row is None or row.state in TERMINAL_STATES:
                    return
                if row.state == RequestState.QUEUED:
                    row.state = RequestState.CANCELLED
                    row.cancellation_requested = True
                    row.finished_at = now
                elif row.state == RequestState.RESERVED:
                    row.state = RequestState.CANCELLED
                    row.cancellation_requested = True
                    row.finished_at = now
                    await scheduler_repository.release_active_reservation(session, request_id, now)
                    await scheduler_repository.abandon_reserved_attempts(session, request_id, now)
                    await scheduler_repository.release_quota_reservations(
                        session, request_id=request_id, now=now
                    )
                else:
                    await scheduler_repository.set_cancellation_requested(session, request_id)

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
        if disposition not in RECONCILABLE_STATES:
            raise ValueError(
                f"disposition must be one of {sorted(RECONCILABLE_STATES)}"
            )
        now = utcnow()
        async with self._session_factory() as session:
            async with session.begin():
                return await scheduler_repository.reconcile_request(
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
                unknown = await scheduler_repository.mark_outcome_unknown_expired(session, now)
        if expired or reclaimed or unknown:
            logger.info(
                "scheduler recovery: expired=%d reclaimed=%d outcome_unknown=%d",
                expired,
                len(reclaimed),
                unknown,
            )

    async def claim_and_reserve(self, worker_id: str) -> ClaimedWork | str | None:
        """Claim one eligible request (FIFO per endpoint) and reserve capacity.

        Iterates over endpoints that hold eligible queued work, oldest first. A
        saturated endpoint is skipped so it never blocks an unrelated endpoint
        with available capacity. Returns :class:`ClaimedWork`, ``"full"`` when
        every candidate endpoint is saturated, ``"processed"`` when a request
        was resolved to a failure, or ``None`` when nothing is eligible.
        """
        now = utcnow()
        async with self._session_factory() as session:
            endpoints = await scheduler_repository.list_queued_endpoints(session, now)

        saw_full = False
        saw_processed = False
        saw_quota = False
        for endpoint_id, _oldest in endpoints:
            outcome = await self._claim_endpoint(worker_id, endpoint_id)
            if isinstance(outcome, ClaimedWork):
                return outcome
            if outcome == "full":
                saw_full = True
            elif outcome == "processed":
                saw_processed = True
            elif outcome == "quota":
                saw_quota = True

        if saw_processed:
            return "processed"
        if saw_quota:
            return "quota"
        if saw_full:
            return "full"
        return None

    async def _claim_endpoint(
        self, worker_id: str, endpoint_id: str | None
    ) -> ClaimedWork | str | None:
        """Claim and reserve the oldest eligible request for one endpoint.

        Reservation now includes shared quota capacity, reserved in the same
        transaction as physical endpoint capacity. Quota is checked before the
        endpoint slot is held, so a request never occupies a slot while merely
        waiting on a future quota window.
        """
        now = utcnow()
        lease_expires = now + timedelta(seconds=self._settings.worker_lease_seconds)
        fencing_token = uuid.uuid4().int & ((1 << 53) - 1)

        async with self._session_factory() as session:
            async with session.begin():
                request = await scheduler_repository.claim_next_queued_for_endpoint(
                    session, now, endpoint_id
                )
                if request is None:
                    return None
                request_id = request.id
                model_alias_id = ModelAliasId(request.model_alias_id)

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
                quota_group_id = (
                    str(resolved.route_binding.quota_group_id)
                    if resolved.route_binding.quota_group_id is not None
                    else None
                )

                if quota_group_id is not None:
                    outcome = await self._reserve_quota_capacity(
                        session,
                        request_id=request_id,
                        resolved=resolved,
                        quota_group_id=quota_group_id,
                        messages=messages,
                        params=params,
                        now=now,
                    )
                    if outcome == "too_large":
                        await scheduler_repository.fail_request_direct(
                            session,
                            request_id=request_id,
                            state=RequestState.FAILED,
                            error_code="quota_request_too_large",
                            finished_at=now,
                        )
                        return "processed"
                    if outcome == "unbounded_output":
                        await scheduler_repository.fail_request_direct(
                            session,
                            request_id=request_id,
                            state=RequestState.FAILED,
                            error_code="quota_unbounded_output",
                            finished_at=now,
                        )
                        return "processed"
                    if outcome == "exhausted":
                        request.wait_reason = "quota_window_exhausted"
                        return "quota"
                    if outcome == "cooldown":
                        request.wait_reason = "quota_group_cooldown"
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

                active = await scheduler_repository.count_active_reservations(
                    session, resolved_endpoint_id
                )
                if active >= endpoint.max_concurrency:
                    return "full"

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
                    if quota_group_id is not None:
                        await scheduler_repository.release_quota_reservations(
                            session, request_id=request_id, now=now
                        )
                    return None
                if dispatch == "not_reserved":
                    if quota_group_id is not None:
                        await scheduler_repository.release_quota_reservations(
                            session, request_id=request_id, now=now
                        )
                    return None
                if quota_group_id is not None:
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
            quota_group_id=quota_group_id,
        )

    async def _reserve_quota_capacity(
        self,
        session: AsyncSession,
        *,
        request_id: str,
        resolved: ResolvedRoute,
        quota_group_id: str,
        messages: list[Message],
        params: GenerationParams,
        now: datetime,
    ) -> str:
        """Reserve shared quota for a request, or report why it cannot proceed.

        Returns ``"ok"``, ``"too_large"``, ``"unbounded_output"``,
        ``"exhausted"``, or ``"cooldown"``.
        """
        group = await scheduler_repository.get_quota_group_for_update(
            session, group_id=quota_group_id
        )
        if group is None:
            return "too_large"
        if group.cooldown_until is not None and group.cooldown_until > now:
            return "cooldown"
        limits = await scheduler_repository.list_quota_limits_for_group(
            session, group_id=quota_group_id
        )
        if not limits:
            return "ok"

        has_token = any(limit.metric == QuotaMetric.TOKENS for limit in limits)
        requested: dict[str, int] = {}
        token_units = 0
        if has_token:
            input_estimate = self._inference.estimate_tokens(resolved, messages, params)
            output_bound = params.max_tokens
            if output_bound is None:
                output_bound = resolved.route_binding.default_output_tokens
            if output_bound is None:
                return "unbounded_output"
            token_units = input_estimate + output_bound
        for limit in limits:
            if limit.metric == QuotaMetric.REQUESTS:
                requested[limit.id] = 1
            elif limit.metric == QuotaMetric.TOKENS:
                requested[limit.id] = token_units
                if token_units > limit.limit_units:
                    return "too_large"

        reserved = await scheduler_repository.reserve_quota(
            session,
            request_id=request_id,
            limits=limits,
            requested_by_limit=requested,
            now=now,
        )
        return "ok" if reserved else "exhausted"

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
            usage_total = (
                result.usage.total_tokens
                if result.usage is not None and result.usage.total_tokens > 0
                else None
            )
            await self._settle(
                claim,
                state=RequestState.SUCCEEDED,
                result_encrypted=result_encrypted,
                upstream_request_id=result.upstream_request_id,
                usage_total=usage_total,
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
        usage_total: int | None = None
        heartbeat = asyncio.create_task(self._heartbeat_loop(claim))
        try:
            async for chunk in claim.prepared.adapter.stream(
                claim.prepared.request, claim.prepared.secret
            ):
                if await self.is_cancelled(claim.request_id):
                    terminal_state = RequestState.CANCELLED
                    break
                if chunk.usage is not None and chunk.usage.total_tokens > 0:
                    usage_total = chunk.usage.total_tokens
                seq += 1
                event = serialize_event(chunk.content, chunk.finish_reason, chunk.usage)
                await self._append_event(claim.request_id, seq, event)
            if terminal_state == RequestState.SUCCEEDED and await self.is_cancelled(
                claim.request_id
            ):
                terminal_state = RequestState.CANCELLED
            await self._settle(
                claim, state=terminal_state, result_encrypted=None, usage_total=usage_total
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
        usage_total: int | None = None,
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
                    usage_total = None
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
        await scheduler_repository.create_request(
            session,
            request_id=request_id,
            project_id=project_id,
            principal_id=principal_id,
            api_credential_id=api_credential_id,
            model_alias_id=resolved.alias.id,
            endpoint_id=resolved.endpoint.id,
            stream=stream,
            payload_encrypted=payload,
            queued_at=now,
            queue_wait_until=queue_wait_until,
            expires_at=expires_at,
        )
        return str(request_id), queue_wait_until, expires_at
