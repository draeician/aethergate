"""SQLAlchemy ORM models for the v2 configuration/identity subset.

These models back PostgreSQL. They use stable opaque string IDs (not public
auto-increment integers), timezone-aware timestamps, foreign keys, and never
store plaintext provider/API secret material — only a reference to a
``secret_refs`` row.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from aethergate.persistence.base import Base


def _new_id() -> str:
    return str(uuid.uuid4())


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class Project(Base, TimestampMixin):
    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_new_id)
    name: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class Principal(Base, TimestampMixin):
    __tablename__ = "principals"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_new_id)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id"), nullable=False, index=True
    )
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class SecretRef(Base, TimestampMixin):
    """Metadata for a secret; the material itself is never persisted here."""

    __tablename__ = "secret_refs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_new_id)
    name: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)


class ApiCredential(Base, TimestampMixin):
    __tablename__ = "api_credentials"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_new_id)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id"), nullable=False, index=True
    )
    principal_id: Mapped[str | None] = mapped_column(
        ForeignKey("principals.id"), nullable=True, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    secret_ref_id: Mapped[str] = mapped_column(
        ForeignKey("secret_refs.id"), nullable=False
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class Provider(Base, TimestampMixin):
    __tablename__ = "providers"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_new_id)
    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    capabilities: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class ProviderAccount(Base, TimestampMixin):
    __tablename__ = "provider_accounts"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_new_id)
    provider_id: Mapped[str] = mapped_column(
        ForeignKey("providers.id"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    external_account_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    secret_ref_id: Mapped[str | None] = mapped_column(
        ForeignKey("secret_refs.id"), nullable=True
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class Endpoint(Base, TimestampMixin):
    __tablename__ = "endpoints"
    __table_args__ = (
        CheckConstraint("max_concurrency >= 1", name="ck_endpoints_max_concurrency_positive"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_new_id)
    provider_account_id: Mapped[str] = mapped_column(
        ForeignKey("provider_accounts.id"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    base_destination: Mapped[str] = mapped_column(Text, nullable=False)
    max_concurrency: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class QuotaGroup(Base, TimestampMixin):
    __tablename__ = "quota_groups"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_new_id)
    provider_account_id: Mapped[str] = mapped_column(
        ForeignKey("provider_accounts.id"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Provider 429 cooldown for this shared scope; null means no active cooldown.
    cooldown_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class QuotaLimit(Base, TimestampMixin):
    """A single configured request/token limit within a quota group."""

    __tablename__ = "quota_limits"
    __table_args__ = (
        CheckConstraint("limit_units >= 1", name="ck_quota_limits_units_positive"),
        CheckConstraint("window_seconds >= 1", name="ck_quota_limits_window_positive"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_new_id)
    quota_group_id: Mapped[str] = mapped_column(
        ForeignKey("quota_groups.id"), nullable=False, index=True
    )
    metric: Mapped[str] = mapped_column(String(32), nullable=False)  # requests | tokens
    limit_units: Mapped[int] = mapped_column(Integer, nullable=False)
    window_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    name: Mapped[str | None] = mapped_column(String(255), nullable=True)


class QuotaWindow(Base, TimestampMixin):
    """Aggregated committed/reserved units for one limit's fixed window.

    Fixed windows are anchored to the UTC epoch. ``committed_units`` records
    actual consumption (may exceed the limit when usage was over-reserved);
    ``reserved_units`` records pre-dispatch reservation not yet committed.
    """

    __tablename__ = "quota_windows"
    __table_args__ = (
        UniqueConstraint(
            "quota_limit_id", "window_start", name="uq_quota_windows_limit_start"
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_new_id)
    quota_limit_id: Mapped[str] = mapped_column(
        ForeignKey("quota_limits.id"), nullable=False, index=True
    )
    window_start: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    committed_units: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    reserved_units: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class QuotaReservation(Base, TimestampMixin):
    """A per-request reservation/commitment against one quota limit's window."""

    __tablename__ = "quota_reservations"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_new_id)
    request_id: Mapped[str] = mapped_column(
        ForeignKey("inference_requests.id"), nullable=False, index=True
    )
    quota_limit_id: Mapped[str] = mapped_column(
        ForeignKey("quota_limits.id"), nullable=False, index=True
    )
    window_start: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    metric: Mapped[str] = mapped_column(String(32), nullable=False)
    reserved_units: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    committed_units: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    state: Mapped[str] = mapped_column(
        String(32), nullable=False, default="reserved"
    )  # reserved | committed | released
    committed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    released_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class ModelAlias(Base, TimestampMixin):
    __tablename__ = "model_aliases"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_new_id)
    name: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    capabilities: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class RouteBinding(Base, TimestampMixin):
    __tablename__ = "route_bindings"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_new_id)
    model_alias_id: Mapped[str] = mapped_column(
        ForeignKey("model_aliases.id"), nullable=False, index=True
    )
    endpoint_id: Mapped[str] = mapped_column(
        ForeignKey("endpoints.id"), nullable=False, index=True
    )
    provider_account_id: Mapped[str] = mapped_column(
        ForeignKey("provider_accounts.id"), nullable=False, index=True
    )
    upstream_model: Mapped[str | None] = mapped_column(String(255), nullable=True)
    quota_group_id: Mapped[str | None] = mapped_column(
        ForeignKey("quota_groups.id"), nullable=True, index=True
    )
    default_output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class InferenceRequest(Base, TimestampMixin):
    """Durable scheduler record for one inference request.

    Prompt/message and completion content is stored encrypted in
    ``payload_encrypted`` / ``result_encrypted``; scheduling metadata (state,
    timestamps, ownership, fencing) is plaintext and contains no content.
    """

    __tablename__ = "inference_requests"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_new_id)
    project_id: Mapped[str | None] = mapped_column(
        ForeignKey("projects.id"), nullable=True, index=True
    )
    principal_id: Mapped[str | None] = mapped_column(
        ForeignKey("principals.id"), nullable=True, index=True
    )
    api_credential_id: Mapped[str | None] = mapped_column(
        ForeignKey("api_credentials.id"), nullable=True, index=True
    )
    model_alias_id: Mapped[str] = mapped_column(
        ForeignKey("model_aliases.id"), nullable=False, index=True
    )
    endpoint_id: Mapped[str | None] = mapped_column(
        ForeignKey("endpoints.id"), nullable=True, index=True
    )
    state: Mapped[str] = mapped_column(String(32), nullable=False, default="queued", index=True)
    stream: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    payload_encrypted: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    result_encrypted: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)

    queued_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    queue_wait_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    cancellation_requested: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )

    worker_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    fencing_token: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Explicit operator reconciliation of ambiguous (outcome_unknown) executions.
    reconciled_state: Mapped[str | None] = mapped_column(String(32), nullable=True)
    reconciled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    reconciled_by: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # Non-content scheduler explanation for a queued request (e.g. a blocking
    # quota window); plaintext and carries no prompt/completion content.
    wait_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)


