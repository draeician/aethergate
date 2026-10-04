"""Scheduler phase 1 correctness-hardening tests (DB-gated).

Covers lease heartbeat, consistent recovery, per-endpoint FIFO, atomic queue
caps, queue-wait expiry, cancellation, and explicit reconciliation.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

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
from aethergate.api.openai_chat import _stream_worker_events
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
from aethergate.errors import ProviderError, QueueFull
from aethergate.inference.service import InferenceService
from aethergate.persistence import models, repository
from aethergate.scheduler import repository as sched_repo
from aethergate.scheduler.service import SchedulingService, utcnow
from aethergate.secrets import EnvSecretResolver
from db_helpers import TEST_DATABASE_URL, reset_schema

pytestmark = pytest.mark.skipif(
    TEST_DATABASE_URL is None, reason="AETHERGATE_TEST_DATABASE_URL not set"
)


class MockAdapter:
    def __init__(
        self,
        *,
        content: str = "Hello from mock",
        delay: float = 0.0,
        stream_fail_after: int | None = None,
        slow_stream: bool = False,
    ) -> None:
        self._content = content
        self._delay = delay
        self._stream_fail_after = stream_fail_after
        self._slow_stream = slow_stream
        self.complete_calls = 0

    async def complete(self, request: ChatRequest, secret: str | None) -> CompletionResult:
        self.complete_calls += 1
        if self._delay:
            await asyncio.sleep(self._delay)
        return CompletionResult(
            content=self._content,
            finish_reason="stop",
            usage=Usage(prompt_tokens=1, completion_tokens=2, total_tokens=3),
            upstream_request_id=f"upstream-{self.complete_calls}",
        )

    async def stream(
        self, request: ChatRequest, secret: str | None
    ) -> AsyncIterator[StreamChunk]:
        if self._slow_stream:
            for i in range(5):
                yield StreamChunk(content=f"c{i}", finish_reason=None, usage=None)
                await asyncio.sleep(0.05)
            yield StreamChunk(
                content=None,
                finish_reason="stop",
                usage=Usage(prompt_tokens=1, completion_tokens=1, total_tokens=2),
            )
            return
        yield StreamChunk(content="a", finish_reason=None, usage=None)
        yield StreamChunk(content="b", finish_reason=None, usage=None)
        if self._stream_fail_after is not None:
            yield StreamChunk(content="c", finish_reason=None, usage=None)
            raise ProviderError("boom")
        yield StreamChunk(
            content="c",
            finish_reason="stop",
            usage=Usage(prompt_tokens=1, completion_tokens=2, total_tokens=3),
        )


async def _seed_endpoint(
    session,
    *,
    provider_id: str,
    account_id: str,
    endpoint_id: str,
    alias_id: str,
    alias_name: str,
    max_concurrency: int,
) -> None:
    await repository.create_provider(
        session,
        domain.Provider(
            id=ProviderId(provider_id), kind="ollama", name=provider_id,
            capabilities=(Capability.TEXT,),
        ),
    )
    await repository.create_provider_account(
        session,
        domain.ProviderAccount(
            id=ProviderAccountId(account_id),
            provider_id=ProviderId(provider_id),
            name=account_id,
        ),
    )
    await repository.create_endpoint(
        session,
        domain.Endpoint(
            id=EndpointId(endpoint_id),
            provider_account_id=ProviderAccountId(account_id),
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
            provider_account_id=ProviderAccountId(account_id),
            upstream_model="qwen3.8-2b-distill:Q6_K",
        ),
    )


async def _build(
    engine,
    *,
    max_concurrency: int = 2,
    two_endpoints: bool = False,
    **overrides,
):
    await reset_schema(engine)
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        async with session.begin():
            await _seed_endpoint(
                session,
                provider_id="prov-ollama",
                account_id="acct-ollama",
                endpoint_id="ep-ollama",
                alias_id="alias-gpt4",
                alias_name="gpt-4",
                max_concurrency=max_concurrency,
            )
            if two_endpoints:
                await _seed_endpoint(
                    session,
                    provider_id="prov-beta",
                    account_id="acct-beta",
                    endpoint_id="ep-beta",
                    alias_id="alias-beta",
                    alias_name="gpt-4b",
                    max_concurrency=1,
                )
            context = await ensure_dev_identity(session)

    mock = MockAdapter()
    encryptor = QueueEncryptor(QueueEncryptor.generate_key())
    settings = Settings(
        database_url="postgresql+asyncpg://u:p@h/db",
        upstream_allowlist="192.168.22.50",
        **overrides,
    )
    inference = InferenceService(
        EnvSecretResolver(),
        DestinationPolicy(settings.upstream_allowlist_hosts),
        adapter_factory=lambda kind: mock,
    )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    service = SchedulingService(
        encryptor=encryptor,
        inference_service=inference,
        session_factory=factory,
        settings=settings,
    )
    return service, factory, encryptor, mock, context


async def _enqueue(service, context, alias: str, n: int = 1, *, stream: bool = False):
    ids = []
    for i in range(n):
        enq = await service.admit_and_enqueue(
            alias_name=alias,
            messages=[Message(role="user", content=f"msg-{i}")],
            params=GenerationParams(),
            stream=stream,
            context=context,
        )
        ids.append(enq.request_id)
    return ids


# 1. healthy lease heartbeat across multiple lease periods


async def test_healthy_heartbeat_across_multiple_lease_periods(sched_engine):
    service, factory, _enc, mock, context = await _build(
        sched_engine, max_concurrency=2,
        worker_lease_seconds=0.3, worker_heartbeat_seconds=0.05,
    )
    mock._delay = 0.9  # > 3 lease periods
    ids = await _enqueue(service, context, "gpt-4", 1)
    claim = await service.claim_and_reserve("w1")
    assert not isinstance(claim, str) and claim is not None

    async def recover_loop():
        for _ in range(25):
            await asyncio.sleep(0.05)
            await service.recover()

    recover_task = asyncio.create_task(recover_loop())
    await service.run_complete(claim)
    await recover_task

    async with factory() as session:
        row = await sched_repo.get_request(session, ids[0])
        assert row.state == RequestState.SUCCEEDED
        # A healthy worker renewing its lease must never surface outcome_unknown.
        assert not (
            await session.execute(
                select(models.InferenceRequest).where(
                    models.InferenceRequest.state == RequestState.OUTCOME_UNKNOWN
                )
            )
        ).scalars().all()


# 2. stale fence cannot renew lease


async def test_stale_fence_cannot_renew_lease(sched_engine):
    service, factory, _enc, _mock, context = await _build(sched_engine, max_concurrency=2)
    await _enqueue(service, context, "gpt-4", 1)
    claim = await service.claim_and_reserve("w1")
    assert not isinstance(claim, str) and claim is not None

    async with factory() as session:
        async with session.begin():
            await session.execute(
                update(models.InferenceRequest)
                .where(models.InferenceRequest.id == claim.request_id)
                .values(fencing_token=claim.fencing_token + 1)
            )

    renewed = await service._renew_lease(claim)
    assert renewed is False


# 3. dead-worker post-dispatch expiry -> request + attempt outcome_unknown


async def test_post_dispatch_expiry_marks_request_and_attempt(sched_engine):
    service, factory, _enc, _mock, context = await _build(sched_engine, max_concurrency=2)
    ids = await _enqueue(service, context, "gpt-4", 1)
    claim = await service.claim_and_reserve("w1")
    assert not isinstance(claim, str) and claim is not None

    async with factory() as session:
        async with session.begin():
            await session.execute(
                update(models.InferenceRequest)
                .where(models.InferenceRequest.id == claim.request_id)
                .values(lease_expires_at=datetime(2020, 1, 1, tzinfo=UTC))
            )

    await service.recover()

    async with factory() as session:
        row = await sched_repo.get_request(session, ids[0])
        assert row.state == RequestState.OUTCOME_UNKNOWN
        attempt_state = (
            await session.execute(
                select(models.ExecutionAttempt.state).where(
                    models.ExecutionAttempt.request_id == ids[0]
                )
            )
        ).scalar_one()
        assert attempt_state == "outcome_unknown"


# 5. explicit reconciliation releases exactly the intended reservation


async def test_reconciliation_releases_intended_reservation(sched_engine):
    service, factory, _enc, _mock, context = await _build(sched_engine, max_concurrency=2)
    await _enqueue(service, context, "gpt-4", 2)
    a = await service.claim_and_reserve("w1")
    b = await service.claim_and_reserve("w2")
    assert not isinstance(a, str) and a is not None
    assert not isinstance(b, str) and b is not None

    async with factory() as session:
        async with session.begin():
            await session.execute(
                update(models.InferenceRequest)
                .where(models.InferenceRequest.id == a.request_id)
                .values(lease_expires_at=datetime(2020, 1, 1, tzinfo=UTC))
            )

    await service.recover()

    async with factory() as session:
        assert await sched_repo.count_active_reservations(session, "ep-ollama") == 2

    ok = await service.reconcile(a.request_id, "failed", "operator-tester")
    assert ok is True

    async with factory() as session:
        row = await sched_repo.get_request(session, a.request_id)
        assert row.state == RequestState.FAILED
        assert row.reconciled_by == "operator-tester"
        assert row.reconciled_state == "failed"
        assert await sched_repo.count_active_reservations(session, "ep-ollama") == 1

    # reconciling again must fail (no longer outcome_unknown)
    assert await service.reconcile(a.request_id, "succeeded", "x") is False


# 6. concurrent recovery workers are idempotent/safe


async def test_concurrent_recovery_idempotent(sched_engine):
    service, factory, _enc, _mock, context = await _build(sched_engine, max_concurrency=3)
    await _enqueue(service, context, "gpt-4", 3)
    a = await service.claim_and_reserve("w1")
    b = await service.claim_and_reserve("w2")
    c = await service.claim_and_reserve("w3")
    assert all(not isinstance(x, str) and x is not None for x in (a, b, c))

    past = datetime(2020, 1, 1, tzinfo=UTC)
    async with factory() as session:
        async with session.begin():
            for claim in (a, b):
                await session.execute(
                    update(models.InferenceRequest)
                    .where(models.InferenceRequest.id == claim.request_id)
                    .values(lease_expires_at=past)
                )
            # c is a pre-dispatch death: reserved with an expired lease.
            await session.execute(
                update(models.InferenceRequest)
                .where(models.InferenceRequest.id == c.request_id)
                .values(state=RequestState.RESERVED, lease_expires_at=past)
            )

    await asyncio.gather(
        service.recover(), service.recover(), service.recover()
    )

    async with factory() as session:
        rows = (
            await session.execute(
                select(models.InferenceRequest).order_by(models.InferenceRequest.queued_at)
            )
        ).scalars().all()
        by_id = {r.id: r.state for r in rows}
        assert by_id[a.request_id] == RequestState.OUTCOME_UNKNOWN
        assert by_id[b.request_id] == RequestState.OUTCOME_UNKNOWN
        assert by_id[c.request_id] == RequestState.QUEUED  # pre-dispatch reclaimed
        assert await sched_repo.count_active_reservations(session, "ep-ollama") == 2


# 7 + 8. per-endpoint FIFO and no cross-endpoint head-of-line blocking


async def test_per_endpoint_fifo(sched_engine):
    service, _factory, _enc, _mock, context = await _build(
        sched_engine, two_endpoints=True, max_concurrency=2
    )
    a_ids = await _enqueue(service, context, "gpt-4", 3)
    await _enqueue(service, context, "gpt-4b", 1)

    a1 = await service.claim_and_reserve("w1")
    assert not isinstance(a1, str) and a1 is not None
    assert a1.request_id == a_ids[0]

    # A is not saturated (concurrency 2), so A2 dispatches next (FIFO within A).
    a2 = await service.claim_and_reserve("w2")
    assert not isinstance(a2, str) and a2 is not None
    assert a2.request_id == a_ids[1]


async def test_saturated_endpoint_does_not_block_other_endpoint(sched_engine):
    service, _factory, _enc, _mock, context = await _build(
        sched_engine, two_endpoints=True, max_concurrency=1
    )
    a_ids = await _enqueue(service, context, "gpt-4", 2)
    b_ids = await _enqueue(service, context, "gpt-4b", 1)

    a1 = await service.claim_and_reserve("w1")
    assert not isinstance(a1, str) and a1 is not None
    assert a1.request_id == a_ids[0]

    # Endpoint A (concurrency 1) is saturated; B must still dispatch.
    b1 = await service.claim_and_reserve("w2")
    assert not isinstance(b1, str) and b1 is not None
    assert b1.request_id == b_ids[0]

    # A2 is still queued, proving B did not wait for A's queued work.
    async with service._session_factory() as session:
        row = await sched_repo.get_request(session, a_ids[1])
        assert row.state == RequestState.QUEUED

    await service.run_complete(a1)
    a2 = await service.claim_and_reserve("w1")
    assert not isinstance(a2, str) and a2 is not None
    assert a2.request_id == a_ids[1]


# 9. concurrent queue-cap admission cannot exceed configured cap


async def test_concurrent_queue_cap_admission(sched_engine):
    service, factory, _enc, _mock, context = await _build(
        sched_engine, queue_max_requests=3
    )

    async def admit(i: int) -> bool:
        try:
            await service.admit_and_enqueue(
                alias_name="gpt-4",
                messages=[Message(role="user", content=f"msg-{i}")],
                params=GenerationParams(),
                stream=False,
                context=context,
            )
            return True
        except QueueFull:
            return False

    results = await asyncio.gather(*[admit(i) for i in range(20)])
    assert sum(results) == 3  # exactly the configured cap admitted

    async with factory() as session:
        assert await sched_repo.count_queued(session) == 3


# 10. queue_wait_until expiry before dispatch


async def test_queue_wait_expiry_before_dispatch(sched_engine):
    service, factory, _enc, _mock, context = await _build(
        sched_engine, max_concurrency=1, queue_max_wait_seconds=0.15,
        scheduler_poll_interval_seconds=0.05,
    )
    await _enqueue(service, context, "gpt-4", 1)
    claim = await service.claim_and_reserve("w1")
    assert not isinstance(claim, str) and claim is not None

    b = await _enqueue(service, context, "gpt-4", 1)

    await asyncio.sleep(0.3)
    await service.recover()

    async with factory() as session:
        row = await sched_repo.get_request(session, b[0])
        assert row.state == RequestState.EXPIRED
        attempts = (
            await session.execute(
                select(models.ExecutionAttempt).where(
                    models.ExecutionAttempt.request_id == b[0]
                )
            )
        ).scalars().all()
        assert attempts == []


# 11. API timeout does not leave later-dispatchable queued work


async def test_api_timeout_cancels_queued_work(sched_engine):
    service, factory, _enc, _mock, context = await _build(
        sched_engine, scheduler_poll_interval_seconds=0.02
    )
    ids = await _enqueue(service, context, "gpt-4", 1)

    result = await service.wait_for_terminal(ids[0], utcnow() + timedelta(seconds=0.05))
    assert result.state == RequestState.EXPIRED
    assert result.error_code == "timeout"

    async with factory() as session:
        row = await sched_repo.get_request(session, ids[0])
        assert row.state == RequestState.CANCELLED

    assert await service.claim_and_reserve("w1") is None


# 12. queued request cancellation transitions to a terminal state


async def test_request_cancellation_queued_transitions_to_cancelled(sched_engine):
    service, factory, _enc, _mock, context = await _build(sched_engine)
    ids = await _enqueue(service, context, "gpt-4", 1)

    await service.request_cancellation(ids[0])

    async with factory() as session:
        row = await sched_repo.get_request(session, ids[0])
        assert row.state == RequestState.CANCELLED
        assert await sched_repo.count_active_reservations(session, "ep-ollama") == 0

    assert await service.claim_and_reserve("w1") is None


# 13. active-stream cancellation settles cancelled and releases capacity


async def test_active_stream_cancellation(sched_engine):
    service, factory, enc, mock, context = await _build(
        sched_engine, max_concurrency=1, scheduler_poll_interval_seconds=0.02
    )
    mock._slow_stream = True
    ids = await _enqueue(service, context, "gpt-4", 1, stream=True)
    claim = await service.claim_and_reserve("w1")
    assert not isinstance(claim, str) and claim is not None

    run_task = asyncio.create_task(service.run_stream(claim))
    await asyncio.sleep(0.08)
    await service.request_cancellation(claim.request_id)
    await run_task

    async with factory() as session:
        row = await sched_repo.get_request(session, ids[0])
        assert row.state == RequestState.CANCELLED
        assert await sched_repo.count_active_reservations(session, "ep-ollama") == 0


# 12b. queued stream client disconnect propagates durable cancellation


async def test_stream_disconnect_cancels_queued_request(sched_engine):
    service, factory, _enc, _mock, context = await _build(sched_engine)
    ids = await _enqueue(service, context, "gpt-4", 1, stream=True)

    class DisconnectingRequest:
        async def is_disconnected(self) -> bool:
            return True

    out: list[str] = []
    async for piece in _stream_worker_events(
        service, ids[0], "gid", "gpt-4", DisconnectingRequest()
    ):
        out.append(piece)

    assert any("data: [DONE]" in p for p in out)

    # The detached cancellation task runs on the loop; allow it to commit.
    for _ in range(20):
        await asyncio.sleep(0.01)

    async with factory() as session:
        row = await sched_repo.get_request(session, ids[0])
        assert row.state == RequestState.CANCELLED

    assert await service.claim_and_reserve("w1") is None


# 14. failed stream does not synthesize a successful stop


async def test_failed_stream_no_synthetic_stop(sched_engine):
    service, factory, enc, mock, context = await _build(sched_engine)
    mock._stream_fail_after = 1
    ids = await _enqueue(service, context, "gpt-4", 1, stream=True)
    claim = await service.claim_and_reserve("w1")
    assert not isinstance(claim, str) and claim is not None
    await service.run_stream(claim)

    class FakeRequest:
        async def is_disconnected(self) -> bool:
            return False

    out: list[str] = []
    async for piece in _stream_worker_events(service, ids[0], "gid", "gpt-4", FakeRequest()):
        out.append(piece)

    text = "".join(out)
    assert "data: [DONE]" in text
    for line in text.splitlines():
        if line.startswith("data: ") and line != "data: [DONE]":
            obj = json.loads(line[len("data: "):])
            assert obj["choices"][0]["finish_reason"] != "stop"


# 16. database CHECK rejects invalid endpoint capacity


async def test_database_check_rejects_invalid_capacity(sched_engine):
    async with async_sessionmaker(sched_engine, expire_on_commit=False)() as session:
        provider = models.Provider(id="p", kind="ollama", name="p", capabilities=[], is_active=True)
        account = models.ProviderAccount(id="acct", provider_id="p", name="acct", is_active=True)
        session.add_all([provider, account])
        await session.flush()
        bad = models.Endpoint(
            id="ep-bad",
            provider_account_id="acct",
            name="bad",
            base_destination="http://example.com",
            max_concurrency=0,
        )
        session.add(bad)
        from sqlalchemy.exc import IntegrityError

        with pytest.raises(IntegrityError):
            await session.flush()
        await session.rollback()
