"""Inline image validation and normalization for vision inference.

AetherGate accepts image input only as inline ``data:`` URLs (the pinned
OpenAI-compatible inline representation). It never fetches a client-supplied
URL, never reads a provider-local path, and never accepts ``file_id`` references,
so client image input cannot become a second egress/SSRF mechanism.

Validation is strict and bounded:

- the media type must be in a small allowlist (PNG/JPEG/WebP) whose decoded
  bytes have a coherent magic-byte signature;
- base64 decoding must be strict (no whitespace/URL-safe alphabet);
- empty payloads are rejected;
- per-image byte, per-request image count, and per-request total byte limits are
  enforced *before* durable enqueue;
- error messages never contain image bytes, base64, or full data URLs.

No decompression/decoding library (e.g. Pillow) is invoked: formats are checked
by magic bytes only, so no image parser runs on untrusted input.
"""

from __future__ import annotations

import base64
import binascii
import re
from dataclasses import dataclass

from aethergate.adapters.base import ImageContentPart
from aethergate.errors import ImageLimitExceeded, UnsupportedFeature

# Conservative defaults matching :class:`~aethergate.config.Settings` (used only
# when a contract is mapped without an explicit runtime settings object).
DEFAULT_MAX_IMAGES_PER_REQUEST = 4
DEFAULT_MAX_IMAGE_BYTES = 8 * 1024 * 1024
DEFAULT_MAX_TOTAL_IMAGE_BYTES = 16 * 1024 * 1024
DEFAULT_MAX_QUEUE_PAYLOAD_BYTES = 24 * 1024 * 1024


@dataclass(frozen=True)
class ImageLimits:
    """Explicit, configurable inline-image limits enforced before enqueue."""

    max_images_per_request: int = DEFAULT_MAX_IMAGES_PER_REQUEST
    max_image_bytes: int = DEFAULT_MAX_IMAGE_BYTES
    max_total_image_bytes: int = DEFAULT_MAX_TOTAL_IMAGE_BYTES
    max_queue_payload_bytes: int = DEFAULT_MAX_QUEUE_PAYLOAD_BYTES

# Proven inline image media types. SVG is intentionally excluded (not proven
# safe/supported by the pinned provider path).
_ALLOWED_MEDIA_TYPES = ("image/png", "image/jpeg", "image/webp")

# Image `detail` values recognized by the pinned OpenAI contracts.
# - Chat `image_url.detail`: Python `openai==2.54.0` `ImageURL` =
#   ``Literal['auto', 'low', 'high']``; JS `openai@7.32.0`
#   `ChatCompletionContentPartImage.ImageURL` adds ``'original'``.
# - Responses `input_image.detail`: Python `ResponseInputImageParam` =
#   ``Literal['low', 'high', 'auto', 'original']`` ("Defaults to `auto`"); JS
#   `ResponseInputImageContent.detail?: ImageDetail | null`.
# ``auto`` is the neutral/default mode. Every other pinned value
# (``low``/``high``/``original``) is a non-default semantic fidelity mode the
# current LiteLLM/Ollama path cannot faithfully implement, so it is explicitly
# rejected (never silently dropped). Any unrecognized value is rejected too.
_NEUTRAL_IMAGE_DETAILS = ("auto",)

_DATA_URL_RE = re.compile(r"^data:([A-Za-z0-9.+-]+/[A-Za-z0-9.+-]+);base64,(.+)$", re.DOTALL)


def _is_base64(value: str) -> bool:
    """Strictly whether ``value`` is canonical base64 with no whitespace."""
    if not value:
        return False
    if len(value) % 4 != 0:
        return False
    return bool(re.fullmatch(r"[A-Za-z0-9+/]+={0,2}", value))


def _is_png(data: bytes) -> bool:
    return data.startswith(b"\x89PNG\r\n\x1a\n")


def _is_jpeg(data: bytes) -> bool:
    return data.startswith(b"\xff\xd8\xff")


def _is_webp(data: bytes) -> bool:
    """Strict WebP container signature: RIFF header + WEBP fourcc.

    A WebP file is a RIFF container whose form type (bytes 8:12) is ``WEBP``.
    RIFF alone is insufficient (RIFF/WAVE, RIFF/AVI, and arbitrary RIFF payloads
    are not WebP), so both the ``RIFF`` marker at bytes 0:4 and the ``WEBP``
    fourcc at bytes 8:12 must be present with a sufficient header length.
    """
    if len(data) < 12:
        return False
    return data[0:4] == b"RIFF" and data[8:12] == b"WEBP"


