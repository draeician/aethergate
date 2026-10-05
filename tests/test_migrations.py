"""Migration from an empty PostgreSQL database (DB-gated, subprocess Alembic)."""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import inspect
from sqlalchemy.ext.asyncio import create_async_engine

from db_helpers import (
    TEST_DATABASE_URL,
    drop_database,
    ensure_database,
    url_for_database,
)

pytestmark = pytest.mark.skipif(
    TEST_DATABASE_URL is None, reason="AETHERGATE_TEST_DATABASE_URL not set"
)

REPO_ROOT = Path(__file__).resolve().parents[1]
ALEMBIC_INI = REPO_ROOT / "src" / "aethergate" / "migrations" / "alembic.ini"

EXPECTED_TABLES = {
    "projects",
    "principals",
    "secret_refs",
    "api_credentials",
    "role_assignments",
    "bootstrap_state",
    "audit_events",
    "external_identities",
    "browser_sessions",
    "oidc_login_states",
    "providers",
    "provider_accounts",
    "endpoints",
    "quota_groups",
    "quota_limits",
    "quota_windows",
    "quota_reservations",
    "model_aliases",
    "route_bindings",
    "inference_requests",
    "reservations",
    "execution_attempts",
    "stream_events",
    "price_policies",
    "price_snapshots",
    "project_budget_policies",
    "budget_windows",
    "budget_reservations",
    "usage_records",
    "ledger_entries",
}


async def _table_names(url: str) -> set[str]:
    engine = create_async_engine(url)
    async with engine.connect() as conn:
        names = await conn.run_sync(lambda sync_conn: set(inspect(sync_conn).get_table_names()))
    await engine.dispose()
    return names


def _run_alembic(*args: str, env: dict[str, str]) -> None:
    subprocess.run(
        [sys.executable, "-m", "alembic", "-c", str(ALEMBIC_INI), *args],
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )


def test_migration_from_empty_database() -> None:
    if not TEST_DATABASE_URL:
        pytest.skip("AETHERGATE_TEST_DATABASE_URL not set")
    url = url_for_database(TEST_DATABASE_URL, "aethergate_test_mig")
    asyncio.run(drop_database(url))
    asyncio.run(ensure_database(url))

    env = {**os.environ, "DATABASE_URL": url}
    try:
        _run_alembic("upgrade", "head", env=env)
        assert EXPECTED_TABLES <= asyncio.run(_table_names(url))

        _run_alembic("downgrade", "base", env=env)
        remaining = asyncio.run(_table_names(url))
        assert "projects" not in remaining
        assert "route_bindings" not in remaining
    finally:
        asyncio.run(drop_database(url))


def _columns(url: str, table: str) -> set[str]:
    engine = create_async_engine(url)
    async def _names() -> set[str]:
        async with engine.connect() as conn:
            return await conn.run_sync(
                lambda sync_conn: {c["name"] for c in inspect(sync_conn).get_columns(table)}
            )
    names = asyncio.run(_names())
    asyncio.run(engine.dispose())
    return names


def _check_constraints(url: str, table: str) -> set[str]:
    engine = create_async_engine(url)
    async def _names() -> set[str]:
        async with engine.connect() as conn:
            return await conn.run_sync(
                lambda sync_conn: {
                    c["name"] for c in inspect(sync_conn).get_check_constraints(table)
                }
            )
    names = asyncio.run(_names())
    asyncio.run(engine.dispose())
    return names


def _indexes(url: str, table: str) -> set[str]:
    engine = create_async_engine(url)
    async def _names() -> set[str]:
        async with engine.connect() as conn:
            return await conn.run_sync(
                lambda sync_conn: {ix["name"] for ix in inspect(sync_conn).get_indexes(table)}
            )
    names = asyncio.run(_names())
    asyncio.run(engine.dispose())
    return names


