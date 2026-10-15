"""Per-route thinking policy resolution (offline unit tests)."""

from __future__ import annotations

import typing

import pytest

from aethergate.contracts.openai import ChatCompletionRequest
from aethergate.domain.enums import ThinkingPolicy
from aethergate.errors import AdminValidationError
from aethergate.thinking import (
    CLIENT_REASONING_EFFORT_VALUES,
    effective_reasoning_effort,
    thinking_levels_supported,
    validate_thinking_policy,
)


@pytest.mark.parametrize(
    ("provider_kind", "upstream_model", "supported"),
    [
        ("ollama", "gpt-oss:20b", True),
        ("ollama", "gpt-oss", True),
        ("ollama", "qwen3.8-2b-distill:Q6_K", False),
        ("ollama", "qwen3-vl:latest", False),
        ("openai", "gpt-oss-20b", False),
        ("unknown", "gpt-oss", False),
    ],
)
def test_thinking_levels_supported(provider_kind, upstream_model, supported):
    assert thinking_levels_supported(provider_kind, upstream_model) is supported


def test_validate_allows_inherit_off_on_for_boolean_upstream():
    for policy in (ThinkingPolicy.INHERIT, ThinkingPolicy.OFF, ThinkingPolicy.ON):
        validate_thinking_policy(policy, "ollama", "qwen3.8-2b-distill:Q6_K")


@pytest.mark.parametrize(
    "policy", [ThinkingPolicy.LOW, ThinkingPolicy.MEDIUM, ThinkingPolicy.HIGH]
)
def test_validate_rejects_level_for_boolean_upstream(policy):
    with pytest.raises(AdminValidationError):
        validate_thinking_policy(policy, "ollama", "qwen3.8-2b-distill:Q6_K")


@pytest.mark.parametrize(
    "policy", [ThinkingPolicy.LOW, ThinkingPolicy.MEDIUM, ThinkingPolicy.HIGH]
)
def test_validate_allows_level_for_level_capable_upstream(policy):
    validate_thinking_policy(policy, "ollama", "gpt-oss:20b")


@pytest.mark.parametrize(
    ("policy", "client_value", "expected"),
    [
        # inherit honors the client only when supplied.
        (ThinkingPolicy.INHERIT, None, None),
        (ThinkingPolicy.INHERIT, "high", "high"),
        (ThinkingPolicy.INHERIT, "none", "none"),
        # off always disables, overriding any client value.
        (ThinkingPolicy.OFF, None, "none"),
        (ThinkingPolicy.OFF, "high", "none"),
        # on enables at the default level, overriding any client value.
        (ThinkingPolicy.ON, None, "medium"),
        (ThinkingPolicy.ON, "none", "medium"),
        # explicit levels are forwarded as a level.
        (ThinkingPolicy.LOW, "none", "low"),
        (ThinkingPolicy.MEDIUM, None, "medium"),
        (ThinkingPolicy.HIGH, "low", "high"),
    ],
)
def test_effective_reasoning_effort(policy, client_value, expected):
    assert effective_reasoning_effort(policy, client_value) == expected


def test_chat_payload_round_trips_reasoning_effort():
    from aethergate.adapters.base import GenerationParams, Message
    from aethergate.scheduler.service import (
        deserialize_chat_payload,
        serialize_chat_payload,
    )

    payload = serialize_chat_payload(
        [Message(role="user", content="hi")],
        GenerationParams(reasoning_effort="high"),
    )
    _, params = deserialize_chat_payload(payload)
    assert params.reasoning_effort == "high"


def test_chat_payload_legacy_without_reasoning_effort_defaults_none():
    """A payload persisted before this milestone deserializes to no directive."""
    import json

    from aethergate.scheduler.service import deserialize_chat_payload

    legacy = json.dumps(
        {
            "messages": [{"role": "user", "content": "hi"}],
            "params": {"temperature": 0.5},
        }
    ).encode("utf-8")
    _, params = deserialize_chat_payload(legacy)
    assert params.reasoning_effort is None
    assert params.temperature == 0.5


def test_client_reasoning_effort_values_match_contract_literal():
    """The accepted client values must match the chat contract's Literal."""
    annotation = ChatCompletionRequest.model_fields["reasoning_effort"].annotation
    literal = next(
        arg for arg in typing.get_args(annotation)
        if typing.get_origin(arg) is typing.Literal
    )
    assert CLIENT_REASONING_EFFORT_VALUES == set(typing.get_args(literal))
