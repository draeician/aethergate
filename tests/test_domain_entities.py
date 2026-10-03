"""Tests for domain entity contracts."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError

from aethergate.domain.entities import (
    ModelAlias,
    PriceSnapshot,
    QuotaGroup,
    RouteBinding,
)
from aethergate.domain.enums import BillingUnit, RequestState
from aethergate.domain.ids import (
    EndpointId,
    ModelAliasId,
    PriceSnapshotId,
    ProviderAccountId,
    QuotaGroupId,
    RouteBindingId,
)


def _ts() -> datetime:
    return datetime(2026, 10, 3, 12, 0, 0, tzinfo=UTC)


def test_request_state_includes_outcome_unknown():
    assert RequestState.OUTCOME_UNKNOWN.value == "outcome_unknown"
    assert RequestState("outcome_unknown") is RequestState.OUTCOME_UNKNOWN


def test_model_alias_is_distinct_from_endpoint_and_provider_identity():
    fields = set(ModelAlias.model_fields)
    assert "endpoint_id" not in fields
    assert "provider_id" not in fields
    assert "id" in fields


def test_route_binding_does_not_imply_quota_ownership():
    rb = RouteBinding(
        id=RouteBindingId("rb-1"),
        model_alias_id=ModelAliasId("ma-1"),
        endpoint_id=EndpointId("ep-1"),
        provider_account_id=ProviderAccountId("pa-1"),
    )
    assert rb.quota_group_id is None
    # The route references a quota group by ID; it never embeds a QuotaGroup object.
    assert "quota_group" not in RouteBinding.model_fields
    assert "quota_group_id" in RouteBinding.model_fields
    assert QuotaGroup.model_fields is not None  # QuotaGroup is its own entity


def test_resource_references_use_typed_ids_not_integer_database_ids():
    rb = RouteBinding(
        id=RouteBindingId("rb-2"),
        model_alias_id=ModelAliasId("ma-2"),
        endpoint_id=EndpointId("ep-2"),
        provider_account_id=ProviderAccountId("pa-2"),
        quota_group_id=QuotaGroupId("qg-1"),
    )
    assert isinstance(rb.model_alias_id, ModelAliasId)
    assert isinstance(rb.endpoint_id, EndpointId)
    assert isinstance(rb.quota_group_id, QuotaGroupId)
    assert not isinstance(rb.model_alias_id, int)
    with pytest.raises(ValidationError):
        RouteBinding(
            id=RouteBindingId("rb-3"),
            model_alias_id=42,  # type: ignore[arg-type]
            endpoint_id=EndpointId("ep-3"),
            provider_account_id=ProviderAccountId("pa-3"),
        )


def test_price_fields_use_decimal_not_float():
    p = PriceSnapshot(
        id=PriceSnapshotId("ps-1"),
        model_alias_id=ModelAliasId("ma-1"),
        billing_unit=BillingUnit.TOKEN,
        price_in=Decimal("0.000001"),
        price_out=Decimal("0.000002"),
        effective_from=_ts(),
    )
    assert isinstance(p.price_in, Decimal)
    assert isinstance(p.price_out, Decimal)
    assert not isinstance(p.price_in, float)
    with pytest.raises(ValidationError):
        PriceSnapshot(
            id=PriceSnapshotId("ps-2"),
            model_alias_id=ModelAliasId("ma-2"),
            billing_unit=BillingUnit.TOKEN,
            price_in=0.000001,  # type: ignore[arg-type]
            price_out=Decimal("0.000002"),
            effective_from=_ts(),
        )


