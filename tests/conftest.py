"""Shared fixtures. DB-gated fixtures skip when AETHERGATE_TEST_DATABASE_URL is unset."""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from aethergate.persistence import models  # noqa: F401  (register tables)
from aethergate.persistence.base import Base
from db_helpers import TEST_DATABASE_URL, ensure_database


@pytest.fixture(scope="session")
async def engine():
    if not TEST_DATABASE_URL:
        pytest.skip("AETHERGATE_TEST_DATABASE_URL not set")
    await ensure_database(TEST_DATABASE_URL)
    engine = create_async_engine(TEST_DATABASE_URL)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest.fixture
async def session(engine):
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
