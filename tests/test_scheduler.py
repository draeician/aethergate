"""Durable scheduler integration tests (DB-gated)."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import async_sessionmaker

from aethergate.adapters.base import (
    ChatRequest,
    CompletionResult,
    GenerationParams,
    Message,
    StreamChunk,
    Usage,
)
from aethergate.config import Settings
from aethergate.dev_identity import ensure_dev_identity
from aethergate.domain import entities as domain
from aethergate.domain.enums import Capability, RequestState
from aethergate.domain.ids import (
    EndpointId,
    ModelAliasId,
    ProviderAccountId,
    ProviderId,
    RouteBindingId,
)
from aethergate.egress import DestinationPolicy
from aethergate.encryption import QueueEncryptor
from aethergate.errors import QueueFull
from aethergate.inference.service import InferenceService
from aethergate.persistence import models, repository
from aethergate.scheduler import repository as sched_repo
from aethergate.scheduler.service import SchedulingService
from aethergate.secrets import EnvSecretResolver
from db_helpers import TEST_DATABASE_URL, reset_schema

pytestmark = pytest.mark.skipif(
    TEST_DATABASE_URL is None, reason="AETHERGATE_TEST_DATABASE_URL not set"
)


class MockAdapter:
    def __init__(self, *, content: str = "Hello from mock", delay: float = 0.0) -> None:
        self._content = content
        self._delay = delay
        self.complete_calls = 0
        self.active = 0
        self.max_active = 0

    async def complete(self, request: ChatRequest, secret: str | None) -> CompletionResult:
        self.complete_calls += 1
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            if self._delay:
                await asyncio.sleep(self._delay)
            return CompletionResult(
                content=self._content,
                finish_reason="stop",
                usage=Usage(prompt_tokens=1, completion_tokens=2, total_tokens=3),
                upstream_request_id=f"upstream-{self.complete_calls}",
            )
        finally:
            self.active -= 1

    async def stream(
        self, request: ChatRequest, secret: str | None
    ) -> AsyncIterator[StreamChunk]:
        yield StreamChunk(content="Hel", finish_reason=None, usage=None)
        yield StreamChunk(
            content="lo",
            finish_reason="stop",
            usage=Usage(prompt_tokens=1, completion_tokens=1, total_tokens=2),
        )


async def _seed(session, *, max_concurrency: int = 2) -> None:
    await repository.create_provider(
        session,
        domain.Provider(
            id=ProviderId("prov-ollama"), kind="ollama", name="ollama",
            capabilities=(Capability.TEXT,),
        ),
    )
    await repository.create_provider_account(
        session,
        domain.ProviderAccount(
            id=ProviderAccountId("acct-ollama"),
            provider_id=ProviderId("prov-ollama"),
            name="ollama-account",
        ),
    )
    await repository.create_endpoint(
        session,
        domain.Endpoint(
            id=EndpointId("ep-ollama"),
            provider_account_id=ProviderAccountId("acct-ollama"),
            name="ollama-endpoint",
            base_destination="http://192.168.22.50:11434",
            max_concurrency=max_concurrency,
        ),
    )
    await repository.create_model_alias(
        session,
        domain.ModelAlias(
            id=ModelAliasId("alias-gpt4"), name="gpt-4", capabilities=(Capability.TEXT,)
        ),
    )
    await repository.create_route_binding(
        session,
        domain.RouteBinding(
            id=RouteBindingId("rb-gpt4"),
            model_alias_id=ModelAliasId("alias-gpt4"),
            endpoint_id=EndpointId("ep-ollama"),
            provider_account_id=ProviderAccountId("acct-ollama"),
            upstream_model="qwen3.8-2b-distill:Q6_K",
        ),
    )


@pytest.fixture
async def scheduler(sched_engine):
    await reset_schema(sched_engine)
    async with async_sessionmaker(sched_engine, expire_on_commit=False)() as session:
        async with session.begin():
            await _seed(session)
            context = await ensure_dev_identity(session)

    mock = MockAdapter()
    encryptor = QueueEncryptor(QueueEncryptor.generate_key())
    settings = Settings(
        database_url="postgresql+asyncpg://u:p@h/db",
        upstream_allowlist="192.168.22.50",
        worker_lease_seconds=120.0,
        queue_max_requests=100,
    )
    inference = InferenceService(
        EnvSecretResolver(),
        DestinationPolicy(settings.upstream_allowlist_hosts),
        adapter_factory=lambda kind: mock,
    )
    factory = async_sessionmaker(sched_engine, expire_on_commit=False)
    service = SchedulingService(
        encryptor=encryptor,
        inference_service=inference,
        session_factory=factory,
        settings=settings,
    )
    yield service, factory, encryptor, mock, context
    await reset_schema(sched_engine)


async def _enqueue_many(service, context, n: int, *, stream: bool = False) -> list[str]:
    ids = []
    for i in range(n):
        enq = await service.admit_and_enqueue(
            alias_name="gpt-4",
            messages=[Message(role="user", content=f"msg-{i}")],
            params=GenerationParams(),
            stream=stream,
            context=context,
        )
        ids.append(enq.request_id)
    return ids


async def test_fifo_queue_order(scheduler):
    service, _factory, _enc, _mock, context = scheduler
    ids = await _enqueue_many(service, context, 3)

    dispatched = []
    for _ in range(3):
        outcome = await service.claim_and_reserve("w1")
        assert not isinstance(outcome, str) and outcome is not None
        dispatched.append(outcome.request_id)
        await service.run_complete(outcome)

    assert dispatched == ids  # strict FIFO order


async def test_max_concurrency_enforced_six_requests_two_slots(scheduler):
    service, factory, _enc, _mock, context = scheduler
    ids = await _enqueue_many(service, context, 6)

    a = await service.claim_and_reserve("w1")
    b = await service.claim_and_reserve("w2")
    assert not isinstance(a, str) and a is not None
    assert not isinstance(b, str) and b is not None

    c = await service.claim_and_reserve("w1")
    assert c == "full"

    async with factory() as session:
        active = await sched_repo.count_active_reservations(session, "ep-ollama")
    assert active == 2

    await service.run_complete(a)
    await service.run_complete(b)

    c = await service.claim_and_reserve("w1")
    d = await service.claim_and_reserve("w2")
    assert not isinstance(c, str) and c is not None
    assert not isinstance(d, str) and d is not None
    await service.run_complete(c)
    await service.run_complete(d)

    e = await service.claim_and_reserve("w1")
    f = await service.claim_and_reserve("w2")
    assert not isinstance(e, str) and e is not None
    assert not isinstance(f, str) and f is not None
    await service.run_complete(e)
    await service.run_complete(f)

    async with factory() as session:
        rows = (
            await session.execute(
                select(models.InferenceRequest).order_by(models.InferenceRequest.queued_at)
            )
        ).scalars().all()
    assert all(r.state == RequestState.SUCCEEDED for r in rows)
    assert [r.id for r in rows] == ids


async def test_encrypted_payload_at_rest(scheduler):
    service, factory, _enc, _mock, context = scheduler
    ids = await _enqueue_many(service, context, 1)

    async with factory() as session:
        row = await sched_repo.get_request(session, ids[0])
        assert row.payload_encrypted is not None
        assert b"msg-0" not in row.payload_encrypted


async def test_result_encrypted_at_rest(scheduler):
    service, factory, _enc, _mock, context = scheduler
    ids = await _enqueue_many(service, context, 1)
    outcome = await service.claim_and_reserve("w1")
    assert not isinstance(outcome, str) and outcome is not None
    await service.run_complete(outcome)

    async with factory() as session:
        row = await sched_repo.get_request(session, ids[0])
        assert b"Hello from mock" not in (row.result_encrypted or b"")


async def test_fencing_rejects_stale_completion(scheduler):
    service, factory, _enc, _mock, context = scheduler
    ids = await _enqueue_many(service, context, 1)
    outcome = await service.claim_and_reserve("w1")
    assert not isinstance(outcome, str) and outcome is not None

    async with factory() as session:
        async with session.begin():
            await session.execute(
                update(models.InferenceRequest)
                .where(models.InferenceRequest.id == outcome.request_id)
                .values(fencing_token=outcome.fencing_token + 1)
            )

    await service._settle(outcome, state=RequestState.SUCCEEDED, result_encrypted=b"x")

    async with factory() as session:
        row = await sched_repo.get_request(session, ids[0])
        assert row.state != RequestState.SUCCEEDED


async def test_safe_reclaim_before_dispatch(scheduler):
    service, factory, _enc, _mock, context = scheduler
    ids = await _enqueue_many(service, context, 1)
    outcome = await service.claim_and_reserve("w1")
    assert not isinstance(outcome, str) and outcome is not None

    async with factory() as session:
        async with session.begin():
            await session.execute(
                update(models.InferenceRequest)
                .where(models.InferenceRequest.id == outcome.request_id)
                .values(
                    state=RequestState.RESERVED,
                    lease_expires_at=datetime(2020, 1, 1, tzinfo=UTC),
                )
            )

    await service.recover()

    async with factory() as session:
        row = await sched_repo.get_request(session, ids[0])
        assert row.state == RequestState.QUEUED


async def test_post_dispatch_lease_expiry_becomes_outcome_unknown(scheduler):
    service, factory, _enc, _mock, context = scheduler
    ids = await _enqueue_many(service, context, 1)
    outcome = await service.claim_and_reserve("w1")
    assert not isinstance(outcome, str) and outcome is not None

    async with factory() as session:
        async with session.begin():
            await session.execute(
                update(models.InferenceRequest)
                .where(models.InferenceRequest.id == outcome.request_id)
                .values(lease_expires_at=datetime(2020, 1, 1, tzinfo=UTC))
            )

    await service.recover()

    async with factory() as session:
        row = await sched_repo.get_request(session, ids[0])
        assert row.state == RequestState.OUTCOME_UNKNOWN


async def test_outcome_unknown_keeps_capacity_reserved(scheduler):
    service, factory, _enc, _mock, context = scheduler
    await _enqueue_many(service, context, 1)
    outcome = await service.claim_and_reserve("w1")
    assert not isinstance(outcome, str) and outcome is not None

    async with factory() as session:
        async with session.begin():
            await session.execute(
                update(models.InferenceRequest)
                .where(models.InferenceRequest.id == outcome.request_id)
                .values(lease_expires_at=datetime(2020, 1, 1, tzinfo=UTC))
            )

    await service.recover()

    async with factory() as session:
        active = await sched_repo.count_active_reservations(session, "ep-ollama")
    assert active == 1


async def test_queued_client_cancellation(scheduler):
    service, factory, _enc, _mock, context = scheduler
    ids = await _enqueue_many(service, context, 1)
    await service.request_cancellation(ids[0])

    async with factory() as session:
        row = await sched_repo.get_request(session, ids[0])
        assert row.cancellation_requested is True

    outcome = await service.claim_and_reserve("w1")
    assert outcome is None


async def test_streaming_event_order(scheduler):
    service, factory, _enc, _mock, context = scheduler
    ids = await _enqueue_many(service, context, 1, stream=True)
    outcome = await service.claim_and_reserve("w1")
    assert not isinstance(outcome, str) and outcome is not None
    await service.run_stream(outcome)

    events = await service.stream_events_after(ids[0], 0)
    assert [e["seq"] for e in events] == [1, 2]
    assert events[0]["content"] == "Hel"
    assert events[1]["content"] == "lo"
    assert events[1]["finish_reason"] == "stop"


async def test_queue_full_rejection(scheduler):
    service, factory, _enc, _mock, context = scheduler
    service._settings.queue_max_requests = 1
    await _enqueue_many(service, context, 1)

    with pytest.raises(QueueFull):
        await service.admit_and_enqueue(
            alias_name="gpt-4",
            messages=[Message(role="user", content="hi")],
            params=GenerationParams(),
            stream=False,
            context=context,
        )


async def test_restart_persistence_of_queued_work(scheduler):
    service, factory, encryptor, mock, context = scheduler
    ids = await _enqueue_many(service, context, 3)

    fresh = SchedulingService(
        encryptor=encryptor,
        inference_service=service._inference,
        session_factory=factory,
        settings=service._settings,
    )
    claimed = []
    for _ in range(3):
        outcome = await fresh.claim_and_reserve("w-after-restart")
        assert not isinstance(outcome, str) and outcome is not None
        claimed.append(outcome.request_id)
        await fresh.run_complete(outcome)
    assert claimed == ids


async def test_concurrent_claims_never_double_dispatch(scheduler):
    service, _factory, _enc, _mock, context = scheduler
    ids = await _enqueue_many(service, context, 10)

    seen: set[str] = set()

    async def worker(wid):
        while True:
            outcome = await service.claim_and_reserve(wid)
            if outcome is None:
                return
            if isinstance(outcome, str):
                if outcome == "full":
                    await asyncio.sleep(0.01)
                    continue
                continue
            assert outcome.request_id not in seen, "double dispatch!"
            seen.add(outcome.request_id)
            await service.run_complete(outcome)

    await asyncio.gather(worker("w1"), worker("w2"), worker("w3"))

    assert seen == set(ids)
    assert len(seen) == 10
