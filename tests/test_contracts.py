"""Tests for initial /admin/v1 DTO foundations."""

from __future__ import annotations

import pytest
from pydantic import BaseModel, ValidationError

from aethergate.contracts.admin_v1 import (
    ProjectCreate,
    ProjectRead,
    ProviderAccountRead,
    QuotaGroupCreate,
    QuotaLimitCreate,
    RouteBindingCreate,
)
from aethergate.domain.enums import QuotaMetric
from aethergate.domain.ids import (
    EndpointId,
    ModelAliasId,
    ProjectId,
    ProviderAccountId,
    ProviderId,
    QuotaGroupId,
    SecretRefId,
)


def test_read_dto_has_no_plaintext_secret_material():
    dto = ProviderAccountRead(
        id=ProviderAccountId("pa-1"),
        provider_id=ProviderId("prov-1"),
        name="acct",
        secret_ref_id=SecretRefId("sr-1"),
        is_active=True,
    )
    data = dto.model_dump(mode="json")
    for key in ("secret", "api_key", "secret_value", "credential", "plaintext", "token"):
        assert key not in data
    assert data["secret_ref_id"] == "sr-1"


def test_read_dto_rejects_injected_plaintext_field():
    # ``extra="forbid"`` means an unknown field (e.g. plaintext secret material)
    # cannot be attached to a read DTO.
    payload = {
        "id": "pa-2",
        "provider_id": "prov-2",
        "name": "acct",
        "inline_credential": "nope",
        "is_active": True,
    }
    with pytest.raises(ValidationError):
        ProviderAccountRead.model_validate(payload)


def test_dto_rejects_invalid_required_values():
    with pytest.raises(ValidationError):
        ProjectCreate(name="")
    with pytest.raises(ValidationError):
        ProjectCreate()  # missing required 'name'
    with pytest.raises(ValidationError):
        ProjectRead(id="", name="x", is_active=True)


def test_dto_accepts_valid_values_and_uses_typed_ids():
    dto = ProjectRead(id=ProjectId("proj-1"), name="x", is_active=True)
    assert isinstance(dto.id, ProjectId)
    assert dto.model_dump(mode="json")["id"] == "proj-1"


def test_quota_group_create_requires_provider_account():
    with pytest.raises(ValidationError):
        QuotaGroupCreate(name="shared")  # missing provider_account_id


def test_quota_limit_dto_validation():
    with pytest.raises(ValidationError):
        QuotaLimitCreate(
            quota_group_id=QuotaGroupId("qg-1"),
            metric=QuotaMetric.REQUESTS,
            limit_units=0,
            window_seconds=60,
        )
    with pytest.raises(ValidationError):
        QuotaLimitCreate(
            quota_group_id=QuotaGroupId("qg-1"),
            metric=QuotaMetric.TOKENS,
            limit_units=100,
            window_seconds=0,
        )
    with pytest.raises(ValidationError):
        QuotaLimitCreate(
            quota_group_id=QuotaGroupId("qg-1"),
            metric="bogus",  # type: ignore[arg-type]
            limit_units=100,
            window_seconds=60,
        )


def test_route_binding_default_output_tokens_ge_1():
    with pytest.raises(ValidationError):
        RouteBindingCreate(
            model_alias_id=ModelAliasId("ma-1"),
            endpoint_id=EndpointId("ep-1"),
            provider_account_id=ProviderAccountId("pa-1"),
            default_output_tokens=0,
        )
    ok = RouteBindingCreate(
        model_alias_id=ModelAliasId("ma-1"),
        endpoint_id=EndpointId("ep-1"),
        provider_account_id=ProviderAccountId("pa-1"),
        default_output_tokens=64,
    )
    assert ok.default_output_tokens == 64


def test_money_serializes_as_canonical_fixed_point():
    from decimal import Decimal

    from aethergate.domain.value_objects import Money, NonNegativeMoney, PositiveMoney

    class MoneyDoc(BaseModel):
        limit: PositiveMoney
        committed: NonNegativeMoney
        headroom: Money

    doc = MoneyDoc(limit="0.000000000200", committed="0", headroom="0.000000000700")
    data = doc.model_dump(mode="json")
    assert data == {"limit": "0.0000000002", "committed": "0", "headroom": "0.0000000007"}
    # Python-mode dumps keep the exact Decimal (no float, no precision loss).
    assert doc.model_dump() == {
        "limit": Decimal("0.000000000200"),
        "committed": Decimal("0"),
        "headroom": Decimal("0.000000000700"),
    }
