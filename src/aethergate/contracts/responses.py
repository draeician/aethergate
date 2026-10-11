"""OpenAI-compatible Responses API request/response DTOs (text + inline-image subset).

These shapes are pinned to the OpenAI compatibility baseline
(``docs/contracts/openai-compatibility-baseline.md``) and verified against the
locked official ``openai==2.54.0`` Python SDK types. The request DTO tolerates
unknown *additive* fields but explicitly rejects transport/provider-control fields
and every known semantic feature this milestone does not faithfully implement
(never silently dropping a semantic field).

The implemented surface is the text + inline-image input subset that produces
text output: string or text message/input-item ``input`` (``input_text``/
``output_text``) plus inline ``input_image`` (``data:`` PNG/JPEG/WebP),
``instructions``, ``max_output_tokens``, ``temperature``, ``top_p``, and
``stream``. Everything else in the pinned request schema — including image
*output*, remote/``file_id`` image sources, and non-default image detail modes —
is explicitly unsupported (`docs/contracts/vision.md`).
"""

from __future__ import annotations

import time
from typing import Any, Literal

from pydantic import ConfigDict, Field, model_validator

from aethergate.adapters.base import ImageContentPart, Message, TextContentPart
from aethergate.contracts.common import ContractModel
from aethergate.errors import UnsupportedFeature
from aethergate.inference.images import (
    ImageLimits,
    enforce_request_image_limits,
    parse_inline_image,
    validate_image_detail,
)

# Reused from the Chat Completions surface: transport/provider-control fields may
# never be supplied by a client. Declared only so their presence is detectable.
_TRANSPORT_CONTROL_FIELDS = (
    "api_base",
    "base_url",
    "headers",
    "extra_headers",
    "api_key",
    "api_token",
    "organization",
    "project",
    "provider",
    "endpoint",
    "upstream_model",
    "deployment",
    "custom_api_base",
)

_INPUT_ROLE_MAP = {
    "user": "user",
    "assistant": "assistant",
    # The canonical provider-neutral Message only distinguishes system/user/
    # assistant. A Responses ``developer`` instruction is mapped to ``system``;
    # the current Ollama adapter cannot faithfully distinguish the two, so they
    # share the highest-precedence instruction slot rather than being dropped.
    "system": "system",
    "developer": "system",
}


def _content_parts(
    content: Any,
    *,
    param: str,
    limits: ImageLimits,
    images_seen: list[ImageContentPart],
) -> tuple[str, tuple[TextContentPart | ImageContentPart, ...] | None]:
    """Map input-item content into canonical text + ordered parts.

    Text-only content (a plain string, or ``input_text``/``output_text`` parts)
    maps to text. ``input_image`` parts are accepted only with an inline
    ``data:`` ``image_url``; remote URLs, ``file_id`` inputs, and other part
    types are explicit unsupported features. Returns ``(text, parts)`` where
    ``parts`` is ``None`` for pure-text content.
    """
    if isinstance(content, str):
        return content, None
    if not isinstance(content, list):
        raise UnsupportedFeature("input_content", param=param)

    text_parts: list[str] = []
    parts: list[TextContentPart | ImageContentPart] = []
    for index, part in enumerate(content):
        part_param = f"{param}[{index}]"
        if not isinstance(part, dict):
            raise UnsupportedFeature("input_content", param=part_param)
        part_type = part.get("type")
        if part_type in ("input_text", "output_text"):
            text = part.get("text")
            if not isinstance(text, str):
                raise UnsupportedFeature("input_content", param=part_param)
            text_parts.append(text)
            parts.append(TextContentPart(text=text))
        elif part_type == "input_image":
            if part.get("file_id") is not None:
                raise UnsupportedFeature("image_file_id", param=part_param)
            url = part.get("image_url")
            if not isinstance(url, str):
                raise UnsupportedFeature("image_source", param=part_param)
            # Never silently ignore the semantic detail selector: the pinned
            # Responses contract is `detail: low|high|auto|original` (defaults
            # to `auto`); only the neutral `auto`/omitted mode is supported.
            validate_image_detail(
                part.get("detail"), param=f"{part_param}.detail"
            )
            image = parse_inline_image(
                url, max_image_bytes=limits.max_image_bytes, param=part_param
            )
            images_seen.append(image)
            enforce_request_image_limits(
                images_seen,
                max_images_per_request=limits.max_images_per_request,
                max_total_image_bytes=limits.max_total_image_bytes,
            )
            parts.append(image)
        else:
            raise UnsupportedFeature(
                f"input_content:{part_type}", param=part_param
            )
    has_image = any(isinstance(p, ImageContentPart) for p in parts)
    return "".join(text_parts), tuple(parts) if has_image else None


