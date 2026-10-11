"""Inline image validation, normalization, and payload back-compat (no DB)."""

from __future__ import annotations

import base64

import pytest

from aethergate.adapters.base import ImageContentPart, Message, TextContentPart
from aethergate.contracts.openai import ChatCompletionRequest
from aethergate.contracts.responses import ResponsesRequest
from aethergate.errors import ImageLimitExceeded, UnsupportedFeature
from aethergate.inference.images import (
    ImageLimits,
    enforce_request_image_limits,
    parse_inline_image,
)
from aethergate.scheduler.service import (
    deserialize_chat_payload,
    serialize_chat_payload,
)
from vision_fixtures import (
    JPEG_DATA_URL,
    PNG_BYTES,
    PNG_DATA_URL,
    WEBP_DATA_URL,
    data_url,
)


def _chat(messages):
    return ChatCompletionRequest.model_validate({"model": "m", "messages": messages})


def test_parse_png_jpeg_webp_inline_data_urls():
    for url, expected in (
        (PNG_DATA_URL, "image/png"),
        (JPEG_DATA_URL, "image/jpeg"),
        (WEBP_DATA_URL, "image/webp"),
    ):
        part = parse_inline_image(url, max_image_bytes=10_000_000)
        assert part.media_type == expected
        assert part.data


def test_remote_image_url_rejected_without_fetch():
    for url in ("http://evil.example/x.png", "https://evil.example/x.png"):
        with pytest.raises(UnsupportedFeature) as info:
            parse_inline_image(url, max_image_bytes=1000)
        assert info.value.feature == "image_remote_url"


def test_file_path_and_non_data_sources_rejected():
    for url in ("/etc/passwd", "file:///etc/passwd", "ftp://x/y.png", ""):
        with pytest.raises(UnsupportedFeature):
            parse_inline_image(url, max_image_bytes=1000)


def test_unsupported_media_type_rejected():
    url = data_url("image/svg+xml", b"<svg/>")
    with pytest.raises(UnsupportedFeature) as info:
        parse_inline_image(url, max_image_bytes=1000)
    assert "image_media_type" in info.value.feature


def test_valid_webp_is_accepted():
    part = parse_inline_image(WEBP_DATA_URL, max_image_bytes=10_000_000)
    assert part.media_type == "image/webp"
    assert part.data.startswith(b"RIFF")
    assert part.data[8:12] == b"WEBP"


def _riff(form_type: bytes, body: bytes = b"") -> bytes:
    """Build a minimal RIFF container with the given 4-byte form type."""
    chunk = form_type + body
    return b"RIFF" + (len(chunk)).to_bytes(4, "little") + chunk


def test_webp_labeled_riff_wave_rejected():
    # RIFF/WAVE (audio) mislabeled as WebP must be rejected: RIFF alone is not
    # a WebP container signature.
    payload = _riff(b"WAVE", b"\x00" * 16)
    with pytest.raises(UnsupportedFeature) as info:
        parse_inline_image(data_url("image/webp", payload), max_image_bytes=1_000_000)
    assert info.value.feature == "image_signature_mismatch"


def test_webp_labeled_riff_avi_rejected():
    payload = _riff(b"AVI ", b"\x00" * 16)
    with pytest.raises(UnsupportedFeature) as info:
        parse_inline_image(data_url("image/webp", payload), max_image_bytes=1_000_000)
    assert info.value.feature == "image_signature_mismatch"


def test_webp_labeled_arbitrary_riff_rejected():
    payload = _riff(b"XXXX", b"\x00" * 16)
    with pytest.raises(UnsupportedFeature) as info:
        parse_inline_image(data_url("image/webp", payload), max_image_bytes=1_000_000)
    assert info.value.feature == "image_signature_mismatch"


def test_webp_body_without_riff_container_rejected():
    # Starts with the WEBP fourcc but has no RIFF container header.
    payload = b"WEBP" + b"\x00" * 20
    with pytest.raises(UnsupportedFeature) as info:
        parse_inline_image(data_url("image/webp", payload), max_image_bytes=1_000_000)
    assert info.value.feature == "image_signature_mismatch"


def test_webp_too_short_header_rejected():
    # "RIFF" with fewer than 12 header bytes cannot carry the WEBP fourcc.
    with pytest.raises(UnsupportedFeature):
        parse_inline_image(
            data_url("image/webp", b"RIFF\x00\x00\x00\x00WEB"),
            max_image_bytes=1_000_000,
        )


def test_webp_signature_error_never_reflects_bytes_or_base64():
    payload = _riff(b"WAVE", b"\x00" * 16)
    encoded = base64.b64encode(payload).decode("ascii")
    with pytest.raises(UnsupportedFeature) as info:
        parse_inline_image(data_url("image/webp", payload), max_image_bytes=1_000_000)
    assert encoded not in str(info.value)
    assert "RIFF" not in str(info.value)
    assert "WAVE" not in str(info.value)


