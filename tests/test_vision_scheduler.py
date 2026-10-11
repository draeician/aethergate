"""Vision requests through the existing durable scheduler authority (DB-gated).

Proves image-bearing requests reuse the exact scheduler/accounting/worker path:
durable queueing, endpoint max_concurrency, encrypted-at-rest payloads, image
capability enforcement (alias + provider, no fallback), dispatch-time capability
revalidation, and the token-quota/budget fail-closed rule for images.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from aethergate.adapters.base import (
    ChatRequest,
    CompletionResult,
    GenerationParams,
    ImageContentPart,
    Message,
    StreamChunk,
    TextContentPart,
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
from aethergate.errors import ResourceInactive
from aethergate.inference.service import InferenceService
from aethergate.persistence import models, repository
from aethergate.scheduler import repository as sched_repo
from aethergate.scheduler.service import SchedulingService
from aethergate.secrets import EnvSecretResolver
from db_helpers import TEST_DATABASE_URL, reset_schema
from vision_fixtures import PNG_BYTES

pytestmark = pytest.mark.skipif(
    TEST_DATABASE_URL is None, reason="AETHERGATE_TEST_DATABASE_URL not set"
)


class MockAdapter:
    def __init__(self, *, content: str = "vision-ok") -> None:
        self._content = content
        self.complete_calls = 0

    async def complete(self, request: ChatRequest, secret: str | None) -> CompletionResult:
        self.complete_calls += 1
        return CompletionResult(
            content=self._content,
            finish_reason="stop",
            usage=Usage(prompt_tokens=1, completion_tokens=2, total_tokens=3),
            upstream_request_id=f"upstream-{self.complete_calls}",
        )

    async def stream(
        self, request: ChatRequest, secret: str | None
    ) -> AsyncIterator[StreamChunk]:
        yield StreamChunk(content=self._content, finish_reason="stop", usage=None)

    def estimate_input_tokens(self, request: ChatRequest) -> int | None:
        # No defensible image token estimator: fail closed for image requests.
        if any(m.has_images for m in request.messages):
            return None
        return 4


def _vision_message() -> Message:
    return Message(
        role="user",
        content="describe ",
        parts=(
            TextContentPart(text="describe "),
            ImageContentPart("image/png", PNG_BYTES),
        ),
    )


async def _seed(session) -> None:
    await repository.create_provider(
        session,
        domain.Provider(
            id=ProviderId("prov-ollama"), kind="ollama", name="ollama",
            capabilities=(Capability.TEXT, Capability.IMAGE),
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
    # Image-capable alias.
    await repository.create_model_alias(
        session,
        domain.ModelAlias(
            id=ModelAliasId("alias-vision"),
            name="vision-model",
            capabilities=(Capability.TEXT, Capability.IMAGE),
        ),
    )
    await repository.create_route_binding(
        session,
        domain.RouteBinding(
            id=RouteBindingId("rb-vision"),
            model_alias_id=ModelAliasId("alias-vision"),
            endpoint_id=EndpointId("ep-ollama"),
            provider_account_id=ProviderAccountId("acct-ollama"),
            upstream_model="vision-upstream",
        ),
    )
    # Text-only alias shares the endpoint.
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
            upstream_model="text-upstream",
        ),
    )


@pytest.fixture
async def vision_scheduler(sched_engine):
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


async def test_vision_six_requests_two_slots_queue_and_drain(vision_scheduler):
    service, factory, _enc, mock, context = vision_scheduler
    ids = []
    for _ in range(6):
        enq = await service.admit_and_enqueue(
            alias_name="vision-model",
            messages=[_vision_message()],
            params=GenerationParams(),
            stream=False,
            context=context,
        )
        ids.append(enq.request_id)

    a = await service.claim_and_reserve("w1")
    b = await service.claim_and_reserve("w2")
    assert not isinstance(a, str) and a is not None
    assert not isinstance(b, str) and b is not None

    c = await service.claim_and_reserve("w1")
    assert c == "full"
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
    assert mock.complete_calls == 6


async def test_vision_payload_encrypted_at_rest(vision_scheduler):
    service, factory, _enc, _mock, context = vision_scheduler
    enq = await service.admit_and_enqueue(
        alias_name="vision-model",
        messages=[_vision_message()],
        params=GenerationParams(),
        stream=False,
        context=context,
    )
    async with factory() as session:
        row = await sched_repo.get_request(session, enq.request_id)
        assert row.payload_encrypted is not None
        assert b"describe" not in row.payload_encrypted
        assert PNG_BYTES not in row.payload_encrypted


async def test_image_on_text_only_alias_rejected_before_dispatch(vision_scheduler):
    service, factory, _enc, _mock, context = vision_scheduler
    with pytest.raises(ResourceInactive):
        await service.admit_and_enqueue(
            alias_name="text-model",
            messages=[_vision_message()],
            params=GenerationParams(),
            stream=False,
            context=context,
        )
    async with factory() as session:
        # No durable request was created for the rejected image request.
        count = (await session.execute(select(models.InferenceRequest))).scalars().all()
    assert count == []


async def test_image_rejected_when_provider_lacks_image_capability(vision_scheduler):
    service, factory, _enc, _mock, context = vision_scheduler
    async with factory() as session:
        async with session.begin():
            await repository.update_provider(
                session, ProviderId("prov-ollama"), capabilities=(Capability.TEXT,)
            )
    with pytest.raises(ResourceInactive):
        await service.admit_and_enqueue(
            alias_name="vision-model",
            messages=[_vision_message()],
            params=GenerationParams(),
            stream=False,
            context=context,
        )


async def test_text_only_request_still_works_without_image_capability(vision_scheduler):
    service, _factory, _enc, _mock, context = vision_scheduler
    # Text request against the text-only alias must not regress.
    enq = await service.admit_and_enqueue(
        alias_name="text-model",
        messages=[Message(role="user", content="hello")],
        params=GenerationParams(),
        stream=False,
        context=context,
    )
    outcome = await service.claim_and_reserve("w1")
    assert not isinstance(outcome, str) and outcome is not None
    await service.run_complete(outcome)
    state = await service.get_state(enq.request_id)
    assert state == RequestState.SUCCEEDED


async def test_dispatch_time_image_capability_revocation_blocks_dispatch(vision_scheduler):
    """Capability revoked while queued must fail the request before upstream."""
    service, factory, _enc, mock, context = vision_scheduler
    enq = await service.admit_and_enqueue(
        alias_name="vision-model",
        messages=[_vision_message()],
        params=GenerationParams(),
        stream=False,
        context=context,
    )
    # Revoke image capability on the provider while the request is queued.
    async with factory() as session:
        async with session.begin():
            await repository.update_provider(
                session, ProviderId("prov-ollama"), capabilities=(Capability.TEXT,)
            )
    outcome = await service.claim_and_reserve("w1")
    assert outcome == "processed"
    assert mock.complete_calls == 0
    state = await service.get_state(enq.request_id)
    assert state == RequestState.FAILED


async def test_token_priced_budget_on_image_request_fails_closed(vision_scheduler):
    """No defensible image token estimator -> token-priced budget fails closed."""
    service, factory, _enc, mock, context = vision_scheduler
    from decimal import Decimal

    from aethergate.domain.enums import BillingUnit
    from aethergate.domain.ids import BudgetPolicyId, PricePolicyId

    async with factory() as session:
        async with session.begin():
            await repository.create_price_policy(
                session,
                domain.PricePolicy(
                    id=PricePolicyId("pp-vision"),
                    route_binding_id=RouteBindingId("rb-vision"),
                    billing_unit=BillingUnit.TOKEN,
                    currency="USD",
                    unit_scale=1_000_000,
                    input_price=Decimal("0.01"),
                    output_price=Decimal("0.01"),
                ),
            )
            await repository.create_project_budget_policy(
                session,
                domain.ProjectBudgetPolicy(
                    id=BudgetPolicyId("bp-vision"),
                    project_id=context.project_id,
                    name="vision-budget",
                    currency="USD",
                    limit_amount=Decimal("100"),
                    window_seconds=3600,
                ),
            )
    enq = await service.admit_and_enqueue(
        alias_name="vision-model",
        messages=[_vision_message()],
        params=GenerationParams(max_tokens=10),
        stream=False,
        context=context,
    )
    outcome = await service.claim_and_reserve("w1")
    assert outcome == "processed"
    assert mock.complete_calls == 0
    async with factory() as session:
        row = await sched_repo.get_request(session, enq.request_id)
    assert row.state == RequestState.FAILED
    assert row.error_code == "budget_token_estimator_unavailable"
