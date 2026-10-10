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
    "device_authorizations",
    "cli_sessions",
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


def test_migration_0014_to_0015() -> None:
    if not TEST_DATABASE_URL:
        pytest.skip("AETHERGATE_TEST_DATABASE_URL not set")
    url = url_for_database(TEST_DATABASE_URL, "aethergate_test_mig1415")
    asyncio.run(drop_database(url))
    asyncio.run(ensure_database(url))

    env = {**os.environ, "DATABASE_URL": url}
    try:
        _run_alembic("upgrade", "0014", env=env)
        assert "operational_state" not in _columns(url, "endpoints")
        assert "ck_endpoints_operational_state" not in _check_constraints(url, "endpoints")

        _run_alembic("upgrade", "head", env=env)
        assert "operational_state" in _columns(url, "endpoints")
        assert "operational_state" not in _nullable_columns(url, "endpoints")
        assert "ck_endpoints_operational_state" in _check_constraints(url, "endpoints")

        # Explicit downgrade is reasonable.
        _run_alembic("downgrade", "0014", env=env)
        assert "operational_state" not in _columns(url, "endpoints")
    finally:
        asyncio.run(drop_database(url))


def test_migration_0015_to_0016() -> None:
    if not TEST_DATABASE_URL:
        pytest.skip("AETHERGATE_TEST_DATABASE_URL not set")
    url = url_for_database(TEST_DATABASE_URL, "aethergate_test_mig1516")
    asyncio.run(drop_database(url))
    asyncio.run(ensure_database(url))

    env = {**os.environ, "DATABASE_URL": url}
    try:
        _run_alembic("upgrade", "0015", env=env)
        assert "device_authorizations" not in asyncio.run(_table_names(url))
        assert "cli_sessions" not in asyncio.run(_table_names(url))

        _run_alembic("upgrade", "head", env=env)
        tables = asyncio.run(_table_names(url))
        assert {"device_authorizations", "cli_sessions"} <= tables
        assert "uq_device_authorizations_code_hash" in _indexes(
            url, "device_authorizations"
        )
        assert "uq_cli_sessions_token_hash" in _indexes(url, "cli_sessions")
        assert "ck_device_authorizations_expiry_order" in _check_constraints(
            url, "device_authorizations"
        )
        assert "ck_device_authorizations_poll_interval_positive" in _check_constraints(
            url, "device_authorizations"
        )
        assert "ck_cli_sessions_expiry_order" in _check_constraints(url, "cli_sessions")
        assert "principal_id" in _columns(url, "cli_sessions")
        assert "device_code_hash" in _columns(url, "device_authorizations")
        assert "device_code_hash" not in _nullable_columns(url, "device_authorizations")
        assert "token_hash" not in _nullable_columns(url, "cli_sessions")

        # Explicit downgrade removes only the new tables.
        _run_alembic("downgrade", "0015", env=env)
        tables = asyncio.run(_table_names(url))
        assert "device_authorizations" not in tables
        assert "cli_sessions" not in tables
        assert "browser_sessions" in tables
    finally:
        asyncio.run(drop_database(url))


def _exec_sql(url: str, statements: list[str]) -> list[tuple]:
    """Run raw SQL statements; return the last statement's rows."""
    from sqlalchemy.ext.asyncio import create_async_engine

    engine = create_async_engine(url)

    async def _run() -> list[tuple]:
        from sqlalchemy import text

        async with engine.begin() as conn:
            rows: list[tuple] = []
            for stmt in statements:
                result = await conn.execute(text(stmt))
                if result.returns_rows:
                    rows = list(result.fetchall())
        return rows

    out = asyncio.run(_run())
    asyncio.run(engine.dispose())
    return out