def test_malformed_data_url_rejected():
    with pytest.raises(UnsupportedFeature) as info:
        parse_inline_image("data:image/png;base64", max_image_bytes=1000)
    assert info.value.feature == "image_data_url"


def test_bad_base64_rejected():
    url = "data:image/png;base64,!!!!notbase64!!!!"
    with pytest.raises(UnsupportedFeature) as info:
        parse_inline_image(url, max_image_bytes=1000)
    assert info.value.feature == "image_base64"


def test_empty_image_rejected():
    url = "data:image/png;base64,"
    with pytest.raises(UnsupportedFeature) as info:
        parse_inline_image(url, max_image_bytes=1000)
    # Empty payload fails the data-url/base64 pattern, never accepted.
    assert info.value.feature in ("image_data_url", "image_base64", "image_empty")


def test_mime_signature_mismatch_rejected():
    # JPEG bytes labeled PNG.
    from vision_fixtures import JPEG_BYTES

    url = data_url("image/png", JPEG_BYTES)
    with pytest.raises(UnsupportedFeature) as info:
        parse_inline_image(url, max_image_bytes=10_000_000)
    assert info.value.feature == "image_signature_mismatch"


def test_oversized_image_rejected():
    with pytest.raises(ImageLimitExceeded) as info:
        parse_inline_image(PNG_DATA_URL, max_image_bytes=10)
    assert info.value.limit == "image_too_large"


def test_error_messages_never_contain_image_bytes_or_base64():
    encoded = base64.b64encode(PNG_BYTES).decode("ascii")
    with pytest.raises(ImageLimitExceeded) as info:
        parse_inline_image(PNG_DATA_URL, max_image_bytes=10)
    assert encoded not in str(info.value)
    assert PNG_DATA_URL not in str(info.value)


def test_chat_maps_image_to_canonical_parts():
    req = _chat(
        [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "look "},
                    {"type": "image_url", "image_url": {"url": PNG_DATA_URL}},
                ],
            }
        ]
    )
    messages = req.to_canonical_messages()
    assert messages[0].content == "look "
    kinds = [type(p).__name__ for p in messages[0].parts]
    assert kinds == ["TextContentPart", "ImageContentPart"]
    assert messages[0].has_images


def test_chat_multiple_images_and_order_preserved():
    req = _chat(
        [
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": PNG_DATA_URL}},
                    {"type": "text", "text": " vs "},
                    {"type": "image_url", "image_url": {"url": JPEG_DATA_URL}},
                ],
            }
        ]
    )
    parts = req.to_canonical_messages()[0].parts
    assert [type(p).__name__ for p in parts] == [
        "ImageContentPart",
        "TextContentPart",
        "ImageContentPart",
    ]


def test_chat_remote_url_rejected_end_to_end():
    req = _chat(
        [
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": "https://x/y.png"}}
                ],
            }
        ]
    )
    with pytest.raises(UnsupportedFeature) as info:
        req.to_canonical_messages()
    assert info.value.feature == "image_remote_url"


def test_chat_string_image_url_form_rejected_when_remote():
    req = _chat(
        [
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": "https://x/y.png"}
                ],
            }
        ]
    )
    with pytest.raises(UnsupportedFeature):
        req.to_canonical_messages()


def test_image_count_limit_enforced_across_messages():
    limits = ImageLimits(max_images_per_request=1)
    image_part = {"type": "image_url", "image_url": {"url": PNG_DATA_URL}}
    req = _chat(
        [
            {"role": "user", "content": [image_part]},
            {"role": "user", "content": [image_part]},
        ]
    )
    with pytest.raises(ImageLimitExceeded):
        req.to_canonical_messages(limits)


