"""FastAPI dependencies for the OpenAI-compatible surface."""

from __future__ import annotations

import uuid

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from aethergate.config import get_settings
from aethergate.dev_identity import ensure_dev_identity
from aethergate.domain.ids import ApiCredentialId, PrincipalId, ProjectId
from aethergate.errors import AuthenticationRequired
from aethergate.persistence.db import get_session_factory
from aethergate.scheduler.runtime import build_scheduling_service
from aethergate.scheduler.service import SchedulingService

RequestContext = tuple[ProjectId, PrincipalId, ApiCredentialId]


def get_gateway_request_id(request: Request) -> str:
    """Return a per-request gateway request ID, creating one on first use."""
    request_id = getattr(request.state, "gateway_request_id", None)
    if request_id is None:
        request_id = uuid.uuid4().hex
        request.state.gateway_request_id = request_id
    return request_id


def build_scheduler() -> SchedulingService:
    """Build the scheduler service from current runtime settings."""
    return build_scheduling_service()


def require_inference_access() -> None:
    """Fail closed unless the explicit development auth bypass is enabled.

    This is a temporary policy for the first nomnom smoke test, not the final
    API-key authentication design.
    """
    if not get_settings().allow_inference_auth_bypass:
        raise AuthenticationRequired()


async def dev_request_context() -> RequestContext:
    """Return the stable development identity context for bypassed requests.

    Only reachable while the development auth bypass is enabled; production
    mode fails closed in the config layer before this dependency runs.
    """
    async with get_session_factory()() as session:
        async with session.begin():
            return await ensure_dev_identity(session)


def scheduler_service() -> SchedulingService:
    """FastAPI dependency yielding a scheduler service instance."""
    return build_scheduling_service()


__all__ = [
    "AsyncSession",
    "RequestContext",
    "build_scheduler",
    "dev_request_context",
    "get_gateway_request_id",
    "require_inference_access",
    "scheduler_service",
]
