"""OpenAI-compatible request/response DTO validation."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from aethergate.contracts.openai import (
    ChatCompletion,
    ChatCompletionChunk,
    ChatCompletionRequest,
    ErrorResponse,
    ModelList,
    ModelObject,
)


def _messages():
    return [{"role": "user", "content": "hello"}]


def test_minimal_request():
    req = ChatCompletionRequest.model_validate({"model": "gpt-4", "messages": _messages()})
    assert req.model == "gpt-4"
    assert req.stream is False
    assert req.generation_max_tokens() is None


def test_transport_override_fields_rejected():
    for field in ("api_base", "base_url", "api_key", "headers", "upstream_model"):
        with pytest.raises(ValidationError):
            ChatCompletionRequest.model_validate(
                {"model": "gpt-4", "messages": _messages(), field: "http://evil"}
            )


def test_unknown_additive_fields_ignored():
    req = ChatCompletionRequest.model_validate(
        {"model": "gpt-4", "messages": _messages(), "some_new_field": 123}
    )
    assert req.model == "gpt-4"


def test_unsupported_role_rejected():
    with pytest.raises(ValidationError):
        ChatCompletionRequest.model_validate(
            {"model": "gpt-4", "messages": [{"role": "tool", "content": "x"}]}
        )


def test_multimodal_content_rejected():
    with pytest.raises(ValidationError):
        ChatCompletionRequest.model_validate(
            {
                "model": "gpt-4",
                "messages": [{"role": "user", "content": [{"type": "text", "text": "x"}]}],
            }
        )


def test_multiple_completions_rejected():
    with pytest.raises(ValidationError):
        ChatCompletionRequest.model_validate(
            {"model": "gpt-4", "messages": _messages(), "n": 2}
        )


def test_both_max_tokens_fields_rejected():
    with pytest.raises(ValidationError):
        ChatCompletionRequest.model_validate(
            {
                "model": "gpt-4",
                "messages": _messages(),
                "max_tokens": 10,
                "max_completion_tokens": 20,
            }
        )


def test_generation_max_tokens_prefers_completion_tokens():
    req = ChatCompletionRequest.model_validate(
        {"model": "gpt-4", "messages": _messages(), "max_completion_tokens": 20}
    )
    assert req.generation_max_tokens() == 20


def test_temperature_bounds():
    with pytest.raises(ValidationError):
        ChatCompletionRequest.model_validate(
            {"model": "gpt-4", "messages": _messages(), "temperature": 3}
        )


def test_model_list_shape():
    models = ModelList(data=[ModelObject(id="gpt-4")])
    data = models.model_dump(mode="json")
    assert data["object"] == "list"
    assert data["data"][0]["id"] == "gpt-4"
    assert data["data"][0]["object"] == "model"
    assert data["data"][0]["owned_by"] == "aethergate"


def test_completion_is_real_json_object():
    completion = ChatCompletion(
        id="gw-1",
        model="gpt-4",
        choices=[
            {
                "index": 0,
                "message": {"role": "assistant", "content": "hi"},
                "finish_reason": "stop",
            }
        ],
        usage={"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3},
    )
    data = completion.model_dump(mode="json")
    assert isinstance(data, dict)
    assert data["object"] == "chat.completion"
    assert data["choices"][0]["message"]["content"] == "hi"


def test_chunk_json_is_not_nested_string():
    chunk = ChatCompletionChunk(
        id="gw-1",
        model="gpt-4",
        choices=[{"index": 0, "delta": {"content": "hi"}, "finish_reason": None}],
    )
    serialized = chunk.model_dump_json()
    import json

    parsed = json.loads(serialized)
    assert parsed["choices"][0]["delta"]["content"] == "hi"


def test_error_envelope_shape():
    err = ErrorResponse(
        error={"message": "nope", "type": "invalid_request_error", "code": "model_not_found"}
    )
    data = err.model_dump(mode="json")
    assert data["error"]["message"] == "nope"
