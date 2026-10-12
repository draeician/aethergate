"""Embedding requests through the durable scheduler authority (DB-gated).

Proves embeddings reuse the exact scheduler/accounting/worker path: durable
queueing, endpoint max_concurrency, encrypted-at-rest payloads, embedding
capability enforcement (alias + provider + adapter, no fallback), dispatch-time
capability revalidation, request-quota accounting (one unit per request), and
the token-quota/budget fail-closed rule when no estimator exists.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from aethergate.adapters.base import (
    ChatRequest,
    CompletionResult,
    EmbeddingRequest,
    EmbeddingResult,
    StreamChunk,
    Usage,
)
from aethergate.config import Settings
from aethergate.dev_identity import ensure_dev_identity
from aethergate.domain import entities as domain
from aethergate.domain.enums import BillingUnit, Capability, QuotaMetric, RequestState
from aethergate.domain.ids import (
    BudgetPolicyId,
    EndpointId,
    ModelAliasId,
    PricePolicyId,
    ProviderAccountId,
    ProviderId,
    QuotaGroupId,
    QuotaLimitId,
    RouteBindingId,
)
from aethergate.egress import DestinationPolicy
from aethergate.encryption import QueueEncryptor
from aethergate.errors import ResourceInactive
from aethergate.inference.service import InferenceService
from aethergate.persistence import models, repository
from aethergate.scheduler import repository as sched_repo
from aethergate.scheduler.service import SchedulingService
from aethergate.secrets import EnvSecretResolver
from db_helpers import TEST_DATABASE_URL, reset_schema

pytestmark = pytest.mark.skipif(
    TEST_DATABASE_URL is None, reason="AETHERGATE_TEST_DATABASE_URL not set"
)


class MockEmbeddingAdapter:
    supports_embedding = True

    def __init__(self) -> None:
        self.embed_calls = 0
        self.complete_calls = 0

    async def embed(self, request: EmbeddingRequest, secret: str | None) -> EmbeddingResult:
        self.embed_calls += 1
        vectors = tuple(
            (float(index + 1),) for index in range(len(request.inputs))
        )
        return EmbeddingResult(
            vectors=vectors,
            usage=Usage(prompt_tokens=5, completion_tokens=0, total_tokens=5),
            upstream_request_id=f"upstream-embed-{self.embed_calls}",
        )

    async def complete(self, request: ChatRequest, secret: str | None) -> CompletionResult:
        self.complete_calls += 1
        return CompletionResult(
            content="ok",
            finish_reason="stop",
            usage=Usage(prompt_tokens=1, completion_tokens=2, total_tokens=3),
            upstream_request_id="unused",
        )

    async def stream(
        self, request: ChatRequest, secret: str | None
    ) -> AsyncIterator[StreamChunk]:
        yield StreamChunk(content="ok", finish_reason="stop", usage=None)

    def estimate_input_tokens(self, request: ChatRequest) -> int | None:
        return 4


async def _seed(session) -> None:
    await repository.create_provider(
        session,
        domain.Provider(
            id=ProviderId("prov-ollama"), kind="ollama", name="ollama",
            capabilities=(Capability.TEXT, Capability.EMBEDDING),
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
            max_concurrency=2,
        ),
    )
    await repository.create_model_alias(
        session,
        domain.ModelAlias(
            id=ModelAliasId("alias-embed"),
            name="embed-model",
            capabilities=(Capability.EMBEDDING,),
        ),
    )
    await repository.create_route_binding(
        session,
        domain.RouteBinding(
            id=RouteBindingId("rb-embed"),
            model_alias_id=ModelAliasId("alias-embed"),
            endpoint_id=EndpointId("ep-ollama"),
            provider_account_id=ProviderAccountId("acct-ollama"),
            upstream_model="nomic-embed-text",
        ),
    )
    await repository.create_model_alias(
        session,
        domain.ModelAlias(
            id=ModelAliasId("alias-text"),
            name="text-model",
            capabilities=(Capability.TEXT,),
        ),
    )
    await repository.create_route_binding(
        session,
        domain.RouteBinding(
            id=RouteBindingId("rb-text"),
            model_alias_id=ModelAliasId("alias-text"),
            endpoint_id=EndpointId("ep-ollama"),
            provider_account_id=ProviderAccountId("acct-ollama"),
            upstream_model="qwen3.8-2b-distill:Q6_K",
        ),
    )


@pytest.fixture
async def embedding_scheduler(sched_engine):
    await reset_schema(sched_engine)
    async with async_sessionmaker(sched_engine, expire_on_commit=False)() as session:
        async with session.begin():
            await _seed(session)
            context = await ensure_dev_identity(session)

    mock = MockEmbeddingAdapter()
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


async def _enqueue_embedding(service, context, *, alias="embed-model", inputs=("hi",)):
    enq = await service.admit_embedding_and_enqueue(
        alias_name=alias, inputs=inputs, context=context
    )
    return enq.request_id


async def test_embedding_six_requests_two_slots_queue_and_drain(embedding_scheduler):
    service, factory, _enc, mock, context = embedding_scheduler
    ids = []
    for i in range(6):
        ids.append(
            await _enqueue_embedding(service, context, inputs=(f"input-{i}",))
        )

    a = await service.claim_and_reserve("w1")
    b = await service.claim_and_reserve("w2")
    assert not isinstance(a, str) and a is not None
    assert not isinstance(b, str) and b is not None
    assert a.operation == "embedding"

    assert await service.claim_and_reserve("w1") == "full"
    async with factory() as session:
        active = await sched_repo.count_active_reservations(session, "ep-ollama")
        queued = await sched_repo.count_queued(session)
    assert active == 2
    assert queued == 4

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
    assert [r.id for r in rows] == ids
    assert all(r.state == RequestState.SUCCEEDED for r in rows)
    assert mock.embed_calls == 6


async def test_embedding_payload_encrypted_at_rest(embedding_scheduler):
    service, factory, _enc, _mock, context = embedding_scheduler
    enq = await service.admit_embedding_and_enqueue(
        alias_name="embed-model", inputs=("secret embedding text",), context=context
    )
    async with factory() as session:
        row = await sched_repo.get_request(session, enq.request_id)
        assert row.payload_encrypted is not None
        assert b"secret embedding text" not in row.payload_encrypted


async def test_embedding_on_text_only_alias_rejected_before_dispatch(embedding_scheduler):
    service, factory, _enc, _mock, context = embedding_scheduler
    with pytest.raises(ResourceInactive):
        await service.admit_embedding_and_enqueue(
            alias_name="text-model", inputs=("hi",), context=context
        )
    async with factory() as session:
        rows = (await session.execute(select(models.InferenceRequest))).scalars().all()
    assert rows == []


async def test_dispatch_time_embedding_capability_revocation_blocks_dispatch(
    embedding_scheduler,
):
    service, factory, _enc, mock, context = embedding_scheduler
    enq = await service.admit_embedding_and_enqueue(
        alias_name="embed-model", inputs=("hi",), context=context
    )
    async with factory() as session:
        async with session.begin():
            await repository.update_provider(
                session, ProviderId("prov-ollama"), capabilities=(Capability.TEXT,)
            )
    outcome = await service.claim_and_reserve("w1")
    assert outcome == "processed"
    assert mock.embed_calls == 0
    async with factory() as session:
        row = await sched_repo.get_request(session, enq.request_id)
    assert row.state == RequestState.FAILED


async def test_embedding_token_quota_estimator_unavailable_fails_closed(
    embedding_scheduler,
):
    service, factory, _enc, mock, context = embedding_scheduler
    async with factory() as session:
        async with session.begin():
            await repository.create_quota_group(
                session,
                domain.QuotaGroup(
                    id=QuotaGroupId("grp-token"),
                    provider_account_id=ProviderAccountId("acct-ollama"),
                    name="token-group",
                ),
            )
            await repository.create_quota_limit(
                session,
                domain.QuotaLimit(
                    id=QuotaLimitId("grp-token-t0"),
                    quota_group_id=QuotaGroupId("grp-token"),
                    metric=QuotaMetric.TOKENS,
                    limit_units=1000,
                    window_seconds=60,
                ),
            )
            await repository.update_route_binding(
                session,
                RouteBindingId("rb-embed"),
                quota_group_id=QuotaGroupId("grp-token"),
            )
    enq = await service.admit_embedding_and_enqueue(
        alias_name="embed-model", inputs=("hi",), context=context
    )
    outcome = await service.claim_and_reserve("w1")
    assert outcome == "processed"
    assert mock.embed_calls == 0
    async with factory() as session:
        row = await sched_repo.get_request(session, enq.request_id)
    assert row.state == RequestState.FAILED
    assert row.error_code == "quota_token_estimator_unavailable"


async def test_embedding_request_priced_budget_accounts_exactly_once(
    embedding_scheduler,
):
    service, factory, _enc, _mock, context = embedding_scheduler
    async with factory() as session:
        async with session.begin():
            await repository.create_price_policy(
                session,
                domain.PricePolicy(
                    id=PricePolicyId("pp-embed"),
                    route_binding_id=RouteBindingId("rb-embed"),
                    billing_unit=BillingUnit.REQUEST,
                    currency="USD",
                    unit_scale=1,
                    request_price=Decimal("0.02"),
                ),
            )
            await repository.create_project_budget_policy(
                session,
                domain.ProjectBudgetPolicy(
                    id=BudgetPolicyId("bp-embed"),
                    project_id=context.project_id,
                    name="embed-budget",
                    currency="USD",
                    limit_amount=Decimal("100"),
                    window_seconds=3600,
                ),
            )
    await service.admit_embedding_and_enqueue(
        alias_name="embed-model", inputs=("a", "b", "c"), context=context
    )
    outcome = await service.claim_and_reserve("w1")
    assert not isinstance(outcome, str) and outcome is not None
    await service.run_complete(outcome)

    async with factory() as session:
        rows = (
            await session.execute(select(models.InferenceRequest))
        ).scalars().all()
        assert len(rows) == 1
        assert rows[0].state == RequestState.SUCCEEDED
        usages = (await session.execute(select(models.UsageRecord))).scalars().all()
        assert len(usages) == 1
        assert usages[0].request_units == 1
        assert usages[0].input_units == 0
        assert usages[0].output_units == 0
        ledgers = (await session.execute(select(models.LedgerEntry))).scalars().all()
        assert len(ledgers) == 1
        assert ledgers[0].entry_type == "usage_debit"
        assert ledgers[0].amount == Decimal("-0.02")


async def test_embedding_cancellation(embedding_scheduler):
    service, factory, _enc, mock, context = embedding_scheduler
    enq = await service.admit_embedding_and_enqueue(
        alias_name="embed-model", inputs=("hi",), context=context
    )
    await service.request_cancellation(enq.request_id)
    outcome = await service.claim_and_reserve("w1")
    # Already terminally cancelled: nothing left to claim.
    assert outcome is None or outcome == "idle" or outcome == "processed"
    assert mock.embed_calls == 0
    async with factory() as session:
        row = await sched_repo.get_request(session, enq.request_id)
    assert row.state in (RequestState.CANCELLED, RequestState.FAILED)
