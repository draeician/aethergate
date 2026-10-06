"""Initial ``/admin/v1`` resource DTO foundations.

Persistence-independent request/response shapes. Requirements: stable opaque
IDs, explicit optionality, no secret values on normal read DTOs, no
database-specific fields, no business logic, and no assumption that the web
console is the only client. Routes/paths/filters are NOT finalized here.

Secret material is never placed in these DTOs; it is referenced through
``SecretRefId`` (or, for API credentials, the material is revealed only at an
explicit create/rotate boundary in a future contract).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import Field, model_validator

from aethergate.contracts.common import ContractModel
from aethergate.domain.enums import (
    AdminAuthenticationKind,
    BillingUnit,
    Capability,
    CredentialAudience,
    CredentialScope,
    LedgerEntryType,
    PrincipalKind,
    QuotaMetric,
    ResourceScopeType,
    Role,
)
from aethergate.domain.ids import (
    ApiCredentialId,
    AuditEventId,
    BrowserSessionId,
    BudgetPolicyId,
    BudgetReservationId,
    CliSessionId,
    EndpointId,
    ExecutionAttemptId,
    ExternalIdentityId,
    LedgerEntryId,
    ModelAliasId,
    PricePolicyId,
    PriceSnapshotId,
    PrincipalId,
    ProjectId,
    ProviderAccountId,
    ProviderId,
    QuotaGroupId,
    QuotaLimitId,
    RequestId,
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

# --- Projects ---------------------------------------------------------------


class ProjectCreate(ContractModel):
    name: str = Field(min_length=1)
    is_active: bool = True


class ProjectRead(ContractModel):
    id: ProjectId
    name: str
    is_active: bool


class ProjectUpdate(ContractModel):
    name: str | None = Field(default=None, min_length=1)
    is_active: bool | None = None


# --- Principals -------------------------------------------------------------


class PrincipalCreate(ContractModel):
    kind: PrincipalKind
    name: str = Field(min_length=1)
    is_active: bool = True


class PrincipalRead(ContractModel):
    id: PrincipalId
    project_id: ProjectId
    kind: PrincipalKind
    name: str
    is_active: bool


class PrincipalUpdate(ContractModel):
    name: str | None = Field(default=None, min_length=1)
    is_active: bool | None = None


# --- API credentials ---------------------------------------------------------


class ApiCredentialCreate(ContractModel):
    project_id: ProjectId
    principal_id: PrincipalId | None = None
    name: str = Field(min_length=1)
    audience: CredentialAudience = CredentialAudience.INFERENCE
    scopes: tuple[CredentialScope, ...] | None = None
    expires_at: datetime | None = None


class ApiCredentialRead(ContractModel):
    """Credential metadata; never the raw key or its verifier/hash."""

    id: ApiCredentialId
    project_id: ProjectId
    principal_id: PrincipalId | None = None
    name: str
    key_prefix: str | None = None
    audience: CredentialAudience
    scopes: tuple[CredentialScope, ...]
    created_at: datetime
    expires_at: datetime | None = None
    revoked_at: datetime | None = None
    is_active: bool


class ApiCredentialCreateResult(ContractModel):
    credential: ApiCredentialRead
    raw_key: str = Field(min_length=1)


class ApiCredentialRotateResult(ContractModel):
    credential: ApiCredentialRead
    raw_key: str = Field(min_length=1)


class ApiCredentialRevokeRequest(ContractModel):
    reason: str | None = Field(default=None, min_length=1)


class ApiCredentialRevokeResult(ContractModel):
    credential: ApiCredentialRead


class ApiCredentialUpdate(ContractModel):
    name: str | None = Field(default=None, min_length=1)
    is_active: bool | None = None


# --- Admin identity / RBAC / bootstrap ---------------------------------------


class BootstrapResult(ContractModel):
    credential: ApiCredentialRead
    raw_key: str = Field(min_length=1)


class RoleAssignmentRead(ContractModel):
    id: RoleAssignmentId
    principal_id: PrincipalId
    role: Role
    resource_scope_type: ResourceScopeType
    resource_id: ProjectId | None = None
    created_at: datetime | None = None
    created_by: PrincipalId | None = None
    revoked_at: datetime | None = None
    is_active: bool


class RoleAssignmentCreate(ContractModel):
    principal_id: PrincipalId
    role: Role
    resource_scope_type: ResourceScopeType
    resource_id: ProjectId | None = None


class RoleAssignmentRevokeRequest(ContractModel):
    reason: str | None = Field(default=None, min_length=1)


class WhoamiRead(ContractModel):
    """Safe admin identity metadata; never includes the raw key or hash.

    Service-credential callers populate ``api_credential_id``/``audience``/
    ``scopes``; browser-session callers populate ``browser_session_id`` with
    ``authentication_kind=browser_session`` and empty/absent credential fields;
    CLI-session callers populate ``cli_session_id`` with
    ``authentication_kind=cli_session`` and empty/absent credential fields.
    """

    principal_id: PrincipalId
    project_id: ProjectId
    authentication_kind: AdminAuthenticationKind
    api_credential_id: ApiCredentialId | None = None
    browser_session_id: BrowserSessionId | None = None
    cli_session_id: CliSessionId | None = None
    audience: CredentialAudience | None = None
    scopes: tuple[CredentialScope, ...] = ()
    roles: tuple[Role, ...]


# --- Human OIDC sessions -----------------------------------------------------


class SessionRead(ContractModel):
    """Current human session metadata. Never includes session/csrf/oidc secrets."""

    principal_id: PrincipalId
    project_id: ProjectId
    authentication_kind: AdminAuthenticationKind
    browser_session_id: BrowserSessionId
    roles: tuple[Role, ...]
    issuer: str | None = None
    subject: str | None = None


class SessionEstablished(ContractModel):
    """Result of a successful OIDC callback: session metadata plus the CSRF token.

    The CSRF token is delivered exactly once here (for the in-memory web client)
    and must be sent back in the ``X-CSRF-Token`` header on mutating requests.
    """

    session: SessionRead
    csrf_token: str = Field(min_length=1)
    csrf_header: str = "X-CSRF-Token"


class LogoutResult(ContractModel):
    revoked: bool


# --- Human CLI (OAuth device flow) sessions -----------------------------------


class DeviceAuthorizationRead(ContractModel):
    """Result of ``device/start``: the one-time device code and display values.

    ``device_code`` is a bearer secret returned exactly once here and never
    persisted beyond its one-way SHA-256 verifier. ``user_code`` and the
    verification URIs are non-secret display values.
    """

    device_code: str = Field(min_length=1)
    user_code: str = Field(min_length=1)
    verification_uri: str = Field(min_length=1)
    verification_uri_complete: str | None = None
    expires_in: int = Field(ge=1)
    interval: int = Field(ge=1)


class DevicePollRequest(ContractModel):
    """A poll request carrying the one-time device code (never echoed back)."""

    device_code: str = Field(min_length=1)


class CliSessionRead(ContractModel):
    """Current human CLI session metadata. Never includes the raw session token."""

    principal_id: PrincipalId
    project_id: ProjectId
    authentication_kind: AdminAuthenticationKind
    cli_session_id: CliSessionId
    roles: tuple[Role, ...]
    issuer: str | None = None
    subject: str | None = None


class DevicePollRead(ContractModel):
    """One device-flow poll outcome.

    ``status`` is ``pending``, ``slow_down``, or ``success``. Only ``success``
    carries the freshly established ``session`` and the one-time raw ``token``
    (returned exactly once, never persisted beyond its one-way SHA-256 verifier).
    """

    status: str
    session: CliSessionRead | None = None
    token: str | None = None
    token_type: str = "Bearer"
    expires_in: int | None = None


class ExternalIdentityCreate(ContractModel):
    principal_id: PrincipalId
    issuer: str = Field(min_length=1)
    subject: str = Field(min_length=1)


class ExternalIdentityRead(ContractModel):
    id: ExternalIdentityId
    principal_id: PrincipalId
    issuer: str
    subject: str
    email: str | None = None
    display_name: str | None = None
    created_at: datetime | None = None
    last_login_at: datetime | None = None
    is_active: bool


# --- Secret references (metadata only) ---------------------------------------


class SecretRefCreate(ContractModel):
    name: str = Field(min_length=1)


class SecretRefRead(ContractModel):
    id: SecretRefId
    name: str
    created_at: datetime


# --- Providers --------------------------------------------------------------


class ProviderCreate(ContractModel):
    kind: str = Field(min_length=1)
    name: str = Field(min_length=1)
    capabilities: tuple[Capability, ...] = ()
    is_active: bool = True


class ProviderRead(ContractModel):
    id: ProviderId
    kind: str
    name: str
    capabilities: tuple[Capability, ...]
    is_active: bool


class ProviderUpdate(ContractModel):
    kind: str | None = Field(default=None, min_length=1)
    name: str | None = Field(default=None, min_length=1)
    capabilities: tuple[Capability, ...] | None = None
    is_active: bool | None = None


# --- Provider accounts ------------------------------------------------------


class ProviderAccountCreate(ContractModel):
    provider_id: ProviderId
    name: str = Field(min_length=1)
    external_account_id: str | None = None
    secret_ref_id: SecretRefId | None = None


class ProviderAccountRead(ContractModel):
    id: ProviderAccountId
    provider_id: ProviderId
    name: str
    external_account_id: str | None = None
    secret_ref_id: SecretRefId | None = None
    is_active: bool


class ProviderAccountUpdate(ContractModel):
    name: str | None = Field(default=None, min_length=1)
    external_account_id: str | None = None
    secret_ref_id: SecretRefId | None = None
    is_active: bool | None = None


# --- Endpoints --------------------------------------------------------------


class EndpointCreate(ContractModel):
    provider_account_id: ProviderAccountId
    name: str = Field(min_length=1)
    base_destination: str = Field(min_length=1)
    max_concurrency: int = Field(default=1, ge=1)


class EndpointRead(ContractModel):
    id: EndpointId
    provider_account_id: ProviderAccountId
    name: str
    base_destination: str
    max_concurrency: int
    is_active: bool


class EndpointUpdate(ContractModel):
    name: str | None = Field(default=None, min_length=1)
    base_destination: str | None = Field(default=None, min_length=1)
    max_concurrency: int | None = Field(default=None, ge=1)
    is_active: bool | None = None


# --- Quota groups -----------------------------------------------------------


class QuotaGroupCreate(ContractModel):
    provider_account_id: ProviderAccountId
    name: str = Field(min_length=1)
    description: str | None = None


class QuotaGroupRead(ContractModel):
    id: QuotaGroupId
    provider_account_id: ProviderAccountId
    name: str
    description: str | None = None


class QuotaGroupUpdate(ContractModel):
    name: str | None = Field(default=None, min_length=1)
    description: str | None = None


# --- Quota limits ------------------------------------------------------------


class QuotaLimitCreate(ContractModel):
    quota_group_id: QuotaGroupId
    metric: QuotaMetric
    limit_units: int = Field(ge=1)
    window_seconds: int = Field(ge=1)
    enabled: bool = True
    name: str | None = None


class QuotaLimitRead(ContractModel):
    id: QuotaLimitId
    quota_group_id: QuotaGroupId
    metric: QuotaMetric
    limit_units: int
    window_seconds: int
    enabled: bool
    name: str | None = None


class QuotaLimitUpdate(ContractModel):
    limit_units: int | None = Field(default=None, ge=1)
    window_seconds: int | None = Field(default=None, ge=1)
    enabled: bool | None = None
    name: str | None = None


# --- Model aliases ----------------------------------------------------------


class ModelAliasCreate(ContractModel):
    name: str = Field(min_length=1)
    capabilities: tuple[Capability, ...] = ()
    is_active: bool = True


class ModelAliasRead(ContractModel):
    id: ModelAliasId
    name: str
    capabilities: tuple[Capability, ...]
    is_active: bool


class ModelAliasUpdate(ContractModel):
    name: str | None = Field(default=None, min_length=1)
    capabilities: tuple[Capability, ...] | None = None
    is_active: bool | None = None


# --- Route bindings ---------------------------------------------------------


class RouteBindingCreate(ContractModel):
    model_alias_id: ModelAliasId
    endpoint_id: EndpointId
    provider_account_id: ProviderAccountId
    upstream_model: str | None = Field(default=None, min_length=1)
    quota_group_id: QuotaGroupId | None = None
    default_output_tokens: int | None = Field(default=None, ge=1)
    is_active: bool = True


class RouteBindingRead(ContractModel):
    id: RouteBindingId
    model_alias_id: ModelAliasId
    endpoint_id: EndpointId
    provider_account_id: ProviderAccountId
    upstream_model: str | None = None
    quota_group_id: QuotaGroupId | None = None
    default_output_tokens: int | None = None
    is_active: bool


class RouteBindingUpdate(ContractModel):
    upstream_model: str | None = Field(default=None, min_length=1)
    quota_group_id: QuotaGroupId | None = None
    default_output_tokens: int | None = Field(default=None, ge=1)
    is_active: bool | None = None


# --- Route pricing -----------------------------------------------------------


class PricePolicyCreate(ContractModel):
    route_binding_id: RouteBindingId
    billing_unit: BillingUnit
    currency: Currency
    unit_scale: int = Field(default=1, ge=1)
    request_price: NonNegativeMoney | None = None
    input_price: NonNegativeMoney | None = None
    output_price: NonNegativeMoney | None = None
    enabled: bool = True
    name: str | None = None

    @model_validator(mode="after")
    def _validate_price_shape(self) -> PricePolicyCreate:
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


class PricePolicyRead(ContractModel):
    id: PricePolicyId
    route_binding_id: RouteBindingId
    billing_unit: BillingUnit
    currency: Currency
    unit_scale: int
    request_price: NonNegativeMoney | None = None
    input_price: NonNegativeMoney | None = None
    output_price: NonNegativeMoney | None = None
    enabled: bool
    name: str | None = None


class PricePolicyUpdate(ContractModel):
    billing_unit: BillingUnit | None = None
    currency: Currency | None = None
    unit_scale: int | None = Field(default=None, ge=1)
    request_price: NonNegativeMoney | None = None
    input_price: NonNegativeMoney | None = None
    output_price: NonNegativeMoney | None = None
    enabled: bool | None = None
    name: str | None = None

    @model_validator(mode="after")
    def _validate_incompatible_prices(self) -> PricePolicyUpdate:
        if self.billing_unit == BillingUnit.REQUEST and (
            self.input_price is not None or self.output_price is not None
        ):
            raise ValueError(
                "request billing must not set input_price or output_price"
            )
        if self.billing_unit == BillingUnit.TOKEN and self.request_price is not None:
            raise ValueError("token billing must not set request_price")
        return self


class PriceSnapshotRead(ContractModel):
    """Immutable captured price at a dispatch decision (read-only historical).

    Never exposes prompt/completion/provider-secret content; it is route/catalog
    pricing history, not project-owned accounting data.
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