def _nullable_columns(url: str, table: str) -> set[str]:
    engine = create_async_engine(url)
    async def _names() -> set[str]:
        async with engine.connect() as conn:
            return await conn.run_sync(
                lambda sync_conn: {
                    c["name"]
                    for c in inspect(sync_conn).get_columns(table)
                    if c["nullable"]
                }
            )
    names = asyncio.run(_names())
    asyncio.run(engine.dispose())
    return names


def test_migration_0003_to_0004() -> None:
    if not TEST_DATABASE_URL:
        pytest.skip("AETHERGATE_TEST_DATABASE_URL not set")
    url = url_for_database(TEST_DATABASE_URL, "aethergate_test_mig34")
    asyncio.run(drop_database(url))
    asyncio.run(ensure_database(url))

    env = {**os.environ, "DATABASE_URL": url}
    try:
        _run_alembic("upgrade", "0003", env=env)
        assert "reconciled_state" not in _columns(url, "inference_requests")
        assert "ck_endpoints_max_concurrency_positive" not in _check_constraints(url, "endpoints")

        _run_alembic("upgrade", "head", env=env)
        assert {"reconciled_state", "reconciled_at", "reconciled_by"} <= _columns(
            url, "inference_requests"
        )
        assert "ck_endpoints_max_concurrency_positive" in _check_constraints(url, "endpoints")
    finally:
        asyncio.run(drop_database(url))


def test_migration_0005_to_0006() -> None:
    if not TEST_DATABASE_URL:
        pytest.skip("AETHERGATE_TEST_DATABASE_URL not set")
    url = url_for_database(TEST_DATABASE_URL, "aethergate_test_mig56")
    asyncio.run(drop_database(url))
    asyncio.run(ensure_database(url))

    env = {**os.environ, "DATABASE_URL": url}
    try:
        _run_alembic("upgrade", "0005", env=env)
        assert (
            {"quota_group_id", "next_eligible_at", "wait_limit_id", "wait_limit_metric"}
            & _columns(url, "inference_requests")
        ) == set()
        assert "ck_quota_limits_metric" not in _check_constraints(url, "quota_limits")
        assert "ck_quota_reservations_state" not in _check_constraints(url, "quota_reservations")

        _run_alembic("upgrade", "head", env=env)
        assert {"quota_group_id", "next_eligible_at", "wait_limit_id", "wait_limit_metric"} <= (
            _columns(url, "inference_requests")
        )
        assert "ck_quota_limits_metric" in _check_constraints(url, "quota_limits")
        assert "ck_quota_reservations_state" in _check_constraints(url, "quota_reservations")
        assert "ck_quota_windows_committed_nonneg" in _check_constraints(url, "quota_windows")
        assert "ck_route_bindings_default_output_positive" in _check_constraints(
            url, "route_bindings"
        )
    finally:
        asyncio.run(drop_database(url))


def test_migration_0006_to_0007() -> None:
    if not TEST_DATABASE_URL:
        pytest.skip("AETHERGATE_TEST_DATABASE_URL not set")
    url = url_for_database(TEST_DATABASE_URL, "aethergate_test_mig67")
    asyncio.run(drop_database(url))
    asyncio.run(ensure_database(url))

    env = {**os.environ, "DATABASE_URL": url}
    try:
        _run_alembic("upgrade", "0006", env=env)
        assert "price_snapshot_id" not in _columns(url, "inference_requests")
        assert "price_snapshots" not in asyncio.run(_table_names(url))

        _run_alembic("upgrade", "head", env=env)
        assert "price_snapshot_id" in _columns(url, "inference_requests")
        assert "price_policies" in asyncio.run(_table_names(url))
        assert "price_snapshots" in asyncio.run(_table_names(url))
        assert "project_budget_policies" in asyncio.run(_table_names(url))
        assert "budget_windows" in asyncio.run(_table_names(url))
        assert "budget_reservations" in asyncio.run(_table_names(url))
        assert "usage_records" in asyncio.run(_table_names(url))
        assert "ledger_entries" in asyncio.run(_table_names(url))
        assert "ck_budget_policies_limit_positive" in _check_constraints(
            url, "project_budget_policies"
        )
        assert "ck_budget_windows_committed_nonneg" in _check_constraints(url, "budget_windows")
        assert "ck_budget_reservations_state" in _check_constraints(url, "budget_reservations")
        assert "ck_usage_records_billing_unit" in _check_constraints(url, "usage_records")
        assert "ck_ledger_entries_type" in _check_constraints(url, "ledger_entries")
    finally:
        asyncio.run(drop_database(url))


