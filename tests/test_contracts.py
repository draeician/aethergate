"""Tests for initial /admin/v1 DTO foundations."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from aethergate.contracts.admin_v1 import (
    ProjectCreate,
    ProjectRead,
    ProviderAccountRead,
)
from aethergate.domain.ids import (
    ProjectId,
    ProviderAccountId,
    ProviderId,
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
