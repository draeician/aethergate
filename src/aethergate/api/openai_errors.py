"""OpenAI-compatible error mapping for the /v1 surface.

Errors never contain credentials, upstream URLs, or secret material.
"""

from __future__ import annotations

from fastapi import Request
from fastapi.responses import JSONResponse

from aethergate.api.deps import get_gateway_request_id
from aethergate.contracts.openai import ErrorResponse
from aethergate.errors import (
    AmbiguousRoute,
    AuthenticationRequired,
    DestinationDenied,
    DomainError,
    ModelAliasNotFound,
    ProviderError,
    QueueFull,
    QueueTimeout,
    ResourceInactive,
    RouteUnresolved,
    SecretResolutionError,
    UnsupportedProvider,
)


def _error(
    status_code: int,
    message: str,
    type_: str,
    *,
    code: str | None = None,
    request_id: str | None = None,
) -> JSONResponse:
    body = ErrorResponse(
        error={
            "message": message,
            "type": type_,
            "code": code,
            "request_id": request_id,
        }
    )
    return JSONResponse(status_code=status_code, content=body.model_dump())


def to_openai_error(exc: DomainError, request_id: str | None) -> JSONResponse:
    if isinstance(exc, ModelAliasNotFound):
        return _error(
            404,
            f"The model `{exc.alias}` does not exist or you do not have access to it.",
            "invalid_request_error",
            code="model_not_found",
            request_id=request_id,
        )
    if isinstance(exc, ResourceInactive):
        return _error(
            400,
            "The requested model is currently unavailable.",
            "invalid_request_error",
            code="model_unavailable",
            request_id=request_id,
        )
    if isinstance(exc, AmbiguousRoute):
        return _error(
            500,
            "The requested model is misconfigured and cannot be routed.",
            "server_error",
            code="ambiguous_route",
            request_id=request_id,
        )
    if isinstance(exc, RouteUnresolved):
        return _error(
            500,
            "The requested model is not fully configured.",
            "server_error",
            code="route_unresolved",
            request_id=request_id,
        )
    if isinstance(exc, DestinationDenied):
        return _error(
            500,
            "The requested model cannot be reached.",
            "server_error",
            code="egress_denied",
            request_id=request_id,
        )
    if isinstance(exc, UnsupportedProvider):
        return _error(
            500,
            "The requested model's provider is not supported.",
            "server_error",
            code="unsupported_provider",
            request_id=request_id,
        )
    if isinstance(exc, SecretResolutionError):
        return _error(
            500,
            "The requested model's credentials could not be resolved.",
            "server_error",
            code="secret_resolution_failed",
            request_id=request_id,
        )
    if isinstance(exc, ProviderError):
        return _error(
            502,
            exc.message,
            "upstream_error",
            code="upstream_error",
            request_id=request_id,
        )
    if isinstance(exc, AuthenticationRequired):
        return _error(
            401,
            "Authentication is required to use inference.",
            "invalid_request_error",
            code="not_authenticated",
            request_id=request_id,
        )
    if isinstance(exc, QueueFull):
        return _error(
            503,
            "The inference queue is at capacity; please retry later.",
            "server_error",
            code="queue_full",
            request_id=request_id,
        )
    if isinstance(exc, QueueTimeout):
        return _error(
            504,
            "The request timed out waiting in the inference queue.",
            "server_error",
            code="queue_timeout",
            request_id=request_id,
        )
    return _error(
        500,
        "An internal error occurred.",
        "server_error",
        code="internal_error",
        request_id=request_id,
    )


async def domain_error_handler(request: Request, exc: DomainError) -> JSONResponse:
    return to_openai_error(exc, get_gateway_request_id(request))
