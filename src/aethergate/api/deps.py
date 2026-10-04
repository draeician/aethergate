"""FastAPI dependencies for the OpenAI-compatible surface."""

from __future__ import annotations

import uuid

from fastapi import Request

from aethergate.config import get_settings
from aethergate.egress import DestinationPolicy
from aethergate.errors import AuthenticationRequired
from aethergate.inference.service import InferenceService
from aethergate.secrets import EnvSecretResolver


def get_gateway_request_id(request: Request) -> str:
    """Return a per-request gateway request ID, creating one on first use."""
    request_id = getattr(request.state, "gateway_request_id", None)
    if request_id is None:
        request_id = uuid.uuid4().hex
        request.state.gateway_request_id = request_id
    return request_id


def build_inference_service() -> InferenceService:
    """Build the inference service from current runtime settings.

    Uses ``EnvSecretResolver``, which is development/test only. Production will
    replace it with the enterprise secret backend.
    """
    settings = get_settings()
    policy = DestinationPolicy(settings.upstream_allowlist_hosts)
    return InferenceService(
        EnvSecretResolver(),
        policy,
        timeout_seconds=settings.inference_timeout_seconds,
    )


def require_inference_access() -> None:
    """Fail closed unless the explicit development auth bypass is enabled.

    This is a temporary policy for the first nomnom smoke test, not the final
    API-key authentication design.
    """
    if not get_settings().allow_inference_auth_bypass:
        raise AuthenticationRequired()
