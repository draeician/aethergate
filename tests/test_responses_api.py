"""Responses API surface tests (DB-gated, real scheduler + mock adapter).

Proves ``POST /v1/responses`` translates into the existing durable generation
authority (canonical messages -> ``admit_and_enqueue`` -> real worker dispatch)
and translates terminal/stream results back into pinned Responses shapes. Also
proves unsupported-feature rejection, cancellation, credential revocation, and
exactly-once accounting settlement.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from aethergate.adapters.base import (
    ChatRequest,
    CompletionResult,
    GenerationParams,
    StreamChunk,
    Usage,
)
from aethergate.api import deps as api_deps
from aethergate.config import Settings
from aethergate.contracts.responses import ResponsesRequest
from aethergate.dev_identity import ensure_dev_identity
from aethergate.domain import entities as domain
from aethergate.domain.entities import RequestContext
from aethergate.domain.enums import BillingUnit, Capability, PrincipalKind, RequestState
from aethergate.domain.ids import (
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
from aethergate.identity import service as identity_service
from aethergate.inference.service import InferenceService
from aethergate.main import app
from aethergate.persistence import db as persistence_db
from aethergate.persistence import models, repository
from aethergate.scheduler.service import ClaimedWork, SchedulingService
from aethergate.secrets import EnvSecretResolver
from db_helpers import TEST_DATABASE_URL, reset_schema

pytestmark = pytest.mark.skipif(
    TEST_DATABASE_URL is None, reason="AETHERGATE_TEST_DATABASE_URL not set"
)


class MockAdapter:
    """Deterministic adapter that records the dispatched canonical request."""

    def __init__(self, *, content: str = "Hello from mock") -> None:
        self._content = content
        self.requests: list[ChatRequest] = []

    async def complete(self, request: ChatRequest, secret: str | None) -> CompletionResult:
        self.requests.append(request)
        return CompletionResult(
            content=self._content,
            finish_reason="stop",
            usage=Usage(prompt_tokens=1, completion_tokens=2, total_tokens=3),
            upstream_request_id="upstream-mock-1",
        )

    async def stream(
        self, request: ChatRequest, secret: str | None
    ) -> AsyncIterator[StreamChunk]:
        self.requests.append(request)
        yield StreamChunk(content="Hello", finish_reason=None, usage=None)
        yield StreamChunk(
            content=" from mock",
            finish_reason="stop",
            usage=Usage(prompt_tokens=1, completion_tokens=2, total_tokens=3),
        )


class FailingAdapter:
    async def complete(self, request: ChatRequest, secret: str | None) -> CompletionResult:
        raise ProviderError("upstream exploded", status_code=500)

    async def stream(
        self, request: ChatRequest, secret: str | None
    ) -> AsyncIterator[StreamChunk]:
        raise ProviderError("upstream exploded", status_code=500)
        yield  # pragma: no cover


async def _seed(session) -> None:
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
            max_concurrency=2,
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


async def _drain(service: SchedulingService, worker_id: str) -> None:
    while True:
        outcome = await service.claim_and_reserve(worker_id)
        if isinstance(outcome, ClaimedWork):
            if outcome.stream:
                await service.run_stream(outcome)
            else:
                await service.run_complete(outcome)
        else:
            await asyncio.sleep(service.poll_interval)


def _build_service(sched_engine, adapter) -> SchedulingService:
    encryptor = QueueEncryptor(QueueEncryptor.generate_key())
    settings = Settings(
        database_url="postgresql+asyncpg://u:p@h/db",
        allow_inference_auth_bypass=True,
        upstream_allowlist="192.168.22.50",
    )
    inference = InferenceService(
        EnvSecretResolver(),
        DestinationPolicy(settings.upstream_allowlist_hosts),
        adapter_factory=lambda kind: adapter,
    )
    factory = async_sessionmaker(sched_engine, expire_on_commit=False)
    return SchedulingService(
        encryptor=encryptor,
        inference_service=inference,
        session_factory=factory,
        settings=settings,
    )


@pytest.fixture
async def api_client(sched_engine, monkeypatch):
    await reset_schema(sched_engine)
    async with async_sessionmaker(sched_engine, expire_on_commit=False)() as session:
        async with session.begin():
            await _seed(session)
            context = await ensure_dev_identity(session)

    mock = MockAdapter()
    service = _build_service(sched_engine, mock)
    settings = service._settings
    factory = async_sessionmaker(sched_engine, expire_on_commit=False)

    monkeypatch.setattr("aethergate.api.deps.get_settings", lambda: settings)
    app.dependency_overrides[api_deps.scheduler_service] = lambda: service
    app.dependency_overrides[api_deps.request_context] = lambda: context
    app.dependency_overrides[api_deps.require_inference_access] = lambda: None

    async def _test_session():
        async with factory() as session:
            yield session

    app.dependency_overrides[persistence_db.get_session] = _test_session

    worker = asyncio.create_task(_drain(service, "w-test"))

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, mock, service, factory
    worker.cancel()
    try:
        await worker
    except asyncio.CancelledError:
        pass
    app.dependency_overrides.clear()
    await reset_schema(sched_engine)


async def test_responses_non_stream_shape(api_client):
    client, mock, _service, _factory = api_client
    resp = await client.post("/v1/responses", json={"model": "gpt-4", "input": "hi"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["object"] == "response"
    assert data["status"] == "completed"
    assert data["model"] == "gpt-4"
    assert "qwen3.8" not in json.dumps(data)
    assert data["output"][0]["type"] == "message"
    assert data["output"][0]["role"] == "assistant"
    assert data["output"][0]["content"][0]["type"] == "output_text"
    assert data["output"][0]["content"][0]["text"] == "Hello from mock"
    assert data["usage"]["input_tokens"] == 1
    assert data["usage"]["output_tokens"] == 2
    assert data["usage"]["total_tokens"] == 3
    assert data["id"].startswith("resp_")
    assert resp.headers.get("x-upstream-request-id") == "upstream-mock-1"


async def test_responses_string_input_maps_to_user_message(api_client):
    client, mock, _service, _factory = api_client
    await client.post("/v1/responses", json={"model": "gpt-4", "input": "hello there"})
    assert len(mock.requests) == 1
    assert [(m.role, m.content) for m in mock.requests[0].messages] == [
        ("user", "hello there")
    ]


async def test_responses_instructions_and_input_items_map_in_order(api_client):
    client, mock, _service, _factory = api_client
    resp = await client.post(
        "/v1/responses",
        json={
            "model": "gpt-4",
            "instructions": "Be terse",
            "input": [
                {"role": "user", "content": "q1"},
                {"role": "assistant", "content": "a1"},
                {"role": "user", "content": [{"type": "input_text", "text": "q2"}]},
            ],
        },
    )
    assert resp.status_code == 200
    assert [(m.role, m.content) for m in mock.requests[0].messages] == [
        ("system", "Be terse"),
        ("user", "q1"),
        ("assistant", "a1"),
        ("user", "q2"),
    ]


async def test_responses_generation_controls_reach_provider(api_client):
    client, mock, _service, _factory = api_client
    resp = await client.post(
        "/v1/responses",
        json={
            "model": "gpt-4",
            "input": "hi",
            "max_output_tokens": 64,
            "temperature": 0.25,
            "top_p": 0.75,
        },
    )
    assert resp.status_code == 200
    params = mock.requests[0].params
    assert params.max_tokens == 64
    assert params.temperature == 0.25
    assert params.top_p == 0.75


async def test_responses_stream_event_sequence(api_client):
    client, _mock, _service, _factory = api_client
    async with client.stream(
        "POST",
        "/v1/responses",
        json={"model": "gpt-4", "input": "hi", "stream": True},
    ) as resp:
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")
        body = b""
        async for chunk in resp.aiter_bytes():
            body += chunk

    text = body.decode()
    assert "[DONE]" not in text
    events = [
        line[len("data: ") :]
        for line in text.splitlines()
        if line.startswith("data: ")
    ]
    parsed = [json.loads(e) for e in events]
    types = [e["type"] for e in parsed]
    assert types == [
        "response.created",
        "response.in_progress",
        "response.output_item.added",
        "response.content_part.added",
        "response.output_text.delta",
        "response.output_text.delta",
        "response.output_text.done",
        "response.content_part.done",
        "response.output_item.done",
        "response.completed",
    ]
    assert all(e["sequence_number"] == i for i, e in enumerate(parsed))
    deltas = "".join(e["delta"] for e in parsed if e["type"] == "response.output_text.delta")
    assert deltas == "Hello from mock"
    completed = parsed[-1]
    assert completed["response"]["status"] == "completed"
    assert completed["response"]["usage"]["total_tokens"] == 3
    # Stable IDs/indices.
    assert parsed[2]["item"]["id"] == parsed[-1]["response"]["output"][0]["id"]
    assert all(e.get("output_index", 0) == 0 for e in parsed)


async def test_responses_unsupported_tools_rejected_and_no_dispatch(api_client):
    client, _mock, _service, factory = api_client
    resp = await client.post(
        "/v1/responses",
        json={"model": "gpt-4", "input": "hi", "tools": [{"type": "function", "name": "x"}]},
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "unsupported_feature"
    async with factory() as session:
        count = int(
            (
                await session.execute(
                    select(func.count()).select_from(models.InferenceRequest)
                )
            ).scalar_one()
        )
    assert count == 0


async def test_responses_transport_override_rejected(api_client):
    client, *_ = api_client
    resp = await client.post(
        "/v1/responses",
        json={"model": "gpt-4", "input": "hi", "api_base": "http://evil.example.com"},
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "unsupported_feature"


async def test_responses_unknown_model_404(api_client):
    client, *_ = api_client
    resp = await client.post("/v1/responses", json={"model": "nope", "input": "hi"})
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "model_not_found"


async def test_official_openai_sdk_responses_non_stream(api_client):
    import openai

    client, *_ = api_client
    http_client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app))
    sdk = openai.AsyncOpenAI(base_url="http://test/v1", api_key="dummy", http_client=http_client)
    try:
        response = await sdk.responses.create(model="gpt-4", input="hi")
        assert response.object == "response"
        assert response.status == "completed"
        assert response.model == "gpt-4"
        assert response.output_text == "Hello from mock"
        assert response.usage is not None
        assert response.usage.total_tokens == 3
    finally:
        await http_client.aclose()


async def test_official_openai_sdk_responses_stream(api_client):
    import openai
    from openai.types.responses import (
        ResponseCompletedEvent,
        ResponseOutputItemAddedEvent,
        ResponseTextDeltaEvent,
    )

    client, *_ = api_client
    http_client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app))
    sdk = openai.AsyncOpenAI(base_url="http://test/v1", api_key="dummy", http_client=http_client)
    try:
        stream = await sdk.responses.create(model="gpt-4", input="hi", stream=True)
        parts: list[str] = []
        saw_completed = False
        async for event in stream:
            if isinstance(event, ResponseTextDeltaEvent):
                parts.append(event.delta)
            if isinstance(event, ResponseOutputItemAddedEvent):
                assert event.item.type == "message"
            if isinstance(event, ResponseCompletedEvent):
                saw_completed = True
                assert event.response.status == "completed"
        assert "".join(parts) == "Hello from mock"
        assert saw_completed
    finally:
        await http_client.aclose()


async def test_official_openai_sdk_responses_unsupported_tools_error(api_client):
    import openai

    client, *_ = api_client
    http_client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app))
    sdk = openai.AsyncOpenAI(base_url="http://test/v1", api_key="dummy", http_client=http_client)
    try:
        with pytest.raises(openai.BadRequestError):
            await sdk.responses.create(
                model="gpt-4",
                input="hi",
                tools=[{"type": "function", "name": "x", "parameters": {}}],
            )
    finally:
        await http_client.aclose()


# --- scheduler integration ---------------------------------------------------


async def _enqueue_responses(service, context, *, alias="gpt-4", stream=False, n=1):
    ids = []
    for i in range(n):
        req = ResponsesRequest.model_validate(
            {"model": alias, "input": f"msg-{i}", "stream": stream}
        )
        enq = await service.admit_and_enqueue(
            alias_name=req.model,
            messages=req.to_canonical_messages(),
            params=GenerationParams(max_tokens=req.max_output_tokens),
            stream=stream,
            context=context,
        )
        ids.append(enq.request_id)
    return ids


async def test_responses_six_requests_two_slots(sched_engine):
    await reset_schema(sched_engine)
    async with async_sessionmaker(sched_engine, expire_on_commit=False)() as session:
        async with session.begin():
            await _seed(session)
            context = await ensure_dev_identity(session)

    mock = MockAdapter()
    service = _build_service(sched_engine, mock)
    factory = async_sessionmaker(sched_engine, expire_on_commit=False)
    ids = await _enqueue_responses(service, context, n=6)

    a = await service.claim_and_reserve("w1")
    b = await service.claim_and_reserve("w2")
    assert not isinstance(a, str) and a is not None
    assert not isinstance(b, str) and b is not None
    assert await service.claim_and_reserve("w1") == "full"

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
    await reset_schema(sched_engine)


async def test_responses_provider_failure_does_not_fake_success(sched_engine):
    await reset_schema(sched_engine)
    async with async_sessionmaker(sched_engine, expire_on_commit=False)() as session:
        async with session.begin():
            await _seed(session)
            context = await ensure_dev_identity(session)

    service = _build_service(sched_engine, FailingAdapter())
    ids = await _enqueue_responses(service, context, n=1)
    outcome = await service.claim_and_reserve("w1")
    assert not isinstance(outcome, str) and outcome is not None
    await service.run_complete(outcome)
    state = await service.get_state(ids[0])
    assert state == RequestState.FAILED
    await reset_schema(sched_engine)


async def test_responses_queued_cancel_never_dispatches(sched_engine):
    await reset_schema(sched_engine)
    async with async_sessionmaker(sched_engine, expire_on_commit=False)() as session:
        async with session.begin():
            await _seed(session)
            context = await ensure_dev_identity(session)

    mock = MockAdapter()
    service = _build_service(sched_engine, mock)
    ids = await _enqueue_responses(service, context, n=1)
    await service.request_cancellation(ids[0])
    outcome = await service.claim_and_reserve("w1")
    assert outcome is None
    assert mock.requests == []
    await reset_schema(sched_engine)


async def test_responses_post_dispatch_cancellation_requests(sched_engine):
    await reset_schema(sched_engine)
    async with async_sessionmaker(sched_engine, expire_on_commit=False)() as session:
        async with session.begin():
            await _seed(session)
            context = await ensure_dev_identity(session)

    mock = MockAdapter()
    service = _build_service(sched_engine, mock)
    ids = await _enqueue_responses(service, context, n=1)
    outcome = await service.claim_and_reserve("w1")
    assert not isinstance(outcome, str) and outcome is not None
    await service.request_cancellation(ids[0])
    assert await service.is_cancelled(ids[0]) is True
    await reset_schema(sched_engine)


async def test_responses_revoked_queued_credential_never_dispatches(sched_engine):
    await reset_schema(sched_engine)
    async with async_sessionmaker(sched_engine, expire_on_commit=False)() as session:
        async with session.begin():
            await _seed(session)
            project = await repository.create_project(
                session, domain.Project(id=ProjectId("proj-1"), name="proj-1")
            )
            principal = await repository.create_principal(
                session,
                domain.Principal(
                    id=PrincipalId("prin-1"),
                    project_id=project.id,
                    kind=PrincipalKind.SERVICE_ACCOUNT,
                    name="svc",
                ),
            )
            credential, _raw = await identity_service.create_credential(
                session, project_id=project.id, principal_id=principal.id, name="k"
            )
            context = RequestContext(
                project_id=project.id,
                principal_id=principal.id,
                api_credential_id=credential.id,
                audience=credential.audience,
                scopes=credential.scopes,
            )
            credential_id = credential.id

    mock = MockAdapter()
    service = _build_service(sched_engine, mock)
    await _enqueue_responses(service, context, n=1)

    # Revoke while queued, then attempt dispatch.
    async with async_sessionmaker(sched_engine, expire_on_commit=False)() as session:
        async with session.begin():
            await identity_service.revoke_credential(session, credential_id)

    while True:
        outcome = await service.claim_and_reserve("w1")
        if outcome is None or isinstance(outcome, str):
            break
    assert mock.requests == []
    await reset_schema(sched_engine)


async def test_responses_exactly_once_accounting_settlement(sched_engine):
    await reset_schema(sched_engine)
    async with async_sessionmaker(sched_engine, expire_on_commit=False)() as session:
        async with session.begin():
            await _seed(session)
            await repository.create_price_policy(
                session,
                domain.PricePolicy(
                    id=PricePolicyId("pp-rb-gpt4"),
                    route_binding_id=RouteBindingId("rb-gpt4"),
                    billing_unit=BillingUnit.REQUEST,
                    currency="USD",
                    unit_scale=1,
                    request_price=Decimal("0.05"),
                ),
            )
            context = await ensure_dev_identity(session)

    mock = MockAdapter()
    service = _build_service(sched_engine, mock)
    factory = async_sessionmaker(sched_engine, expire_on_commit=False)
    await _enqueue_responses(service, context, n=1)
    outcome = await service.claim_and_reserve("w1")
    assert not isinstance(outcome, str) and outcome is not None
    await service.run_complete(outcome)

    async with factory() as session:
        usage_records = (await session.execute(select(models.UsageRecord))).scalars().all()
        ledgers = (await session.execute(select(models.LedgerEntry))).scalars().all()
        snapshots = (await session.execute(select(models.PriceSnapshot))).scalars().all()
    assert len(usage_records) == 1
    assert len(snapshots) == 1
    assert len(ledgers) == 1
    assert ledgers[0].entry_type == "usage_debit"
    assert ledgers[0].amount == Decimal("-0.05")
    await reset_schema(sched_engine)
