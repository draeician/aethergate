"""Human CLI authentication HTTP surface (OAuth device flow).

Endpoints:

- ``POST /admin/v1/auth/device/start`` — begin an RFC 8628 device authorization;
  returns the one-time ``device_code`` and non-secret ``user_code``/verification
  URIs.
- ``POST /admin/v1/auth/device/poll`` — poll the provider; returns
  ``pending``/``slow_down``/``success`` (success carries the one-time CLI session
  token) or a terminal error (``device_expired``/``device_access_denied``/
  ``device_code_invalid``).
- ``POST /admin/v1/auth/cli/logout`` — durably revoke the authenticated CLI
  session (Bearer ``ags_...`` token).

The device ``start``/``poll`` endpoints are unauthenticated (they are the
authentication boundary); the ``logout`` endpoint requires an authenticated CLI
session. The raw ``device_code`` and raw CLI session token are each returned
exactly once and never stored or logged.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from aethergate.api.admin import AdminContextDep
from aethergate.config import get_settings
from aethergate.contracts.admin_v1 import (
    CliSessionRead,
    DeviceAuthorizationRead,
    DevicePollRead,
    DevicePollRequest,
    LogoutResult,
)
from aethergate.domain.enums import AdminAuthenticationKind
from aethergate.errors import (
    CliSessionInvalid,
    DeviceFlowUnavailable,
)
from aethergate.identity import cli_session as cli_session_service
from aethergate.identity import oidc as oidc_module
from aethergate.identity import session as session_service
from aethergate.persistence.db import get_session

router = APIRouter(prefix="/admin/v1", tags=["admin-cli-auth"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]


@router.post("/auth/device/start", response_model=DeviceAuthorizationRead)
async def device_start(session: SessionDep) -> DeviceAuthorizationRead:
    settings = get_settings()
    if not settings.device_flow_enabled:
        raise DeviceFlowUnavailable()
    provider = oidc_module.get_oidc_provider()
    async with session.begin():
        entity, raw = await cli_session_service.start_device_transaction(
            session, provider
        )
        await session_service.record_audit(
            session,
            actor_principal_id=None,
            action=cli_session_service.AUDIT_CLI_DEVICE_STARTED,
            resource_type="device_authorization",
            resource_id=str(entity.id),
            metadata={"verification_uri": entity.verification_uri},
        )
    return DeviceAuthorizationRead(
        device_code=raw["device_code"],
        user_code=raw["user_code"],
        verification_uri=raw["verification_uri"],
        verification_uri_complete=raw.get("verification_uri_complete"),
        expires_in=int(raw["expires_in"]),
        interval=int(raw["interval"]),
    )


@router.post("/auth/device/poll", response_model=DevicePollRead)
async def device_poll(body: DevicePollRequest, session: SessionDep) -> DevicePollRead:
    settings = get_settings()
    if not settings.device_flow_enabled:
        raise DeviceFlowUnavailable()
    provider = oidc_module.get_oidc_provider()
    now = cli_session_service.utcnow()
    async with session.begin():
        result = await cli_session_service.poll_device_transaction(
            session, provider, body.device_code, now
        )
        if result.status != "success":
            return DevicePollRead(status=result.status)

        assert result.principal is not None
        assert result.cli_session_id is not None
        assert result.raw_token is not None
        session_read = CliSessionRead(
            principal_id=result.principal.id,
            project_id=result.principal.project_id,
            authentication_kind=AdminAuthenticationKind.CLI_SESSION,
            cli_session_id=result.cli_session_id,
            roles=result.roles,
        )
    return DevicePollRead(
        status="success",
        session=session_read,
        token=result.raw_token,
        token_type="Bearer",
        expires_in=settings.cli_session_ttl_seconds,
    )


@router.post("/auth/cli/logout", response_model=LogoutResult)
async def cli_logout(
    context: AdminContextDep, session: SessionDep
) -> LogoutResult:
    if context.authentication_kind is not AdminAuthenticationKind.CLI_SESSION:
        raise CliSessionInvalid()
    cli_session_id = context.cli_session_id
    if cli_session_id is None:
        raise CliSessionInvalid()
    async with session.begin():
        entity = await cli_session_service.revoke_cli_session(
            session, cli_session_id, cli_session_service.utcnow()
        )
        await session_service.record_audit(
            session,
            actor_principal_id=context.principal_id,
            action=cli_session_service.AUDIT_CLI_LOGOUT,
            resource_type="cli_session",
            resource_id=str(cli_session_id),
            project_id=context.project_id,
        )
    return LogoutResult(revoked=entity is not None)


__all__ = ["router"]
