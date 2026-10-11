"""Accounting service: pure monetary computation and settlement orchestration.

Monetary amounts are ``Decimal`` end-to-end and quantized to the fixed money
precision. No binary floating point is used for prices, budgets, reservations,
or ledger amounts.
"""

from __future__ import annotations

from decimal import Decimal

from aethergate.domain.enums import BillingUnit
from aethergate.domain.value_objects import quantize_money
from aethergate.persistence import models


def _require_price(value: Decimal | None, field: str) -> Decimal:
    if value is None:
        raise ValueError(f"{field} is required for this billing unit")
    return value


def request_reservation_amount(price: models.PricePolicy | models.PriceSnapshot) -> Decimal:
    """Exact per-request monetary amount for a request-priced route."""
    if price.billing_unit != BillingUnit.REQUEST.value:
        raise ValueError("request_reservation_amount requires a request-priced route")
    return quantize_money(_require_price(price.request_price, "request_price"))


def token_amount(
    price: models.PricePolicy | models.PriceSnapshot,
    *,
    input_units: int,
    output_units: int,
) -> Decimal:
    """Monetary amount for token units under a token-priced route.

    Uses the positive integer ``unit_scale`` so prices are expressed per
    ``unit_scale`` units (e.g. price per 1,000,000 tokens).
    """
    if price.billing_unit != BillingUnit.TOKEN.value:
        raise ValueError("token_amount requires a token-priced route")
    scale = Decimal(str(price.unit_scale))
    if scale <= 0:
        raise ValueError("unit_scale must be positive")
    input_price = _require_price(price.input_price, "input_price")
    output_price = _require_price(price.output_price, "output_price")
    amount = (
        Decimal(input_units) * input_price
        + Decimal(output_units) * output_price
    ) / scale
    return quantize_money(amount)


def reservation_amount(
    price: models.PricePolicy | models.PriceSnapshot,
    *,
    input_units: int,
    output_units: int,
) -> Decimal:
    """The monetary amount to reserve for a request under ``price``."""
    if price.billing_unit == BillingUnit.REQUEST.value:
        return request_reservation_amount(price)
    return token_amount(price, input_units=input_units, output_units=output_units)
