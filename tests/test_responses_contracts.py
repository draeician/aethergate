"""OpenAI-compatible Responses API request/response DTO validation (no DB)."""

from __future__ import annotations

import json

import pytest

from aethergate.contracts.responses import (
    ResponseCompletedEvent,
    ResponseObject,
    ResponseOutputMessageItem,
    ResponseOutputText,
    ResponsesRequest,
    ResponseUsage,
)
from aethergate.errors import UnsupportedFeature


def test_simple_string_input_maps_to_single_user_message():
    req = ResponsesRequest.model_validate({"model": "gpt-4", "input": "Hello"})
    messages = req.to_canonical_messages()
    assert len(messages) == 1
    assert messages[0].role == "user"
    assert messages[0].content == "Hello"


def test_text_input_item_form():
    req = ResponsesRequest.model_validate(
        {
            "model": "gpt-4",
            "input": [
                {"role": "user", "content": [{"type": "input_text", "text": "part one "}]},
                {"role": "assistant", "content": [{"type": "output_text", "text": "prior"}]},
                {"role": "user", "content": "plain"},
            ],
        }
    )
    messages = req.to_canonical_messages()
    assert [m.role for m in messages] == ["user", "assistant", "user"]
    assert [m.content for m in messages] == ["part one ", "prior", "plain"]


def test_instructions_precede_and_precedence_is_preserved():
    req = ResponsesRequest.model_validate(
        {"model": "gpt-4", "input": "Hello", "instructions": "Be terse"}
    )
    messages = req.to_canonical_messages()
    assert [m.role for m in messages] == ["system", "user"]
    assert messages[0].content == "Be terse"
    assert messages[1].content == "Hello"


def test_developer_role_maps_to_system_and_is_not_dropped():
    req = ResponsesRequest.model_validate(
        {
            "model": "gpt-4",
            "input": [{"role": "developer", "content": "dev instruction"}],
        }
    )
    messages = req.to_canonical_messages()
    assert len(messages) == 1
    assert messages[0].role == "system"
    assert messages[0].content == "dev instruction"


def test_generation_controls_parse():
    req = ResponsesRequest.model_validate(
        {
            "model": "gpt-4",
            "input": "hi",
            "max_output_tokens": 64,
            "temperature": 0.5,
            "top_p": 0.9,
            "stream": True,
        }
    )
    assert req.max_output_tokens == 64
    assert req.temperature == 0.5
    assert req.top_p == 0.9
    assert req.stream is True


def test_transport_control_fields_rejected():
    for field in ("api_base", "base_url", "api_key", "headers", "upstream_model"):
        with pytest.raises(UnsupportedFeature) as info:
            ResponsesRequest.model_validate(
                {"model": "gpt-4", "input": "hi", field: "http://evil"}
            )
        assert "transport_override" in info.value.feature


@pytest.mark.parametrize(
    "field, value",
    [
        ("tools", [{"type": "function", "name": "x"}]),
        ("tool_choice", "auto"),
        ("previous_response_id", "resp_1"),
        ("conversation", "conv_1"),
        ("store", True),
        ("background", True),
        ("include", ["reasoning.encrypted_content"]),
        ("reasoning", {"effort": "high"}),
        ("parallel_tool_calls", False),
        ("max_tool_calls", 3),
        ("truncation", "auto"),
        ("metadata", {"k": "v"}),
        ("service_tier", "flex"),
        ("stream_options", {"include_obfuscation": False}),
        ("user", "u1"),
        ("safety_identifier", "safe1"),
        ("prompt", {"id": "p1"}),
        ("prompt_cache_key", "c1"),
        ("prompt_cache_retention", "24h"),
        ("text", {"format": {"type": "json_schema"}}),
        ("moderation", {"model": "omni"}),
        ("context_management", [{"type": "compaction"}]),
        ("top_logprobs", 3),
    ],
)
def test_unsupported_semantic_fields_rejected(field, value):
    with pytest.raises(UnsupportedFeature):
        ResponsesRequest.model_validate({"model": "gpt-4", "input": "hi", field: value})


def test_remote_image_input_rejected():
    req = ResponsesRequest.model_validate(
        {
            "model": "gpt-4",
            "input": [
                {
                    "role": "user",
                    "content": [{"type": "input_image", "image_url": "http://x/y.png"}],
                }
            ],
        }
    )
    with pytest.raises(UnsupportedFeature) as info:
        req.to_canonical_messages()
    assert info.value.feature == "image_remote_url"


def test_image_file_id_input_rejected():
    req = ResponsesRequest.model_validate(
        {
            "model": "gpt-4",
            "input": [
                {
                    "role": "user",
                    "content": [{"type": "input_image", "file_id": "file-1"}],
                }
            ],
        }
    )
    with pytest.raises(UnsupportedFeature) as info:
        req.to_canonical_messages()
    assert info.value.feature == "image_file_id"


def test_unsupported_input_item_type_rejected():
    req = ResponsesRequest.model_validate(
        {"model": "gpt-4", "input": [{"type": "function_call_output", "call_id": "c"}]}
    )
    with pytest.raises(UnsupportedFeature):
        req.to_canonical_messages()


def test_unknown_additive_fields_ignored():
    req = ResponsesRequest.model_validate(
        {"model": "gpt-4", "input": "hi", "some_new_field": 123}
    )
    assert req.model == "gpt-4"


def test_response_object_shape_and_output_text():
    obj = ResponseObject(
        id="resp_1",
        model="gpt-4",
        status="completed",
        output=[
            ResponseOutputMessageItem(
                id="msg_1", content=[ResponseOutputText(text="hello world")]
            )
        ],
        usage=ResponseUsage(input_tokens=5, output_tokens=2, total_tokens=7),
    )
    data = obj.model_dump(mode="json")
    assert data["object"] == "response"
    assert data["status"] == "completed"
    assert data["model"] == "gpt-4"
    assert data["output"][0]["type"] == "message"
    assert data["output"][0]["role"] == "assistant"
    assert data["output"][0]["content"][0]["type"] == "output_text"
    assert data["output"][0]["content"][0]["text"] == "hello world"
    assert data["usage"]["input_tokens"] == 5
    assert data["usage"]["total_tokens"] == 7
    # Required pinned fields with safe defaults.
    assert data["parallel_tool_calls"] is True
    assert data["tool_choice"] == "auto"
    assert data["tools"] == []


def test_usage_details_shape():
    data = ResponseUsage(
        input_tokens=1, output_tokens=2, total_tokens=3
    ).model_dump(mode="json")
    assert data["input_tokens_details"] == {"cached_tokens": 0, "cache_write_tokens": 0}
    assert data["output_tokens_details"] == {"reasoning_tokens": 0}


def test_completed_event_shape_is_real_json_object():
    event = ResponseCompletedEvent(
        sequence_number=9,
        response=ResponseObject(
            id="resp_1",
            model="gpt-4",
            status="completed",
            output=[
                ResponseOutputMessageItem(
                    id="msg_1", content=[ResponseOutputText(text="hi")]
                )
            ],
        ),
    )
    parsed = json.loads(event.model_dump_json())
    assert parsed["type"] == "response.completed"
    assert parsed["sequence_number"] == 9
    assert parsed["response"]["object"] == "response"
    assert parsed["response"]["output"][0]["content"][0]["text"] == "hi"
