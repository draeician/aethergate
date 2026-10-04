"""OpenAI-compatible API surface tests (DB-gated, mock upstream adapter)."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

import httpx
import pytest

from aethergate.adapters import registry as registry_module
from aethergate.adapters.base import (
    ChatRequest,
    CompletionResult,
    StreamChunk,
    Usage,
)
from aethergate.config import Settings
from aethergate.domain import entities as domain
from aethergate.domain.enums import Capability
from aethergate.domain.ids import (
    EndpointId,
    ModelAliasId,
    ProviderAccountId,
    ProviderId,
    RouteBindingId,
)
from aethergate.main import app
from aethergate.persistence import db as db_module
from aethergate.persistence import repository


class MockAdapter:
    """In-memory upstream adapter for deterministic endpoint tests."""

    def __init__(self, *, content: str = "Hello from mock", closed: bool = False) -> None:
        self._content = content
        self.closed = closed

    async def complete(self, request: ChatRequest, secret: str | None) -> CompletionResult:
        return CompletionResult(
            content=self._content,
            finish_reason="stop",
            usage=Usage(prompt_tokens=1, completion_tokens=2, total_tokens=3),
            upstream_request_id="upstream-mock-1",
        )

    async def stream(self, request: ChatRequest, secret: str | None) -> AsyncIterator[StreamChunk]:
        try:
            yield StreamChunk(content="Hello", finish_reason=None, usage=None)
            yield StreamChunk(
                content=" from mock",
                finish_reason="stop",
                usage=Usage(prompt_tokens=1, completion_tokens=2, total_tokens=3),
            )
        finally:
            self.closed = True


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
        ),
    )
    await repository.create_model_alias(
        session,
        domain.ModelAlias(
            id=ModelAliasId("alias-gpt4"), name="gpt-4", capabilities=(Capability.TEXT,)
        ),
    )
    await repository.create_model_alias(
        session,
        domain.ModelAlias(
            id=ModelAliasId("alias-inactive"), name="inactive-model", is_active=False
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
async def api_client(session, monkeypatch):
    await _seed(session)

    mock = MockAdapter()
    monkeypatch.setitem(registry_module._ADAPTERS, "ollama", mock)

    fake_settings = Settings(
        database_url="postgresql+asyncpg://u:p@h/db",
        allow_inference_auth_bypass=True,
        upstream_allowlist="192.168.22.50",
    )
    monkeypatch.setattr("aethergate.api.deps.get_settings", lambda: fake_settings)

    async def _override_session():
        yield session

    app.dependency_overrides[db_module.get_session] = _override_session
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, mock
    app.dependency_overrides.clear()


async def test_models_list_public_active_aliases(api_client):
    client, _ = api_client
    resp = await client.get("/v1/models")
    assert resp.status_code == 200
    data = resp.json()
    assert data["object"] == "list"
    ids = {m["id"] for m in data["data"]}
    assert "gpt-4" in ids
    assert "inactive-model" not in ids


async def test_model_retrieval_and_unknown(api_client):
    client, _ = api_client
    ok = await client.get("/v1/models/gpt-4")
    assert ok.status_code == 200
    assert ok.json()["id"] == "gpt-4"

    missing = await client.get("/v1/models/does-not-exist")
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "model_not_found"


async def test_chat_completion_non_stream_shape(api_client):
    client, _ = api_client
    resp = await client.post(
        "/v1/chat/completions",
        json={"model": "gpt-4", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["object"] == "chat.completion"
    assert data["model"] == "gpt-4"
    assert "qwen3.8" not in json.dumps(data)
    assert data["choices"][0]["message"]["content"] == "Hello from mock"
    assert data["usage"]["total_tokens"] == 3
    assert data["id"]
    assert resp.headers.get("x-upstream-request-id") == "upstream-mock-1"


async def test_chat_completion_stream_sse_shape_and_termination(api_client):
    client, _ = api_client
    async with client.stream(
        "POST",
        "/v1/chat/completions",
        json={
            "model": "gpt-4",
            "messages": [{"role": "user", "content": "hi"}],
            "stream": True,
        },
    ) as resp:
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")
        body = b""
        async for chunk in resp.aiter_bytes():
            body += chunk

    text = body.decode()
    assert "data: [DONE]" in text
    assert "qwen3.8" not in text
    events = [
        json.loads(line[len("data: ") :])
        for line in text.splitlines()
        if line.startswith("data: ") and line != "data: [DONE]"
    ]
    assert events, "expected at least one SSE chunk"
    assert events[0]["object"] == "chat.completion.chunk"
    assert all(e["model"] == "gpt-4" for e in events)
    assert events[0]["choices"][0]["delta"]["role"] == "assistant"
    assert events[-1]["choices"][0]["finish_reason"] == "stop"


async def test_transport_override_fields_rejected(api_client):
    client, _ = api_client
    resp = await client.post(
        "/v1/chat/completions",
        json={
            "model": "gpt-4",
            "messages": [{"role": "user", "content": "hi"}],
            "api_base": "http://evil.example.com",
        },
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "invalid_request"


async def test_unknown_model_returns_404_and_does_not_dispatch(api_client):
    client, mock = api_client
    resp = await client.post(
        "/v1/chat/completions",
        json={"model": "nope", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "model_not_found"


async def test_official_openai_sdk_non_stream(api_client):
    import openai

    client, _ = api_client
    http_client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app))
    sdk = openai.AsyncOpenAI(base_url="http://test/v1", api_key="dummy", http_client=http_client)
    try:
        completion = await sdk.chat.completions.create(
            model="gpt-4",
            messages=[{"role": "user", "content": "hi"}],
        )
        assert completion.model == "gpt-4"
        assert completion.choices[0].message.content == "Hello from mock"
    finally:
        await http_client.aclose()


async def test_official_openai_sdk_stream(api_client):
    import openai

    client, _ = api_client
    http_client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app))
    sdk = openai.AsyncOpenAI(base_url="http://test/v1", api_key="dummy", http_client=http_client)
    try:
        stream = await sdk.chat.completions.create(
            model="gpt-4",
            messages=[{"role": "user", "content": "hi"}],
            stream=True,
        )
        parts: list[str] = []
        async for event in stream:
            if event.choices and event.choices[0].delta.content:
                parts.append(event.choices[0].delta.content)
        assert "".join(parts) == "Hello from mock"
    finally:
        await http_client.aclose()