def _decoded_signature_matches(media_type: str, data: bytes) -> bool:
    """Per-format magic-byte validation (no generic abstraction to mis-apply)."""
    if media_type == "image/png":
        return _is_png(data)
    if media_type == "image/jpeg":
        return _is_jpeg(data)
    if media_type == "image/webp":
        return _is_webp(data)
    return False


def validate_image_detail(detail: object, *, param: str) -> None:
    """Validate an image ``detail`` selector against the pinned contract.

    ``None``/omitted is permitted (the pinned contracts defaulting to ``auto``).
    The neutral ``auto`` mode is permitted. Any other *recognized* semantic mode
    (``low``/``high``/``original``) is explicitly rejected — the pinned
    LiteLLM/Ollama path has no faithful non-default detail control, so accepting
    it would silently drop a semantic field. Unrecognized values are rejected as
    contract violations. Error text never contains image bytes or base64.
    """
    if detail is None:
        return
    if not isinstance(detail, str):
        raise UnsupportedFeature("image_detail", param=param)
    if detail in _NEUTRAL_IMAGE_DETAILS:
        return
    # Recognized non-default semantic modes (low/high/original) and any
    # unrecognized value are both explicit rejections; a recognized mode is
    # rejected because it is not faithfully implementable, an unrecognized one
    # because it violates the pinned contract.
    raise UnsupportedFeature(f"image_detail:{detail}", param=param)


def parse_inline_image(
    image_url: str,
    *,
    max_image_bytes: int,
    param: str = "image_url",
) -> ImageContentPart:
    """Parse and strictly validate one inline ``data:`` image URL.

    Raises :class:`UnsupportedFeature` (or :class:`ImageLimitExceeded`, a
    subclass) for any non-inline source, unsupported/mismatched media type,
    malformed base64, empty payload, or oversized image. Error text never
    includes the image bytes or base64.
    """
    if not isinstance(image_url, str) or not image_url:
        raise UnsupportedFeature("image_source", param=param)

    lowered = image_url.strip().lower()
    if lowered.startswith(("http://", "https://")):
        # Remote fetch is never performed; reject before any provider dispatch.
        raise UnsupportedFeature("image_remote_url", param=param)
    if not image_url.startswith("data:"):
        raise UnsupportedFeature("image_source", param=param)

    match = _DATA_URL_RE.match(image_url)
    if match is None:
        raise UnsupportedFeature("image_data_url", param=param)
    media_type = match.group(1).lower()
    payload = match.group(2)

    if media_type not in _ALLOWED_MEDIA_TYPES:
        raise UnsupportedFeature(f"image_media_type:{media_type}", param=param)
    if not _is_base64(payload):
        raise UnsupportedFeature("image_base64", param=param)

    # Bound decoded size before decoding to avoid allocating oversized input.
    # base64 expands ~4/3; a strict floor/ceiling check keeps this cheap.
    if (len(payload) // 4) * 3 - 3 > max_image_bytes:
        raise ImageLimitExceeded("image_too_large", param=param)

    try:
        data = base64.b64decode(payload, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise UnsupportedFeature("image_base64", param=param) from exc

    if not data:
        raise UnsupportedFeature("image_empty", param=param)
    if len(data) > max_image_bytes:
        raise ImageLimitExceeded("image_too_large", param=param)
    if not _decoded_signature_matches(media_type, data):
        raise UnsupportedFeature("image_signature_mismatch", param=param)

    return ImageContentPart(media_type=media_type, data=data)


def enforce_request_image_limits(
    parts: list[ImageContentPart],
    *,
    max_images_per_request: int,
    max_total_image_bytes: int,
) -> None:
    """Enforce per-request image count and total decoded-byte limits."""
    if len(parts) > max_images_per_request:
        raise ImageLimitExceeded(
            "too_many_images", param="messages.content[image]"
        )
    total = sum(len(part.data) for part in parts)
    if total > max_total_image_bytes:
        raise ImageLimitExceeded(
            "total_image_bytes", param="messages.content[image]"
        )


__all__ = [
    "ImageLimitExceeded",
    "parse_inline_image",
    "enforce_request_image_limits",
    "validate_image_detail",
]
