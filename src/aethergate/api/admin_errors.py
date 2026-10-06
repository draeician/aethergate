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
    ActiveRouteConflictError,
    AdminAuthenticationRequired,
    AdminAuthorizationError,
    AdminResourceNotFound,
    AdminValidationError,
    BootstrapAlreadyCompleted,
    BootstrapTokenRejected,
    CatalogConflictError,
    CatalogDestinationDenied,
    CatalogParentMismatchError,
    CredentialLifecycleError,
    CsrfValidationError,
    OidcAuthenticationFailed,
    OidcConfigurationError,
    OidcLoginStateInvalid,
    PricePolicyConflictError,
    QueueTransitionError,
    SessionInvalid,
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


async def admin_resource_not_found_handler(
    request: Request, exc: AdminResourceNotFound
) -> JSONResponse:
    return _admin_error(
        404,
        "not_found",
        "Resource not found.",
        get_gateway_request_id(request),
    )


async def admin_validation_error_handler(
    request: Request, exc: AdminValidationError
) -> JSONResponse:
    return _admin_error(
        400,
        "invalid_request",
        str(exc),
        get_gateway_request_id(request),
    )


async def oidc_configuration_error_handler(
    request: Request, exc: OidcConfigurationError
) -> JSONResponse:
    return _admin_error(
        503,
        "oidc_unavailable",
        "The identity provider is unavailable or misconfigured.",
        get_gateway_request_id(request),
    )


async def oidc_authentication_failed_handler(
    request: Request, exc: OidcAuthenticationFailed
) -> JSONResponse:
    return _admin_error(
        401,
        "oidc_authentication_failed",
        "OIDC authentication failed.",
        get_gateway_request_id(request),
    )


async def oidc_login_state_invalid_handler(
    request: Request, exc: OidcLoginStateInvalid
) -> JSONResponse:
    return _admin_error(
        400,
        "invalid_login_state",
        "The login attempt is invalid or expired.",
        get_gateway_request_id(request),
    )


async def session_invalid_handler(
    request: Request, exc: SessionInvalid
) -> JSONResponse:
    return _admin_error(
        401,
        "not_authenticated",
        "Authentication is required for the admin API.",
        get_gateway_request_id(request),
    )


async def csrf_validation_error_handler(
    request: Request, exc: CsrfValidationError
) -> JSONResponse:
    return _admin_error(
        403,
        "invalid_csrf_token",
        "The CSRF token is missing or invalid.",
        get_gateway_request_id(request),
    )


async def catalog_conflict_error_handler(
    request: Request, exc: CatalogConflictError
) -> JSONResponse:
    return _admin_error(
        409,
        "resource_conflict",
        str(exc),
        get_gateway_request_id(request),
    )


async def active_route_conflict_error_handler(
    request: Request, exc: ActiveRouteConflictError
) -> JSONResponse:
    return _admin_error(
        409,
        "active_route_conflict",
        "The model alias already has an active route binding.",
        get_gateway_request_id(request),
    )


async def catalog_parent_mismatch_error_handler(
    request: Request, exc: CatalogParentMismatchError
) -> JSONResponse:
    return _admin_error(
        400,
        "parent_mismatch",
        str(exc),
        get_gateway_request_id(request),
    )


async def catalog_destination_denied_error_handler(
    request: Request, exc: CatalogDestinationDenied
) -> JSONResponse:
    return _admin_error(
        400,
        "destination_denied",
        str(exc),
        get_gateway_request_id(request),
    )


async def price_policy_conflict_error_handler(
    request: Request, exc: PricePolicyConflictError
) -> JSONResponse:
    return _admin_error(
        409,
        "price_policy_conflict",
        "The route binding already has an enabled price policy.",
        get_gateway_request_id(request),
    )


async def queue_transition_error_handler(
    request: Request, exc: QueueTransitionError
) -> JSONResponse:
    return _admin_error(
        409,
        exc.detail,
        str(exc),
        get_gateway_request_id(request),
    )


__all__ = [
    "admin_auth_error_handler",
    "admin_authorization_error_handler",
    "bootstrap_token_rejected_handler",
    "bootstrap_already_completed_handler",
    "credential_lifecycle_error_handler",
    "admin_resource_not_found_handler",
    "admin_validation_error_handler",
    "oidc_configuration_error_handler",
    "oidc_authentication_failed_handler",
    "oidc_login_state_invalid_handler",
    "session_invalid_handler",
    "csrf_validation_error_handler",
    "catalog_conflict_error_handler",
    "active_route_conflict_error_handler",
    "catalog_parent_mismatch_error_handler",
    "catalog_destination_denied_error_handler",
    "price_policy_conflict_error_handler",
    "queue_transition_error_handler",
]
