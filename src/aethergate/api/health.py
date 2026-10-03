"""Liveness and readiness endpoints.

Liveness reports only process liveness. Readiness reports whether the
authoritative PostgreSQL database answers a trivial query; it never leaks
database URLs, credentials, or errors.
"""

from __future__ import annotations

from fastapi import APIRouter, Response, status

from aethergate.persistence import db

router = APIRouter()


@router.get("/health/live")
async def live() -> dict[str, str]:
    return {"status": "alive"}


@router.get("/health/ready")
async def ready(response: Response) -> dict[str, str]:
    if await db.check_database_connection():
        return {"status": "ready"}
    response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return {"status": "not_ready"}
