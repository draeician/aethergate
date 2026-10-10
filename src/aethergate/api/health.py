"""Liveness and readiness endpoints.

Liveness reports only process liveness. Readiness reports whether the
authoritative PostgreSQL database answers a trivial query **and** the applied
schema revision matches the code's expected migration head; an incompatible /
unmigrated schema fails readiness rather than serving normal traffic against an
unsupported schema. It never leaks database URLs, credentials, or errors.

Readiness intentionally does not depend on any configured upstream provider: a
single unhealthy provider endpoint must not make the gateway not-ready.
"""

from __future__ import annotations

from fastapi import APIRouter, Response, status

from aethergate.persistence import db
from aethergate.schema_check import schema_is_current

router = APIRouter()


@router.get("/health/live")
async def live() -> dict[str, str]:
    return {"status": "alive"}


@router.get("/health/ready")
async def ready(response: Response) -> dict[str, str]:
    if not await db.check_database_connection():
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {"status": "not_ready", "reason": "database_unreachable"}
    if not await schema_is_current():
        # The DB is reachable but the applied schema is not the expected head:
        # serve 503 so the instance does not handle traffic against a
        # possibly-incompatible schema. Operators apply migrations explicitly.
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {"status": "not_ready", "reason": "schema_not_current"}
    return {"status": "ready"}
