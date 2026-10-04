"""Cross-domain entity contracts.

These are persistence- and provider-independent contracts, not ORM models.
Entities reference other resources by typed ID only; there are no bidirectional
object graphs or implicit lazy relationships. Secret material is never embedded;
it is referenced through :class:`~aethergate.domain.ids.SecretRefId`.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from aethergate.domain.enums import (
    BillingUnit,
    Capability,
    LedgerEntryType,
    PrincipalKind,
    RequestState,
)
from aethergate.domain.ids import (
    ApiCredentialId,
    AuditEventId,
    EndpointId,
    ExecutionAttemptId,
    LedgerEntryId,
    ModelAliasId,
    PriceSnapshotId,
    PrincipalId,
    ProjectId,
    ProviderAccountId,
    ProviderId,
    QuotaGroupId,
    RequestId,
    ReservationId,
    RouteBindingId,
    SecretRefId,
    UsageRecordId,
)
from aethergate.domain.value_objects import Money, NonNegativeMoney


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
    """A shared allowance scope that may span multiple routes/endpoints/models."""

    id: QuotaGroupId
    name: str
    description: str | None = None


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
    is_active: bool = True


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


class PriceSnapshot(Entity):
    id: PriceSnapshotId
    model_alias_id: ModelAliasId
    billing_unit: BillingUnit
    price_in: NonNegativeMoney
    price_out: NonNegativeMoney
    effective_from: datetime


class UsageRecord(Entity):
    id: UsageRecordId
    request_id: RequestId
    project_id: ProjectId
    model_alias_id: ModelAliasId
    billing_unit: BillingUnit
    input_units: int
    output_units: int
    price_snapshot_id: PriceSnapshotId


class LedgerEntry(Entity):
    id: LedgerEntryId
    project_id: ProjectId
    usage_record_id: UsageRecordId | None = None
    entry_type: LedgerEntryType
    amount: Money
    created_at: datetime


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
    "ModelAlias",
    "RouteBinding",
    "InferenceRequest",
    "ExecutionAttempt",
    "Reservation",
    "UsageRecord",
    "PriceSnapshot",
    "LedgerEntry",
    "AuditEvent",
]
