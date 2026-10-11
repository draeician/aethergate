"""Persistence-independent value objects.

Monetary values are fixed-point ``Decimal``; binary floating point (``float``)
is rejected outright for money and pricing contracts.
"""

from __future__ import annotations

import re
from decimal import ROUND_HALF_UP, Decimal
from typing import Annotated

from pydantic import BeforeValidator, PlainSerializer

# Fixed-point money quantization. 12 decimal places is enough for very small
# per-token prices (e.g. a price of 2.50 per 1,000,000 tokens is 0.0000025 per
# token) while leaving ample integer digits for large enterprise totals.
MONEY_PRECISION = 12
MONEY_QUANTUM = Decimal("1e-12")

_CURRENCY_RE = re.compile(r"^[A-Z]{3}$")


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


def _to_positive_decimal(value: object) -> Decimal:
    decimal_value = _to_decimal(value)
    if decimal_value <= 0:
        raise ValueError("monetary value must be positive")
    return decimal_value


def _to_currency(value: object) -> str:
    if not isinstance(value, str):
        raise TypeError(f"currency must be a str, got {type(value).__name__}")
    normalized = value.strip().upper()
    if not _CURRENCY_RE.fullmatch(normalized):
        raise ValueError("currency must be an uppercase ISO-style 3-letter code")
    return normalized


def quantize_money(value: Decimal) -> Decimal:
    """Quantize a monetary amount to the fixed money precision (half-up)."""
    if not isinstance(value, Decimal):
        value = _to_decimal(value)
    return value.quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP)


def _serialize_money(value: Decimal) -> str:
    """Serialize money as a canonical fixed-point string.

    ``str(Decimal)`` emits scientific notation for small magnitudes (e.g.
    ``2.00E-10``), which is an exact Decimal but not a fixed-point
    representation. The wire contract documents money as fixed-point
    ``Numeric(24,12)``; serialize it as a plain fixed-point string (trailing
    zeros stripped) so clients see ``0.0000000002`` rather than ``2.00E-10``.
    No float is ever involved.
    """
    return format(value.normalize(), "f")


# A monetary amount (fixed-point). Never float.
Money = Annotated[
    Decimal,
    BeforeValidator(_to_decimal),
    PlainSerializer(_serialize_money, return_type=str, when_used="json"),
]

# A non-negative monetary amount, e.g. a price or a budget allowance.
NonNegativeMoney = Annotated[
    Decimal,
    BeforeValidator(_to_non_negative_decimal),
    PlainSerializer(_serialize_money, return_type=str, when_used="json"),
]

# A strictly positive monetary amount (e.g. a budget limit).
PositiveMoney = Annotated[
    Decimal,
    BeforeValidator(_to_positive_decimal),
    PlainSerializer(_serialize_money, return_type=str, when_used="json"),
]

# An explicit, normalized uppercase ISO-style three-letter currency code.
Currency = Annotated[str, BeforeValidator(_to_currency)]


__all__ = [
    "MONEY_PRECISION",
    "MONEY_QUANTUM",
    "Money",
    "NonNegativeMoney",
    "PositiveMoney",
    "Currency",
    "quantize_money",
]
