"""Cross-domain entity contracts.

These are persistence- and provider-independent contracts, not ORM models.
Entities reference other resources by typed ID only; there are no bidirectional
object graphs or implicit lazy relationships. Secret material is never embedded;
it is referenced through :class:`~aethergate.domain.ids.SecretRefId`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from aethergate.domain.enums import (
    AdminAuthenticationKind,
    BillingUnit,
    BudgetReservationState,
    Capability,
    CredentialAudience,
    CredentialScope,
    LedgerEntryType,
    PrincipalKind,
    QuotaMetric,
    RequestState,
    ResourceScopeType,
    Role,
)
from aethergate.domain.ids import (
    ApiCredentialId,
    AuditEventId,
    BrowserSessionId,
    BudgetPolicyId,
    BudgetReservationId,
    BudgetWindowId,
    EndpointId,
    ExecutionAttemptId,
    ExternalIdentityId,
    LedgerEntryId,
    ModelAliasId,
    OidcLoginStateId,
    PricePolicyId,
    PriceSnapshotId,
    PrincipalId,
    ProjectId,
    ProviderAccountId,
    ProviderId,
    QuotaGroupId,
    QuotaLimitId,
    RequestId,
    ReservationId,
    RoleAssignmentId,
    RouteBindingId,
    SecretRefId,
    UsageRecordId,
)
from aethergate.domain.value_objects import (
    Currency,
    Money,
    NonNegativeMoney,
    PositiveMoney,
)


class Entity(BaseModel):
    """Marker base for domain entity contracts."""

    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------------------
# Identity / access
# ---------------------------------------------------------------------------


class Project(Entity):
    id: ProjectId
    name: str
    is_active: bool = True


class Principal(Entity):
    id: PrincipalId
    project_id: ProjectId
    kind: PrincipalKind
    name: str
    is_active: bool = True


class ApiCredential(Entity):
    """A one-way-verifiable scoped client credential.

    The raw key is high-entropy random material that is never stored or
    returned after creation; only the SHA-256 verifier (``key_hash``) and a
    non-secret display prefix (``key_prefix``) are persisted. ``secret_ref_id``
    is intentionally absent: that model fits retrievable upstream provider
    secrets, not client API keys whose plaintext never needs recovering.
    """

    id: ApiCredentialId
    project_id: ProjectId
    principal_id: PrincipalId | None = None
    name: str
    key_prefix: str | None = None
    key_hash: str | None = None
    audience: CredentialAudience = CredentialAudience.INFERENCE
    scopes: tuple[CredentialScope, ...] = (CredentialScope.INFERENCE_INVOKE,)
    created_at: datetime | None = None
    expires_at: datetime | None = None
    revoked_at: datetime | None = None
    last_used_at: datetime | None = None
    is_active: bool = True


@dataclass(frozen=True)
class RequestContext:
    """The durable authorization context resolved from an authenticated request.

    Carried by the scheduler/admission stack so usage/accounting attribution
    remains correct; safe to log by opaque IDs only (never a raw token).
    """

    project_id: ProjectId
    principal_id: PrincipalId
    api_credential_id: ApiCredentialId
    audience: CredentialAudience
    scopes: tuple[CredentialScope, ...]


class RoleAssignment(Entity):
    """A durable grant of an administrative role to a principal.

    ``resource_scope_type`` is ``deployment`` (system-wide; ``resource_id`` is
    ``None``) or ``project`` (``resource_id`` is the target project). Revocation
    is durable (``revoked_at`` + ``is_active``) and never deletes the history.
    """

    id: RoleAssignmentId
    principal_id: PrincipalId
    role: Role
    resource_scope_type: ResourceScopeType
    resource_id: ProjectId | None = None
    created_at: datetime | None = None
    created_by: PrincipalId | None = None
    revoked_at: datetime | None = None
    is_active: bool = True


@dataclass(frozen=True)
class AdminRequestContext:
    """The durable context resolved from an authenticated admin request.

    Carries the effective (active) role assignments for the authenticated
    principal so the authorization service can answer ``authorize_admin``
    without re-resolving identity. The actor may be authenticated by an admin
    service credential (``api_credential_id`` + ``audience`` + ``scopes``) or by
    a human browser session (``browser_session_id``, no credential). The
    ``authentication_kind`` disambiguates the two; authorization always flows
    through the same centralized RBAC engine.
    """

    project_id: ProjectId
    principal_id: PrincipalId
    authentication_kind: AdminAuthenticationKind
    roles: tuple[Role, ...]
    assignments: tuple[RoleAssignment, ...]
    api_credential_id: ApiCredentialId | None = None
    browser_session_id: BrowserSessionId | None = None
    audience: CredentialAudience | None = None
    scopes: tuple[CredentialScope, ...] = ()


class ExternalIdentity(Entity):
    """A durable link from an OIDC provider identity to a Principal.

    ``(issuer, subject)`` is the stable, case-sensitive identity key; email is a
    non-authoritative display claim only. No raw ID/access/refresh token is ever
    stored here.
    """

    id: ExternalIdentityId
    principal_id: PrincipalId
    issuer: str
    subject: str
    email: str | None = None
    display_name: str | None = None
    created_at: datetime | None = None
    last_login_at: datetime | None = None
    is_active: bool = True


class BrowserSession(Entity):
    """An authoritative server-managed browser session.

    Only one-way verifiers (SHA-256 of the raw cookie and raw CSRF token) are
    persisted; the raw values are returned only in ``Set-Cookie`` / the session
    response and are never stored or logged.
    """

    id: BrowserSessionId
    principal_id: PrincipalId
    session_hash: str
    csrf_token_hash: str
    created_at: datetime | None = None
    last_seen_at: datetime | None = None
    idle_expires_at: datetime
    absolute_expires_at: datetime
    revoked_at: datetime | None = None
    is_active: bool = True


class OidcLoginState(Entity):
    """A one-time OIDC authorization-code login transaction.

    Binds the random ``state``, ``nonce``, and S256 PKCE material to the
    initiating browser via a dedicated short-lived transaction cookie (only its
    one-way SHA-256 ``txn_cookie_hash`` is persisted). It is short-lived and
    consumed exactly once; the PKCE verifier is required only to exchange the
    authorization code and is never written to the audit log.
    """

    id: OidcLoginStateId
    state: str
    nonce: str
    code_verifier: str
    code_challenge: str
    txn_cookie_hash: str
    created_at: datetime | None = None
    expires_at: datetime
    consumed_at: datetime | None = None


class BootstrapState(Entity):
    """DB-authoritative one-use bootstrap completion state (safe metadata only)."""

    id: str
    completed: bool = False
    completed_at: datetime | None = None
    initial_project_id: ProjectId | None = None
    initial_admin_principal_id: PrincipalId | None = None
    initial_admin_credential_id: ApiCredentialId | None = None


# ---------------------------------------------------------------------------
# Catalog / routing
# ---------------------------------------------------------------------------


class Provider(Entity):
    id: ProviderId
    kind: str  # adapter/provider type; pinned to a known set later
    name: str
    capabilities: tuple[Capability, ...] = ()
    is_active: bool = True


class SecretRef(Entity):
    """A reference to secret material; never the material itself."""

    id: SecretRefId
    name: str
    created_at: datetime


class ProviderAccount(Entity):
    """A distinct billing/quota scope at a provider; not identified by URL."""

    id: ProviderAccountId
    provider_id: ProviderId
    name: str
    external_account_id: str | None = None
    secret_ref_id: SecretRefId | None = None
    is_active: bool = True


class Endpoint(Entity):
    """A physical serving location (deployment) for a provider account."""

    id: EndpointId
    provider_account_id: ProviderAccountId
    name: str
    base_destination: str
    max_concurrency: int = 1
    is_active: bool = True

    @field_validator("max_concurrency")
    @classmethod
    def _positive_concurrency(cls, value: int) -> int:
        if value < 1:
            raise ValueError("max_concurrency must be >= 1")
        return value


class QuotaGroup(Entity):
    """A shared allowance scope that may span multiple routes/endpoints/models.

    A quota group belongs to exactly one provider account; routes may reference
    it only when their provider account matches.
    """

    id: QuotaGroupId
    provider_account_id: ProviderAccountId
    name: str
    description: str | None = None

    @field_validator("provider_account_id")
    @classmethod
    def _non_empty_account(cls, value: ProviderAccountId) -> ProviderAccountId:
        if not str(value).strip():
            raise ValueError("provider_account_id must not be empty")
        return value


class QuotaLimit(Entity):
    """A single configured limit within a quota group.

    ``metric`` is ``requests`` or ``tokens``; ``limit_units`` and
    ``window_seconds`` are positive integers; windows are fixed and anchored
    deterministically to the UTC epoch.
    """

    id: QuotaLimitId
    quota_group_id: QuotaGroupId
    metric: QuotaMetric
    limit_units: int
    window_seconds: int
    enabled: bool = True
    name: str | None = None

    @field_validator("limit_units")
    @classmethod
    def _positive_limit(cls, value: int) -> int:
        if value < 1:
            raise ValueError("limit_units must be >= 1")
        return value

    @field_validator("window_seconds")
    @classmethod
    def _positive_window(cls, value: int) -> int:
        if value < 1:
            raise ValueError("window_seconds must be >= 1")
        return value


class ModelAlias(Entity):
    """A stable public model identity. Distinct from endpoint/provider identity."""

    id: ModelAliasId
    name: str
    capabilities: tuple[Capability, ...] = ()
    is_active: bool = True


class RouteBinding(Entity):
    """A permitted route from a model alias to an endpoint/account.

    ``upstream_model`` is the provider-facing model/deployment identifier that
    must be invoked. It is provider-specific opaque configuration, separate from
    the public ``ModelAlias.name``, and is never derived implicitly from it.

    Referencing a quota group here does not grant quota ownership; it only
    associates the route with a shared allowance scope.
    """

    id: RouteBindingId
    model_alias_id: ModelAliasId
    endpoint_id: EndpointId
    provider_account_id: ProviderAccountId
    upstream_model: str | None = None
    quota_group_id: QuotaGroupId | None = None
    default_output_tokens: int | None = None
    is_active: bool = True

    @field_validator("default_output_tokens")
    @classmethod
    def _positive_default_output(cls, value: int | None) -> int | None:
        if value is not None and value < 1:
            raise ValueError("default_output_tokens must be >= 1 when set")
        return value


# ---------------------------------------------------------------------------
# Scheduler / execution
# ---------------------------------------------------------------------------


class InferenceRequest(Entity):
    id: RequestId
    project_id: ProjectId
    principal_id: PrincipalId
    api_credential_id: ApiCredentialId
    model_alias_id: ModelAliasId
    state: RequestState
    created_at: datetime


class ExecutionAttempt(Entity):
    id: ExecutionAttemptId
    request_id: RequestId
    state: RequestState
    started_at: datetime | None = None


class Reservation(Entity):
    id: ReservationId
    request_id: RequestId
    quota_group_id: QuotaGroupId
    requested_units: int
    granted_units: int


# ---------------------------------------------------------------------------
# Accounting / audit
# ---------------------------------------------------------------------------


class PricePolicy(Entity):
    """Mutable pricing configuration associated with a route binding.

    This is the editable configuration, *not* the historical record. A captured
    :class:`PriceSnapshot` is immutable and never changes when this policy is
    edited later.
    """

    id: PricePolicyId
    route_binding_id: RouteBindingId
    billing_unit: BillingUnit
    currency: Currency
    unit_scale: int = 1
    request_price: NonNegativeMoney | None = None
    input_price: NonNegativeMoney | None = None
    output_price: NonNegativeMoney | None = None
    enabled: bool = True
    name: str | None = None

    @field_validator("unit_scale")
    @classmethod
    def _positive_unit_scale(cls, value: int) -> int:
        if value < 1:
            raise ValueError("unit_scale must be >= 1")
        return value

    @model_validator(mode="after")
    def _validate_price_shape(self) -> PricePolicy:
        if self.billing_unit == BillingUnit.REQUEST:
            if self.request_price is None:
                raise ValueError("request billing requires request_price")
            if self.input_price is not None or self.output_price is not None:
                raise ValueError(
                    "request billing must not set input_price or output_price"
                )
        elif self.billing_unit == BillingUnit.TOKEN:
            if self.input_price is None or self.output_price is None:
                raise ValueError(
                    "token billing requires both input_price and output_price"
                )
            if self.request_price is not None:
                raise ValueError("token billing must not set request_price")
        return self


class PriceSnapshot(Entity):
    """An immutable capture of the effective price at a dispatch decision.

    Contains everything needed to reproduce the monetary calculation later
    without reading mutable pricing configuration. Never updated or deleted by
    application code during normal operation.
    """

    id: PriceSnapshotId
    source_price_policy_id: PricePolicyId
    route_binding_id: RouteBindingId
    provider_account_id: ProviderAccountId
    model_alias_id: ModelAliasId
    billing_unit: BillingUnit
    currency: Currency
    unit_scale: int
    request_price: NonNegativeMoney | None = None
    input_price: NonNegativeMoney | None = None
    output_price: NonNegativeMoney | None = None
    captured_at: datetime


class ProjectBudgetPolicy(Entity):
    """An optional project-level spending-cap policy.

    A project with no enabled budget policy is never monetarily blocked. A
    project may carry more than one enabled policy; every applicable
    same-currency policy must admit a request.
    """

    id: BudgetPolicyId
    project_id: ProjectId
    name: str
    currency: Currency
    limit_amount: PositiveMoney
    window_seconds: int
    enabled: bool = True

    @field_validator("window_seconds")
    @classmethod
    def _positive_window(cls, value: int) -> int:
        if value < 1:
            raise ValueError("window_seconds must be >= 1")
        return value


class BudgetWindow(Entity):
    """Authoritative committed/reserved monetary amounts for one policy window."""

    id: BudgetWindowId
    budget_policy_id: BudgetPolicyId
    window_start: datetime
    committed_amount: NonNegativeMoney
    reserved_amount: NonNegativeMoney


class BudgetReservation(Entity):
    """A per-request monetary reservation against one budget policy window."""

    id: BudgetReservationId
    request_id: RequestId
    budget_policy_id: BudgetPolicyId
    # Detached (released) pre-dispatch reservations store NULL: their snapshot is
    # discarded, so this must be nullable to match persistence truth.
    price_snapshot_id: PriceSnapshotId | None = None
    window_start: datetime
    reserved_amount: NonNegativeMoney
    committed_amount: NonNegativeMoney
    state: BudgetReservationState
    settlement_reason: str | None = None


class UsageRecord(Entity):
    """An immutable record of trustworthy measured usage for one request.

    One logical settled usage record exists per request; uniqueness is enforced
    in PostgreSQL. Carries no prompt/completion content or secret material.
    """

    id: UsageRecordId
    request_id: RequestId
    execution_attempt_id: ExecutionAttemptId
    project_id: ProjectId
    principal_id: PrincipalId | None = None
    api_credential_id: ApiCredentialId | None = None
    model_alias_id: ModelAliasId
    route_binding_id: RouteBindingId
    provider_account_id: ProviderAccountId
    price_snapshot_id: PriceSnapshotId
    billing_unit: BillingUnit
    input_units: int
    output_units: int
    request_units: int | None = None
    amount: NonNegativeMoney
    currency: Currency
    recorded_at: datetime
    upstream_request_id: str | None = None


class LedgerEntry(Entity):
    """An immutable, append-only monetary ledger entry.

    A ``usage_debit`` entry references its :class:`UsageRecord`; an explicit
    ``adjustment_credit``/``adjustment_debit`` entry does not. ``amount`` is a
    signed, typed Decimal amount.
    """

    id: LedgerEntryId
    project_id: ProjectId
    usage_record_id: UsageRecordId | None = None
    entry_type: LedgerEntryType
    amount: Money
    currency: Currency
    created_at: datetime
    idempotency_key: str | None = None
    reason: str | None = None


class AuditEvent(Entity):
    id: AuditEventId
    actor_principal_id: PrincipalId | None = None
    project_id: ProjectId | None = None
    action: str
    resource_type: str
    resource_id: str
    occurred_at: datetime
    metadata: dict[str, Any] = Field(default_factory=dict)


__all__ = [
    "Project",
    "Principal",
    "ApiCredential",
    "RequestContext",
    "RoleAssignment",
    "AdminRequestContext",
    "ExternalIdentity",
    "BrowserSession",
    "OidcLoginState",
    "BootstrapState",
    "Provider",
    "SecretRef",
    "ProviderAccount",
    "Endpoint",
    "QuotaGroup",
    "QuotaLimit",
    "ModelAlias",
    "RouteBinding",
    "InferenceRequest",
    "ExecutionAttempt",
    "Reservation",
    "PricePolicy",
    "PriceSnapshot",
    "ProjectBudgetPolicy",
    "BudgetWindow",
    "BudgetReservation",
    "UsageRecord",
    "LedgerEntry",
    "AuditEvent",
]
