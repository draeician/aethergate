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

from pydantic import Field, model_validator

from aethergate.contracts.common import ContractModel
from aethergate.domain.enums import (
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
    BudgetPolicyId,
    BudgetReservationId,
    EndpointId,
    ExecutionAttemptId,
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
    """Safe admin identity metadata; never includes the raw key or hash."""

    principal_id: PrincipalId
    project_id: ProjectId
    api_credential_id: ApiCredentialId
    audience: CredentialAudience
    scopes: tuple[CredentialScope, ...]
    roles: tuple[Role, ...]


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


class BudgetReservationRead(ContractModel):
    id: BudgetReservationId
    request_id: RequestId
    budget_policy_id: BudgetPolicyId
    price_snapshot_id: PriceSnapshotId
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
    "ProjectBudgetPolicyCreate",
    "ProjectBudgetPolicyRead",
    "ProjectBudgetPolicyUpdate",
    "BudgetStatusRead",
    "BudgetReservationRead",
    "UsageRecordRead",
    "LedgerEntryRead",
]
