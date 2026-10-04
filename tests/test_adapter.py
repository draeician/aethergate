"""LiteLLM adapter configuration and behavior (offline)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from aethergate.adapters.base import ChatRequest, GenerationParams, Message
from aethergate.adapters.litellm import LiteLLMChatAdapter
from aethergate.errors import ProviderError, UnsupportedProvider


def _request(**overrides) -> ChatRequest:
    base = dict(
        provider_kind="ollama",
        base_destination="http://192.168.22.50:11434",
        upstream_model="qwen3.8-2b-distill:Q6_K",
        messages=(Message(role="user", content="hi"),),
        params=GenerationParams(temperature=0.7),
        timeout_seconds=10.0,
    )
    base.update(overrides)
    return ChatRequest(**base)


def _fake_response():
    return SimpleNamespace(
        id="upstream-1",
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content="hello"),
                finish_reason="stop",
            )
        ],
        usage=SimpleNamespace(prompt_tokens=1, completion_tokens=2, total_tokens=3),
    )


class _Chunks:
    def __init__(self, chunks):
        self._chunks = chunks

    def __aiter__(self):
        self._iter = iter(self._chunks)
        return self

    async def __anext__(self):
        try:
            return next(self._iter)
        except StopIteration:
            raise StopAsyncIteration from None


def _fake_chunk(content, finish=None):
    delta = SimpleNamespace(content=content) if content is not None else None
    return SimpleNamespace(choices=[SimpleNamespace(delta=delta, finish_reason=finish)], usage=None)


async def test_adapter_sends_resolved_endpoint_and_model(monkeypatch):
    captured: dict = {}
    monkeypatch.setattr(
        "aethergate.adapters.litellm.litellm.acompletion",
        _make_capture_acompletion(captured, _fake_response()),
    )
    await LiteLLMChatAdapter().complete(_request(), None)
    assert captured["api_base"] == "http://192.168.22.50:11434"
    assert captured["model"] == "ollama/qwen3.8-2b-distill:Q6_K"
    assert captured["messages"] == [{"role": "user", "content": "hi"}]


async def test_adapter_disables_retries_and_fallback(monkeypatch):
    captured: dict = {}
    monkeypatch.setattr(
        "aethergate.adapters.litellm.litellm.acompletion",
        _make_capture_acompletion(captured, _fake_response()),
    )
    await LiteLLMChatAdapter().complete(_request(), None)
    assert captured["num_retries"] == 0
    assert captured["max_retries"] == 0
    assert captured["drop_params"] is False
    assert "fallbacks" not in captured
    assert captured["stream"] is False


def test_adapter_rejects_unknown_provider_kind():
    with pytest.raises(UnsupportedProvider):
        LiteLLMChatAdapter()._litellm_model(_request(provider_kind="unknown"))


async def test_adapter_non_stream_result(monkeypatch):
    captured: dict = {}
    monkeypatch.setattr(
        "aethergate.adapters.litellm.litellm.acompletion",
        _make_capture_acompletion(captured, _fake_response()),
    )
    result = await LiteLLMChatAdapter().complete(_request(), None)
    assert result.content == "hello"
    assert result.finish_reason == "stop"
    assert result.upstream_request_id == "upstream-1"
    assert result.usage.total_tokens == 3


async def test_adapter_stream_yields_chunks(monkeypatch):
    chunks = _Chunks(
        [_fake_chunk("hel"), _fake_chunk("lo", finish="stop")]
    )
    captured: dict = {}

    async def fake(model, messages, stream, **kwargs):
        captured.update(kwargs)
        captured["model"] = model
        captured["stream"] = stream
        return chunks

    monkeypatch.setattr("aethergate.adapters.litellm.litellm.acompletion", fake)
    adapter = LiteLLMChatAdapter()
    collected = [c async for c in adapter.stream(_request(), None)]
    assert captured["stream"] is True
    assert [c.content for c in collected] == ["hel", "lo"]
    assert collected[-1].finish_reason == "stop"


async def test_adapter_maps_provider_error_safely(monkeypatch):
    async def fake(model, messages, stream, **kwargs):
        exc = Exception("boom with http://secret.example.com and sk-live-abcdefgh")
        exc.status_code = 429
        raise exc

    monkeypatch.setattr("aethergate.adapters.litellm.litellm.acompletion", fake)
    with pytest.raises(ProviderError) as excinfo:
        await LiteLLMChatAdapter().complete(_request(), "sk-super-secret")
    assert "secret" not in str(excinfo.value.message)
    assert "http" not in str(excinfo.value.message)
    assert excinfo.value.status_code is None


def _make_capture_acompletion(captured: dict, response):
    async def fake(model, messages, stream, **kwargs):
        captured.update(kwargs)
        captured["model"] = model
        captured["messages"] = messages
        captured["stream"] = stream
        return response

    return fake
