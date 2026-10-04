"""Cross-domain entity contracts.

These are persistence- and provider-independent contracts, not ORM models.
Entities reference other resources by typed ID only; there are no bidirectional
object graphs or implicit lazy relationships. Secret material is never embedded;
it is referenced through :class:`~aethergate.domain.ids.SecretRefId`.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from aethergate.domain.enums import (
    BillingUnit,
    BudgetReservationState,
    Capability,
    LedgerEntryType,
    PrincipalKind,
    QuotaMetric,
    RequestState,
)
from aethergate.domain.ids import (
    ApiCredentialId,
    AuditEventId,
    BudgetPolicyId,
    BudgetReservationId,
    BudgetWindowId,
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
    ReservationId,
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
    """Metadata for a scoped API credential; its material lives behind a SecretRef."""

    id: ApiCredentialId
    project_id: ProjectId
    principal_id: PrincipalId | None = None
    name: str
    secret_ref_id: SecretRefId
    is_active: bool = True


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
    price_snapshot_id: PriceSnapshotId
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
