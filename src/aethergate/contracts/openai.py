"""OpenAI-compatible Chat Completions and Models request/response DTOs.

These shapes are pinned to the OpenAI compatibility baseline
(``docs/contracts/openai-compatibility-baseline.md``). Request DTOs tolerate
unknown additive fields but explicitly reject transport/provider-control fields
and unsupported semantic features rather than silently dropping them.
"""

from __future__ import annotations

import time
from typing import Any, Literal

from pydantic import ConfigDict, Field, field_validator, model_validator

from aethergate.adapters.base import ImageContentPart, Message, TextContentPart
from aethergate.contracts.common import ContractModel
from aethergate.errors import UnsupportedFeature
from aethergate.inference.images import (
    ImageLimits,
    enforce_request_image_limits,
    parse_inline_image,
    validate_image_detail,
)

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


class ChatMessage(ContractModel):
    """A single Chat Completions message.

    ``content`` is either a plain string (text-only) or the pinned multimodal
    content-parts union: an ordered list of ``{"type": "text", "text": ...}`` and
    ``{"type": "image_url", "image_url": {"url": "data:..."}}`` parts. Only inline
    ``data:`` image sources are accepted; remote URLs, ``file_id``/file inputs,
    and other part types are explicitly rejected. Tool/function payloads remain
    unsupported and are rejected.
    """

    model_config = ConfigDict(extra="forbid")

    role: str
    content: str | list[Any]

    @field_validator("role")
    @classmethod
    def _supported_roles(cls, value: str) -> str:
        if value not in {"system", "user", "assistant"}:
            raise ValueError(
                f"unsupported message role {value!r}; only system/user/assistant are supported"
            )
        return value

    def to_canonical(
        self, *, limits: ImageLimits, images_seen: list[ImageContentPart]
    ) -> Message:
        """Map into the canonical multimodal message.

        Text-only content maps to ``Message(role, content:str)``. A content-parts
        list maps to ordered canonical parts; each inline image is strictly
        validated and image count/byte limits are enforced across the request via
        the shared ``images_seen`` accumulator.
        """
        if isinstance(self.content, str):
            return Message(role=self.role, content=self.content)

        parts: list[TextContentPart | ImageContentPart] = []
        for index, part in enumerate(self.content):
            param = f"messages[{self.role}].content[{index}]"
            if not isinstance(part, dict):
                raise UnsupportedFeature("chat_content_part", param=param)
            part_type = part.get("type")
            if part_type == "text":
                text = part.get("text")
                if not isinstance(text, str):
                    raise UnsupportedFeature("chat_text_part", param=param)
                parts.append(TextContentPart(text=text))
            elif part_type == "image_url":
                image_url = part.get("image_url")
                if isinstance(image_url, dict):
                    url = image_url.get("url")
                    detail = image_url.get("detail")
                else:
                    url = image_url
                    detail = None
                if not isinstance(url, str):
                    raise UnsupportedFeature("image_source", param=param)
                # Never silently ignore the semantic detail selector: the pinned
                # Chat contract is `detail: auto|low|high` (JS also `original`);
                # only the neutral `auto`/omitted mode is supported.
                validate_image_detail(detail, param=f"{param}.image_url.detail")
                image = parse_inline_image(
                    url, max_image_bytes=limits.max_image_bytes, param=param
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
                    f"chat_content_part:{part_type}", param=param
                )
        content_text = "".join(
            p.text for p in parts if isinstance(p, TextContentPart)
        )
        return Message(role=self.role, content=content_text, parts=tuple(parts))


class StreamOptions(ContractModel):
    model_config = ConfigDict(extra="ignore")

    include_usage: bool = False


class ChatCompletionRequest(ContractModel):
    """Pinned Chat Completions request surface.

    Unknown additive fields are ignored; transport/provider-control fields are
    explicitly rejected (they are never honored and never forwarded).
    """

    model_config = ConfigDict(extra="ignore")

    model: str = Field(min_length=1)
    messages: list[ChatMessage] = Field(min_length=1)
    stream: bool = False
    stream_options: StreamOptions | None = None

    temperature: float | None = Field(default=None, ge=0, le=2)
    top_p: float | None = Field(default=None, ge=0, le=1)
    max_tokens: int | None = Field(default=None, ge=1)
    max_completion_tokens: int | None = Field(default=None, ge=1)
    stop: str | list[str] | None = None
    frequency_penalty: float | None = Field(default=None, ge=-2, le=2)
    presence_penalty: float | None = Field(default=None, ge=-2, le=2)
    seed: int | None = None
    n: int = 1

    # Transport/provider-control fields are declared only so their presence is
    # detectable and rejected; they are never used.
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

    @model_validator(mode="after")
    def _reject_transport_controls(self) -> ChatCompletionRequest:
        for name in _TRANSPORT_CONTROL_FIELDS:
            if getattr(self, name) is not None:
                raise ValueError(
                    f"client-supplied transport/provider-control field {name!r} is not permitted"
                )
        return self

    @model_validator(mode="after")
    def _validate_unsupported_semantics(self) -> ChatCompletionRequest:
        if self.n != 1:
            raise ValueError("multiple completions (n != 1) are not supported")
        if self.max_tokens is not None and self.max_completion_tokens is not None:
            raise ValueError(
                "provide only one of max_tokens or max_completion_tokens"
            )
        return self

    def generation_max_tokens(self) -> int | None:
        if self.max_completion_tokens is not None:
            return self.max_completion_tokens
        return self.max_tokens

    def to_canonical_messages(
        self, limits: ImageLimits | None = None
    ) -> list[Message]:
        """Map messages into the canonical multimodal representation.

        Image count/byte limits are enforced across the whole request (a single
        shared accumulator), so multiple messages cannot combine to bypass the
        per-request bounds.
        """
        effective = limits or ImageLimits()
        images_seen: list[ImageContentPart] = []
        return [
            message.to_canonical(limits=effective, images_seen=images_seen)
            for message in self.messages
        ]


# --- Response DTOs -----------------------------------------------------------


def _now() -> int:
    return int(time.time())


class ModelObject(ContractModel):
    id: str
    object: Literal["model"] = "model"
    created: int = Field(default_factory=_now)
    owned_by: str = "aethergate"


class ModelList(ContractModel):
    object: Literal["list"] = "list"
    data: list[ModelObject]


class ChatMessageResponse(ContractModel):
    role: str
    content: str


class ChatCompletionChoice(ContractModel):
    index: int = 0
    message: ChatMessageResponse
    finish_reason: str | None = None


class ChatCompletionUsage(ContractModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class ChatCompletion(ContractModel):
    id: str
    object: Literal["chat.completion"] = "chat.completion"
    created: int = Field(default_factory=_now)
    model: str
    choices: list[ChatCompletionChoice]
    usage: ChatCompletionUsage | None = None


class ChatCompletionChunkDelta(ContractModel):
    role: str | None = None
    content: str | None = None


class ChatCompletionChunkChoice(ContractModel):
    index: int = 0
    delta: ChatCompletionChunkDelta
    finish_reason: str | None = None


class ChatCompletionChunk(ContractModel):
    id: str
    object: Literal["chat.completion.chunk"] = "chat.completion.chunk"
    created: int = Field(default_factory=_now)
    model: str
    choices: list[ChatCompletionChunkChoice]
    usage: ChatCompletionUsage | None = None


class ErrorDetail(ContractModel):
    message: str
    type: str
    param: str | None = None
    code: str | None = None
    request_id: str | None = None


class ErrorResponse(ContractModel):
    error: ErrorDetail
