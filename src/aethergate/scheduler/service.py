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
from aethergate.domain.enums import RequestState
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
    fencing_token: int
    endpoint_id: str
    public_alias: str
    stream: bool
    prepared: PreparedDispatch


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
            request_id = await self._enqueue(
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
        )

    async def wait_for_terminal(
        self, request_id: str, deadline: datetime
    ) -> TerminalResult:
        """Poll until the request reaches a terminal state or the deadline.

        Returns a decrypted, ready-to-serve terminal result.
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
        return TerminalResult(RequestState.EXPIRED, None, None, None, None, "timeout")

    def terminal_deadline(self) -> datetime:
        """Return the absolute deadline for a request to reach a terminal state."""
        return utcnow() + timedelta(seconds=self._settings.queue_total_lifetime_seconds)

    @property
    def poll_interval(self) -> float:
        return self._settings.scheduler_poll_interval_seconds

    async def request_cancellation(self, request_id: str) -> None:
        async with self._session_factory() as session:
            async with session.begin():
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
        """Claim one eligible request, reserve capacity, and record dispatch intent.

        Returns a :class:`ClaimedWork`, the string ``"full"`` when capacity is
        exhausted, or ``None`` when nothing is eligible.
        """
        now = utcnow()
        lease_expires = now + timedelta(seconds=self._settings.worker_lease_seconds)
        fencing_token = uuid.uuid4().int & ((1 << 53) - 1)

        async with self._session_factory() as session:
            async with session.begin():
                request = await scheduler_repository.claim_next_queued(session, now)
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

                endpoint_id = resolved.endpoint.id
                endpoint = await scheduler_repository.lock_endpoint(session, endpoint_id)
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
                    session, endpoint_id
                )
                if active >= endpoint.max_concurrency:
                    return "full"

                reservation = await scheduler_repository.create_reservation(
                    session,
                    reservation_id=ReservationId(_new_id()),
                    request_id=request_id,
                    endpoint_id=endpoint_id,
                    acquired_at=now,
                )
                attempt = await scheduler_repository.create_attempt(
                    session,
                    attempt_id=ExecutionAttemptId(_new_id()),
                    request_id=request_id,
                    endpoint_id=endpoint_id,
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
                dispatched = await scheduler_repository.mark_request_dispatched(
                    session, request_id=request_id, fencing_token=fencing_token
                )
                if not dispatched:
                    return None
                await scheduler_repository.mark_attempt_dispatched(
                    session, attempt_id=attempt_id, fencing_token=fencing_token
                )

        async with self._session_factory() as session:
            row = await scheduler_repository.get_request(session, request_id)
            messages, params = deserialize_chat_payload(
                self._encryptor.decrypt(row.payload_encrypted)
            )
            prepared = await self._inference.prepare_from_resolved(
                session, resolved, messages, params
            )

        return ClaimedWork(
            request_id=request_id,
            attempt_id=attempt_id,
            reservation_id=reservation_id,
            fencing_token=fencing_token,
            endpoint_id=endpoint_id,
            public_alias=public_alias,
            stream=stream,
            prepared=prepared,
        )

    async def run_complete(self, claim: ClaimedWork) -> None:
        """Dispatch a non-streaming completion and settle."""
        try:
            result = await claim.prepared.adapter.complete(
                claim.prepared.request, claim.prepared.secret
            )
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
            )
        except (ProviderError, UnsupportedProvider, SecretResolutionError) as exc:
            await self._settle(
                claim, state=RequestState.FAILED, error_code=_sanitize_error_code(_error_hint(exc))
            )

    async def run_stream(self, claim: ClaimedWork) -> None:
        """Dispatch a streaming completion, persisting encrypted events, then settle."""
        seq = 0
        terminal_state = RequestState.SUCCEEDED
        try:
            async for chunk in claim.prepared.adapter.stream(
                claim.prepared.request, claim.prepared.secret
            ):
                if await self.is_cancelled(claim.request_id):
                    terminal_state = RequestState.CANCELLED
                    break
                seq += 1
                event = serialize_event(chunk.content, chunk.finish_reason, chunk.usage)
                await self._append_event(claim.request_id, seq, event)
            if terminal_state == RequestState.SUCCEEDED and await self.is_cancelled(
                claim.request_id
            ):
                terminal_state = RequestState.CANCELLED
            await self._settle(claim, state=terminal_state, result_encrypted=None)
        except (ProviderError, UnsupportedProvider, SecretResolutionError) as exc:
            await self._settle(
                claim, state=RequestState.FAILED, error_code=_sanitize_error_code(_error_hint(exc))
            )

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
    ) -> None:
        now = utcnow()
        async with self._session_factory() as session:
            async with session.begin():
                ok = await scheduler_repository.settle_request(
                    session,
                    request_id=claim.request_id,
                    fencing_token=claim.fencing_token,
                    state=state,
                    result_encrypted=result_encrypted,
                    error_code=error_code,
                    finished_at=now,
                )
                if not ok:
                    return
                await scheduler_repository.settle_attempt(
                    session,
                    attempt_id=claim.attempt_id,
                    fencing_token=claim.fencing_token,
                    state=state,
                    finished_at=now,
                    upstream_request_id=upstream_request_id,
                    error_code=error_code,
                )
                await scheduler_repository.release_reservation(
                    session, claim.reservation_id, now
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
    ) -> str:
        queued = await scheduler_repository.count_queued(session)
        if queued >= self._settings.queue_max_requests:
            raise QueueFull("the inference queue is at capacity")

        payload = self._encryptor.encrypt(serialize_chat_payload(messages, params))
        now = utcnow()
        request_id = RequestId(_new_id())
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
            queue_wait_until=now + timedelta(seconds=self._settings.queue_max_wait_seconds),
            expires_at=now + timedelta(seconds=self._settings.queue_total_lifetime_seconds),
        )
        return str(request_id)