def test_populated_0016_to_0017_preserves_rows() -> None:
    """Populated 0016 -> 0017 preserves representative rows and defaults."""
    if not TEST_DATABASE_URL:
        pytest.skip("AETHERGATE_TEST_DATABASE_URL not set")
    url = url_for_database(TEST_DATABASE_URL, "aethergate_test_mig1617pop")
    asyncio.run(drop_database(url))
    asyncio.run(ensure_database(url))

    env = {**os.environ, "DATABASE_URL": url}
    try:
        _run_alembic("upgrade", "0016", env=env)

        # Representative identity/catalog/queue/accounting rows at 0016.
        _exec_sql(
            url,
            [
                "INSERT INTO projects (id, name, is_active) VALUES ('p1','proj',true)",
                "INSERT INTO principals (id, project_id, kind, name, is_active) "
                "VALUES ('pr1','p1','user','alice',true)",
                "INSERT INTO providers (id, kind, name, capabilities, is_active) "
                "VALUES ('prov1','ollama','ollama','[]',true)",
                "INSERT INTO provider_accounts (id, provider_id, name, is_active) "
                "VALUES ('acct1','prov1','acct',true)",
                "INSERT INTO endpoints (id, provider_account_id, name, base_destination, "
                "max_concurrency, is_active, operational_state) "
                "VALUES ('ep1','acct1','ep','http://localhost',1,true,'active')",
                "INSERT INTO model_aliases (id, name, capabilities, is_active) "
                "VALUES ('ma1','m1','[]',true)",
                "INSERT INTO route_bindings (id, model_alias_id, endpoint_id, "
                "provider_account_id, is_active) "
                "VALUES ('rb1','ma1','ep1','acct1',true)",
                "INSERT INTO inference_requests (id, project_id, model_alias_id, "
                "endpoint_id, state, stream, payload_encrypted, queued_at, expires_at, "
                "cancellation_requested) "
                "VALUES ('req1','p1','ma1','ep1','dispatched',false,decode('00','hex'),"
                "now(), now() + interval '1 hour', false)",
                # A pre-existing execution attempt (no first_token_at at 0016).
                "INSERT INTO execution_attempts (id, request_id, endpoint_id, state, "
                "fencing_token, started_at) "
                "VALUES ('att1','req1','ep1','dispatched',7,now())",
            ],
        )

        _run_alembic("upgrade", "head", env=env)

        # Historical rows readable; new columns have correct defaults/nulls.
        rows = _exec_sql(
            url,
            [
                "SELECT state FROM inference_requests WHERE id='req1'",
                "SELECT upstream_error, upstream_status_code, first_token_at "
                "FROM execution_attempts WHERE id='att1'",
            ],
        )
        # The last SELECT is the attempt defaults.
        upstream_error, upstream_status_code, first_token_at = rows[0]
        assert upstream_error is False
        assert upstream_status_code is None
        assert first_token_at is None  # no fake backfill

        # Pre-existing request/attempt survived.
        assert _exec_sql(
            url, ["SELECT count(*) FROM inference_requests WHERE id='req1'"]
        ) == [(1,)]
        assert _exec_sql(
            url, ["SELECT count(*) FROM execution_attempts WHERE id='att1'"]
        ) == [(1,)]
        # Head is 0017.
        assert _exec_sql(url, ["SELECT version_num FROM alembic_version"]) == [("0017",)]
    finally:
        asyncio.run(drop_database(url))


def test_migration_0016_to_0017() -> None:
    if not TEST_DATABASE_URL:
        pytest.skip("AETHERGATE_TEST_DATABASE_URL not set")
    url = url_for_database(TEST_DATABASE_URL, "aethergate_test_mig1617")
    asyncio.run(drop_database(url))
    asyncio.run(ensure_database(url))

    env = {**os.environ, "DATABASE_URL": url}
    try:
        _run_alembic("upgrade", "0016", env=env)
        assert {
            "first_token_at",
            "upstream_error",
            "upstream_status_code",
        } & _columns(url, "execution_attempts") == set()
        assert "ix_execution_attempts_endpoint_finished" not in _indexes(
            url, "execution_attempts"
        )

        _run_alembic("upgrade", "head", env=env)
        cols = _columns(url, "execution_attempts")
        assert {"first_token_at", "upstream_error", "upstream_status_code"} <= cols
        assert "first_token_at" in _nullable_columns(url, "execution_attempts")
        assert "upstream_status_code" in _nullable_columns(url, "execution_attempts")
        assert "upstream_error" not in _nullable_columns(url, "execution_attempts")
        assert "ix_execution_attempts_started_at" in _indexes(url, "execution_attempts")
        assert "ix_execution_attempts_endpoint_finished" in _indexes(
            url, "execution_attempts"
        )
        assert "ix_inference_requests_queued_at" in _indexes(
            url, "inference_requests"
        )

        # Explicit, reversible downgrade.
        _run_alembic("downgrade", "0016", env=env)
        assert {
            "first_token_at",
            "upstream_error",
            "upstream_status_code",
        } & _columns(url, "execution_attempts") == set()
        assert "ix_execution_attempts_endpoint_finished" not in _indexes(
            url, "execution_attempts"
        )
    finally:
        asyncio.run(drop_database(url))