class ResponsesRequest(ContractModel):
    """Pinned Responses API request surface (text + inline-image subset)."""

    model_config = ConfigDict(extra="ignore")

    model: str = Field(min_length=1)
    input: Any
    instructions: str | None = None
    max_output_tokens: int | None = Field(default=None, ge=1)
    temperature: float | None = Field(default=None, ge=0, le=2)
    top_p: float | None = Field(default=None, ge=0, le=1)
    stream: bool = False

    # Transport/provider-control fields: declared only so their presence can be
    # detected and rejected; they are never used.
    api_base: Any = None
    base_url: Any = None
    headers: Any = None
    extra_headers: Any = None
    api_key: Any = None
    api_token: Any = None
    organization: Any = None
    project: Any = None
    provider: Any = None
    endpoint: Any = None
    upstream_model: Any = None
    deployment: Any = None
    custom_api_base: Any = None

    # Known semantic fields this milestone does not implement. Declared so their
    # presence is detected and explicitly rejected (never silently ignored).
    background: Any = None
    context_management: Any = None
    conversation: Any = None
    include: Any = None
    max_tool_calls: Any = None
    metadata: Any = None
    moderation: Any = None
    parallel_tool_calls: Any = None
    previous_response_id: Any = None
    prompt: Any = None
    prompt_cache_key: Any = None
    prompt_cache_options: Any = None
    prompt_cache_retention: Any = None
    reasoning: Any = None
    safety_identifier: Any = None
    service_tier: Any = None
    store: Any = None
    stream_options: Any = None
    text: Any = None
    tool_choice: Any = None
    tools: Any = None
    top_logprobs: Any = None
    truncation: Any = None
    user: Any = None

    @model_validator(mode="after")
    def _reject_transport_controls(self) -> ResponsesRequest:
        for name in _TRANSPORT_CONTROL_FIELDS:
            if getattr(self, name) is not None:
                raise UnsupportedFeature(
                    f"transport_override:{name}", param=name
                )
        return self

    @model_validator(mode="after")
    def _reject_unsupported_semantics(self) -> ResponsesRequest:
        # Boolean/default no-ops are permitted only at their harmless default.
        if self.background:
            raise UnsupportedFeature("background", param="background")
        if self.store:
            raise UnsupportedFeature("store", param="store")
        if self.context_management is not None:
            raise UnsupportedFeature("context_management", param="context_management")
        if self.conversation is not None:
            raise UnsupportedFeature("conversation", param="conversation")
        if self.include:
            raise UnsupportedFeature("include", param="include")
        if self.max_tool_calls is not None:
            raise UnsupportedFeature("max_tool_calls", param="max_tool_calls")
        if self.metadata is not None:
            raise UnsupportedFeature("metadata", param="metadata")
        if self.moderation is not None:
            raise UnsupportedFeature("moderation", param="moderation")
        if self.parallel_tool_calls is not None:
            raise UnsupportedFeature("parallel_tool_calls", param="parallel_tool_calls")
        if self.previous_response_id is not None:
            raise UnsupportedFeature("previous_response_id", param="previous_response_id")
        if self.prompt is not None:
            raise UnsupportedFeature("prompt", param="prompt")
        if self.prompt_cache_key is not None:
            raise UnsupportedFeature("prompt_cache_key", param="prompt_cache_key")
        if self.prompt_cache_options is not None:
            raise UnsupportedFeature("prompt_cache_options", param="prompt_cache_options")
        if self.prompt_cache_retention is not None:
            raise UnsupportedFeature(
                "prompt_cache_retention", param="prompt_cache_retention"
            )
        if self.reasoning is not None:
            raise UnsupportedFeature("reasoning", param="reasoning")
        if self.safety_identifier is not None:
            raise UnsupportedFeature("safety_identifier", param="safety_identifier")
        if self.service_tier is not None:
            raise UnsupportedFeature("service_tier", param="service_tier")
        if self.stream_options is not None:
            raise UnsupportedFeature("stream_options", param="stream_options")
        if self.text is not None and not _is_plain_text_config(self.text):
            raise UnsupportedFeature("text_format", param="text")
        if self.tool_choice is not None:
            raise UnsupportedFeature("tool_choice", param="tool_choice")
        if self.tools:
            raise UnsupportedFeature("tools", param="tools")
        if self.top_logprobs is not None:
            raise UnsupportedFeature("top_logprobs", param="top_logprobs")
        if self.truncation is not None and self.truncation != "disabled":
            raise UnsupportedFeature("truncation", param="truncation")
        if self.user is not None:
            raise UnsupportedFeature("user", param="user")
        return self

    def to_canonical_messages(
        self, limits: ImageLimits | None = None
    ) -> list[Message]:
        """Map the request into the canonical provider-neutral message list.

        ``instructions`` becomes the leading ``system`` message (highest
        precedence), followed by the input items in order. A ``developer`` role is
        mapped to ``system`` (documented canonical mapping). Image count/byte
        limits are enforced across the whole request via one shared accumulator.
        """
        effective = limits or ImageLimits()
        images_seen: list[ImageContentPart] = []
        messages: list[Message] = []
        if self.instructions is not None:
            messages.append(Message(role="system", content=self.instructions))

        raw_input = self.input
        if isinstance(raw_input, str):
            messages.append(Message(role="user", content=raw_input))
            return messages
        if not isinstance(raw_input, list):
            raise UnsupportedFeature("input", param="input")
        for index, item in enumerate(raw_input):
            if not isinstance(item, dict):
                raise UnsupportedFeature("input_item", param=f"input[{index}]")
            item_type = item.get("type")
            if item_type not in (None, "message"):
                raise UnsupportedFeature(
                    f"input_item:{item_type}", param=f"input[{index}]"
                )
            role = item.get("role")
            canonical_role = _INPUT_ROLE_MAP.get(role)
            if canonical_role is None:
                raise UnsupportedFeature(
                    f"input_role:{role}", param=f"input[{index}].role"
                )
            text, parts = _content_parts(
                item.get("content"),
                param=f"input[{index}].content",
                limits=effective,
                images_seen=images_seen,
            )
            messages.append(
                Message(role=canonical_role, content=text, parts=parts)
            )
        return messages


