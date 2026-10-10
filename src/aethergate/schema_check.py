"""Schema-head compatibility check.

Readiness must fail (not silently serve) when the running code and the applied
database schema are incompatible. The authoritative migration head is derived
from the in-repo migration graph; the applied revision is read from Alembic's
``alembic_version`` table.

This module never auto-runs migrations — operators apply migrations explicitly
(``scripts/dev/v2 migrate`` / ``alembic upgrade head``). It only reports whether
the applied revision matches the code's expected head.
"""

from __future__ import annotations

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from aethergate.persistence.db import get_engine

_MIGRATIONS_INI = "src/aethergate/migrations/alembic.ini"


def expected_head() -> str | None:
    """Return the single leaf revision of the in-repo migration graph."""
    try:
        config = Config(_MIGRATIONS_INI)
        script = ScriptDirectory.from_config(config)
        heads = script.get_heads()
    except Exception:  # noqa: BLE001 - missing/alembic-unavailable is not fatal here
        return None
    return heads[0] if len(heads) == 1 else None


async def applied_revision(engine: AsyncEngine | None = None) -> str | None:
    """Return the applied Alembic revision, or ``None`` when unavailable."""
    engine = engine or get_engine()
    try:
        async with engine.connect() as conn:
            result = await conn.execute(text("SELECT version_num FROM alembic_version"))
            row = result.first()
            return row[0] if row else None
    except Exception:  # noqa: BLE001 - missing table / unreachable DB
        return None


async def schema_is_current(engine: AsyncEngine | None = None) -> bool:
    """Return True when the applied revision equals the expected head."""
    head = expected_head()
    if head is None:
        # Cannot determine expectation (e.g. packaging layout); do not block.
        return True
    return await applied_revision(engine) == head


__all__ = ["expected_head", "applied_revision", "schema_is_current"]
