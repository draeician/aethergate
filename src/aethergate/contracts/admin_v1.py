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

from pydantic import Field

from aethergate.contracts.common import ContractModel
from aethergate.domain.enums import Capability, PrincipalKind
from aethergate.domain.ids import (
    ApiCredentialId,
    EndpointId,
    ModelAliasId,
    PrincipalId,
    ProjectId,
    ProviderAccountId,
    ProviderId,
    QuotaGroupId,
    RouteBindingId,
    SecretRefId,
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
    project_id: ProjectId
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


# --- API credentials metadata -----------------------------------------------


class ApiCredentialCreate(ContractModel):
    project_id: ProjectId
    principal_id: PrincipalId | None = None
    name: str = Field(min_length=1)


class ApiCredentialRead(ContractModel):
    id: ApiCredentialId
    project_id: ProjectId
    principal_id: PrincipalId | None = None
    name: str
    secret_ref_id: SecretRefId
    is_active: bool


class ApiCredentialUpdate(ContractModel):
    name: str | None = Field(default=None, min_length=1)
    is_active: bool | None = None


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
    name: str = Field(min_length=1)
    description: str | None = None


class QuotaGroupRead(ContractModel):
    id: QuotaGroupId
    name: str
    description: str | None = None


class QuotaGroupUpdate(ContractModel):
    name: str | None = Field(default=None, min_length=1)
    description: str | None = None


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


class RouteBindingRead(ContractModel):
    id: RouteBindingId
    model_alias_id: ModelAliasId
    endpoint_id: EndpointId
    provider_account_id: ProviderAccountId
    upstream_model: str | None = None
    quota_group_id: QuotaGroupId | None = None
    is_active: bool


class RouteBindingUpdate(ContractModel):
    upstream_model: str | None = Field(default=None, min_length=1)
    quota_group_id: QuotaGroupId | None = None
    is_active: bool | None = None


__all__ = [
    "ProjectCreate",
    "ProjectRead",
    "ProjectUpdate",
    "PrincipalCreate",
    "PrincipalRead",
    "PrincipalUpdate",
    "ApiCredentialCreate",
    "ApiCredentialRead",
    "ApiCredentialUpdate",
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
    "ModelAliasCreate",
    "ModelAliasRead",
    "ModelAliasUpdate",
    "RouteBindingCreate",
    "RouteBindingRead",
    "RouteBindingUpdate",
]
