"""FastAPI dependencies for the OpenAI-compatible surface.

Authentication resolves a strict ``Authorization: Bearer agk_...`` credential
into a durable, typed :class:`~aethergate.domain.entities.RequestContext`. The
development-only bypass (``AETHERGATE_ALLOW_INFERENCE_AUTH_BYPASS``) supplies a
stable seeded identity only when no Authorization header is present; a supplied
header is always authenticated normally and never falls through to bypass.
"""

from __future__ import annotations

import uuid

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from aethergate.config import get_settings
from aethergate.dev_identity import ensure_dev_identity
from aethergate.domain.entities import RequestContext
from aethergate.errors import AuthenticationRequired
from aethergate.identity import service as identity_service
from aethergate.persistence.db import get_session_factory
from aethergate.scheduler.runtime import build_scheduling_service
from aethergate.scheduler.service import SchedulingService


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


async def _resolve_auth(request: Request) -> RequestContext:
    """Authenticate the request and return the durable authorization context.

    A present Authorization header is always parsed strictly and authenticated;
    an invalid key never falls through to the development bypass. The bypass is
    consulted only when no header is supplied and is enabled (dev/test mode).
    """
    settings = get_settings()
    auth_values = request.headers.getlist("authorization")
    if auth_values:
        token = identity_service.parse_bearer_token(auth_values)
        async with get_session_factory()() as session:
            async with session.begin():
                return await identity_service.authenticate(session, token)

    if settings.allow_inference_auth_bypass:
        async with get_session_factory()() as session:
            async with session.begin():
                return await ensure_dev_identity(session)

    raise AuthenticationRequired()


async def request_context(request: Request) -> RequestContext:
    """FastAPI dependency yielding the authenticated request context."""
    return await _resolve_auth(request)


async def require_inference_access(request: Request) -> None:
    """FastAPI dependency that authenticates but discards the resolved context."""
    await _resolve_auth(request)


def scheduler_service() -> SchedulingService:
    """FastAPI dependency yielding a scheduler service instance."""
    return build_scheduling_service()


__all__ = [
    "AsyncSession",
    "RequestContext",
    "build_scheduler",
    "get_gateway_request_id",
    "request_context",
    "require_inference_access",
    "scheduler_service",
]