def test_migration_0007_to_0008() -> None:
    if not TEST_DATABASE_URL:
        pytest.skip("AETHERGATE_TEST_DATABASE_URL not set")
    url = url_for_database(TEST_DATABASE_URL, "aethergate_test_mig78")
    asyncio.run(drop_database(url))
    asyncio.run(ensure_database(url))

    env = {**os.environ, "DATABASE_URL": url}
    try:
        _run_alembic("upgrade", "0007", env=env)
        assert "ck_price_policies_request_shape" not in _check_constraints(
            url, "price_policies"
        )
        assert "uq_price_policies_one_enabled_per_route" not in _indexes(
            url, "price_policies"
        )
        assert "price_snapshot_id" not in _nullable_columns(url, "budget_reservations")

        _run_alembic("upgrade", "head", env=env)
        assert "ck_price_policies_request_shape" in _check_constraints(
            url, "price_policies"
        )
        assert "ck_price_policies_token_shape" in _check_constraints(
            url, "price_policies"
        )
        assert "uq_price_policies_one_enabled_per_route" in _indexes(url, "price_policies")
        assert "price_snapshot_id" in _nullable_columns(url, "budget_reservations")
    finally:
        asyncio.run(drop_database(url))


def test_migration_0008_to_0009() -> None:
    if not TEST_DATABASE_URL:
        pytest.skip("AETHERGATE_TEST_DATABASE_URL not set")
    url = url_for_database(TEST_DATABASE_URL, "aethergate_test_mig89")
    asyncio.run(drop_database(url))
    asyncio.run(ensure_database(url))

    env = {**os.environ, "DATABASE_URL": url}
    try:
        _run_alembic("upgrade", "0008", env=env)
        assert "secret_ref_id" in _columns(url, "api_credentials")
        assert "key_hash" not in _columns(url, "api_credentials")

        _run_alembic("upgrade", "head", env=env)
        cred_cols = _columns(url, "api_credentials")
        assert {
            "key_hash",
            "key_prefix",
            "audience",
            "scopes",
            "expires_at",
            "revoked_at",
            "last_used_at",
        } <= cred_cols
        assert "secret_ref_id" not in cred_cols
        assert "uq_api_credentials_key_hash" in _indexes(url, "api_credentials")
        assert "ck_api_credentials_audience" in _check_constraints(url, "api_credentials")
    finally:
        asyncio.run(drop_database(url))


def test_migration_0009_to_0010() -> None:
    if not TEST_DATABASE_URL:
        pytest.skip("AETHERGATE_TEST_DATABASE_URL not set")
    url = url_for_database(TEST_DATABASE_URL, "aethergate_test_mig910")
    asyncio.run(drop_database(url))
    asyncio.run(ensure_database(url))

    env = {**os.environ, "DATABASE_URL": url}
    try:
        _run_alembic("upgrade", "0009", env=env)
        assert "role_assignments" not in asyncio.run(_table_names(url))
        assert "bootstrap_state" not in asyncio.run(_table_names(url))
        assert "audit_events" not in asyncio.run(_table_names(url))

        _run_alembic("upgrade", "head", env=env)
        assert {
            "role_assignments",
            "bootstrap_state",
            "audit_events",
        } <= asyncio.run(_table_names(url))
        assert "ck_role_assignments_role" in _check_constraints(url, "role_assignments")
        assert "ck_role_assignments_scope_type" in _check_constraints(url, "role_assignments")
        assert "uq_role_assignments_active_equivalent" in _indexes(url, "role_assignments")
        assert "details" in _columns(url, "audit_events")
    finally:
        asyncio.run(drop_database(url))


