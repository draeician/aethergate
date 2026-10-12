"""OpenAI-compatible Embeddings API surface tests (DB-gated, scheduler + mock)."""

from __future__ import annotations

import asyncio
import base64
import struct
from collections.abc import AsyncIterator

import httpx
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from aethergate.adapters.base import (
    ChatRequest,
    CompletionResult,
    EmbeddingRequest,
    EmbeddingResult,
    StreamChunk,
    Usage,
)
from aethergate.api import deps as api_deps
from aethergate.config import Settings
from aethergate.contracts.embeddings import MAX_EMBEDDING_INPUTS
from aethergate.dev_identity import ensure_dev_identity
from aethergate.domain import entities as domain
from aethergate.domain.enums import Capability
from aethergate.domain.ids import (
    EndpointId,
    ModelAliasId,
    ProviderAccountId,
    ProviderId,
    RouteBindingId,
)
from aethergate.egress import DestinationPolicy
from aethergate.encryption import QueueEncryptor
from aethergate.inference.service import InferenceService
from aethergate.main import app
from aethergate.persistence import db as persistence_db
from aethergate.persistence import repository
from aethergate.scheduler.service import ClaimedWork, SchedulingService
from aethergate.secrets import EnvSecretResolver
from db_helpers import TEST_DATABASE_URL, reset_schema

pytestmark = pytest.mark.skipif(
    TEST_DATABASE_URL is None, reason="AETHERGATE_TEST_DATABASE_URL not set"
)


class MockEmbeddingAdapter:
    supports_embedding = True

    def __init__(self) -> None:
        self.embed_calls = 0

    async def embed(self, request: EmbeddingRequest, secret: str | None) -> EmbeddingResult:
        self.embed_calls += 1
        vectors = tuple(
            (float(index + 1), float(index + 1) + 0.5)
            for index in range(len(request.inputs))
        )
        return EmbeddingResult(
            vectors=vectors,
            usage=Usage(prompt_tokens=7, completion_tokens=0, total_tokens=7),
            upstream_request_id="upstream-embed-1",
        )

    async def complete(self, request: ChatRequest, secret: str | None) -> CompletionResult:
        return CompletionResult(
            content="unused",
            finish_reason="stop",
            usage=Usage(prompt_tokens=1, completion_tokens=2, total_tokens=3),
            upstream_request_id="unused",
        )

    async def stream(
        self, request: ChatRequest, secret: str | None
    ) -> AsyncIterator[StreamChunk]:
        yield StreamChunk(content="unused", finish_reason="stop", usage=None)


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
            id=RouteBindingId("rb-embed"),
            model_alias_id=ModelAliasId("alias-embed"),
            endpoint_id=EndpointId("ep-ollama"),
            provider_account_id=ProviderAccountId("acct-ollama"),
            upstream_model="nomic-embed-text",
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


async def _drain(service: SchedulingService, worker_id: str) -> None:
    while True:
        outcome = await service.claim_and_reserve(worker_id)
        if isinstance(outcome, ClaimedWork):
            await service.run_complete(outcome)
        else:
            await asyncio.sleep(service.poll_interval)


@pytest.fixture
async def api_client(sched_engine, monkeypatch):
    await reset_schema(sched_engine)
    async with async_sessionmaker(sched_engine, expire_on_commit=False)() as session:
        async with session.begin():
            await _seed(session)
            context = await ensure_dev_identity(session)

    mock = MockEmbeddingAdapter()
    encryptor = QueueEncryptor(QueueEncryptor.generate_key())
    settings = Settings(
        database_url="postgresql+asyncpg://u:p@h/db",
        allow_inference_auth_bypass=True,
        upstream_allowlist="192.168.22.50",
    )
    factory = async_sessionmaker(sched_engine, expire_on_commit=False)
    inference = InferenceService(
        EnvSecretResolver(),
        DestinationPolicy(settings.upstream_allowlist_hosts),
        adapter_factory=lambda kind: mock,
    )
    service = SchedulingService(
        encryptor=encryptor,
        inference_service=inference,
        session_factory=factory,
        settings=settings,
    )

    monkeypatch.setattr("aethergate.api.deps.get_settings", lambda: settings)
    app.dependency_overrides[api_deps.scheduler_service] = lambda: service
    app.dependency_overrides[api_deps.request_context] = lambda: context
    app.dependency_overrides[api_deps.require_inference_access] = lambda: None

    async def _test_session():
        factory = async_sessionmaker(sched_engine, expire_on_commit=False)
        async with factory() as session:
            yield session

    app.dependency_overrides[persistence_db.get_session] = _test_session

    worker = asyncio.create_task(_drain(service, "w-test"))

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, mock
    worker.cancel()
    try:
        await worker
    except asyncio.CancelledError:
        pass
    app.dependency_overrides.clear()
    await reset_schema(sched_engine)