def _is_plain_text_config(value: Any) -> bool:
    """Whether a ``text`` config only requests plain text (the default)."""
    if not isinstance(value, dict):
        return False
    for key, item in value.items():
        if key == "format":
            if not isinstance(item, dict):
                return False
            if item.get("type") not in (None, "text"):
                return False
        else:
            return False
    return True


# --- Response DTOs -----------------------------------------------------------


def _now() -> float:
    return float(int(time.time()))


ResponseStatus = Literal[
    "completed", "failed", "in_progress", "cancelled", "queued", "incomplete"
]


class ResponseInputTokensDetails(ContractModel):
    cached_tokens: int = 0
    cache_write_tokens: int = 0


class ResponseOutputTokensDetails(ContractModel):
    reasoning_tokens: int = 0


class ResponseUsage(ContractModel):
    input_tokens: int = 0
    input_tokens_details: ResponseInputTokensDetails = Field(
        default_factory=ResponseInputTokensDetails
    )
    output_tokens: int = 0
    output_tokens_details: ResponseOutputTokensDetails = Field(
        default_factory=ResponseOutputTokensDetails
    )
    total_tokens: int = 0


class ResponseOutputText(ContractModel):
    type: Literal["output_text"] = "output_text"
    text: str
    annotations: list[Any] = Field(default_factory=list)