def test_total_image_bytes_limit_enforced():
    limits = ImageLimits(
        max_images_per_request=10, max_image_bytes=10_000_000, max_total_image_bytes=1
    )
    req = _chat(
        [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": PNG_DATA_URL}}]}]
    )
    with pytest.raises(ImageLimitExceeded) as info:
        req.to_canonical_messages(limits)
    assert info.value.limit == "total_image_bytes"


def test_enforce_request_image_limits_direct():
    parts = [ImageContentPart("image/png", PNG_BYTES)]
    with pytest.raises(ImageLimitExceeded):
        enforce_request_image_limits(
            parts, max_images_per_request=0, max_total_image_bytes=10**9
        )
    with pytest.raises(ImageLimitExceeded):
        enforce_request_image_limits(
            parts, max_images_per_request=4, max_total_image_bytes=1
        )


def _chat_image(image_url: str, **image_url_extra) -> dict:
    inner = {"url": image_url}
    inner.update(image_url_extra)
    return {"type": "image_url", "image_url": inner}


@pytest.mark.parametrize("detail", [None, "auto"])
def test_chat_omitted_and_auto_detail_succeed(detail):
    part = _chat_image(PNG_DATA_URL)
    if detail is not None:
        part["image_url"]["detail"] = detail
    req = _chat([{"role": "user", "content": [part]}])
    messages = req.to_canonical_messages()
    assert messages[0].has_images


@pytest.mark.parametrize("detail", ["low", "high", "original"])
def test_chat_non_default_detail_rejected(detail):
    req = _chat(
        [{"role": "user", "content": [_chat_image(PNG_DATA_URL, detail=detail)]}]
    )
    with pytest.raises(UnsupportedFeature) as info:
        req.to_canonical_messages()
    assert "image_detail" in info.value.feature
    assert info.value.param == "messages[user].content[0].image_url.detail"


def test_chat_invalid_detail_enum_rejected():
    req = _chat(
        [{"role": "user", "content": [_chat_image(PNG_DATA_URL, detail="ultra")]}]
    )
    with pytest.raises(UnsupportedFeature) as info:
        req.to_canonical_messages()
    assert "image_detail" in info.value.feature


def test_chat_non_string_detail_rejected():
    req = _chat(
        [{"role": "user", "content": [_chat_image(PNG_DATA_URL, detail=2)]}]
    )
    with pytest.raises(UnsupportedFeature) as info:
        req.to_canonical_messages()
    assert "image_detail" in info.value.feature


def _responses_image(url: str, **part_extra) -> dict:
    part = {"type": "input_image", "image_url": url}
    part.update(part_extra)
    return part


@pytest.mark.parametrize("detail", [None, "auto"])
def test_responses_omitted_and_auto_detail_succeed(detail):
    part = _responses_image(PNG_DATA_URL)
    if detail is not None:
        part["detail"] = detail
    req = ResponsesRequest.model_validate(
        {"model": "m", "input": [{"role": "user", "content": [part]}]}
    )
    messages = req.to_canonical_messages()
    assert messages[0].has_images


@pytest.mark.parametrize("detail", ["low", "high", "original"])
def test_responses_non_default_detail_rejected(detail):
    req = ResponsesRequest.model_validate(
        {
            "model": "m",
            "input": [
                {"role": "user", "content": [_responses_image(PNG_DATA_URL, detail=detail)]}
            ],
        }
    )
    with pytest.raises(UnsupportedFeature) as info:
        req.to_canonical_messages()
    assert "image_detail" in info.value.feature
    assert info.value.param == "input[0].content[0].detail"


def test_responses_invalid_detail_enum_rejected():
    req = ResponsesRequest.model_validate(
        {
            "model": "m",
            "input": [
                {"role": "user", "content": [_responses_image(PNG_DATA_URL, detail="tiny")]}
            ],
        }
    )
    with pytest.raises(UnsupportedFeature) as info:
        req.to_canonical_messages()
    assert "image_detail" in info.value.feature


def test_responses_maps_input_image_to_canonical_parts():
    req = ResponsesRequest.model_validate(
        {
            "model": "m",
            "input": [
                {
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": "look "},
                        {"type": "input_image", "image_url": PNG_DATA_URL, "detail": "auto"},
                    ],
                }
            ],
        }
    )
    messages = req.to_canonical_messages()
    assert messages[0].content == "look "
    assert [type(p).__name__ for p in messages[0].parts] == [
        "TextContentPart",
        "ImageContentPart",
    ]


def test_legacy_text_payload_deserializes_exactly_as_before():
    """A pre-AGV2-028 text payload (no ``parts`` key) round-trips unchanged."""
    legacy = (
        b'{"messages": [{"role": "user", "content": "legacy text"}], '
        b'"params": {"temperature": 0.5, "max_tokens": 7}}'
    )
    messages, params = deserialize_chat_payload(legacy)
    assert messages[0].role == "user"
    assert messages[0].content == "legacy text"
    assert messages[0].parts is None
    assert messages[0].has_images is False
    assert params.temperature == 0.5
    assert params.max_tokens == 7


def test_multimodal_payload_round_trips_without_plaintext_image_leak():
    message = Message(
        role="user",
        content="look ",
        parts=(
            TextContentPart(text="look "),
            ImageContentPart("image/png", PNG_BYTES),
        ),
    )
    payload = serialize_chat_payload([message], __import__(
        "aethergate.adapters.base", fromlist=["GenerationParams"]
    ).GenerationParams())
    messages, _ = deserialize_chat_payload(payload)
    assert messages[0].parts[1].media_type == "image/png"
    assert messages[0].parts[1].data == PNG_BYTES
