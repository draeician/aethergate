"""Persistence-independent value objects.

Monetary values are fixed-point ``Decimal``; binary floating point (``float``)
is rejected outright for money and pricing contracts.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated

from pydantic import BeforeValidator


def _to_decimal(value: object) -> Decimal:
    if isinstance(value, float):
        raise ValueError("monetary values must use Decimal, not float")
    if isinstance(value, Decimal):
        return value
    if isinstance(value, (str, int)):
        return Decimal(str(value))
    raise TypeError(f"unsupported monetary type {type(value).__name__}")


def _to_non_negative_decimal(value: object) -> Decimal:
    decimal_value = _to_decimal(value)
    if decimal_value < 0:
        raise ValueError("monetary value must be non-negative")
    return decimal_value


# A monetary amount (fixed-point). Never float.
Money = Annotated[Decimal, BeforeValidator(_to_decimal)]

# A non-negative monetary amount, e.g. a price or a budget allowance.
NonNegativeMoney = Annotated[Decimal, BeforeValidator(_to_non_negative_decimal)]


__all__ = ["Money", "NonNegativeMoney"]