class ResponseOutputMessageItem(ContractModel):
    id: str
    type: Literal["message"] = "message"
    role: Literal["assistant"] = "assistant"
    status: Literal["in_progress", "completed", "incomplete"] = "completed"
    content: list[ResponseOutputText] = Field(default_factory=list)


class ResponseErrorDetail(ContractModel):
    code: str = "server_error"
    message: str


class ResponseIncompleteDetails(ContractModel):
    reason: Literal["max_output_tokens", "content_filter"] | None = None


class ResponseObject(ContractModel):
    id: str
    object: Literal["response"] = "response"
    created_at: float = Field(default_factory=_now)
    status: ResponseStatus = "completed"
    model: str
    output: list[ResponseOutputMessageItem] = Field(default_factory=list)
    parallel_tool_calls: bool = True
    tool_choice: str = "auto"
    tools: list[Any] = Field(default_factory=list)
    error: ResponseErrorDetail | None = None
    incomplete_details: ResponseIncompleteDetails | None = None
    usage: ResponseUsage | None = None


# --- Streaming event DTOs ----------------------------------------------------


class _Event(ContractModel):
    sequence_number: int


class ResponseCreatedEvent(_Event):
    type: Literal["response.created"] = "response.created"
    response: ResponseObject


class ResponseInProgressEvent(_Event):
    type: Literal["response.in_progress"] = "response.in_progress"
    response: ResponseObject


class ResponseOutputItemAddedEvent(_Event):
    type: Literal["response.output_item.added"] = "response.output_item.added"
    output_index: int
    item: ResponseOutputMessageItem


class ResponseContentPartAddedEvent(_Event):
    type: Literal["response.content_part.added"] = "response.content_part.added"
    output_index: int
    item_id: str
    content_index: int
    part: ResponseOutputText


class ResponseTextDeltaEvent(_Event):
    type: Literal["response.output_text.delta"] = "response.output_text.delta"
    output_index: int
    item_id: str
    content_index: int
    delta: str
    logprobs: list[Any] = Field(default_factory=list)


class ResponseTextDoneEvent(_Event):
    type: Literal["response.output_text.done"] = "response.output_text.done"
    output_index: int
    item_id: str
    content_index: int
    text: str
    logprobs: list[Any] = Field(default_factory=list)


class ResponseContentPartDoneEvent(_Event):
    type: Literal["response.content_part.done"] = "response.content_part.done"
    output_index: int
    item_id: str
    content_index: int
    part: ResponseOutputText


class ResponseOutputItemDoneEvent(_Event):
    type: Literal["response.output_item.done"] = "response.output_item.done"
    output_index: int
    item: ResponseOutputMessageItem


class ResponseCompletedEvent(_Event):
    type: Literal["response.completed"] = "response.completed"
    response: ResponseObject


class ResponseFailedEvent(_Event):
    type: Literal["response.failed"] = "response.failed"
    response: ResponseObject


class ResponseIncompleteEvent(_Event):
    type: Literal["response.incomplete"] = "response.incomplete"
    response: ResponseObject


__all__ = [
    "ResponsesRequest",
    "ResponseObject",
    "ResponseUsage",
    "ResponseOutputMessageItem",
    "ResponseOutputText",
    "ResponseErrorDetail",
    "ResponseCreatedEvent",
    "ResponseInProgressEvent",
    "ResponseOutputItemAddedEvent",
    "ResponseContentPartAddedEvent",
    "ResponseTextDeltaEvent",
    "ResponseTextDoneEvent",
    "ResponseContentPartDoneEvent",
    "ResponseOutputItemDoneEvent",
    "ResponseCompletedEvent",
    "ResponseFailedEvent",
    "ResponseIncompleteEvent",
]
