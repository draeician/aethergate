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
    DateTime,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Text,
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
    name: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)


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
    "ModelAlias",
    "RouteBinding",
    "InferenceRequest",
    "Reservation",
    "ExecutionAttempt",
    "StreamEvent",
]
