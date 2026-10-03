"""Tests for typed identifiers and monetary value objects."""

from __future__ import annotations

from decimal import Decimal

import pytest
from pydantic import ValidationError

from aethergate.domain.ids import (
    EndpointId,
    ModelAliasId,
    ProjectId,
    ResourceId,
)
from aethergate.domain.value_objects import Money, NonNegativeMoney


def test_identifiers_are_distinct_runtime_types():
    assert ModelAliasId("x") != EndpointId("x")
    assert not isinstance(ModelAliasId("x"), EndpointId)
    assert isinstance(ModelAliasId("x"), str)
    assert isinstance(ModelAliasId("x"), ResourceId)


def test_identifier_rejects_empty_and_non_string():
    with pytest.raises(ValueError):
        ProjectId("")
    with pytest.raises(ValueError):
        ProjectId("   ")
    with pytest.raises(TypeError):
        ProjectId(123)  # type: ignore[arg-type]


def test_money_rejects_float_and_uses_decimal():
    assert Money.__name__ != "float"
    with pytest.raises(ValidationError):
        _validate_money(0.01)
    assert _validate_money(Decimal("0.01")) == Decimal("0.01")
    assert _validate_money("0.01") == Decimal("0.01")


def test_non_negative_money_rejects_negative():
    assert _validate_non_negative(Decimal("0.00")) == Decimal("0.00")
    with pytest.raises(ValidationError):
        _validate_non_negative(Decimal("-0.01"))


def _validate_money(value: object) -> Decimal:
    from pydantic import TypeAdapter

    return TypeAdapter(Money).validate_python(value)


def _validate_non_negative(value: object) -> Decimal:
    from pydantic import TypeAdapter

    return TypeAdapter(NonNegativeMoney).validate_python(value)
