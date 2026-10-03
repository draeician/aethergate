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
    "providers",
    "provider_accounts",
    "endpoints",
    "quota_groups",
    "model_aliases",
    "route_bindings",
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