class Reservation(Base, TimestampMixin):
    """A held unit of physical endpoint capacity."""

    __tablename__ = "reservations"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_new_id)
    request_id: Mapped[str] = mapped_column(
        ForeignKey("inference_requests.id"), nullable=False, index=True
    )
    endpoint_id: Mapped[str] = mapped_column(
        ForeignKey("endpoints.id"), nullable=False, index=True
    )
    requested_units: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    granted_units: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    acquired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ExecutionAttempt(Base, TimestampMixin):
    """A single worker execution attempt for a request, guarded by a fence token."""

    __tablename__ = "execution_attempts"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_new_id)
    request_id: Mapped[str] = mapped_column(
        ForeignKey("inference_requests.id"), nullable=False, index=True
    )
    endpoint_id: Mapped[str | None] = mapped_column(
        ForeignKey("endpoints.id"), nullable=True, index=True
    )
    reservation_id: Mapped[str | None] = mapped_column(
        ForeignKey("reservations.id"), nullable=True
    )
    state: Mapped[str] = mapped_column(String(32), nullable=False, default="reserved")
    worker_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    fencing_token: Mapped[int] = mapped_column(BigInteger, nullable=False)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    upstream_request_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)


class StreamEvent(Base, TimestampMixin):
    """An encrypted, monotonically ordered per-request streaming event."""

    __tablename__ = "stream_events"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_new_id)
    request_id: Mapped[str] = mapped_column(
        ForeignKey("inference_requests.id"), nullable=False, index=True
    )
    seq: Mapped[int] = mapped_column(BigInteger, nullable=False)
    event_encrypted: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)


__all__ = [
    "Project",
    "Principal",
    "SecretRef",
    "ApiCredential",
    "Provider",
    "ProviderAccount",
    "Endpoint",
    "QuotaGroup",
    "QuotaLimit",
    "QuotaWindow",
    "QuotaReservation",
    "ModelAlias",
    "RouteBinding",
    "InferenceRequest",
    "Reservation",
    "ExecutionAttempt",
    "StreamEvent",
]
