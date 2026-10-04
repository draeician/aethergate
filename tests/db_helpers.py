"""Helpers for tests that need a real PostgreSQL database.

These are used only when ``AETHERGATE_TEST_DATABASE_URL`` is set; otherwise the
DB-gated tests skip. They create/drop short-lived databases via an admin
connection to the server's ``postgres`` maintenance database.
"""

from __future__ import annotations

import os
from urllib.parse import quote_plus, unquote, urlsplit

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

TEST_DATABASE_URL = os.environ.get("AETHERGATE_TEST_DATABASE_URL")


def split_url(url: str) -> dict[str, object]:
    parts = urlsplit(url)
    return {
        "user": unquote(parts.username or ""),
        "password": unquote(parts.password or ""),
        "host": parts.hostname or "localhost",
        "port": parts.port or 5432,
        "db": parts.path.lstrip("/"),
    }


def _admin_url(url: str) -> str:
    p = split_url(url)
    user = quote_plus(str(p["user"]))
    password = quote_plus(str(p["password"]))
    return f"postgresql+asyncpg://{user}:{password}@{p['host']}:{p['port']}/postgres"


def url_for_database(url: str, dbname: str) -> str:
    p = split_url(url)
    user = quote_plus(str(p["user"]))
    password = quote_plus(str(p["password"]))
    return f"postgresql+asyncpg://{user}:{password}@{p['host']}:{p['port']}/{dbname}"


def _assert_safe_identifier(name: str) -> None:
    if not name.replace("_", "").replace("-", "").isalnum():
        raise ValueError(f"unsafe database identifier {name!r}")


async def ensure_database(url: str) -> None:
    p = split_url(url)
    dbname = str(p["db"])
    _assert_safe_identifier(dbname)
    engine = create_async_engine(_admin_url(url), isolation_level="AUTOCOMMIT")
    async with engine.connect() as conn:
        result = await conn.execute(
            text("SELECT 1 FROM pg_database WHERE datname = :name"), {"name": dbname}
        )
        if result.scalar() is None:
            await conn.execute(text(f'CREATE DATABASE "{dbname}"'))
    await engine.dispose()


async def drop_database(url: str) -> None:
    p = split_url(url)
    dbname = str(p["db"])
    _assert_safe_identifier(dbname)
    engine = create_async_engine(_admin_url(url), isolation_level="AUTOCOMMIT")
    async with engine.connect() as conn:
        await conn.execute(text(f'DROP DATABASE IF EXISTS "{dbname}" WITH (FORCE)'))
    await engine.dispose()


async def reset_schema(engine) -> None:
    """Drop and recreate all tables on ``engine`` (leaves an empty schema)."""
    from aethergate.persistence import models  # noqa: F401  (register tables)
    from aethergate.persistence.base import Base

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
