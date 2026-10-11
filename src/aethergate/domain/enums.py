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


class AdminAuthenticationKind(StrEnum):
    """How an admin request was authenticated.

    A service credential presents an ``Authorization: Bearer agk_...`` header; a
    human browser session presents the server-managed session cookie; a human CLI
    session presents an ``Authorization: Bearer ags_...`` header established
    through the OAuth device flow. All three resolve to the same
    :class:`~aethergate.domain.entities.AdminRequestContext` and the same
    centralized RBAC engine, but browser/CLI sessions carry no API credential.
    """

    SERVICE_CREDENTIAL = "service_credential"
    BROWSER_SESSION = "browser_session"
    CLI_SESSION = "cli_session"


class CredentialAudience(StrEnum):
    """Which surface a scoped API credential may authenticate against."""

    INFERENCE = "inference"
    ADMIN = "admin"


class CredentialScope(StrEnum):
    """Granular permission scopes carried by a credential.

    Inference credentials carry only ``inference:invoke``; admin credentials
    carry only ``admin:*`` permission scopes. The enum is extensible so future
    resource scopes can be added without replacing the credential model. The
    ``admin:*`` values double as the typed administrative permission names
    checked by the RBAC authorization service.
    """

    INFERENCE_INVOKE = "inference:invoke"

    ADMIN_CREDENTIALS_READ = "admin:credentials:read"
    ADMIN_CREDENTIALS_WRITE = "admin:credentials:write"
    ADMIN_PROJECTS_READ = "admin:projects:read"
    ADMIN_PROJECTS_WRITE = "admin:projects:write"
    ADMIN_PRINCIPALS_READ = "admin:principals:read"
    ADMIN_PRINCIPALS_WRITE = "admin:principals:write"
    ADMIN_CATALOG_READ = "admin:catalog:read"
    ADMIN_CATALOG_WRITE = "admin:catalog:write"
    ADMIN_ACCOUNTING_READ = "admin:accounting:read"
    ADMIN_ACCOUNTING_WRITE = "admin:accounting:write"
    ADMIN_QUEUE_READ = "admin:queue:read"
    ADMIN_QUEUE_WRITE = "admin:queue:write"
    ADMIN_AUDIT_READ = "admin:audit:read"


class Role(StrEnum):
    """Built-in administrative roles for the control plane.

    ``system_admin`` is deployment-wide; ``project_admin`` and ``project_viewer``
    are scoped to exactly one project (their ``resource_id``). Custom-role CRUD
    is deferred, but the model is designed so new roles can be added without a
    rewrite of the authorization service.
    """

    SYSTEM_ADMIN = "system_admin"
    PROJECT_ADMIN = "project_admin"
    PROJECT_VIEWER = "project_viewer"


class ResourceScopeType(StrEnum):
    """The scope of a role assignment's authority."""

    DEPLOYMENT = "deployment"
    PROJECT = "project"


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


class EndpointOperationalState(StrEnum):
    """Durable scheduler/operator state of an endpoint's dispatch.

    Distinct from catalog ``Endpoint.is_active`` (configuration/lifecycle).
    ``active`` is normal dispatch; ``paused`` and ``draining`` gate new capacity
    reservations while leaving existing in-flight work to settle.
    """

    ACTIVE = "active"
    PAUSED = "paused"
    DRAINING = "draining"


__all__ = [
    "Capability",
    "BillingUnit",
    "PrincipalKind",
    "AdminAuthenticationKind",
    "CredentialAudience",
    "CredentialScope",
    "Role",
    "ResourceScopeType",
    "RequestState",
    "ExecutionAttemptState",
    "LedgerEntryType",
    "QuotaMetric",
    "QuotaReservationState",
    "BudgetReservationState",
    "EndpointOperationalState",
]