# --- Project budget policies -------------------------------------------------


class ProjectBudgetPolicyCreate(ContractModel):
    project_id: ProjectId
    name: str = Field(min_length=1)
    currency: Currency
    limit_amount: PositiveMoney
    window_seconds: int = Field(ge=1)
    enabled: bool = True


class ProjectBudgetPolicyRead(ContractModel):
    id: BudgetPolicyId
    project_id: ProjectId
    name: str
    currency: Currency
    limit_amount: PositiveMoney
    window_seconds: int
    enabled: bool


class ProjectBudgetPolicyUpdate(ContractModel):
    name: str | None = Field(default=None, min_length=1)
    currency: Currency | None = None
    limit_amount: PositiveMoney | None = None
    window_seconds: int | None = Field(default=None, ge=1)
    enabled: bool | None = None


# --- Budget status / headroom -------------------------------------------------


class BudgetStatusRead(ContractModel):
    project_id: ProjectId
    budget_policy_id: BudgetPolicyId
    currency: Currency
    limit_amount: PositiveMoney
    committed_amount: NonNegativeMoney
    reserved_amount: NonNegativeMoney
    headroom: Money  # limit - committed - reserved; may be negative after overage
    window_start: datetime
    window_end: datetime
    enabled: bool = True


class BudgetReservationRead(ContractModel):
    id: BudgetReservationId
    request_id: RequestId
    budget_policy_id: BudgetPolicyId
    # Released pre-dispatch reservations detach their snapshot (NULL in the DB).
    price_snapshot_id: PriceSnapshotId | None = None
    reserved_amount: NonNegativeMoney
    committed_amount: NonNegativeMoney
    state: str
    settlement_reason: str | None = None


