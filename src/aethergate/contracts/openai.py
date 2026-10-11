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

from aethergate.contracts.common import ContractModel

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
    """A single chat message. Multimodal/tool payloads are unsupported and rejected."""

    model_config = ConfigDict(extra="forbid")

    role: str
    content: str

    @field_validator("role")
    @classmethod
    def _supported_roles(cls, value: str) -> str:
        if value not in {"system", "user", "assistant"}:
            raise ValueError(
                f"unsupported message role {value!r}; only system/user/assistant are supported"
            )
        return value


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
