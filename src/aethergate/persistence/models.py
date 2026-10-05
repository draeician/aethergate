"""SQLAlchemy ORM models for the v2 configuration/identity subset.

These models back PostgreSQL. They use stable opaque string IDs (not public
auto-increment integers), timezone-aware timestamps, foreign keys, and never
store plaintext provider/API secret material — only a reference to a
``secret_refs`` row.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
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
    """A one-way-verifiable scoped client credential.

    Only the SHA-256 verifier (``key_hash``) and non-secret display prefix
    (``key_prefix``) are stored; the raw key is never persisted and never
    recoverable from reads/backups. ``key_hash`` is nullable so a synthetic
    development-bypass identity (which is never presented as a Bearer token)
    can exist without a verifiable key. NULLs are distinct under the unique
    index, so many synthetic identities may coexist.
    """

    __tablename__ = "api_credentials"
    __table_args__ = (
        Index("uq_api_credentials_key_hash", "key_hash", unique=True),
        CheckConstraint(
            "audience IN ('inference', 'admin')", name="ck_api_credentials_audience"
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_new_id)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id"), nullable=False, index=True
    )
    principal_id: Mapped[str | None] = mapped_column(
        ForeignKey("principals.id"), nullable=True, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    key_prefix: Mapped[str | None] = mapped_column(String(64), nullable=True)
    key_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    audience: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default="inference"
    )
    scopes: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class RoleAssignment(Base, TimestampMixin):
    """A durable grant of an administrative role to a principal.

    ``resource_scope_type`` is ``deployment`` (``resource_id`` is ``''``) or
    ``project`` (``resource_id`` is the target project id). ``resource_id`` is
    stored as a non-empty string so the active-equivalence unique index treats
    deployment assignments as equivalent; the repository maps ``''`` <-> None.
    """

    __tablename__ = "role_assignments"
    __table_args__ = (
        CheckConstraint(
            "role IN ('system_admin', 'project_admin', 'project_viewer')",
            name="ck_role_assignments_role",
        ),
        CheckConstraint(
            "resource_scope_type IN ('deployment', 'project')",
            name="ck_role_assignments_scope_type",
        ),
        CheckConstraint(
            "resource_scope_type <> 'project' OR resource_id <> ''",
            name="ck_role_assignments_project_scope_requires_resource",
        ),
        Index(
            "uq_role_assignments_active_equivalent",
            "principal_id",
            "role",
            "resource_scope_type",
            "resource_id",
            unique=True,
            postgresql_where=text("is_active AND revoked_at IS NULL"),
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_new_id)
    principal_id: Mapped[str] = mapped_column(
        ForeignKey("principals.id"), nullable=False, index=True
    )
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    resource_scope_type: Mapped[str] = mapped_column(String(32), nullable=False)
    resource_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    created_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class BootstrapState(Base, TimestampMixin):
    """Singleton DB-authoritative bootstrap completion state.

    Exactly one row exists (fixed id ``bootstrap``), created by migration 0010.
    The bootstrap service locks it with ``SELECT ... FOR UPDATE`` so concurrent
    bootstrap attempts serialize and only one can transition it to completed.
    """

    __tablename__ = "bootstrap_state"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    completed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    initial_project_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    initial_admin_principal_id: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )
    initial_admin_credential_id: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )


class AuditEvent(Base, TimestampMixin):
    """Immutable administrative audit event (safe metadata only).

    ``actor_principal_id`` is ``NULL`` for the bootstrap (no authenticated actor
    yet) — represented explicitly rather than inventing an actor. No raw keys,
    hashes, tokens, prompts, or provider secrets are ever written here.
    """

    __tablename__ = "audit_events"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_new_id)
    actor_principal_id: Mapped[str | None] = mapped_column(
        String(64), nullable=True, index=True
    )
    project_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    action: Mapped[str] = mapped_column(String(128), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(128), nullable=False)
    resource_id: Mapped[str] = mapped_column(String(128), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=func.now()
    )
    details: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)


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
        CheckConstraint(
            "metric IN ('requests', 'tokens')", name="ck_quota_limits_metric"
        ),
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
        CheckConstraint("committed_units >= 0", name="ck_quota_windows_committed_nonneg"),
        CheckConstraint("reserved_units >= 0", name="ck_quota_windows_reserved_nonneg"),
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

    __table_args__ = (
        CheckConstraint(
            "reserved_units >= 0", name="ck_quota_reservations_reserved_nonneg"
        ),
        CheckConstraint(
            "committed_units >= 0", name="ck_quota_reservations_committed_nonneg"
        ),
        CheckConstraint(
            "metric IN ('requests', 'tokens')", name="ck_quota_reservations_metric"
        ),
        CheckConstraint(
            "state IN ('reserved', 'committed', 'released')",
            name="ck_quota_reservations_state",
        ),
    )

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
    __table_args__ = (
        CheckConstraint(
            "default_output_tokens IS NULL OR default_output_tokens >= 1",
            name="ck_route_bindings_default_output_positive",
        ),
    )

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

    # Effective scheduling scope (endpoint + quota group) persisted as non-content
    # metadata so the worker can group eligible queued work by scope. Revalidated
    # against the freshly-resolved route immediately before dispatch.
    quota_group_id: Mapped[str | None] = mapped_column(
        ForeignKey("quota_groups.id"), nullable=True, index=True
    )

    # Earliest time this queued request may become eligible again (next fixed-window
    # reset or cooldown expiry). When in the future, the worker skips the request.
    next_eligible_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Limiting quota detail for inspection (why this request is blocked).
    wait_limit_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    wait_limit_metric: Mapped[str | None] = mapped_column(String(32), nullable=True)

    # Captured immutable price snapshot at the dispatch decision (nullable when
    # the route carries no pricing configuration). Non-content accounting metadata.
    price_snapshot_id: Mapped[str | None] = mapped_column(
        ForeignKey("price_snapshots.id"), nullable=True, index=True
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


class PricePolicy(Base, TimestampMixin):
    """Mutable route pricing configuration (never the historical record)."""

    __tablename__ = "price_policies"
    __table_args__ = (
        CheckConstraint(
            "billing_unit IN ('request', 'token')", name="ck_price_policies_billing_unit"
        ),
        CheckConstraint("unit_scale >= 1", name="ck_price_policies_unit_scale_positive"),
        CheckConstraint(
            "currency ~ '^[A-Z]{3}$'", name="ck_price_policies_currency_format"
        ),
        CheckConstraint(
            "request_price IS NULL OR request_price >= 0",
            name="ck_price_policies_request_price_nonneg",
        ),
        CheckConstraint(
            "input_price IS NULL OR input_price >= 0",
            name="ck_price_policies_input_price_nonneg",
        ),
        CheckConstraint(
            "output_price IS NULL OR output_price >= 0",
            name="ck_price_policies_output_price_nonneg",
        ),
        CheckConstraint(
            "billing_unit <> 'request' OR "
            "(request_price IS NOT NULL AND input_price IS NULL AND output_price IS NULL)",
            name="ck_price_policies_request_shape",
        ),
        CheckConstraint(
            "billing_unit <> 'token' OR "
            "(request_price IS NULL AND input_price IS NOT NULL AND output_price IS NOT NULL)",
            name="ck_price_policies_token_shape",
        ),
        Index(
            "uq_price_policies_one_enabled_per_route",
            "route_binding_id",
            unique=True,
            postgresql_where=text("enabled"),
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_new_id)
    route_binding_id: Mapped[str] = mapped_column(
        ForeignKey("route_bindings.id"), nullable=False, index=True
    )
    billing_unit: Mapped[str] = mapped_column(String(32), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    unit_scale: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    request_price: Mapped[Decimal | None] = mapped_column(Numeric(24, 12), nullable=True)
    input_price: Mapped[Decimal | None] = mapped_column(Numeric(24, 12), nullable=True)
    output_price: Mapped[Decimal | None] = mapped_column(Numeric(24, 12), nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    name: Mapped[str | None] = mapped_column(String(255), nullable=True)


class PriceSnapshot(Base, TimestampMixin):
    """Immutable capture of the effective price at a dispatch decision."""

    __tablename__ = "price_snapshots"
    __table_args__ = (
        CheckConstraint(
            "billing_unit IN ('request', 'token')", name="ck_price_snapshots_billing_unit"
        ),
        CheckConstraint("unit_scale >= 1", name="ck_price_snapshots_unit_scale_positive"),
        CheckConstraint(
            "currency ~ '^[A-Z]{3}$'", name="ck_price_snapshots_currency_format"
        ),
        CheckConstraint(
            "request_price IS NULL OR request_price >= 0",
            name="ck_price_snapshots_request_price_nonneg",
        ),
        CheckConstraint(
            "input_price IS NULL OR input_price >= 0",
            name="ck_price_snapshots_input_price_nonneg",
        ),
        CheckConstraint(
            "output_price IS NULL OR output_price >= 0",
            name="ck_price_snapshots_output_price_nonneg",
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_new_id)
    source_price_policy_id: Mapped[str] = mapped_column(
        ForeignKey("price_policies.id"), nullable=False, index=True
    )
    route_binding_id: Mapped[str] = mapped_column(
        ForeignKey("route_bindings.id"), nullable=False, index=True
    )
    provider_account_id: Mapped[str] = mapped_column(
        ForeignKey("provider_accounts.id"), nullable=False, index=True
    )
    model_alias_id: Mapped[str] = mapped_column(
        ForeignKey("model_aliases.id"), nullable=False, index=True
    )
    billing_unit: Mapped[str] = mapped_column(String(32), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    unit_scale: Mapped[int] = mapped_column(Integer, nullable=False)
    request_price: Mapped[Decimal | None] = mapped_column(Numeric(24, 12), nullable=True)
    input_price: Mapped[Decimal | None] = mapped_column(Numeric(24, 12), nullable=True)
    output_price: Mapped[Decimal | None] = mapped_column(Numeric(24, 12), nullable=True)
    captured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=func.now()
    )


class ProjectBudgetPolicy(Base, TimestampMixin):
    """An optional project-level spending-cap policy."""

    __tablename__ = "project_budget_policies"
    __table_args__ = (
        CheckConstraint("limit_amount > 0", name="ck_budget_policies_limit_positive"),
        CheckConstraint("window_seconds >= 1", name="ck_budget_policies_window_positive"),
        CheckConstraint(
            "currency ~ '^[A-Z]{3}$'", name="ck_budget_policies_currency_format"
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_new_id)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    limit_amount: Mapped[Decimal] = mapped_column(Numeric(24, 12), nullable=False)
    window_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class BudgetWindow(Base, TimestampMixin):
    """Committed/reserved monetary amounts for one budget policy window."""

    __tablename__ = "budget_windows"
    __table_args__ = (
        UniqueConstraint(
            "budget_policy_id", "window_start", name="uq_budget_windows_policy_start"
        ),
        CheckConstraint(
            "committed_amount >= 0", name="ck_budget_windows_committed_nonneg"
        ),
        CheckConstraint(
            "reserved_amount >= 0", name="ck_budget_windows_reserved_nonneg"
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_new_id)
    budget_policy_id: Mapped[str] = mapped_column(
        ForeignKey("project_budget_policies.id"), nullable=False, index=True
    )
    window_start: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    committed_amount: Mapped[Decimal] = mapped_column(
        Numeric(24, 12), nullable=False, default=Decimal("0")
    )
    reserved_amount: Mapped[Decimal] = mapped_column(
        Numeric(24, 12), nullable=False, default=Decimal("0")
    )


class BudgetReservation(Base, TimestampMixin):
    """A per-request monetary reservation against one budget policy window."""

    __tablename__ = "budget_reservations"
    __table_args__ = (
        CheckConstraint(
            "reserved_amount >= 0", name="ck_budget_reservations_reserved_nonneg"
        ),
        CheckConstraint(
            "committed_amount >= 0", name="ck_budget_reservations_committed_nonneg"
        ),
        CheckConstraint(
            "state IN ('reserved', 'committed', 'released')",
            name="ck_budget_reservations_state",
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_new_id)
    request_id: Mapped[str] = mapped_column(
        ForeignKey("inference_requests.id"), nullable=False, index=True
    )
    budget_policy_id: Mapped[str] = mapped_column(
        ForeignKey("project_budget_policies.id"), nullable=False, index=True
    )
    price_snapshot_id: Mapped[str | None] = mapped_column(
        ForeignKey("price_snapshots.id"), nullable=True
    )
    window_start: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    reserved_amount: Mapped[Decimal] = mapped_column(Numeric(24, 12), nullable=False)
    committed_amount: Mapped[Decimal] = mapped_column(
        Numeric(24, 12), nullable=False, default=Decimal("0")
    )
    state: Mapped[str] = mapped_column(
        String(32), nullable=False, default="reserved"
    )  # reserved | committed | released
    settlement_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    committed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    released_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class UsageRecord(Base, TimestampMixin):
    """An immutable record of trustworthy measured usage for one request."""

    __tablename__ = "usage_records"
    __table_args__ = (
        UniqueConstraint("request_id", name="uq_usage_records_request"),
        CheckConstraint("amount >= 0", name="ck_usage_records_amount_nonneg"),
        CheckConstraint("input_units >= 0", name="ck_usage_records_input_nonneg"),
        CheckConstraint("output_units >= 0", name="ck_usage_records_output_nonneg"),
        CheckConstraint(
            "currency ~ '^[A-Z]{3}$'", name="ck_usage_records_currency_format"
        ),
        CheckConstraint(
            "billing_unit IN ('request', 'token')", name="ck_usage_records_billing_unit"
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_new_id)
    request_id: Mapped[str] = mapped_column(
        ForeignKey("inference_requests.id"), nullable=False, index=True
    )
    execution_attempt_id: Mapped[str] = mapped_column(
        ForeignKey("execution_attempts.id"), nullable=False
    )
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id"), nullable=False, index=True
    )
    principal_id: Mapped[str | None] = mapped_column(
        ForeignKey("principals.id"), nullable=True
    )
    api_credential_id: Mapped[str | None] = mapped_column(
        ForeignKey("api_credentials.id"), nullable=True
    )
    model_alias_id: Mapped[str] = mapped_column(
        ForeignKey("model_aliases.id"), nullable=False
    )
    route_binding_id: Mapped[str] = mapped_column(
        ForeignKey("route_bindings.id"), nullable=False
    )
    provider_account_id: Mapped[str] = mapped_column(
        ForeignKey("provider_accounts.id"), nullable=False
    )
    price_snapshot_id: Mapped[str] = mapped_column(
        ForeignKey("price_snapshots.id"), nullable=False
    )
    billing_unit: Mapped[str] = mapped_column(String(32), nullable=False)
    input_units: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    output_units: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    request_units: Mapped[int | None] = mapped_column(Integer, nullable=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(24, 12), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=func.now()
    )
    upstream_request_id: Mapped[str | None] = mapped_column(String(255), nullable=True)


class LedgerEntry(Base, TimestampMixin):
    """An immutable, append-only monetary ledger entry."""

    __tablename__ = "ledger_entries"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_ledger_entries_idempotency"),
        CheckConstraint(
            "entry_type IN ('usage_debit', 'adjustment_credit', 'adjustment_debit')",
            name="ck_ledger_entries_type",
        ),
        CheckConstraint(
            "currency ~ '^[A-Z]{3}$'", name="ck_ledger_entries_currency_format"
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_new_id)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id"), nullable=False, index=True
    )
    usage_record_id: Mapped[str | None] = mapped_column(
        ForeignKey("usage_records.id"), nullable=True
    )
    entry_type: Mapped[str] = mapped_column(String(32), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(24, 12), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    idempotency_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    reason: Mapped[str | None] = mapped_column(String(255), nullable=True)


__all__ = [
    "Project",
    "Principal",
    "SecretRef",
    "ApiCredential",
    "RoleAssignment",
    "BootstrapState",
    "AuditEvent",
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
    "PricePolicy",
    "PriceSnapshot",
    "ProjectBudgetPolicy",
    "BudgetWindow",
    "BudgetReservation",
    "UsageRecord",
    "LedgerEntry",
]