# --- Usage records -----------------------------------------------------------


class UsageRecordRead(ContractModel):
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


# --- Ledger entries -----------------------------------------------------------


class LedgerEntryRead(ContractModel):
    id: LedgerEntryId
    project_id: ProjectId
    usage_record_id: UsageRecordId | None = None
    entry_type: LedgerEntryType
    amount: Money  # signed
    currency: Currency
    created_at: datetime
    idempotency_key: str | None = None
    reason: str | None = None


# --- Audit events ------------------------------------------------------------

class AuditEventRead(ContractModel):
    """Safe immutable administrative audit event (never secret-bearing metadata).

    ``project_id`` is ``None`` for deployment-scoped events; only a ``system_admin``
    may read those. ``metadata`` carries safe non-secret fields only.
    """

    id: AuditEventId
    actor_principal_id: PrincipalId | None = None
    project_id: ProjectId | None = None
    action: str
    resource_type: str
    resource_id: str
    occurred_at: datetime
    metadata: dict[str, Any] = Field(default_factory=dict)


# --- Queue / operator control plane ------------------------------------------


class QueueRequestRead(ContractModel):
    """Safe queue request metadata (never prompt/completion/stream content).

    Explicitly omits ``payload_encrypted``, ``result_encrypted``, stream-event
    bodies, ``fencing_token``, and any provider secret material.
    """

    request_id: RequestId
    project_id: ProjectId | None = None
    principal_id: PrincipalId | None = None
    api_credential_id: ApiCredentialId | None = None
    model_alias_id: ModelAliasId
    endpoint_id: EndpointId | None = None
    quota_group_id: QuotaGroupId | None = None
    state: str
    stream: bool
    queued_at: datetime | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    queue_wait_until: datetime | None = None
    expires_at: datetime | None = None
    cancellation_requested: bool
    wait_reason: str | None = None
    wait_limit_id: str | None = None
    wait_limit_metric: str | None = None
    next_eligible_at: datetime | None = None
    error_code: str | None = None
    price_snapshot_id: PriceSnapshotId | None = None
    reconciled_state: str | None = None
    reconciled_at: datetime | None = None
    reconciled_by: str | None = None
    worker_id: str | None = None
    lease_expires_at: datetime | None = None
    effective_wait_reason: str | None = None


