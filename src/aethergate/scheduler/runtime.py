"""Shared construction of scheduler runtime dependencies.

Keeps the API and worker entrypoints in sync: both build the same encryptor,
inference service, and scheduler service from process settings.
"""

from __future__ import annotations

from aethergate.config import get_settings
from aethergate.egress import build_destination_policy
from aethergate.encryption import encryptor_from_key
from aethergate.inference.service import InferenceService
from aethergate.persistence.db import get_session_factory
from aethergate.scheduler.service import SchedulingService
from aethergate.secrets import build_secret_resolver


def build_inference_service() -> InferenceService:
    """Build the inference service from current runtime settings."""
    settings = get_settings()
    policy = build_destination_policy(settings)
    return InferenceService(
        build_secret_resolver(settings),
        policy,
        timeout_seconds=settings.inference_timeout_seconds,
    )


def build_scheduling_service() -> SchedulingService:
    """Build the scheduler service with a queue encryptor from runtime settings."""
    settings = get_settings()
    encryptor = encryptor_from_key(
        settings.queue_key.get_secret_value() if settings.queue_key else None
    )
    return SchedulingService(
        encryptor=encryptor,
        inference_service=build_inference_service(),
        session_factory=get_session_factory(),
        settings=settings,
    )
