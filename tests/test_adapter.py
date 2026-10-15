"""LiteLLM adapter configuration and behavior (offline)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from aethergate.adapters.base import (
    ChatRequest,
    EmbeddingRequest,
    GenerationParams,
    ImageContentPart,
    Message,
    TextContentPart,
)
from aethergate.adapters.litellm import LiteLLMChatAdapter
from aethergate.errors import ProviderError, UnsupportedProvider
from vision_fixtures import PNG_BYTES, PNG_DATA_URL


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


async def test_adapter_forwards_resolved_reasoning_effort(monkeypatch):
    """A resolved thinking directive reaches the pinned transport."""
    captured: dict = {}
    monkeypatch.setattr(
        "aethergate.adapters.litellm.litellm.acompletion",
        _make_capture_acompletion(captured, _fake_response()),
    )
    await LiteLLMChatAdapter().complete(_request(reasoning_effort="none"), None)
    assert captured["reasoning_effort"] == "none"


async def test_adapter_omits_reasoning_effort_when_unresolved(monkeypatch):
    """No resolved directive -> the parameter is not sent (provider default)."""
    captured: dict = {}
    monkeypatch.setattr(
        "aethergate.adapters.litellm.litellm.acompletion",
        _make_capture_acompletion(captured, _fake_response()),
    )
    await LiteLLMChatAdapter().complete(_request(), None)
    assert "reasoning_effort" not in captured


def test_adapter_rejects_unknown_provider_kind():
    with pytest.raises(UnsupportedProvider):
        LiteLLMChatAdapter()._litellm_model(_request(provider_kind="unknown"))


def test_estimate_input_tokens_unavailable_for_ollama_without_tokenizer():
    """No provider/model-specific tokenizer -> fail closed, no char heuristic."""
    adapter = LiteLLMChatAdapter()
    assert adapter.estimate_input_tokens(_request()) is None


def test_estimate_input_tokens_fails_closed_for_image_requests():
    """Image input has no defensible token estimator -> fail closed (None)."""
    adapter = LiteLLMChatAdapter()
    message = Message(
        role="user",
        content="describe ",
        parts=(
            TextContentPart(text="describe "),
            ImageContentPart(media_type="image/png", data=PNG_BYTES),
        ),
    )
    request = _request(messages=(message,))
    assert adapter.estimate_input_tokens(request) is None


async def test_adapter_maps_image_parts_to_openai_content_union(monkeypatch):
    """Canonical image parts map to the pinned OpenAI chat content-parts union.

    The adapter must not send image meaning as a text placeholder; it uses an
    ``image_url`` part carrying the inline ``data:`` URL. The pinned LiteLLM
    ollama adapter then extracts the pure base64 into ``images``.
    """
    captured: dict = {}
    monkeypatch.setattr(
        "aethergate.adapters.litellm.litellm.acompletion",
        _make_capture_acompletion(captured, _fake_response()),
    )
    message = Message(
        role="user",
        content="describe ",
        parts=(
            TextContentPart(text="describe "),
            ImageContentPart(media_type="image/png", data=PNG_BYTES),
        ),
    )
    await LiteLLMChatAdapter().complete(_request(messages=(message,)), None)
    sent = captured["messages"]
    assert sent[0]["role"] == "user"
    content = sent[0]["content"]
    assert content[0] == {"type": "text", "text": "describe "}
    assert content[1]["type"] == "image_url"
    assert content[1]["image_url"]["url"] == PNG_DATA_URL
    # No text placeholder substitutes for the image.
    assert all(
        "image" not in part.get("text", "") for part in content if part["type"] == "text"
    )


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
    assert excinfo.value.status_code == 429


def _make_capture_acompletion(captured: dict, response):
    async def fake(model, messages, stream, **kwargs):
        captured.update(kwargs)
        captured["model"] = model
        captured["messages"] = messages
        captured["stream"] = stream
        return response

    return fake


def _embedding_request(**overrides) -> EmbeddingRequest:
    base = dict(
        provider_kind="ollama",
        base_destination="http://192.168.22.50:11434",
        upstream_model="nomic-embed-text",
        inputs=("a", "b"),
        timeout_seconds=10.0,
    )
    base.update(overrides)
    return EmbeddingRequest(**base)


def _fake_embed_response(
    vectors, *, prompt_tokens: int = 5, model: str = "embed"
) -> SimpleNamespace:
    data = [{"index": i, "embedding": v} for i, v in enumerate(vectors)]
    return SimpleNamespace(
        id="upstream-embed-1",
        model=model,
        data=data,
        usage=SimpleNamespace(prompt_tokens=prompt_tokens, total_tokens=prompt_tokens),
    )


async def test_adapter_embed_sends_model_input_and_disables_retries(monkeypatch):
    captured: dict = {}

    async def fake(model, input, **kwargs):
        captured.update(kwargs)
        captured["model"] = model
        captured["input"] = input
        return _fake_embed_response([[1.0], [2.0]])

    monkeypatch.setattr("aethergate.adapters.litellm.litellm.aembedding", fake)
    await LiteLLMChatAdapter().embed(_embedding_request(), None)
    assert captured["model"] == "ollama/nomic-embed-text"
    assert captured["input"] == ["a", "b"]
    assert captured["api_base"] == "http://192.168.22.50:11434"
    assert captured["num_retries"] == 0
    assert captured["max_retries"] == 0
    assert captured["drop_params"] is False


async def test_adapter_embed_returns_ordered_vectors_and_usage(monkeypatch):
    async def fake(model, input, **kwargs):
        return _fake_embed_response([[1.0, 2.0], [3.0, 4.0]], prompt_tokens=9)

    monkeypatch.setattr("aethergate.adapters.litellm.litellm.aembedding", fake)
    result = await LiteLLMChatAdapter().embed(_embedding_request(), None)
    assert result.vectors == ((1.0, 2.0), (3.0, 4.0))
    assert result.usage.prompt_tokens == 9
    assert result.upstream_request_id == "upstream-embed-1"


async def test_adapter_embed_rejects_wrong_count(monkeypatch):
    async def fake(model, input, **kwargs):
        return _fake_embed_response([[1.0]])  # one fewer than the two inputs

    monkeypatch.setattr("aethergate.adapters.litellm.litellm.aembedding", fake)
    with pytest.raises(ProviderError):
        await LiteLLMChatAdapter().embed(_embedding_request(), None)


async def test_adapter_embed_rejects_non_finite_vector(monkeypatch):
    async def fake(model, input, **kwargs):
        return _fake_embed_response([[float("inf")], [2.0]])

    monkeypatch.setattr("aethergate.adapters.litellm.litellm.aembedding", fake)
    with pytest.raises(ProviderError):
        await LiteLLMChatAdapter().embed(_embedding_request(), None)


async def test_adapter_embed_rejects_malformed_vector(monkeypatch):
    async def fake(model, input, **kwargs):
        return _fake_embed_response([["not-a-number"], [2.0]])

    monkeypatch.setattr("aethergate.adapters.litellm.litellm.aembedding", fake)
    with pytest.raises(ProviderError):
        await LiteLLMChatAdapter().embed(_embedding_request(), None)


def _fake_embed_response_with_indexes(
    entries, *, prompt_tokens: int = 5, model: str = "embed"
) -> SimpleNamespace:
    data = [{"index": i, "embedding": v} for i, v in entries]
    return SimpleNamespace(
        id="upstream-embed-1",
        model=model,
        data=data,
        usage=SimpleNamespace(prompt_tokens=prompt_tokens, total_tokens=prompt_tokens),
    )


async def test_adapter_embed_accepts_valid_in_order_indexes(monkeypatch):
    async def fake(model, input, **kwargs):
        return _fake_embed_response_with_indexes(
            [(0, [1.0, 2.0]), (1, [3.0, 4.0])]
        )

    monkeypatch.setattr("aethergate.adapters.litellm.litellm.aembedding", fake)
    result = await LiteLLMChatAdapter().embed(_embedding_request(), None)
    assert result.vectors == ((1.0, 2.0), (3.0, 4.0))


async def test_adapter_embed_restores_out_of_order_indexes(monkeypatch):
    async def fake(model, input, **kwargs):
        # Provider returned index 1 before index 0; the adapter must restore the
        # input order, not the provider wire order.
        return _fake_embed_response_with_indexes(
            [(1, [3.0, 4.0]), (0, [1.0, 2.0])]
        )

    monkeypatch.setattr("aethergate.adapters.litellm.litellm.aembedding", fake)
    result = await LiteLLMChatAdapter().embed(_embedding_request(), None)
    assert result.vectors == ((1.0, 2.0), (3.0, 4.0))


async def test_adapter_embed_rejects_duplicate_indexes(monkeypatch):
    async def fake(model, input, **kwargs):
        return _fake_embed_response_with_indexes([(0, [1.0]), (0, [2.0])])

    monkeypatch.setattr("aethergate.adapters.litellm.litellm.aembedding", fake)
    with pytest.raises(ProviderError):
        await LiteLLMChatAdapter().embed(_embedding_request(), None)


async def test_adapter_embed_rejects_negative_index(monkeypatch):
    async def fake(model, input, **kwargs):
        return _fake_embed_response_with_indexes([(0, [1.0]), (-1, [2.0])])

    monkeypatch.setattr("aethergate.adapters.litellm.litellm.aembedding", fake)
    with pytest.raises(ProviderError):
        await LiteLLMChatAdapter().embed(_embedding_request(), None)


async def test_adapter_embed_rejects_out_of_range_index(monkeypatch):
    async def fake(model, input, **kwargs):
        # Two inputs but indexes 0 and 2: a gap and an out-of-range index.
        return _fake_embed_response_with_indexes([(0, [1.0]), (2, [2.0])])

    monkeypatch.setattr("aethergate.adapters.litellm.litellm.aembedding", fake)
    with pytest.raises(ProviderError):
        await LiteLLMChatAdapter().embed(_embedding_request(), None)


@pytest.mark.parametrize("bad_index", [None, "1", 1.5, True])
async def test_adapter_embed_rejects_non_integer_index(monkeypatch, bad_index):
    async def fake(model, input, **kwargs):
        return _fake_embed_response_with_indexes([(0, [1.0]), (bad_index, [2.0])])

    monkeypatch.setattr("aethergate.adapters.litellm.litellm.aembedding", fake)
    with pytest.raises(ProviderError):
        await LiteLLMChatAdapter().embed(_embedding_request(), None)


async def test_adapter_embed_index_error_message_is_safe(monkeypatch):
    async def fake(model, input, **kwargs):
        return _fake_embed_response_with_indexes([(0, [1.0]), (0, [987.654])])

    monkeypatch.setattr("aethergate.adapters.litellm.litellm.aembedding", fake)
    request = _embedding_request(inputs=("secretinputA", "secretinputB"))
    with pytest.raises(ProviderError) as excinfo:
        await LiteLLMChatAdapter().embed(request, None)
    message = str(excinfo.value.message)
    assert "secretinput" not in message
    assert "987.654" not in message


async def test_adapter_embed_maps_provider_error_safely(monkeypatch):
    async def fake(model, input, **kwargs):
        exc = Exception("boom http://secret.example.com sk-live-abcdefgh")
        exc.status_code = 429
        raise exc

    monkeypatch.setattr("aethergate.adapters.litellm.litellm.aembedding", fake)
    with pytest.raises(ProviderError) as excinfo:
        await LiteLLMChatAdapter().embed(_embedding_request(), "sk-secret")
    assert "secret" not in str(excinfo.value.message)
    assert "http" not in str(excinfo.value.message)
    assert excinfo.value.status_code == 429
