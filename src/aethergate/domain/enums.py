"""Domain enumerations shared across workstreams."""

from __future__ import annotations

from enum import StrEnum


class Capability(StrEnum):
    TEXT = "text"
    IMAGE = "image"
    AUDIO_STT = "audio_stt"
    AUDIO_TTS = "audio_tts"
    EMBEDDING = "embedding"


class BillingUnit(StrEnum):
    TOKEN = "token"
    IMAGE = "image"
    MINUTE = "minute"
    REQUEST = "request"


class PrincipalKind(StrEnum):
    USER = "user"
    SERVICE_ACCOUNT = "service_account"


class RequestState(StrEnum):
    """Lifecycle states for an inference request (scheduler-facing)."""

    VALIDATED = "validated"
    QUEUED = "queued"
    RESERVED = "reserved"
    DISPATCHED = "dispatched"
    STREAMING = "streaming"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"
    OUTCOME_UNKNOWN = "outcome_unknown"


class ExecutionAttemptState(StrEnum):
    """State of a single execution attempt (dispatched to a provider)."""

    PENDING = "pending"
    DISPATCHED = "dispatched"
    STREAMING = "streaming"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    OUTCOME_UNKNOWN = "outcome_unknown"


class LedgerEntryType(StrEnum):
    DEBIT = "debit"
    CREDIT = "credit"
    ADJUSTMENT = "adjustment"


class QuotaMetric(StrEnum):
    """The unit of a shared quota limit."""

    REQUESTS = "requests"
    TOKENS = "tokens"


class QuotaReservationState(StrEnum):
    """Lifecycle of a single per-request quota reservation."""

    RESERVED = "reserved"
    COMMITTED = "committed"
    RELEASED = "released"


__all__ = [
    "Capability",
    "BillingUnit",
    "PrincipalKind",
    "RequestState",
    "ExecutionAttemptState",
    "LedgerEntryType",
    "QuotaMetric",
    "QuotaReservationState",
]