class QueueSummaryRead(ContractModel):
    """Side-effect-free operational counts for the caller's authorized scope."""

    counts_by_state: dict[str, int]
    queued_total: int
    in_flight_total: int
    outcome_unknown_total: int
    oldest_queued_at: datetime | None = None
    oldest_wait_seconds: int | None = None


class EndpointRuntimeRead(ContractModel):
    """Runtime/slot status for one endpoint (deployment-scoped).

    ``operational_state`` is the durable scheduler/operator state
    (``active``/``paused``/``draining``), distinct from catalog ``is_active``.
    """

    endpoint_id: EndpointId
    name: str
    is_active: bool
    operational_state: str
    max_concurrency: int
    occupied_slots: int
    available_slots: int
    draining_complete: bool
    oldest_queued_at: datetime | None = None


class CancelResult(ContractModel):
    """Result of a safe request cancellation.

    ``result`` is one of ``cancelled_now``, ``cancellation_requested``, or
    ``already_cancelled``. A terminal-state request and an ``outcome_unknown``
    request return ``409 invalid_transition`` rather than a result.
    """

    request_id: RequestId
    result: str
    state: str


class ReconcileRequest(ContractModel):
    """Reconciliation disposition. Only ``failed``/``cancelled`` are accepted."""

    disposition: str


