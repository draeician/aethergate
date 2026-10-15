"""Deterministic recovery / fault-injection release gates (DB-gated).

These tests prove the durable-state recovery invariants required for a Beta 1
release on top of the lease/fencing semantics already covered in
``tests/test_scheduler_hardening.py``. Each test reconstructs a *fresh*
``SchedulingService`` over the same PostgreSQL database to simulate an API or
worker process restart, proving that durable work, reservations, accounting, and
recovery survive process boundaries.

Provider failure injection uses a deterministic adapter that raises a
:class:`ProviderError` (no hidden adapter/SDK retry exists in the pinned adapter).
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import select, update
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import async_sessionmaker
from starlette.responses import Response

from aethergate.adapters.base import (
    ChatRequest,
    CompletionResult,
    GenerationParams,
    Message,
    Usage,
)
from aethergate.api.openai_chat import _stream_worker_events, chat_completions
from aethergate.config import Settings
from aethergate.contracts.openai import ChatCompletionRequest, ChatMessage
from aethergate.dev_identity import ensure_dev_identity
from aethergate.domain import entities as domain
from aethergate.domain.enums import (
    BillingUnit,
    Capability,
    CredentialAudience,
    CredentialScope,
    RequestState,
)
from aethergate.domain.ids import (
    ApiCredentialId,
    EndpointId,
    ModelAliasId,
    PricePolicyId,
    PrincipalId,
    ProjectId,
    ProviderAccountId,
    ProviderId,
    RouteBindingId,
)
from aethergate.egress import DestinationPolicy
from aethergate.encryption import QueueEncryptor
from aethergate.errors import ProviderError
from aethergate.inference.service import InferenceService
from aethergate.persistence import models, repository
from aethergate.scheduler import repository as sched_repo
from aethergate.scheduler.service import SchedulingService
from aethergate.secrets import EnvSecretResolver
from db_helpers import TEST_DATABASE_URL, reset_schema

pytestmark = pytest.mark.skipif(
    TEST_DATABASE_URL is None, reason="AETHERGATE_TEST_DATABASE_URL not set"
)


class _ProviderFailureAdapter:
    """Deterministic adapter that raises a provider error on every completion."""

    async def complete(self, request: ChatRequest, secret: str | None) -> CompletionResult:
        raise ProviderError("upstream timeout")


class _OkAdapter:
    async def complete(self, request: ChatRequest, secret: str | None) -> CompletionResult:
        return CompletionResult(
            content="ok",
            finish_reason="stop",
            usage=Usage(prompt_tokens=1, completion_tokens=2, total_tokens=3),
            upstream_request_id="upstream-1",
        )


class _TrackingAdapter:
    """Records every dispatch so a DB outage can be proven to dispatch nothing."""

    def __init__(self) -> None:
        self.complete_calls = 0

    async def complete(self, request: ChatRequest, secret: str | None) -> CompletionResult:
        self.complete_calls += 1
        return CompletionResult(
            content="ok",
            finish_reason="stop",
            usage=Usage(prompt_tokens=1, completion_tokens=2, total_tokens=3),
            upstream_request_id="upstream-1",
        )


class _DownSessionFactory:
    """A session factory whose every connection attempt raises ``OperationalError``,
    the SQLAlchemy surface of an unreachable PostgreSQL database. Used to prove the
    scheduler fails closed rather than silently succeeding while the DB is down."""

    def __init__(self) -> None:
        self.opens = 0

    def __call__(self) -> _DownSessionFactory:
        return self

    async def __aenter__(self):
        self.opens += 1
        raise OperationalError("SELECT 1", {}, Exception("connection refused"))

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        return False


async def _seed_endpoint(
    session,
    *,
    endpoint_id: str,
    alias_id: str,
    alias_name: str,
    max_concurrency: int,
) -> None:
    await repository.create_provider(
        session,
        domain.Provider(
            id=ProviderId("prov-ollama"), kind="ollama", name="prov-ollama",
            capabilities=(Capability.TEXT,),
        ),
    )
    await repository.create_provider_account(
        session,
        domain.ProviderAccount(
            id=ProviderAccountId("acct-ollama"),
            provider_id=ProviderId("prov-ollama"),
            name="acct-ollama",
        ),
    )
    await repository.create_endpoint(
        session,
        domain.Endpoint(
            id=EndpointId(endpoint_id),
            provider_account_id=ProviderAccountId("acct-ollama"),
            name=endpoint_id,
            base_destination="http://192.168.22.50:11434",
            max_concurrency=max_concurrency,
        ),
    )
    await repository.create_model_alias(
        session,
        domain.ModelAlias(
            id=ModelAliasId(alias_id), name=alias_name, capabilities=(Capability.TEXT,)
        ),
    )
    await repository.create_route_binding(
        session,
        domain.RouteBinding(
            id=RouteBindingId(f"rb-{alias_id}"),
            model_alias_id=ModelAliasId(alias_id),
            endpoint_id=EndpointId(endpoint_id),
            provider_account_id=ProviderAccountId("acct-ollama"),
            upstream_model="qwen3.8-2b-distill:Q6_K",
        ),
    )


async def _seed_request_price_policy(session, route_binding_id: str) -> None:
    """Seed a request-priced policy so a successful settle would record usage."""
    await repository.create_price_policy(
        session,
        domain.PricePolicy(
            id=PricePolicyId(f"pp-{route_binding_id}"),
            route_binding_id=RouteBindingId(route_binding_id),
            billing_unit=BillingUnit.REQUEST,
            currency="USD",
            unit_scale=1,
            request_price=Decimal("0.000000000123"),
            input_price=None,
            output_price=None,
        ),
    )


async def _build_service(
    engine,
    *,
    adapter,
    max_concurrency: int = 2,
    **overrides,
) -> tuple[SchedulingService, object, QueueEncryptor]:
    """Create a SchedulingService over ``engine`` after seeding a fresh schema."""
    await reset_schema(engine)
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        async with session.begin():
            await _seed_endpoint(
                session,
                endpoint_id="ep-ollama",
                alias_id="alias-gpt4",
                alias_name="gpt-4",
                max_concurrency=max_concurrency,
            )
            await ensure_dev_identity(session)

    encryptor = QueueEncryptor(QueueEncryptor.generate_key())
    settings = Settings(
        _env_file=None,
        database_url="postgresql+asyncpg://u:p@h/db",
        app_env="dev",
        upstream_allowlist="192.168.22.50",
        oidc_enabled=False,
        allow_inference_auth_bypass=False,
        bootstrap_token=None,
        **overrides,
    )
    inference = InferenceService(
        EnvSecretResolver(),
        DestinationPolicy(settings.upstream_allowlist_hosts),
        adapter_factory=lambda kind: adapter,
    )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    service = SchedulingService(
        encryptor=encryptor,
        inference_service=inference,
        session_factory=factory,
        settings=settings,
    )
    return service, factory, encryptor


def _new_service(
    engine, encryptor: QueueEncryptor, settings: Settings, adapter
) -> SchedulingService:
    """A fresh service instance (fresh session factory, same queue key) over the
    same database — the analogue of an API/worker process restart."""
    inference = InferenceService(
        EnvSecretResolver(),
        DestinationPolicy(settings.upstream_allowlist_hosts),
        adapter_factory=lambda kind: adapter,
    )
    return SchedulingService(
        encryptor=encryptor,
        inference_service=inference,
        session_factory=async_sessionmaker(engine, expire_on_commit=False),
        settings=settings,
    )


async def _enqueue(service, context, n: int = 1) -> list[str]:
    ids = []
    for i in range(n):
        enq = await service.admit_and_enqueue(
            alias_name="gpt-4",
            messages=[Message(role="user", content=f"msg-{i}")],
            params=GenerationParams(),
            stream=False,
            context=context,
        )
        ids.append(enq.request_id)
    return ids


# 1. worker restart preserves queued work and a new worker dispatches it


async def test_worker_restart_preserves_queued_work(sched_engine):
    service, factory, encryptor = await _build_service(sched_engine, adapter=_OkAdapter())
    async with factory() as session:
        context = await ensure_dev_identity(session)

    ids = await _enqueue(service, context, 3)

    # First worker claims one request, dispatches it, and is "killed": its lease
    # is forced into the past and never renewed, so the request goes
    # outcome_unknown on recovery (holding its slot conservatively). The other
    # two requests are never claimed.
    first = await service.claim_and_reserve("w1")
    assert not isinstance(first, str) and first is not None

    past = datetime(2020, 1, 1, tzinfo=UTC)
    async with factory() as session:
        async with session.begin():
            await session.execute(
                update(models.InferenceRequest)
                .where(models.InferenceRequest.id == first.request_id)
                .values(lease_expires_at=past)
            )

    await service.recover()

    # The dead worker's request is conservatively outcome_unknown; the still-queued
    # work is NOT lost.
    async with factory() as session:
        assert (await sched_repo.get_request(session, ids[0])).state == RequestState.OUTCOME_UNKNOWN
        assert (await sched_repo.get_request(session, ids[1])).state == RequestState.QUEUED
        assert (await sched_repo.get_request(session, ids[2])).state == RequestState.QUEUED

    # A "restarted" worker (fresh service instance, same queue key) drains the
    # remaining queued work.
    settings = Settings(
        _env_file=None,
        database_url="postgresql+asyncpg://u:p@h/db",
        app_env="dev",
        upstream_allowlist="192.168.22.50",
        oidc_enabled=False,
        allow_inference_auth_bypass=False,
        bootstrap_token=None,
    )
    restarted = _new_service(sched_engine, encryptor, settings, _OkAdapter())
    await restarted.recover()

    second = await restarted.claim_and_reserve("w3")
    assert not isinstance(second, str) and second is not None
    assert second.request_id == ids[1]
    await restarted.run_complete(second)

    third = await restarted.claim_and_reserve("w3")
    assert not isinstance(third, str) and third is not None
    assert third.request_id == ids[2]
    await restarted.run_complete(third)

    async with factory() as session:
        assert (await sched_repo.get_request(session, ids[1])).state == RequestState.SUCCEEDED
        assert (await sched_repo.get_request(session, ids[2])).state == RequestState.SUCCEEDED


# 2. API restart preserves durable queued + reserved + accounting state


async def test_api_restart_preserves_durable_state(sched_engine):
    service, factory, encryptor = await _build_service(sched_engine, adapter=_OkAdapter())
    async with factory() as session:
        context = await ensure_dev_identity(session)

    ids = await _enqueue(service, context, 2)
    first = await service.claim_and_reserve("w1")
    assert not isinstance(first, str) and first is not None

    # Simulate an API process restart: a brand-new service over the same DB must
    # observe the same queued request and the reserved request with no phantom
    # reservations or lost work.
    settings = Settings(
        _env_file=None,
        database_url="postgresql+asyncpg://u:p@h/db",
        app_env="dev",
        upstream_allowlist="192.168.22.50",
        oidc_enabled=False,
        allow_inference_auth_bypass=False,
        bootstrap_token=None,
    )
    restarted = _new_service(sched_engine, encryptor, settings, _OkAdapter())

    async with factory() as session:
        assert await sched_repo.count_queued(session) == 1
        assert await sched_repo.count_active_reservations(session, "ep-ollama") == 1
        assert (await sched_repo.get_request(session, ids[0])).state == RequestState.DISPATCHED

    # The restarted worker can settle the reserved work and drain the queue.
    await restarted.run_complete(first)

    second = await restarted.claim_and_reserve("w2")
    assert not isinstance(second, str) and second is not None
    assert second.request_id == ids[1]
    await restarted.run_complete(second)

    async with factory() as session:
        assert await sched_repo.count_active_reservations(session, "ep-ollama") == 0


# 3. provider failure settles failed, releases reservation, no double settlement


async def test_provider_failure_settles_failed_without_retry(sched_engine):
    service, factory, _encryptor = await _build_service(
        sched_engine, adapter=_ProviderFailureAdapter(), max_concurrency=1
    )
    async with factory() as session:
        context = await ensure_dev_identity(session)

    ids = await _enqueue(service, context, 1)
    claim = await service.claim_and_reserve("w1")
    assert not isinstance(claim, str) and claim is not None

    await service.run_complete(claim)

    async with factory() as session:
        row = await sched_repo.get_request(session, ids[0])
        assert row.state == RequestState.FAILED
        assert row.error_code == "upstream timeout"
        assert await sched_repo.count_active_reservations(session, "ep-ollama") == 0
        # Exactly one attempt: the adapter has no hidden retry path.
        attempts = (
            await session.execute(
                select(models.ExecutionAttempt).where(
                    models.ExecutionAttempt.request_id == ids[0]
                )
            )
        ).scalars().all()
        assert len(attempts) == 1


# 4. PostgreSQL outage fails closed: no admitted work, no unguarded dispatch


async def test_database_outage_fails_closed_without_dispatch():
    """A DB outage must never admit work or dispatch upstream.

    ``claim_and_reserve`` and ``admit_and_enqueue`` both open a session first; when
    the database is unreachable that open raises ``OperationalError`` and must
    propagate (fail closed) rather than returning a synthetic success, and the
    upstream adapter must never be reached.
    """
    encryptor = QueueEncryptor(QueueEncryptor.generate_key())
    settings = Settings(
        _env_file=None,
        database_url="postgresql+asyncpg://u:p@h/db",
        app_env="dev",
        upstream_allowlist="192.168.22.50",
        oidc_enabled=False,
        allow_inference_auth_bypass=False,
        bootstrap_token=None,
    )
    adapter = _TrackingAdapter()
    inference = InferenceService(
        EnvSecretResolver(),
        DestinationPolicy(settings.upstream_allowlist_hosts),
        adapter_factory=lambda kind: adapter,
    )
    factory = _DownSessionFactory()
    service = SchedulingService(
        encryptor=encryptor,
        inference_service=inference,
        session_factory=factory,
        settings=settings,
    )
    context = domain.RequestContext(
        project_id=ProjectId("proj-down"),
        principal_id=PrincipalId("principal-down"),
        api_credential_id=ApiCredentialId("cred-down"),
        audience=CredentialAudience.INFERENCE,
        scopes=(CredentialScope.INFERENCE_INVOKE,),
    )

    with pytest.raises(OperationalError):
        await service.claim_and_reserve("w1")

    with pytest.raises(OperationalError):
        await service.admit_and_enqueue(
            alias_name="gpt-4",
            messages=[Message(role="user", content="hi")],
            params=GenerationParams(),
            stream=False,
            context=context,
        )

    assert factory.opens >= 2
    assert adapter.complete_calls == 0


# 5. a PostgreSQL outage mid-dispatch must never re-dispatch or settle twice


async def test_database_outage_during_dispatch_no_double_settlement(sched_engine):
    """An in-flight request lost to a DB outage is outcome_unknown, never re-run.

    When PostgreSQL becomes unavailable after a request has been claimed and
    dispatched (its lease can no longer be renewed or settled), recovery must
    surface it conservatively as ``outcome_unknown``: exactly one execution
    attempt, zero usage records, and zero ledger entries. It is never silently
    re-dispatched (a second attempt) nor billed twice (a second settlement).
    """
    service, factory, _encryptor = await _build_service(
        sched_engine, adapter=_OkAdapter(), max_concurrency=1
    )
    async with factory() as session:
        async with session.begin():
            await _seed_request_price_policy(session, "rb-alias-gpt4")
        context = await ensure_dev_identity(session)

    ids = await _enqueue(service, context, 1)
    claim = await service.claim_and_reserve("w1")
    assert not isinstance(claim, str) and claim is not None

    # Simulate the DB going down while the request is in flight: the worker can
    # neither renew its lease nor commit settlement, so the lease lapses.
    past = datetime(2020, 1, 1, tzinfo=UTC)
    async with factory() as session:
        async with session.begin():
            await session.execute(
                update(models.InferenceRequest)
                .where(models.InferenceRequest.id == ids[0])
                .values(lease_expires_at=past)
            )

    await service.recover()

    async with factory() as session:
        row = await sched_repo.get_request(session, ids[0])
        assert row.state == RequestState.OUTCOME_UNKNOWN

        attempts = (
            await session.execute(
                select(models.ExecutionAttempt).where(
                    models.ExecutionAttempt.request_id == ids[0]
                )
            )
        ).scalars().all()
        assert len(attempts) == 1

        usage = (
            await session.execute(
                select(models.UsageRecord).where(
                    models.UsageRecord.request_id == ids[0]
                )
            )
        ).scalars().all()
        assert len(usage) == 0

        ledger = (
            await session.execute(
                select(models.LedgerEntry)
                .join(
                    models.UsageRecord,
                    models.LedgerEntry.usage_record_id == models.UsageRecord.id,
                )
                .where(models.UsageRecord.request_id == ids[0])
            )
        ).scalars().all()
        assert len(ledger) == 0


# 6. an API-side PostgreSQL error must not cancel durably admitted work
#
# A transient server-side failure (the database becoming unreachable and raising
# ``OperationalError`` from the terminal waiter) is NOT a client disconnect. The
# endpoint must surface the error (a real 5xx) while leaving the admitted request
# QUEUED and claimable so a worker can drain it after recovery. Only a genuine
# response-cycle cancellation (client disconnect) may durably cancel queued work.


class _WaiterService(SchedulingService):
    """A real scheduler whose terminal wait/stream readers raise configured errors.

    ``waiter_exception`` reproduces the API observing an unreachable PostgreSQL
    (``OperationalError``) versus a client disconnect (``asyncio.CancelledError``).
    ``stream_exception`` does the same for the SSE event reader.
    """

    def __init__(
        self,
        *,
        waiter_exception: BaseException | None = None,
        stream_exception: BaseException | None = None,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self._waiter_exception = waiter_exception
        self._stream_exception = stream_exception

    async def wait_for_terminal(self, request_id: str, deadline):
        raise self._waiter_exception

    async def wait_for_terminal_embedding(self, request_id: str, deadline):
        raise self._waiter_exception

    async def stream_events_after(self, request_id: str, after_seq: int):
        raise self._stream_exception


class _FakeRequest:
    """Minimal request stand-in for the endpoint helpers (no ASGI stack)."""

    def __init__(self, *, disconnected: bool = False) -> None:
        self.state = SimpleNamespace()
        self._disconnected = disconnected

    async def is_disconnected(self) -> bool:
        return self._disconnected


def _chat_body() -> ChatCompletionRequest:
    return ChatCompletionRequest(
        model="gpt-4", messages=[ChatMessage(role="user", content="hi")]
    )


def _db_down() -> OperationalError:
    return OperationalError("SELECT 1", {}, Exception("connection refused"))


async def _request_rows(factory) -> list:
    async with factory() as session:
        return (await session.execute(select(models.InferenceRequest))).scalars().all()


async def test_api_db_error_waiter_preserves_queued_work(sched_engine, monkeypatch):
    """Red before the fix: a transient DB error destroyed admitted queued work.

    The endpoint's ``finally`` cancelled the request whenever its terminal waiter
    raised, so a PostgreSQL ``OperationalError`` (not a client disconnect)
    cancelled the QUEUED request and lost admitted work. After the fix the error
    still propagates (the API returns a real 5xx) but the request stays QUEUED.
    """
    service, factory, encryptor = await _build_service(sched_engine, adapter=_OkAdapter())
    async with factory() as session:
        context = await ensure_dev_identity(session)
    monkeypatch.setattr("aethergate.api.deps.get_settings", lambda: service._settings)

    failing = _WaiterService(
        encryptor=encryptor,
        inference_service=service._inference,
        session_factory=factory,
        settings=service._settings,
        waiter_exception=_db_down(),
    )

    with pytest.raises(OperationalError):
        await chat_completions(_chat_body(), _FakeRequest(), Response(), context, failing)

    # Let any (incorrect) detached cancellation task run before asserting.
    for _ in range(20):
        await asyncio.sleep(0)

    rows = await _request_rows(factory)
    assert len(rows) == 1
    assert rows[0].state == RequestState.QUEUED

    # The preserved work remains claimable and drains exactly once.
    claim = await service.claim_and_reserve("w1")
    assert not isinstance(claim, str) and claim is not None
    assert claim.request_id == rows[0].id
    await service.run_complete(claim)
    rows = await _request_rows(factory)
    assert rows[0].state == RequestState.SUCCEEDED


async def test_api_client_disconnect_still_cancels_queued_work(sched_engine, monkeypatch):
    """A genuine client disconnect must still durably cancel pre-dispatch work."""
    service, factory, encryptor = await _build_service(sched_engine, adapter=_OkAdapter())
    async with factory() as session:
        context = await ensure_dev_identity(session)
    monkeypatch.setattr("aethergate.api.deps.get_settings", lambda: service._settings)

    disconnecting = _WaiterService(
        encryptor=encryptor,
        inference_service=service._inference,
        session_factory=factory,
        settings=service._settings,
        waiter_exception=asyncio.CancelledError(),
    )

    with pytest.raises(asyncio.CancelledError):
        await chat_completions(_chat_body(), _FakeRequest(), Response(), context, disconnecting)

    # The detached cancellation task runs on the loop; allow it to commit.
    for _ in range(50):
        await asyncio.sleep(0.01)

    rows = await _request_rows(factory)
    assert rows[0].state == RequestState.CANCELLED
    assert await service.claim_and_reserve("w1") is None


async def test_api_stream_db_error_preserves_queued_work(sched_engine):
    """A DB error while streaming must not cancel admitted queued work."""
    service, factory, _encryptor = await _build_service(sched_engine, adapter=_OkAdapter())
    async with factory() as session:
        context = await ensure_dev_identity(session)

    failing = _WaiterService(
        encryptor=service._encryptor,
        inference_service=service._inference,
        session_factory=factory,
        settings=service._settings,
        stream_exception=_db_down(),
    )
    ids = await _enqueue(failing, context, 1)

    with pytest.raises(OperationalError):
        async for _ in _stream_worker_events(
            failing, ids[0], "gid", "gpt-4", _FakeRequest()
        ):
            pass

    for _ in range(20):
        await asyncio.sleep(0)

    rows = await _request_rows(factory)
    assert rows[0].state == RequestState.QUEUED
    assert await service.claim_and_reserve("w1") is not None
