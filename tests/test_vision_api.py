"""Vision input through the API surfaces via the durable scheduler (DB-gated).

Exercises the full scheduler-admitted path for image-bearing requests on both
Chat Completions and Responses, non-stream and stream, with a mock adapter that
records the canonical message it receives (proving image parts survive to the
adapter and are not reduced to a text placeholder).
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator

import httpx
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from aethergate.adapters.base import (
    ChatRequest,
    CompletionResult,
    ImageContentPart,
    StreamChunk,
    Usage,
)
from aethergate.api import deps as api_deps
from aethergate.config import Settings
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
from vision_fixtures import JPEG_DATA_URL, PNG_DATA_URL

pytestmark = pytest.mark.skipif(
    TEST_DATABASE_URL is None, reason="AETHERGATE_TEST_DATABASE_URL not set"
)


class RecordingAdapter:
    def __init__(self, *, content: str = "vision text") -> None:
        self._content = content
        self.requests: list[ChatRequest] = []

    async def complete(self, request: ChatRequest, secret: str | None) -> CompletionResult:
        self.requests.append(request)
        return CompletionResult(
            content=self._content,
            finish_reason="stop",
            usage=Usage(prompt_tokens=3, completion_tokens=2, total_tokens=5),
            upstream_request_id="upstream-vision-1",
        )

    async def stream(
        self, request: ChatRequest, secret: str | None
    ) -> AsyncIterator[StreamChunk]:
        self.requests.append(request)
        yield StreamChunk(content="vision ", finish_reason=None, usage=None)
        yield StreamChunk(
            content="text",
            finish_reason="stop",
            usage=Usage(prompt_tokens=3, completion_tokens=2, total_tokens=5),
        )


async def _seed(session) -> None:
    await repository.create_provider(
        session,
        domain.Provider(
            id=ProviderId("prov-ollama"),
            kind="ollama",
            name="ollama",
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


@pytest.fixture
async def vision_client(sched_engine):
    await reset_schema(sched_engine)
    async with async_sessionmaker(sched_engine, expire_on_commit=False)() as session:
        async with session.begin():
            await _seed(session)
            context = await ensure_dev_identity(session)

    adapter = RecordingAdapter()
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
        adapter_factory=lambda kind: adapter,
    )
    service = SchedulingService(
        encryptor=encryptor,
        inference_service=inference,
        session_factory=factory,
        settings=settings,
    )

    monkeypatch_settings = settings

    import aethergate.api.deps as deps_module

    original_get_settings = deps_module.get_settings
    deps_module.get_settings = lambda: monkeypatch_settings
    app.dependency_overrides[api_deps.scheduler_service] = lambda: service
    app.dependency_overrides[api_deps.request_context] = lambda: context
    app.dependency_overrides[api_deps.require_inference_access] = lambda: None

    async def _test_session():
        f = async_sessionmaker(sched_engine, expire_on_commit=False)
        async with f() as session:
            yield session

    app.dependency_overrides[persistence_db.get_session] = _test_session

    worker = asyncio.create_task(_drain(service, "w-test"))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, adapter
    worker.cancel()
    try:
        await worker
    except asyncio.CancelledError:
        pass
    app.dependency_overrides.clear()
    deps_module.get_settings = original_get_settings
    await reset_schema(sched_engine)


def _chat_vision_body(model: str = "vision-model", *, stream: bool = False) -> dict:
    return {
        "model": model,
        "stream": stream,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "describe "},
                    {"type": "image_url", "image_url": {"url": PNG_DATA_URL}},
                ],
            }
        ],
    }


def _responses_vision_body(model: str = "vision-model", *, stream: bool = False) -> dict:
    return {
        "model": model,
        "stream": stream,
        "input": [
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": "describe "},
                    {"type": "input_image", "image_url": JPEG_DATA_URL, "detail": "auto"},
                ],
            }
        ],
    }


async def test_chat_vision_non_stream(vision_client):
    client, adapter = vision_client
    resp = await client.post("/v1/chat/completions", json=_chat_vision_body())
    assert resp.status_code == 200
    data = resp.json()
    assert data["choices"][0]["message"]["content"] == "vision text"
    assert data["usage"]["total_tokens"] == 5
    # The image reached the adapter as canonical image content parts.
    parts = adapter.requests[0].messages[0].parts
    assert any(isinstance(p, ImageContentPart) for p in parts)


async def test_chat_vision_stream(vision_client):
    client, _adapter = vision_client
    async with client.stream(
        "POST", "/v1/chat/completions", json=_chat_vision_body(stream=True)
    ) as resp:
        assert resp.status_code == 200
        body = b""
        async for chunk in resp.aiter_bytes():
            body += chunk
    text = body.decode()
    assert "data: [DONE]" in text
    deltas = "".join(
        json.loads(line[len("data: "):])["choices"][0]["delta"].get("content") or ""
        for line in text.splitlines()
        if line.startswith("data: ") and line != "data: [DONE]"
    )
    assert deltas == "vision text"


async def test_responses_vision_non_stream(vision_client):
    client, _adapter = vision_client
    resp = await client.post("/v1/responses", json=_responses_vision_body())
    assert resp.status_code == 200
    data = resp.json()
    assert data["object"] == "response"
    assert data["status"] == "completed"
    assert data["output"][0]["content"][0]["text"] == "vision text"


async def test_responses_vision_stream(vision_client):
    client, _adapter = vision_client
    async with client.stream(
        "POST", "/v1/responses", json=_responses_vision_body(stream=True)
    ) as resp:
        assert resp.status_code == 200
        body = b""
        async for chunk in resp.aiter_bytes():
            body += chunk
    text = body.decode()
    assert "response.completed" in text
    assert "event: response.output_text.delta" in text
    combined = "".join(
        json.loads(line[len("data: "):])["delta"]
        for line in text.splitlines()
        if line.startswith("data: ") and '"delta"' in line
    )
    assert combined == "vision text"


async def test_chat_vision_remote_url_returns_400(vision_client):
    client, _adapter = vision_client
    body = _chat_vision_body()
    body["messages"][0]["content"][1]["image_url"]["url"] = "https://evil.example/x.png"
    resp = await client.post("/v1/chat/completions", json=body)
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "unsupported_feature"


async def test_responses_vision_file_id_returns_400(vision_client):
    client, _adapter = vision_client
    body = _responses_vision_body()
    body["input"][0]["content"][1] = {"type": "input_image", "file_id": "file-1"}
    resp = await client.post("/v1/responses", json=body)
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "unsupported_feature"


async def test_chat_image_on_text_only_alias_rejected(vision_client):
    client, adapter = vision_client
    resp = await client.post(
        "/v1/chat/completions", json=_chat_vision_body(model="text-model")
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "model_unavailable"
    assert adapter.requests == []


async def test_chat_auto_detail_succeeds_and_reaches_adapter(vision_client):
    client, adapter = vision_client
    body = _chat_vision_body()
    body["messages"][0]["content"][1]["image_url"]["detail"] = "auto"
    resp = await client.post("/v1/chat/completions", json=body)
    assert resp.status_code == 200
    assert adapter.requests  # reached the adapter


async def test_chat_omitted_detail_succeeds_and_reaches_adapter(vision_client):
    client, adapter = vision_client
    resp = await client.post("/v1/chat/completions", json=_chat_vision_body())
    assert resp.status_code == 200
    assert adapter.requests


async def test_chat_explicit_null_detail_returns_400_without_dispatch(vision_client):
    # Explicit JSON null is distinct from omission; Chat is non-nullable.
    client, adapter = vision_client
    body = _chat_vision_body()
    body["messages"][0]["content"][1]["image_url"]["detail"] = None
    resp = await client.post("/v1/chat/completions", json=body)
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "unsupported_feature"
    assert adapter.requests == []  # never reached the adapter/upstream


async def test_responses_explicit_null_detail_tolerated_and_dispatches(vision_client):
    client, adapter = vision_client
    body = _responses_vision_body()
    body["input"][0]["content"][1]["detail"] = None
    resp = await client.post("/v1/responses", json=body)
    assert resp.status_code == 200
    assert adapter.requests


@pytest.mark.parametrize("detail", ["low", "high", "original"])
async def test_chat_non_default_detail_returns_400_without_dispatch(
    vision_client, detail
):
    client, adapter = vision_client
    body = _chat_vision_body()
    body["messages"][0]["content"][1]["image_url"]["detail"] = detail
    resp = await client.post("/v1/chat/completions", json=body)
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "unsupported_feature"
    assert detail in resp.json()["error"]["message"]
    assert adapter.requests == []  # never reached the adapter/upstream


async def test_responses_auto_detail_succeeds_and_reaches_adapter(vision_client):
    client, adapter = vision_client
    body = _responses_vision_body()
    assert body["input"][0]["content"][1]["detail"] == "auto"
    resp = await client.post("/v1/responses", json=body)
    assert resp.status_code == 200
    assert adapter.requests


@pytest.mark.parametrize("detail", ["low", "high", "original"])
async def test_responses_non_default_detail_returns_400_without_dispatch(
    vision_client, detail
):
    client, adapter = vision_client
    body = _responses_vision_body()
    body["input"][0]["content"][1]["detail"] = detail
    resp = await client.post("/v1/responses", json=body)
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "unsupported_feature"
    assert adapter.requests == []


async def test_chat_text_only_still_works(vision_client):
    client, _adapter = vision_client
    resp = await client.post(
        "/v1/chat/completions",
        json={"model": "text-model", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert resp.status_code == 200
    assert resp.json()["choices"][0]["message"]["content"] == "vision text"


async def test_official_sdk_chat_vision_non_stream(vision_client):
    import openai

    client, _adapter = vision_client
    http_client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app))
    sdk = openai.AsyncOpenAI(
        base_url="http://test/v1", api_key="dummy", http_client=http_client
    )
    try:
        completion = await sdk.chat.completions.create(
            model="vision-model",
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "describe "},
                        {
                            "type": "image_url",
                            "image_url": {"url": PNG_DATA_URL},
                        },
                    ],
                }
            ],
        )
        assert completion.choices[0].message.content == "vision text"
    finally:
        await http_client.aclose()


async def test_official_sdk_responses_vision_stream(vision_client):
    import openai
    from openai.types.responses import (
        ResponseCompletedEvent,
        ResponseTextDeltaEvent,
    )

    client, _adapter = vision_client
    http_client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app))
    sdk = openai.AsyncOpenAI(
        base_url="http://test/v1", api_key="dummy", http_client=http_client
    )
    try:
        stream = await sdk.responses.create(
            model="vision-model",
            input=[
                {
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": "describe "},
                        {"type": "input_image", "image_url": PNG_DATA_URL},
                    ],
                }
            ],
            stream=True,
        )
        parts: list[str] = []
        completed = False
        async for event in stream:
            if isinstance(event, ResponseTextDeltaEvent):
                parts.append(event.delta)
            if isinstance(event, ResponseCompletedEvent):
                completed = True
        assert "".join(parts) == "vision text"
        assert completed
    finally:
        await http_client.aclose()


async def test_official_sdk_chat_vision_remote_url_raises_bad_request(vision_client):
    import openai

    client, _adapter = vision_client
    http_client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app))
    sdk = openai.AsyncOpenAI(
        base_url="http://test/v1", api_key="dummy", http_client=http_client
    )
    try:
        with pytest.raises(openai.BadRequestError):
            await sdk.chat.completions.create(
                model="vision-model",
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "image_url",
                                "image_url": {"url": "https://evil.example/x.png"},
                            }
                        ],
                    }
                ],
            )
    finally:
        await http_client.aclose()
