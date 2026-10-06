"""FastAPI application entrypoint for AetherGate v2."""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from aethergate import __version__
from aethergate.api import accounting_admin, admin_errors, catalog_admin, session_auth
from aethergate.api import admin as admin_api
from aethergate.api.deps import get_gateway_request_id
from aethergate.api.health import router as health_router
from aethergate.api.openai_chat import router as chat_router
from aethergate.api.openai_errors import _error, domain_error_handler
from aethergate.api.openai_models import router as models_router
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
    DomainError,
    OidcAuthenticationFailed,
    OidcConfigurationError,
    OidcLoginStateInvalid,
    PricePolicyConflictError,
    SessionInvalid,
)

app = FastAPI(title="AetherGate", version=__version__)
app.include_router(health_router)
app.include_router(models_router)
app.include_router(chat_router)
app.include_router(admin_api.router)
app.include_router(catalog_admin.router)
app.include_router(accounting_admin.router)
app.include_router(session_auth.router)

app.add_exception_handler(DomainError, domain_error_handler)
app.add_exception_handler(
    AdminAuthenticationRequired, admin_errors.admin_auth_error_handler
)
app.add_exception_handler(
    AdminAuthorizationError, admin_errors.admin_authorization_error_handler
)
app.add_exception_handler(
    BootstrapTokenRejected, admin_errors.bootstrap_token_rejected_handler
)
app.add_exception_handler(
    BootstrapAlreadyCompleted, admin_errors.bootstrap_already_completed_handler
)
app.add_exception_handler(
    CredentialLifecycleError, admin_errors.credential_lifecycle_error_handler
)
app.add_exception_handler(
    AdminResourceNotFound, admin_errors.admin_resource_not_found_handler
)
app.add_exception_handler(
    AdminValidationError, admin_errors.admin_validation_error_handler
)
app.add_exception_handler(
    OidcConfigurationError, admin_errors.oidc_configuration_error_handler
)
app.add_exception_handler(
    OidcAuthenticationFailed, admin_errors.oidc_authentication_failed_handler
)
app.add_exception_handler(
    OidcLoginStateInvalid, admin_errors.oidc_login_state_invalid_handler
)
app.add_exception_handler(SessionInvalid, admin_errors.session_invalid_handler)
app.add_exception_handler(CsrfValidationError, admin_errors.csrf_validation_error_handler)
app.add_exception_handler(CatalogConflictError, admin_errors.catalog_conflict_error_handler)
app.add_exception_handler(
    ActiveRouteConflictError, admin_errors.active_route_conflict_error_handler
)
app.add_exception_handler(
    CatalogParentMismatchError, admin_errors.catalog_parent_mismatch_error_handler
)
app.add_exception_handler(
    CatalogDestinationDenied, admin_errors.catalog_destination_denied_error_handler
)
app.add_exception_handler(
    PricePolicyConflictError, admin_errors.price_policy_conflict_error_handler
)


@app.exception_handler(RequestValidationError)
async def validation_error_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    return _error(
        400,
        "The request was malformed or contained unsupported fields.",
        "invalid_request_error",
        code="invalid_request",
        request_id=get_gateway_request_id(request),
    )