async def test_embeddings_float_shape(api_client):
    client, _ = api_client
    resp = await client.post(
        "/v1/embeddings",
        json={"model": "embed-model", "input": ["a", "b"], "encoding_format": "float"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["object"] == "list"
    assert data["model"] == "embed-model"
    assert "nomic-embed-text" not in str(data)
    assert [d["index"] for d in data["data"]] == [0, 1]
    assert data["data"][0]["embedding"] == [1.0, 1.5]
    assert data["data"][1]["embedding"] == [2.0, 2.5]
    assert data["usage"]["prompt_tokens"] == 7
    assert data["usage"]["total_tokens"] == 7
    assert "completion_tokens" not in data["usage"]


async def test_embeddings_base64_encoding(api_client):
    client, _ = api_client
    resp = await client.post(
        "/v1/embeddings",
        json={"model": "embed-model", "input": ["a"], "encoding_format": "base64"},
    )
    assert resp.status_code == 200
    entry = resp.json()["data"][0]
    assert isinstance(entry["embedding"], str)
    decoded = struct.unpack("<2f", base64.b64decode(entry["embedding"]))
    assert decoded == (1.0, 1.5)


async def test_embeddings_single_string_input(api_client):
    client, _ = api_client
    resp = await client.post(
        "/v1/embeddings",
        json={"model": "embed-model", "input": "hello"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["data"]) == 1
    assert data["data"][0]["index"] == 0
    # Default encoding is float.
    assert isinstance(data["data"][0]["embedding"], list)


async def test_embeddings_token_ids_rejected(api_client):
    client, _ = api_client
    resp = await client.post(
        "/v1/embeddings",
        json={"model": "embed-model", "input": [1, 2, 3]},
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "unsupported_feature"


async def test_embeddings_dimensions_rejected(api_client):
    client, _ = api_client
    resp = await client.post(
        "/v1/embeddings",
        json={"model": "embed-model", "input": "hi", "dimensions": 128},
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "unsupported_feature"


async def test_embeddings_user_rejected(api_client):
    client, _ = api_client
    resp = await client.post(
        "/v1/embeddings",
        json={"model": "embed-model", "input": "hi", "user": "u-1"},
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "unsupported_feature"


async def test_embeddings_transport_override_rejected(api_client):
    client, _ = api_client
    resp = await client.post(
        "/v1/embeddings",
        json={"model": "embed-model", "input": "hi", "api_base": "http://evil"},
    )
    assert resp.status_code == 400


async def test_embeddings_capability_mismatch_returns_400(api_client):
    client, _ = api_client
    resp = await client.post(
        "/v1/embeddings",
        json={"model": "text-model", "input": "hi"},
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "model_unavailable"


async def test_embeddings_encoding_format_null_rejected_without_dispatch(api_client):
    client, mock = api_client
    resp = await client.post(
        "/v1/embeddings",
        json={"model": "embed-model", "input": "hi", "encoding_format": None},
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "invalid_request"
    assert mock.embed_calls == 0


async def test_embeddings_dimensions_null_rejected_without_dispatch(api_client):
    client, mock = api_client
    resp = await client.post(
        "/v1/embeddings",
        json={"model": "embed-model", "input": "hi", "dimensions": None},
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "unsupported_feature"
    assert mock.embed_calls == 0


async def test_embeddings_user_null_rejected_without_dispatch(api_client):
    client, mock = api_client
    resp = await client.post(
        "/v1/embeddings",
        json={"model": "embed-model", "input": "hi", "user": None},
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "unsupported_feature"
    assert mock.embed_calls == 0


async def test_embeddings_input_count_over_max_rejected_without_dispatch(api_client):
    client, mock = api_client
    resp = await client.post(
        "/v1/embeddings",
        json={"model": "embed-model", "input": ["x"] * (MAX_EMBEDDING_INPUTS + 1)},
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "invalid_request"
    assert mock.embed_calls == 0


async def test_embeddings_input_count_at_max_succeeds(api_client):
    client, mock = api_client
    resp = await client.post(
        "/v1/embeddings",
        json={"model": "embed-model", "input": ["x"] * MAX_EMBEDDING_INPUTS},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["data"]) == MAX_EMBEDDING_INPUTS
    assert data["data"][-1]["index"] == MAX_EMBEDDING_INPUTS - 1
    assert mock.embed_calls == 1


async def test_official_openai_sdk_embeddings(api_client):
    import openai

    client, _ = api_client
    http_client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app))
    sdk = openai.AsyncOpenAI(
        base_url="http://test/v1", api_key="dummy", http_client=http_client
    )
    try:
        result = await sdk.embeddings.create(
            model="embed-model", input=["alpha", "beta", "gamma"]
        )
        assert result.model == "embed-model"
        assert [d.index for d in result.data] == [0, 1, 2]
        assert all(len(d.embedding) == 2 for d in result.data)
        assert result.usage.prompt_tokens == 7
    finally:
        await http_client.aclose()
