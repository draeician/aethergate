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


class CredentialAudience(StrEnum):
    """Which surface a scoped API credential may authenticate against."""

    INFERENCE = "inference"
    ADMIN = "admin"


class CredentialScope(StrEnum):
    """Granular permission scopes carried by a credential.

    Inference phase 1 implements only ``inference:invoke``; the enum is
    extensible so future resource/admin scopes can be added without replacing
    the credential model.
    """

    INFERENCE_INVOKE = "inference:invoke"


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
    """The type of an append-only monetary ledger entry.

    A ledger entry is either a priced measured-usage debit (backed by a
    ``UsageRecord``) or an explicit adjustment credit/debit (no usage record).
    It is an accounting/event primitive, not a mandatory prepaid balance.
    """

    USAGE_DEBIT = "usage_debit"
    ADJUSTMENT_CREDIT = "adjustment_credit"
    ADJUSTMENT_DEBIT = "adjustment_debit"


class QuotaMetric(StrEnum):
    """The unit of a shared quota limit."""

    REQUESTS = "requests"
    TOKENS = "tokens"


class QuotaReservationState(StrEnum):
    """Lifecycle of a single per-request quota reservation."""

    RESERVED = "reserved"
    COMMITTED = "committed"
    RELEASED = "released"


class BudgetReservationState(StrEnum):
    """Lifecycle of a single per-request monetary budget reservation."""

    RESERVED = "reserved"
    COMMITTED = "committed"
    RELEASED = "released"


__all__ = [
    "Capability",
    "BillingUnit",
    "PrincipalKind",
    "CredentialAudience",
    "CredentialScope",
    "RequestState",
    "ExecutionAttemptState",
    "LedgerEntryType",
    "QuotaMetric",
    "QuotaReservationState",
    "BudgetReservationState",
]