def test_migration_0010_to_0011() -> None:
    if not TEST_DATABASE_URL:
        pytest.skip("AETHERGATE_TEST_DATABASE_URL not set")
    url = url_for_database(TEST_DATABASE_URL, "aethergate_test_mig1011")
    asyncio.run(drop_database(url))
    asyncio.run(ensure_database(url))

    env = {**os.environ, "DATABASE_URL": url}
    try:
        _run_alembic("upgrade", "0010", env=env)
        assert "ck_role_assignments_system_admin_deployment" not in _check_constraints(
            url, "role_assignments"
        )
        assert "ck_role_assignments_project_role_project_scope" not in _check_constraints(
            url, "role_assignments"
        )

        _run_alembic("upgrade", "head", env=env)
        assert "ck_role_assignments_system_admin_deployment" in _check_constraints(
            url, "role_assignments"
        )
        assert "ck_role_assignments_project_role_project_scope" in _check_constraints(
            url, "role_assignments"
        )
    finally:
        asyncio.run(drop_database(url))


def test_migration_0011_to_0012() -> None:
    if not TEST_DATABASE_URL:
        pytest.skip("AETHERGATE_TEST_DATABASE_URL not set")
    url = url_for_database(TEST_DATABASE_URL, "aethergate_test_mig1112")
    asyncio.run(drop_database(url))
    asyncio.run(ensure_database(url))

    env = {**os.environ, "DATABASE_URL": url}
    try:
        _run_alembic("upgrade", "0011", env=env)
        assert "external_identities" not in asyncio.run(_table_names(url))
        assert "browser_sessions" not in asyncio.run(_table_names(url))
        assert "oidc_login_states" not in asyncio.run(_table_names(url))

        _run_alembic("upgrade", "head", env=env)
        tables = asyncio.run(_table_names(url))
        assert {
            "external_identities",
            "browser_sessions",
            "oidc_login_states",
        } <= tables
        assert "uq_external_identities_issuer_subject" in _indexes(
            url, "external_identities"
        )
        assert "uq_browser_sessions_session_hash" in _indexes(url, "browser_sessions")
        assert "uq_oidc_login_states_state" in _indexes(url, "oidc_login_states")
        assert "ck_browser_sessions_expiry_order" in _check_constraints(
            url, "browser_sessions"
        )
    finally:
        asyncio.run(drop_database(url))


def test_migration_0012_to_0013() -> None:
    if not TEST_DATABASE_URL:
        pytest.skip("AETHERGATE_TEST_DATABASE_URL not set")
    url = url_for_database(TEST_DATABASE_URL, "aethergate_test_mig1213")
    asyncio.run(drop_database(url))
    asyncio.run(ensure_database(url))

    env = {**os.environ, "DATABASE_URL": url}
    try:
        _run_alembic("upgrade", "0012", env=env)
        assert "txn_cookie_hash" not in _columns(url, "oidc_login_states")

        _run_alembic("upgrade", "head", env=env)
        assert "txn_cookie_hash" in _columns(url, "oidc_login_states")
        assert "txn_cookie_hash" not in _nullable_columns(url, "oidc_login_states")
    finally:
        asyncio.run(drop_database(url))


def test_migration_0013_to_0014() -> None:
    if not TEST_DATABASE_URL:
        pytest.skip("AETHERGATE_TEST_DATABASE_URL not set")
    url = url_for_database(TEST_DATABASE_URL, "aethergate_test_mig1314")
    asyncio.run(drop_database(url))
    asyncio.run(ensure_database(url))

    env = {**os.environ, "DATABASE_URL": url}
    try:
        _run_alembic("upgrade", "0013", env=env)
        assert "uq_route_bindings_one_active_per_alias" not in _indexes(
            url, "route_bindings"
        )

        _run_alembic("upgrade", "head", env=env)
        assert "uq_route_bindings_one_active_per_alias" in _indexes(
            url, "route_bindings"
        )
    finally:
        asyncio.run(drop_database(url))
