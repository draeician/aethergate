"""Human OIDC login/session HTTP surface.

Endpoints:

- ``GET  /admin/v1/auth/oidc/login`` — start an authorization-code + PKCE login,
  redirecting to the configured provider.
- ``GET  /admin/v1/auth/oidc/callback`` — complete login, set the session cookie,
  return session metadata + the one-time CSRF token.
- ``GET  /admin/v1/auth/session`` — resolve the current browser session.
- ``POST /admin/v1/auth/logout`` — revoke the session (CSRF-protected).

No ID token, access token, refresh token, client secret, session cookie value, or
CSRF token is ever returned in JSON except the single CSRF token delivered at
session establishment (and never re-exposed afterward).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from aethergate.config import get_settings
from aethergate.contracts.admin_v1 import (
    LogoutResult,
    SessionEstablished,
    SessionRead,
)
from aethergate.domain import entities as domain
from aethergate.domain.enums import AdminAuthenticationKind
from aethergate.errors import OidcAuthenticationFailed, OidcConfigurationError, SessionInvalid
from aethergate.identity import oidc as oidc_module
from aethergate.identity import session as session_service
from aethergate.persistence import repository
from aethergate.persistence.db import get_session

router = APIRouter(prefix="/admin/v1", tags=["admin-auth"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]

_COOKIE_PATH = "/admin"

LOGIN_FAILED_EXCHANGE = "token_exchange_failed"
LOGIN_FAILED_ID_TOKEN = "invalid_id_token"
LOGIN_FAILED_IDENTITY = "unknown_identity"


def _set_session_cookie(response: JSONResponse, raw_cookie: str) -> None:
    settings = get_settings()
    response.set_cookie(
        key=session_service.SESSION_COOKIE_NAME,
        value=raw_cookie,
        httponly=True,
        secure=settings.app_env == "prod",
        samesite="lax",
        path=_COOKIE_PATH,
        max_age=settings.oidc_session_absolute_seconds,
    )


def _clear_session_cookie(response: JSONResponse) -> None:
    response.delete_cookie(key=session_service.SESSION_COOKIE_NAME, path=_COOKIE_PATH)


async def _record_login_failed(
    session: AsyncSession, *, reason: str, state: str
) -> None:
    await session_service.record_audit(
        session,
        actor_principal_id=None,
        action="human.login_failed",
        resource_type="session",
        resource_id=state,
        metadata={"reason": reason},
    )


def _session_read(
    session_entity: domain.BrowserSession,
    principal: domain.Principal,
    roles: tuple,
) -> SessionRead:
    return SessionRead(
        principal_id=principal.id,
        project_id=principal.project_id,
        authentication_kind=AdminAuthenticationKind.BROWSER_SESSION,
        browser_session_id=session_entity.id,
        roles=roles,
    )


@router.get("/auth/oidc/login")
async def oidc_login(session: SessionDep) -> RedirectResponse:
    settings = get_settings()
    if not settings.oidc_enabled:
        raise OidcConfigurationError("OIDC is not enabled")
    async with session.begin():
        transaction = await session_service.create_login_transaction(session)
    url = await oidc_module.get_oidc_provider().begin_login(
        state=transaction.state,
        nonce=transaction.nonce,
        code_challenge=transaction.code_challenge,
    )
    return RedirectResponse(url, status_code=302)


@router.get("/auth/oidc/callback")
async def oidc_callback(
    session: SessionDep,
    code: str | None = Query(default=None),
    state: str | None = Query(default=None),
    error: str | None = Query(default=None),
) -> JSONResponse:
    if error is not None or not code or not state:
        raise OidcAuthenticationFailed()

    now = session_service.utcnow()
    settings = get_settings()

    # Consume the one-time login transaction first (committed independently so a
    # replayed or failed callback cannot be retried with the same state).
    async with session.begin():
        transaction = await session_service.consume_login_transaction(session, state, now)

    provider = oidc_module.get_oidc_provider()
    try:
        token_response = await provider.exchange_code(
            code=code, code_verifier=transaction.code_verifier
        )
    except OidcAuthenticationFailed:
        async with session.begin():
            await _record_login_failed(session, reason=LOGIN_FAILED_EXCHANGE, state=state)
        raise

    id_token = token_response.get("id_token")
    if not isinstance(id_token, str) or not id_token:
        async with session.begin():
            await _record_login_failed(session, reason=LOGIN_FAILED_ID_TOKEN, state=state)
        raise OidcAuthenticationFailed()

    try:
        claims = await provider.validate_id_token(id_token, expected_nonce=transaction.nonce)
    except OidcAuthenticationFailed:
        async with session.begin():
            await _record_login_failed(session, reason=LOGIN_FAILED_ID_TOKEN, state=state)
        raise

    subject = claims.get("sub")
    issuer = settings.oidc_issuer or ""
    try:
        async with session.begin():
            principal = await session_service.resolve_or_provision_principal(
                session,
                issuer=issuer,
                subject=subject,
                email=claims.get("email"),
                display_name=claims.get("name"),
                now=now,
            )
            session_entity, raw_cookie, raw_csrf = (
                await session_service.create_browser_session(session, principal.id, now)
            )
            assignments = await repository.list_active_role_assignments(
                session, principal.id
            )
            await session_service.record_audit(
                session,
                actor_principal_id=principal.id,
                action="human.login_success",
                resource_type="session",
                resource_id=str(session_entity.id),
                project_id=principal.project_id,
                metadata={"issuer": issuer, "subject": subject},
            )
    except OidcAuthenticationFailed:
        async with session.begin():
            await _record_login_failed(session, reason=LOGIN_FAILED_IDENTITY, state=state)
        raise

    body = SessionEstablished(
        session=_session_read(
            session_entity, principal, tuple(a.role for a in assignments)
        ),
        csrf_token=raw_csrf,
    )
    response = JSONResponse(status_code=200, content=body.model_dump(mode="json"))
    _set_session_cookie(response, raw_cookie)
    return response


@router.get("/auth/session", response_model=SessionRead)
async def get_session(request: Request, session: SessionDep) -> SessionRead:
    raw_cookie = request.cookies.get(session_service.SESSION_COOKIE_NAME)
    if not raw_cookie:
        raise SessionInvalid()
    async with session.begin():
        session_entity, principal = await session_service.resolve_browser_session(
            session, raw_cookie, session_service.utcnow()
        )
        assignments = await repository.list_active_role_assignments(session, principal.id)
    return _session_read(session_entity, principal, tuple(a.role for a in assignments))


@router.post("/auth/logout", response_model=LogoutResult)
async def logout(request: Request, session: SessionDep) -> JSONResponse:
    raw_cookie = request.cookies.get(session_service.SESSION_COOKIE_NAME)
    revoked = False
    if raw_cookie:
        raw_csrf = request.headers.get(session_service.CSRF_HEADER_NAME)
        try:
            async with session.begin():
                session_entity, principal = await session_service.resolve_browser_session(
                    session, raw_cookie, session_service.utcnow()
                )
                session_service.validate_csrf(session_entity, raw_csrf)
                await session_service.revoke_browser_session(
                    session, session_entity.id, session_service.utcnow()
                )
                await session_service.record_audit(
                    session,
                    actor_principal_id=principal.id,
                    action="session.logout",
                    resource_type="session",
                    resource_id=str(session_entity.id),
                    project_id=principal.project_id,
                )
                revoked = True
        except SessionInvalid:
            revoked = False

    response = JSONResponse(content=LogoutResult(revoked=revoked).model_dump(mode="json"))
    _clear_session_cookie(response)
    return response


__all__ = ["router"]
