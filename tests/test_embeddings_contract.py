"""Embeddings contract and base64 encoding unit tests (offline, no DB)."""

from __future__ import annotations

import pytest

from aethergate.contracts.embeddings import (
    MAX_EMBEDDING_INPUTS,
    CreateEmbeddingResponse,
    EmbeddingCreateRequest,
    EmbeddingData,
    EmbeddingUsage,
    encode_base64_float32,
)
from aethergate.errors import InvalidRequest, UnsupportedFeature


def _parse(payload: dict) -> EmbeddingCreateRequest:
    return EmbeddingCreateRequest.model_validate(payload)


def test_single_string_input_normalizes_to_tuple():
    req = _parse({"model": "m", "input": "hello"})
    assert req.to_canonical_inputs() == ("hello",)


def test_list_of_strings_preserves_order():
    req = _parse({"model": "m", "input": ["a", "b", "c"]})
    assert req.to_canonical_inputs() == ("a", "b", "c")


def test_empty_string_input_rejected():
    req = _parse({"model": "m", "input": ""})
    with pytest.raises(UnsupportedFeature):
        req.to_canonical_inputs()


def test_empty_list_input_rejected():
    req = _parse({"model": "m", "input": []})
    with pytest.raises(UnsupportedFeature):
        req.to_canonical_inputs()


def test_list_with_empty_string_rejected():
    req = _parse({"model": "m", "input": ["a", ""]})
    with pytest.raises(UnsupportedFeature):
        req.to_canonical_inputs()


def test_token_id_array_rejected_as_unsupported():
    req = _parse({"model": "m", "input": [1, 2, 3]})
    with pytest.raises(UnsupportedFeature) as excinfo:
        req.to_canonical_inputs()
    assert excinfo.value.feature == "embedding_token_ids"


def test_token_id_array_of_arrays_rejected_as_unsupported():
    req = _parse({"model": "m", "input": [[1, 2], [3, 4]]})
    with pytest.raises(UnsupportedFeature) as excinfo:
        req.to_canonical_inputs()
    assert excinfo.value.feature == "embedding_token_ids"


def test_encoding_format_omitted_is_accepted():
    req = _parse({"model": "m", "input": "hi"})
    assert req.encoding_format is None
    assert "encoding_format" not in req.model_fields_set


@pytest.mark.parametrize("encoding", ["float", "base64"])
def test_encoding_format_supported_values_accepted(encoding):
    req = _parse({"model": "m", "input": "hi", "encoding_format": encoding})
    assert req.encoding_format == encoding


def test_encoding_format_explicit_null_rejected():
    with pytest.raises(InvalidRequest) as excinfo:
        _parse({"model": "m", "input": "hi", "encoding_format": None})
    assert excinfo.value.param == "encoding_format"


def test_dimensions_rejected_as_unsupported():
    with pytest.raises(UnsupportedFeature) as excinfo:
        _parse({"model": "m", "input": "hi", "dimensions": 512})
    assert excinfo.value.feature == "dimensions"


def test_dimensions_explicit_null_rejected_as_unsupported():
    with pytest.raises(UnsupportedFeature) as excinfo:
        _parse({"model": "m", "input": "hi", "dimensions": None})
    assert excinfo.value.feature == "dimensions"


def test_user_rejected_as_unsupported():
    with pytest.raises(UnsupportedFeature) as excinfo:
        _parse({"model": "m", "input": "hi", "user": "u-123"})
    assert excinfo.value.feature == "user"


def test_user_explicit_null_rejected_as_unsupported():
    with pytest.raises(UnsupportedFeature) as excinfo:
        _parse({"model": "m", "input": "hi", "user": None})
    assert excinfo.value.feature == "user"


def test_omitted_dimensions_and_user_are_accepted():
    req = _parse({"model": "m", "input": "hi"})
    assert "dimensions" not in req.model_fields_set
    assert "user" not in req.model_fields_set


def test_input_count_at_max_accepted():
    req = _parse({"model": "m", "input": ["x"] * MAX_EMBEDDING_INPUTS})
    assert len(req.to_canonical_inputs()) == MAX_EMBEDDING_INPUTS


def test_input_count_over_max_rejected():
    req = _parse({"model": "m", "input": ["x"] * (MAX_EMBEDDING_INPUTS + 1)})
    with pytest.raises(InvalidRequest) as excinfo:
        req.to_canonical_inputs()
    assert excinfo.value.param == "input"


def test_transport_control_field_rejected():
    with pytest.raises(UnsupportedFeature):
        _parse({"model": "m", "input": "hi", "api_base": "http://evil"})


def test_unknown_additive_field_is_ignored():
    req = _parse({"model": "m", "input": "hi", "some_future_field": 42})
    assert req.to_canonical_inputs() == ("hi",)


def test_base64_float32_is_little_endian_single_precision():
    # [1.0, 2.0, 3.0] as IEEE-754 little-endian float32 -> the exact bytes the
    # pinned JS SDK decodes with Float32Array.
    assert encode_base64_float32([1.0, 2.0, 3.0]) == "AACAPwAAAEAAAEBA"


def test_response_shape_serializes_usage_without_completion_tokens():
    resp = CreateEmbeddingResponse(
        data=[EmbeddingData(index=0, embedding=[1.0, 2.0])],
        model="gpt-4",
        usage=EmbeddingUsage(prompt_tokens=7, total_tokens=7),
    )
    dumped = resp.model_dump()
    assert dumped["object"] == "list"
    assert dumped["data"][0]["object"] == "embedding"
    assert dumped["data"][0]["index"] == 0
    assert dumped["usage"] == {"prompt_tokens": 7, "total_tokens": 7}
    assert "completion_tokens" not in dumped["usage"]