class ReconcileResult(ContractModel):
    request_id: RequestId
    disposition: str
    reconciled_by: str


class QuotaStatusRead(ContractModel):
    """Runtime provider-quota status for one limit (deployment-scoped).

    A read never fabricates a ``QuotaWindow`` row; absent current window reports
    zero committed/reserved. ``remaining_units = max(0, limit - committed -
    reserved)``.
    """

    quota_group_id: QuotaGroupId
    quota_group_name: str
    provider_account_id: ProviderAccountId
    quota_limit_id: QuotaLimitId
    quota_limit_name: str | None = None
    metric: str
    limit_units: int
    window_seconds: int
    window_start: datetime
    window_end: datetime
    committed_units: int
    reserved_units: int
    remaining_units: int
    enabled: bool
    cooldown_until: datetime | None = None


__all__ = [
    "ProjectCreate",
    "ProjectRead",
    "ProjectUpdate",
    "PrincipalCreate",
    "PrincipalRead",
    "PrincipalUpdate",
    "ApiCredentialCreate",
    "ApiCredentialCreateResult",
    "ApiCredentialRead",
    "ApiCredentialRotateResult",
    "ApiCredentialRevokeRequest",
    "ApiCredentialRevokeResult",
    "ApiCredentialUpdate",
    "BootstrapResult",
    "RoleAssignmentRead",
    "RoleAssignmentCreate",
    "RoleAssignmentRevokeRequest",
    "WhoamiRead",
    "SessionRead",
    "SessionEstablished",
    "LogoutResult",
    "DeviceAuthorizationRead",
    "DevicePollRequest",
    "CliSessionRead",
    "DevicePollRead",
    "ExternalIdentityCreate",
    "ExternalIdentityRead",
    "SecretRefCreate",
    "SecretRefRead",
    "ProviderCreate",
    "ProviderRead",
    "ProviderUpdate",
    "ProviderAccountCreate",
    "ProviderAccountRead",
    "ProviderAccountUpdate",
    "EndpointCreate",
    "EndpointRead",
    "EndpointUpdate",
    "QuotaGroupCreate",
    "QuotaGroupRead",
    "QuotaGroupUpdate",
    "QuotaLimitCreate",
    "QuotaLimitRead",
    "QuotaLimitUpdate",
    "ModelAliasCreate",
    "ModelAliasRead",
    "ModelAliasUpdate",
    "RouteBindingCreate",
    "RouteBindingRead",
    "RouteBindingUpdate",
    "PricePolicyCreate",
    "PricePolicyRead",
    "PricePolicyUpdate",
    "PriceSnapshotRead",
    "ProjectBudgetPolicyCreate",
    "ProjectBudgetPolicyRead",
    "ProjectBudgetPolicyUpdate",
    "BudgetStatusRead",
    "BudgetReservationRead",
    "UsageRecordRead",
    "LedgerEntryRead",
    "AuditEventRead",
    "QueueRequestRead",
    "QueueSummaryRead",
    "EndpointRuntimeRead",
    "CancelResult",
    "ReconcileRequest",
    "ReconcileResult",
    "QuotaStatusRead",
]
