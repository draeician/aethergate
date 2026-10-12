"""Deterministic fake Ollama upstream tests (offline; exercises real LiteLLM adapter).

These tests use the real pinned LiteLLM ollama transport against the in-repo fake
upstream, proving the CI provider path works with no Internet, no model download,
and no provider credentials.
"""

from __future__ import annotations

import time

import pytest

from aethergate.adapters.base import (
    ChatRequest,
    ImageContentPart,
    Message,
    TextContentPart,
)
from aethergate.adapters.litellm import LiteLLMChatAdapter
from aethergate.errors import ProviderError
from aethergate.testing.fake_ollama import (
    DEFAULT_CONTENT,
    DEFAULT_STREAM_CHUNKS,
    FakeOllamaConfig,
    FakeOllamaServer,
)
from vision_fixtures import PNG_BYTES


def _request(base_url: str, *, model: str = "fixture") -> ChatRequest:
    return ChatRequest(
        provider_kind="ollama",
        base_destination=base_url,
        upstream_model=model,
        messages=(Message(role="user", content="hello fixture"),),
    )


def _vision_request(base_url: str, *, model: str = "fixture") -> ChatRequest:
    message = Message(
        role="user",
        content="describe ",
        parts=(
            TextContentPart(text="describe "),
            ImageContentPart(media_type="image/png", data=PNG_BYTES),
        ),
    )
    return ChatRequest(
        provider_kind="ollama",
        base_destination=base_url,
        upstream_model=model,
        messages=(message,),
    )


async def test_fake_ollama_non_stream_deterministic():
    with FakeOllamaServer() as server:
        result = await LiteLLMChatAdapter().complete(_request(server.base_url), None)
    assert result.content == DEFAULT_CONTENT
    assert result.finish_reason == "stop"
    assert result.usage.prompt_tokens == 5
    assert result.usage.completion_tokens == 4
    assert server.config.request_count == 1


async def test_fake_ollama_stream_deterministic_chunks():
    with FakeOllamaServer() as server:
        chunks = [
            c.content
            async for c in LiteLLMChatAdapter().stream(_request(server.base_url), None)
        ]
    content = [c for c in chunks if c]
    assert content == list(DEFAULT_STREAM_CHUNKS)


async def test_fake_ollama_missing_model_returns_404():
    with FakeOllamaServer(
        FakeOllamaConfig(missing_models=("missing-model",))
    ) as server:
        with pytest.raises(ProviderError) as excinfo:
            await LiteLLMChatAdapter().complete(
                _request(server.base_url, model="missing-model"), None
            )
    assert excinfo.value.status_code == 404
    # The client-visible message never leaks the raw provider body/URL.
    assert "http://" not in excinfo.value.message
    assert "127.0.0.1" not in excinfo.value.message


async def test_fake_ollama_429_status_propagates_safely():
    with FakeOllamaServer(FakeOllamaConfig(fail_status=429)) as server:
        with pytest.raises(ProviderError) as excinfo:
            await LiteLLMChatAdapter().complete(_request(server.base_url), None)
    assert excinfo.value.status_code == 429


async def test_fake_ollama_configurable_delay():
    with FakeOllamaServer(FakeOllamaConfig(delay_seconds=0.3)) as server:
        started = time.perf_counter()
        await LiteLLMChatAdapter().complete(_request(server.base_url), None)
        elapsed = time.perf_counter() - started
    assert elapsed >= 0.25


async def test_fake_ollama_does_not_persist_request_content():
    """The fixture records only counts/IDs — never prompt content."""
    config = FakeOllamaConfig()
    with FakeOllamaServer(config) as server:
        await LiteLLMChatAdapter().complete(_request(server.base_url), None)
    # No attribute on the config holds the prompt/content.
    for value in vars(config).values():
        assert "hello fixture" not in str(value)


async def test_fake_ollama_vision_reaches_transport_with_image_metadata():
    """A canonical image message reaches the real LiteLLM transport as an image.

    The pinned LiteLLM ollama adapter emits ``/api/generate`` with pure-base64
    ``images``. The fake records only the count and decoded byte lengths — never
    the base64/bytes themselves.
    """
    config = FakeOllamaConfig()
    with FakeOllamaServer(config) as server:
        result = await LiteLLMChatAdapter().complete(
            _vision_request(server.base_url), None
        )
    assert result.content == DEFAULT_CONTENT
    assert server.config.last_image_count == 1
    assert server.config.last_image_byte_lengths == (len(PNG_BYTES),)
    assert server.config.image_request_count == 1


async def test_fake_ollama_vision_does_not_persist_image_bytes():
    config = FakeOllamaConfig()
    with FakeOllamaServer(config) as server:
        await LiteLLMChatAdapter().complete(_vision_request(server.base_url), None)
    # Neither the raw bytes nor their base64 appear anywhere in captured state.
    import base64 as _b64

    encoded = _b64.b64encode(PNG_BYTES).decode("ascii")
    for value in vars(config).values():
        assert encoded not in str(value)
        assert PNG_BYTES.hex() not in str(value)
