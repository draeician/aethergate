"""Structured admin error envelope and exception handlers.

The control plane returns its own safe error shape (no secrets, no OpenAI
envelope). Authentication/authorization/bootstrap failures are fixed,
indistinguishable messages so a client cannot probe bootstrap state or the
existence of a target resource.
"""

from __future__ import annotations

from fastapi import Request
from fastapi.responses import JSONResponse

from aethergate.api.deps import get_gateway_request_id
from aethergate.errors import (
    AdminAuthenticationRequired,
    AdminAuthorizationError,
    BootstrapAlreadyCompleted,
    BootstrapTokenRejected,
    CredentialLifecycleError,
)


def _admin_error(
    status_code: int,
    code: str,
    message: str,
    request_id: str | None,
    *,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"error": {"code": code, "message": message, "request_id": request_id}},
        headers=headers,
    )


async def admin_auth_error_handler(
    request: Request, exc: AdminAuthenticationRequired
) -> JSONResponse:
    return _admin_error(
        401,
        "not_authenticated",
        "Authentication is required for the admin API.",
        get_gateway_request_id(request),
        headers={"WWW-Authenticate": "Bearer"},
    )


async def admin_authorization_error_handler(
    request: Request, exc: AdminAuthorizationError
) -> JSONResponse:
    return _admin_error(
        403,
        "forbidden",
        "The authenticated principal is not authorized for this action.",
        get_gateway_request_id(request),
    )


async def bootstrap_token_rejected_handler(
    request: Request, exc: BootstrapTokenRejected
) -> JSONResponse:
    return _admin_error(
        401,
        "bootstrap_token_rejected",
        "The bootstrap token is missing or invalid.",
        get_gateway_request_id(request),
    )


async def bootstrap_already_completed_handler(
    request: Request, exc: BootstrapAlreadyCompleted
) -> JSONResponse:
    return _admin_error(
        409,
        "bootstrap_already_completed",
        "Bootstrap has already been completed.",
        get_gateway_request_id(request),
    )


async def credential_lifecycle_error_handler(
    request: Request, exc: CredentialLifecycleError
) -> JSONResponse:
    return _admin_error(
        400,
        "invalid_credential_transition",
        str(exc),
        get_gateway_request_id(request),
    )


__all__ = [
    "admin_auth_error_handler",
    "admin_authorization_error_handler",
    "bootstrap_token_rejected_handler",
    "bootstrap_already_completed_handler",
    "credential_lifecycle_error_handler",
]
