"""Adapter-facing data structures and the adapter protocol."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class TextContentPart:
    """A canonical text content part (ordered within a multimodal message)."""

    text: str
    type: str = "text"


@dataclass(frozen=True)
class ImageContentPart:
    """A canonical image content part.

    ``media_type`` is the normalized MIME type (``image/png``, ``image/jpeg``,
    ``image/webp``) and ``data`` is the decoded, validated image bytes. The bytes
    are never logged, put into metadata, or persisted in plaintext.
    """

    media_type: str
    data: bytes
    type: str = "image"


ContentPart = TextContentPart | ImageContentPart


@dataclass(frozen=True)
class Message:
    """A single canonical chat message.

    Text-only messages use ``content`` (a plain string). Multimodal messages set
    ``parts`` to an ordered tuple of :class:`TextContentPart`/
    :class:`ImageContentPart`. ``content`` remains the concatenated text for
    backward compatibility and text-only token estimation.
    """

    role: str
    content: str
    parts: tuple[ContentPart, ...] | None = None

    @property
    def has_images(self) -> bool:
        return self.parts is not None and any(
            isinstance(part, ImageContentPart) for part in self.parts
        )

    @property
    def images(self) -> tuple[ImageContentPart, ...]:
        if self.parts is None:
            return ()
        return tuple(p for p in self.parts if isinstance(p, ImageContentPart))


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
class EmbeddingRequest:
    """A fully resolved upstream embedding request.

    ``inputs`` are ordered, non-empty text strings in client order. ``base_destination``
    and ``secret`` are only ever derived from persisted administrator-controlled
    configuration and the trusted secret resolver.
    """

    provider_kind: str
    base_destination: str
    upstream_model: str
    inputs: tuple[str, ...]
    timeout_seconds: float = 120.0


@dataclass(frozen=True)
class EmbeddingResult:
    """A validated upstream embedding result (ordered numeric vectors)."""

    vectors: tuple[tuple[float, ...], ...]
    usage: Usage
    upstream_request_id: str | None


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

    async def embed(
        self, request: EmbeddingRequest, secret: str | None
    ) -> EmbeddingResult: ...

    def estimate_input_tokens(self, request: ChatRequest) -> int | None:
        """Return a conservative upper bound on input tokens, or ``None``.

        ``None`` means no provider/model-specific tokenizer is known to be usable
        for this route, so no defensible pre-dispatch estimate exists. Callers
        that enforce token quotas must fail closed rather than substitute a
        generic character/word heuristic. The returned value (when not ``None``)
        is a conservative estimate, never an authoritative provider token count.

        For requests carrying image parts, this returns ``None``: no
        provider/model-specific, defensible image-input token estimator is
        available, so token quotas and token-priced budgets fail closed rather
        than treating bytes/dimensions as fabricated token counts.
        """
        ...
