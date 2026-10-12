"""OpenAI-compatible Embeddings request/response DTOs.

These shapes are pinned to the OpenAI compatibility baseline
(``docs/contracts/openai-compatibility-baseline.md``) and verified against the
locked official SDK clients (``openai==2.54.0``, ``openai@7.32.0``). The request
DTO tolerates unknown *additive* fields but explicitly rejects transport/
provider-control fields and every known semantic field this milestone does not
faithfully implement (never silently dropping a semantic field).

Supported input is one non-empty string or a non-empty ordered list of up to
``MAX_EMBEDDING_INPUTS`` non-empty strings. Token-ID inputs (``number[]`` /
``number[][]``), ``dimensions``, and ``user`` are explicitly rejected with
``400 unsupported_feature``.

The pinned OpenAI request schema marks ``encoding_format``/``dimensions``/``user``
as *optional but not nullable* (Python ``openai==2.54.0`` ``EmbeddingCreateParams``
is ``total=False``; JS ``openai@7.32.0`` uses ``?`` without ``| null``). An omitted
key means "use the server default"; an explicit JSON ``null`` is a contract
violation and is rejected rather than silently treated as omission.
"""

from __future__ import annotations

import base64
import struct
import time
from typing import Any, Literal

from pydantic import ConfigDict, Field, model_validator

from aethergate.contracts.common import ContractModel
from aethergate.errors import InvalidRequest, UnsupportedFeature

#: Maximum number of embedding inputs accepted in one request.
#:
#: Source: the pinned upstream OpenAI OpenAPI reference
#: (``docs/contracts/openai-compatibility-baseline.md``: commit
#: ``f7bc81b7f30e834bbdb6bcc3370903e49fe7e5b1``), schema
#: ``CreateEmbeddingRequest.input``, whose array forms declare
#: ``minItems: 1`` / ``maxItems: 2048``. This is the provider-neutral,
#: OpenAI-compatible request-shape bound; it is deliberately not an
#: Ollama-specific limit and is enforced independently of the queue payload
#: byte bound.
MAX_EMBEDDING_INPUTS = 2048

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


def encode_base64_float32(vector: list[float]) -> str:
    """Encode a float vector as the pinned SDK's little-endian Float32 base64.

    ``openai@7.32.0`` decodes a base64 embedding with ``Float32Array`` (native
    little-endian) and ``openai==2.54.0`` with ``array.array("f")``; both require
    exactly this IEEE-754 little-endian single-precision byte representation.
    """
    return base64.b64encode(struct.pack(f"<{len(vector)}f", *vector)).decode("ascii")


class EmbeddingCreateRequest(ContractModel):
    """Pinned Embeddings request surface (string input, float/base64 encoding)."""

    model_config = ConfigDict(extra="ignore")

    model: str = Field(min_length=1)
    input: Any
    encoding_format: Literal["float", "base64"] | None = None

    # Known semantic fields not faithfully implemented; declared only so their
    # presence is detectable and rejected (never silently ignored).
    dimensions: Any = None
    user: Any = None

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

    @model_validator(mode="before")
    @classmethod
    def _reject_presence_and_null_semantics(cls, data: Any) -> Any:
        """Distinguish key absence from explicit JSON ``null``.

        ``encoding_format``/``dimensions``/``user`` are optional but not nullable
        in the pinned SDK contracts. A missing key is an accepted omission; a
        present key is authoritative. ``dimensions``/``user`` are rejected for
        *any* supplied value (including ``null``); ``encoding_format: null`` is an
        invalid value (never silently defaulted to ``float``).
        """
        if not isinstance(data, dict):
            return data
        if "encoding_format" in data and data["encoding_format"] is None:
            raise InvalidRequest(
                "encoding_format must not be null; omit it for the default",
                param="encoding_format",
            )
        if "dimensions" in data:
            raise UnsupportedFeature("dimensions", param="dimensions")
        if "user" in data:
            raise UnsupportedFeature("user", param="user")
        return data

    @model_validator(mode="after")
    def _reject_transport_controls(self) -> EmbeddingCreateRequest:
        for name in _TRANSPORT_CONTROL_FIELDS:
            if getattr(self, name) is not None:
                raise UnsupportedFeature(f"transport_override:{name}", param=name)
        return self

    def to_canonical_inputs(self) -> tuple[str, ...]:
        """Normalize ``input`` into an ordered tuple of non-empty text strings.

        Token-ID inputs are never coerced to strings: they are rejected as an
        explicit unsupported feature.
        """
        return _normalize_embedding_input(self.input)


def _normalize_embedding_input(raw: Any) -> tuple[str, ...]:
    if isinstance(raw, str):
        if raw == "":
            raise UnsupportedFeature("embedding_empty_input", param="input")
        return (raw,)
    if isinstance(raw, list):
        if not raw:
            raise UnsupportedFeature("embedding_empty_input", param="input")
        if all(isinstance(item, str) for item in raw):
            if any(item == "" for item in raw):
                raise UnsupportedFeature("embedding_empty_input", param="input")
            if len(raw) > MAX_EMBEDDING_INPUTS:
                raise InvalidRequest(
                    "input exceeds the maximum of "
                    f"{MAX_EMBEDDING_INPUTS} embedding inputs",
                    param="input",
                )
            return tuple(raw)
        if all(isinstance(item, (int, float)) and not isinstance(item, bool) for item in raw):
            raise UnsupportedFeature("embedding_token_ids", param="input")
        if all(isinstance(item, list) for item in raw):
            raise UnsupportedFeature("embedding_token_ids", param="input")
    raise UnsupportedFeature("embedding_input", param="input")


def _now() -> int:
    return int(time.time())


class EmbeddingData(ContractModel):
    """A single embedding entry (numeric floats or a base64 Float32 string)."""

    index: int
    object: Literal["embedding"] = "embedding"
    embedding: list[float] | str


class EmbeddingUsage(ContractModel):
    prompt_tokens: int
    total_tokens: int


class CreateEmbeddingResponse(ContractModel):
    data: list[EmbeddingData]
    model: str
    object: Literal["list"] = "list"
    usage: EmbeddingUsage
