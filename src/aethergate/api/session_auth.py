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
from fastapi.responses import JSONResponse, RedirectResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession

from aethergate.api.deps import get_gateway_request_id
from aethergate.config import get_settings
from aethergate.contracts.admin_v1 import (
    LogoutResult,
    SessionEstablished,
    SessionRead,
)
from aethergate.domain import entities as domain
from aethergate.domain.enums import AdminAuthenticationKind
from aethergate.errors import (
    OidcAuthenticationFailed,
    OidcConfigurationError,
    OidcLoginStateInvalid,
    SessionInvalid,
)
from aethergate.identity import oidc as oidc_module
from aethergate.identity import session as session_service
from aethergate.persistence import repository
from aethergate.persistence.db import get_session

router = APIRouter(prefix="/admin/v1", tags=["admin-auth"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]

_COOKIE_PATH = "/admin"
_TXN_COOKIE_PATH = "/admin/v1/auth/oidc"
_CSRF_COOKIE_PATH = "/"

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


def _clear_session_cookie(response: Response) -> None:
    response.delete_cookie(key=session_service.SESSION_COOKIE_NAME, path=_COOKIE_PATH)


def _set_csrf_cookie(response: Response, raw_csrf: str) -> None:
    """Issue the JS-readable CSRF cookie for the web console.

    Unlike the session cookie it is NOT HttpOnly (browser JS must read it to echo
    ``X-CSRF-Token``), but it is not an authentication credential: the server
    still validates it against the session's one-way verifier. It is never stored
    in local/sessionStorage by the frontend.
    """
    settings = get_settings()
    response.set_cookie(
        key=session_service.CSRF_COOKIE_NAME,
        value=raw_csrf,
        httponly=False,
        secure=settings.app_env == "prod",
        samesite="lax",
        path=_CSRF_COOKIE_PATH,
        max_age=settings.oidc_session_absolute_seconds,
    )


def _clear_csrf_cookie(response: Response) -> None:
    response.delete_cookie(key=session_service.CSRF_COOKIE_NAME, path=_CSRF_COOKIE_PATH)


def _set_txn_cookie(response: RedirectResponse, raw_txn_cookie: str) -> None:
    settings = get_settings()
    response.set_cookie(
        key=session_service.LOGIN_TXN_COOKIE_NAME,
        value=raw_txn_cookie,
        httponly=True,
        secure=settings.app_env == "prod",
        samesite="lax",
        path=_TXN_COOKIE_PATH,
        max_age=settings.oidc_login_ttl_seconds,
    )


def _clear_txn_cookie(response: Response) -> None:
    response.delete_cookie(
        key=session_service.LOGIN_TXN_COOKIE_NAME, path=_TXN_COOKIE_PATH
    )


def _web_callback_redirect() -> RedirectResponse:
    """Fixed, server-configured web-console completion redirect (no user input)."""
    path = get_settings().oidc_web_callback_path
    assert path is not None  # guarded by web_console_enabled
    return RedirectResponse(path, status_code=302)


def _oidc_failure_response(
    request: Request, *, code: str, message: str, status_code: int
) -> Response:
    """Return a standardized OIDC failure and clear the transaction cookie.

    Used for every callback failure path (pre- and post-consume). The browser's
    binding cookie is cleared alongside the error, but a valid pending login
    transaction is never consumed or deleted merely because a callback failed its
    binding/state check.

    In web-console mode the JSON error is replaced by a fixed 302 redirect to the
    server-configured callback path; the frontend callback page then detects the
    absent session (401) and renders a fixed failure state.
    """
    if get_settings().web_console_enabled:
        response: Response = _web_callback_redirect()
        _clear_txn_cookie(response)
        return response
    response = JSONResponse(
        status_code=status_code,
        content={
            "error": {
                "code": code,
                "message": message,
                "request_id": get_gateway_request_id(request),
            }
        },
    )
    _clear_txn_cookie(response)
    return response


async def _record_login_failed(
    session: AsyncSession, *, reason: str, resource_id: str
) -> None:
    await session_service.record_audit(
        session,
        actor_principal_id=None,
        action="human.login_failed",
        resource_type="oidc_login",
        resource_id=resource_id,
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
        transaction, raw_txn_cookie = await session_service.create_login_transaction(
            session
        )
    url = await oidc_module.get_oidc_provider().begin_login(
        state=transaction.state,
        nonce=transaction.nonce,
        code_challenge=transaction.code_challenge,
    )
    response = RedirectResponse(url, status_code=302)
    _set_txn_cookie(response, raw_txn_cookie)
    return response


@router.get("/auth/oidc/callback")
async def oidc_callback(
    session: SessionDep,
    request: Request,
    code: str | None = Query(default=None),
    state: str | None = Query(default=None),
    error: str | None = Query(default=None),
) -> Response:
    if error is not None or not code or not state:
        return _oidc_failure_response(
            request,
            code="oidc_authentication_failed",
            message="OIDC authentication failed.",
            status_code=401,
        )

    now = session_service.utcnow()
    settings = get_settings()
    txn_cookie = request.cookies.get(session_service.LOGIN_TXN_COOKIE_NAME)

    # Consume the one-time login transaction. A missing/wrong/unknown state or a
    # missing/wrong binding cookie fails here WITHOUT deleting the transaction, so
    # a legitimate initiating browser can still complete its pending login.
    try:
        async with session.begin():
            transaction = await session_service.consume_login_transaction(
                session, state, txn_cookie, now
            )
    except OidcLoginStateInvalid:
        return _oidc_failure_response(
            request,
            code="invalid_login_state",
            message="The login attempt is invalid or expired.",
            status_code=400,
        )
    txn_id = str(transaction.id)

    provider = oidc_module.get_oidc_provider()
    try:
        token_response = await provider.exchange_code(
            code=code, code_verifier=transaction.code_verifier
        )
    except OidcAuthenticationFailed:
        async with session.begin():
            await _record_login_failed(
                session, reason=LOGIN_FAILED_EXCHANGE, resource_id=txn_id
            )
        return _oidc_failure_response(
            request,
            code="oidc_authentication_failed",
            message="OIDC authentication failed.",
            status_code=401,
        )

    id_token = token_response.get("id_token")
    if not isinstance(id_token, str) or not id_token:
        async with session.begin():
            await _record_login_failed(
                session, reason=LOGIN_FAILED_ID_TOKEN, resource_id=txn_id
            )
        return _oidc_failure_response(
            request,
            code="oidc_authentication_failed",
            message="OIDC authentication failed.",
            status_code=401,
        )

    try:
        claims = await provider.validate_id_token(id_token, expected_nonce=transaction.nonce)
    except OidcAuthenticationFailed:
        async with session.begin():
            await _record_login_failed(
                session, reason=LOGIN_FAILED_ID_TOKEN, resource_id=txn_id
            )
        return _oidc_failure_response(
            request,
            code="oidc_authentication_failed",
            message="OIDC authentication failed.",
            status_code=401,
        )

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
            await _record_login_failed(
                session, reason=LOGIN_FAILED_IDENTITY, resource_id=txn_id
            )
        return _oidc_failure_response(
            request,
            code="oidc_authentication_failed",
            message="OIDC authentication failed.",
            status_code=401,
        )

    body = SessionEstablished(
        session=_session_read(
            session_entity, principal, tuple(a.role for a in assignments)
        ),
        csrf_token=raw_csrf,
    )

    if settings.web_console_enabled:
        # Web-console completion: HttpOnly session cookie + JS-readable CSRF cookie,
        # then a fixed 302 to the frontend callback route. The raw CSRF token is
        # never placed in the URL/query/fragment.
        response: Response = _web_callback_redirect()
        _set_session_cookie(response, raw_cookie)
        _set_csrf_cookie(response, raw_csrf)
        _clear_txn_cookie(response)
        return response

    response = JSONResponse(status_code=200, content=body.model_dump(mode="json"))
    _set_session_cookie(response, raw_cookie)
    _clear_txn_cookie(response)
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
    _clear_csrf_cookie(response)
    return response


__all__ = ["router"]
