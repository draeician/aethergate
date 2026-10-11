"""FastAPI application entrypoint for AetherGate v2."""

from __future__ import annotations

import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from aethergate import __version__
from aethergate.api import (
    accounting_admin,
    admin_errors,
    catalog_admin,
    cli_auth,
    observability_admin,
    queue_admin,
    session_auth,
)
from aethergate.api import admin as admin_api
from aethergate.api.deps import get_gateway_request_id
from aethergate.api.health import router as health_router
from aethergate.api.openai_chat import router as chat_router
from aethergate.api.openai_errors import _error, domain_error_handler
from aethergate.api.openai_models import router as models_router
from aethergate.api.openai_responses import router as responses_router
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
    CliSessionInvalid,
    CredentialLifecycleError,
    CsrfValidationError,
    DeviceAccessDenied,
    DeviceCodeInvalid,
    DeviceExpired,
    DeviceFlowUnavailable,
    DomainError,
    OidcAuthenticationFailed,
    OidcConfigurationError,
    OidcLoginStateInvalid,
    PricePolicyConflictError,
    QueueTransitionError,
    SessionInvalid,
)
from aethergate.logging_config import (
    configure_logging_from_settings,
    gateway_request_id_var,
    log_event,
)

_access_logger = logging.getLogger("aethergate.access")


@asynccontextmanager
async def _lifespan(_app: FastAPI) -> AsyncIterator[None]:
    configure_logging_from_settings()
    yield


# Route templates that use the OpenAI/admin versioned surface. The access log
# records only the matched route template (never the raw path/query), so opaque
# resource IDs and any sensitive query strings are never logged.
app = FastAPI(title="AetherGate", version=__version__, lifespan=_lifespan)


@app.middleware("http")
async def _structured_access_log(request: Request, call_next):
    """Emit one safe structured access log per request.

    Only the matched route template, method, status, duration, and the gateway
    request ID are logged. Query strings, headers, cookies, bodies, resource IDs,
    and content are never logged.
    """
    started = time.perf_counter()
    # Establish the gateway request ID up front and expose it to log correlation;
    # handler code reuses the same ID via ``get_gateway_request_id``.
    request_id = get_gateway_request_id(request)
    token = gateway_request_id_var.set(request_id)
    try:
        try:
            response = await call_next(request)
        except Exception:
            route = request.scope.get("route")
            log_event(
                _access_logger,
                "http.request",
                level=logging.ERROR,
                method=request.method,
                route=getattr(route, "path", None) or "unmatched",
                status=500,
                duration_ms=round((time.perf_counter() - started) * 1000, 3),
                request_id=request_id,
            )
            raise
    finally:
        gateway_request_id_var.reset(token)
    route = request.scope.get("route")
    log_event(
        _access_logger,
        "http.request",
        method=request.method,
        route=getattr(route, "path", None) or "unmatched",
        status=response.status_code,
        duration_ms=round((time.perf_counter() - started) * 1000, 3),
        request_id=request_id,
    )
    return response


app.include_router(health_router)
app.include_router(models_router)
app.include_router(chat_router)
app.include_router(responses_router)
app.include_router(admin_api.router)
app.include_router(catalog_admin.router)
app.include_router(accounting_admin.router)
app.include_router(queue_admin.router)
app.include_router(observability_admin.router)
app.include_router(session_auth.router)
app.include_router(cli_auth.router)

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
app.add_exception_handler(CliSessionInvalid, admin_errors.cli_session_invalid_handler)
app.add_exception_handler(DeviceFlowUnavailable, admin_errors.device_flow_unavailable_handler)
app.add_exception_handler(DeviceCodeInvalid, admin_errors.device_code_invalid_handler)
app.add_exception_handler(DeviceExpired, admin_errors.device_expired_handler)
app.add_exception_handler(DeviceAccessDenied, admin_errors.device_access_denied_handler)
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
app.add_exception_handler(
    QueueTransitionError, admin_errors.queue_transition_error_handler
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
