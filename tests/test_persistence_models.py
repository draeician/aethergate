"""Static checks over the v2 persistence models (no database required)."""

from __future__ import annotations

import sqlalchemy as sa

from aethergate.persistence import models
from aethergate.persistence.base import Base


def test_secret_ref_has_no_material_column():
    columns = {c.name for c in models.SecretRef.__table__.columns}
    for forbidden in (
        "value",
        "material",
        "secret",
        "plaintext",
        "token",
        "password",
        "credential",
        "key",
    ):
        assert forbidden not in columns


def test_accounts_reference_secret_not_store_it():
    credential_columns = {c.name for c in models.ApiCredential.__table__.columns}
    account_columns = {c.name for c in models.ProviderAccount.__table__.columns}
    assert "secret_ref_id" in account_columns
    for columns in (credential_columns, account_columns):
        assert "secret_value" not in columns
        assert "api_key" not in columns
        assert "plaintext" not in columns


def test_api_credential_stores_only_one_way_verifier():
    credential_columns = {c.name for c in models.ApiCredential.__table__.columns}
    assert "key_hash" in credential_columns
    assert "key_prefix" in credential_columns
    assert "secret_ref_id" not in credential_columns
    for forbidden in ("raw_key", "key", "secret", "token", "password", "material"):
        assert forbidden not in credential_columns


def test_all_models_use_opaque_string_primary_keys():
    for table in Base.metadata.sorted_tables:
        for column in table.primary_key.columns:
            assert isinstance(column.type, sa.String), (
                f"{table.name}.{column.name} is not a String primary key"
            )


def test_expected_tables_registered():
    names = set(Base.metadata.tables)
    assert {
        "projects",
        "principals",
        "secret_refs",
        "api_credentials",
        "providers",
        "provider_accounts",
        "endpoints",
        "quota_groups",
        "model_aliases",
        "route_bindings",
        "inference_requests",
        "reservations",
        "execution_attempts",
        "stream_events",
    } <= names


def test_scheduler_content_columns_are_binary_not_text():
    """Prompt/completion content must not be a plaintext text column."""
    request_columns = {c.name: c.type for c in models.InferenceRequest.__table__.columns}
    event_columns = {c.name: c.type for c in models.StreamEvent.__table__.columns}
    for name, coltype in request_columns.items():
        if name in ("payload_encrypted", "result_encrypted"):
            assert not isinstance(coltype, sa.Text), f"{name} must not be text"
    for name, coltype in event_columns.items():
        if name == "event_encrypted":
            assert not isinstance(coltype, sa.Text), f"{name} must not be text"


def test_endpoint_max_concurrency_check_constraint_present():
    constraints = models.Endpoint.__table__.constraints
    names = {c.name for c in constraints}
    assert "ck_endpoints_max_concurrency_positive" in names


def test_inference_request_reconciliation_columns_present():
    columns = {c.name for c in models.InferenceRequest.__table__.columns}
    assert {"reconciled_state", "reconciled_at", "reconciled_by"} <= columns
