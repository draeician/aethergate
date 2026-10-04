"""Adapter-facing data structures and the adapter protocol."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class Message:
    """A single text chat message."""

    role: str
    content: str


@dataclass(frozen=True)
class GenerationParams:
    """Validated, provider-neutral generation parameters."""

    temperature: float | None = None
    top_p: float | None = None
    max_tokens: int | None = None
    stop: str | list[str] | None = None
    frequency_penalty: float | None = None
    presence_penalty: float | None = None
    seed: int | None = None


@dataclass(frozen=True)
class ChatRequest:
    """A fully resolved upstream completion request.

    ``base_destination`` and ``secret`` are only ever derived from persisted,
    administrator-controlled configuration and the trusted secret resolver.
    """

    provider_kind: str
    base_destination: str
    upstream_model: str
    messages: tuple[Message, ...]
    params: GenerationParams = field(default_factory=GenerationParams)
    timeout_seconds: float = 120.0


@dataclass(frozen=True)
class Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


@dataclass(frozen=True)
class CompletionResult:
    content: str
    finish_reason: str | None
    usage: Usage
    upstream_request_id: str | None


@dataclass(frozen=True)
class StreamChunk:
    content: str | None
    finish_reason: str | None
    usage: Usage | None


@runtime_checkable
class ChatAdapter(Protocol):
    async def complete(
        self, request: ChatRequest, secret: str | None
    ) -> CompletionResult: ...

    def stream(
        self, request: ChatRequest, secret: str | None
    ) -> AsyncIterator[StreamChunk]: ...

    def estimate_input_tokens(self, request: ChatRequest) -> int | None:
        """Return a conservative upper bound on input tokens, or ``None``.

        ``None`` means no provider/model-specific tokenizer is known to be usable
        for this route, so no defensible pre-dispatch estimate exists. Callers
        that enforce token quotas must fail closed rather than substitute a
        generic character/word heuristic. The returned value (when not ``None``)
        is a conservative estimate, never an authoritative provider token count.
        """
        ...
