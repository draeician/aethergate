"""Repositories mapping ORM rows to domain entities (and back).

These functions accept/return domain entities from ``aethergate.domain``; ORM
objects never escape this module.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from aethergate.domain import entities as domain
from aethergate.domain.enums import (
    BillingUnit,
    Capability,
    CredentialAudience,
    CredentialScope,
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
    EndpointId,
    ExternalIdentityId,
    ModelAliasId,
    OidcLoginStateId,
    PricePolicyId,
    PrincipalId,
    ProjectId,
    ProviderAccountId,
    ProviderId,
    QuotaGroupId,
    QuotaLimitId,
    RoleAssignmentId,
    RouteBindingId,
    SecretRefId,
)
from aethergate.errors import PricePolicyConflictError
from aethergate.persistence import models


def _capabilities(values: list | None) -> tuple[Capability, ...]:
    return tuple(Capability(v) for v in (values or []))


# ---------------------------------------------------------------------------
# Identity / access
# ---------------------------------------------------------------------------


def _project_to_domain(row: models.Project) -> domain.Project:
    return domain.Project(
        id=ProjectId(row.id), name=row.name, is_active=row.is_active
    )


async def create_project(session: AsyncSession, entity: domain.Project) -> domain.Project:
    row = models.Project(id=str(entity.id), name=entity.name, is_active=entity.is_active)
    session.add(row)
    await session.flush()
    return _project_to_domain(row)


async def get_project(session: AsyncSession, project_id: ProjectId) -> domain.Project | None:
    row = await session.get(models.Project, str(project_id))
    return _project_to_domain(row) if row else None


async def get_project_by_name(session: AsyncSession, name: str) -> domain.Project | None:
    result = await session.execute(
        select(models.Project).where(models.Project.name == name)
    )
    row = result.scalar_one_or_none()
    return _project_to_domain(row) if row else None


async def set_project_active(
    session: AsyncSession, project_id: ProjectId, is_active: bool
) -> domain.Project | None:
    row = await session.get(models.Project, str(project_id))
    if row is None:
        return None
    row.is_active = is_active
    await session.flush()
    return _project_to_domain(row)


async def update_project(
    session: AsyncSession,
    project_id: ProjectId,
    *,
    name: str | None = None,
    is_active: bool | None = None,
) -> domain.Project | None:
    row = await session.get(models.Project, str(project_id))
    if row is None:
        return None
    if name is not None:
        row.name = name
    if is_active is not None:
        row.is_active = is_active
    await session.flush()
    return _project_to_domain(row)


async def list_projects(
    session: AsyncSession,
    *,
    project_ids: set[ProjectId] | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[domain.Project]:
    stmt = select(models.Project)
    if project_ids is not None:
        if not project_ids:
            return []
        stmt = stmt.where(models.Project.id.in_([str(p) for p in project_ids]))
    stmt = (
        stmt.order_by(models.Project.created_at.asc(), models.Project.id.asc())
        .limit(limit)
        .offset(offset)
    )
    result = await session.execute(stmt)
    return [_project_to_domain(r) for r in result.scalars().all()]


async def count_projects(
    session: AsyncSession, *, project_ids: set[ProjectId] | None = None
) -> int:
    stmt = select(func.count()).select_from(models.Project)
    if project_ids is not None:
        if not project_ids:
            return 0
        stmt = stmt.where(models.Project.id.in_([str(p) for p in project_ids]))
    return (await session.execute(stmt)).scalar_one()


def _secret_ref_to_domain(row: models.SecretRef) -> domain.SecretRef:
    return domain.SecretRef(
        id=SecretRefId(row.id), name=row.name, created_at=row.created_at
    )


async def create_secret_ref(
    session: AsyncSession, entity: domain.SecretRef
) -> domain.SecretRef:
    row = models.SecretRef(id=str(entity.id), name=entity.name)
    session.add(row)
    await session.flush()
    await session.refresh(row)
    return _secret_ref_to_domain(row)


async def get_secret_ref(
    session: AsyncSession, ref_id: SecretRefId
) -> domain.SecretRef | None:
    row = await session.get(models.SecretRef, str(ref_id))
    return _secret_ref_to_domain(row) if row else None


# ---------------------------------------------------------------------------
# Catalog / routing
# ---------------------------------------------------------------------------


def _provider_to_domain(row: models.Provider) -> domain.Provider:
    return domain.Provider(
        id=ProviderId(row.id),
        kind=row.kind,
        name=row.name,
        capabilities=_capabilities(row.capabilities),
        is_active=row.is_active,
    )


async def create_provider(session: AsyncSession, entity: domain.Provider) -> domain.Provider:
    row = models.Provider(
        id=str(entity.id),
        kind=entity.kind,
        name=entity.name,
        capabilities=[c.value for c in entity.capabilities],
        is_active=entity.is_active,
    )
    session.add(row)
    await session.flush()
    return _provider_to_domain(row)


async def get_provider(session: AsyncSession, provider_id: ProviderId) -> domain.Provider | None:
    row = await session.get(models.Provider, str(provider_id))
    return _provider_to_domain(row) if row else None


async def get_provider_by_name(session: AsyncSession, name: str) -> domain.Provider | None:
    result = await session.execute(
        select(models.Provider).where(models.Provider.name == name)
    )
    row = result.scalar_one_or_none()
    return _provider_to_domain(row) if row else None


def _provider_account_to_domain(row: models.ProviderAccount) -> domain.ProviderAccount:
    return domain.ProviderAccount(
        id=ProviderAccountId(row.id),
        provider_id=ProviderId(row.provider_id),
        name=row.name,
        external_account_id=row.external_account_id,
        secret_ref_id=SecretRefId(row.secret_ref_id) if row.secret_ref_id else None,
        is_active=row.is_active,
    )


async def create_provider_account(
    session: AsyncSession, entity: domain.ProviderAccount
) -> domain.ProviderAccount:
    row = models.ProviderAccount(
        id=str(entity.id),
        provider_id=str(entity.provider_id),
        name=entity.name,
        external_account_id=entity.external_account_id,
        secret_ref_id=str(entity.secret_ref_id) if entity.secret_ref_id else None,
        is_active=entity.is_active,
    )
    session.add(row)
    await session.flush()
    return _provider_account_to_domain(row)


async def get_provider_account(
    session: AsyncSession, account_id: ProviderAccountId
) -> domain.ProviderAccount | None:
    row = await session.get(models.ProviderAccount, str(account_id))
    return _provider_account_to_domain(row) if row else None


async def get_provider_account_by_name(
    session: AsyncSession, name: str
) -> domain.ProviderAccount | None:
    result = await session.execute(
        select(models.ProviderAccount).where(models.ProviderAccount.name == name)
    )
    row = result.scalar_one_or_none()
    return _provider_account_to_domain(row) if row else None


def _endpoint_to_domain(row: models.Endpoint) -> domain.Endpoint:
    return domain.Endpoint(
        id=EndpointId(row.id),
        provider_account_id=ProviderAccountId(row.provider_account_id),
        name=row.name,
        base_destination=row.base_destination,
        max_concurrency=row.max_concurrency,
        is_active=row.is_active,
    )


async def create_endpoint(session: AsyncSession, entity: domain.Endpoint) -> domain.Endpoint:
    row = models.Endpoint(
        id=str(entity.id),
        provider_account_id=str(entity.provider_account_id),
        name=entity.name,
        base_destination=entity.base_destination,
        max_concurrency=entity.max_concurrency,
        is_active=entity.is_active,
    )
    session.add(row)
    await session.flush()
    return _endpoint_to_domain(row)


async def get_endpoint(session: AsyncSession, endpoint_id: EndpointId) -> domain.Endpoint | None:
    row = await session.get(models.Endpoint, str(endpoint_id))
    return _endpoint_to_domain(row) if row else None


async def get_endpoint_by_name(session: AsyncSession, name: str) -> domain.Endpoint | None:
    result = await session.execute(
        select(models.Endpoint).where(models.Endpoint.name == name)
    )
    row = result.scalar_one_or_none()
    return _endpoint_to_domain(row) if row else None


def _quota_group_to_domain(row: models.QuotaGroup) -> domain.QuotaGroup:
    return domain.QuotaGroup(
        id=QuotaGroupId(row.id),
        provider_account_id=ProviderAccountId(row.provider_account_id),
        name=row.name,
        description=row.description,
    )


async def create_quota_group(
    session: AsyncSession, entity: domain.QuotaGroup
) -> domain.QuotaGroup:
    row = models.QuotaGroup(
        id=str(entity.id),
        provider_account_id=str(entity.provider_account_id),
        name=entity.name,
        description=entity.description,
    )
    session.add(row)
    await session.flush()
    return _quota_group_to_domain(row)


async def get_quota_group_by_name(
    session: AsyncSession, name: str
) -> domain.QuotaGroup | None:
    result = await session.execute(
        select(models.QuotaGroup).where(models.QuotaGroup.name == name)
    )
    row = result.scalar_one_or_none()
    return _quota_group_to_domain(row) if row else None


def _quota_limit_to_domain(row: models.QuotaLimit) -> domain.QuotaLimit:
    return domain.QuotaLimit(
        id=QuotaLimitId(row.id),
        quota_group_id=QuotaGroupId(row.quota_group_id),
        metric=QuotaMetric(row.metric),
        limit_units=row.limit_units,
        window_seconds=row.window_seconds,
        enabled=row.enabled,
        name=row.name,
    )


async def create_quota_limit(
    session: AsyncSession, entity: domain.QuotaLimit
) -> domain.QuotaLimit:
    row = models.QuotaLimit(
        id=str(entity.id),
        quota_group_id=str(entity.quota_group_id),
        metric=entity.metric.value,
        limit_units=entity.limit_units,
        window_seconds=entity.window_seconds,
        enabled=entity.enabled,
        name=entity.name,
    )
    session.add(row)
    await session.flush()
    return _quota_limit_to_domain(row)


async def list_quota_limits_for_group(
    session: AsyncSession, group_id: QuotaGroupId
) -> list[domain.QuotaLimit]:
    result = await session.execute(
        select(models.QuotaLimit)
        .where(models.QuotaLimit.quota_group_id == str(group_id))
        .order_by(models.QuotaLimit.id)
    )
    return [_quota_limit_to_domain(r) for r in result.scalars().all()]


async def update_endpoint_max_concurrency(
    session: AsyncSession, endpoint_id: EndpointId, max_concurrency: int
) -> domain.Endpoint:
    row = await session.get(models.Endpoint, str(endpoint_id))
    if row is None:
        raise ValueError(f"endpoint {endpoint_id!s} not found")
    row.max_concurrency = max_concurrency
    await session.flush()
    return _endpoint_to_domain(row)


def _model_alias_to_domain(row: models.ModelAlias) -> domain.ModelAlias:
    return domain.ModelAlias(
        id=ModelAliasId(row.id),
        name=row.name,
        capabilities=_capabilities(row.capabilities),
        is_active=row.is_active,
    )


async def create_model_alias(
    session: AsyncSession, entity: domain.ModelAlias
) -> domain.ModelAlias:
    row = models.ModelAlias(
        id=str(entity.id),
        name=entity.name,
        capabilities=[c.value for c in entity.capabilities],
        is_active=entity.is_active,
    )
    session.add(row)
    await session.flush()
    return _model_alias_to_domain(row)


async def get_model_alias_by_name(
    session: AsyncSession, name: str
) -> domain.ModelAlias | None:
    result = await session.execute(
        select(models.ModelAlias).where(models.ModelAlias.name == name)
    )
    row = result.scalar_one_or_none()
    return _model_alias_to_domain(row) if row else None


async def get_model_alias(
    session: AsyncSession, alias_id: ModelAliasId
) -> domain.ModelAlias | None:
    row = await session.get(models.ModelAlias, str(alias_id))
    return _model_alias_to_domain(row) if row else None


async def list_active_model_aliases(session: AsyncSession) -> list[domain.ModelAlias]:
    """Return all active public model aliases, ordered by name."""
    result = await session.execute(
        select(models.ModelAlias)
        .where(models.ModelAlias.is_active.is_(True))
        .order_by(models.ModelAlias.name)
    )
    return [_model_alias_to_domain(r) for r in result.scalars().all()]


def _route_binding_to_domain(row: models.RouteBinding) -> domain.RouteBinding:
    return domain.RouteBinding(
        id=RouteBindingId(row.id),
        model_alias_id=ModelAliasId(row.model_alias_id),
        endpoint_id=EndpointId(row.endpoint_id),
        provider_account_id=ProviderAccountId(row.provider_account_id),
        upstream_model=row.upstream_model,
        quota_group_id=QuotaGroupId(row.quota_group_id) if row.quota_group_id else None,
        default_output_tokens=row.default_output_tokens,
        is_active=row.is_active,
    )


async def create_route_binding(
    session: AsyncSession, entity: domain.RouteBinding
) -> domain.RouteBinding:
    if entity.quota_group_id is not None:
        group = await session.get(models.QuotaGroup, str(entity.quota_group_id))
        if group is None:
            raise ValueError(f"quota group {entity.quota_group_id!s} not found")
        if group.provider_account_id != str(entity.provider_account_id):
            raise ValueError(
                "route quota group must belong to the route's provider account"
            )
    row = models.RouteBinding(
        id=str(entity.id),
        model_alias_id=str(entity.model_alias_id),
        endpoint_id=str(entity.endpoint_id),
        provider_account_id=str(entity.provider_account_id),
        upstream_model=entity.upstream_model,
        quota_group_id=str(entity.quota_group_id) if entity.quota_group_id else None,
        default_output_tokens=entity.default_output_tokens,
        is_active=entity.is_active,
    )
    session.add(row)
    await session.flush()
    return _route_binding_to_domain(row)


async def list_route_bindings(
    session: AsyncSession, model_alias_id: ModelAliasId
) -> list[domain.RouteBinding]:
    result = await session.execute(
        select(models.RouteBinding).where(
            models.RouteBinding.model_alias_id == str(model_alias_id)
        )
    )
    return [_route_binding_to_domain(r) for r in result.scalars().all()]


def _principal_to_domain(row: models.Principal) -> domain.Principal:
    return domain.Principal(
        id=PrincipalId(row.id),
        project_id=ProjectId(row.project_id),
        kind=PrincipalKind(row.kind),
        name=row.name,
        is_active=row.is_active,
    )


async def create_principal(session: AsyncSession, entity: domain.Principal) -> domain.Principal:
    row = models.Principal(
        id=str(entity.id),
        project_id=str(entity.project_id),
        kind=entity.kind.value,
        name=entity.name,
        is_active=entity.is_active,
    )
    session.add(row)
    await session.flush()
    return _principal_to_domain(row)


async def get_principal(
    session: AsyncSession, principal_id: PrincipalId
) -> domain.Principal | None:
    row = await session.get(models.Principal, str(principal_id))
    return _principal_to_domain(row) if row else None


async def get_principal_by_name(
    session: AsyncSession, project_id: ProjectId, name: str
) -> domain.Principal | None:
    result = await session.execute(
        select(models.Principal).where(
            models.Principal.project_id == str(project_id),
            models.Principal.name == name,
        )
    )
    row = result.scalar_one_or_none()
    return _principal_to_domain(row) if row else None


async def set_principal_active(
    session: AsyncSession, principal_id: PrincipalId, is_active: bool
) -> domain.Principal | None:
    row = await session.get(models.Principal, str(principal_id))
    if row is None:
        return None
    row.is_active = is_active
    await session.flush()
    return _principal_to_domain(row)


async def update_principal(
    session: AsyncSession,
    principal_id: PrincipalId,
    *,
    name: str | None = None,
    is_active: bool | None = None,
) -> domain.Principal | None:
    row = await session.get(models.Principal, str(principal_id))
    if row is None:
        return None
    if name is not None:
        row.name = name
    if is_active is not None:
        row.is_active = is_active
    await session.flush()
    return _principal_to_domain(row)


async def list_principals(
    session: AsyncSession,
    project_id: ProjectId,
    *,
    limit: int = 50,
    offset: int = 0,
) -> list[domain.Principal]:
    stmt = (
        select(models.Principal)
        .where(models.Principal.project_id == str(project_id))
        .order_by(models.Principal.created_at.asc(), models.Principal.id.asc())
        .limit(limit)
        .offset(offset)
    )
    result = await session.execute(stmt)
    return [_principal_to_domain(r) for r in result.scalars().all()]


async def count_principals(session: AsyncSession, project_id: ProjectId) -> int:
    stmt = select(func.count()).select_from(models.Principal).where(
        models.Principal.project_id == str(project_id)
    )
    return (await session.execute(stmt)).scalar_one()


def _api_credential_to_domain(row: models.ApiCredential) -> domain.ApiCredential:
    return domain.ApiCredential(
        id=ApiCredentialId(row.id),
        project_id=ProjectId(row.project_id),
        principal_id=PrincipalId(row.principal_id) if row.principal_id else None,
        name=row.name,
        key_prefix=row.key_prefix,
        key_hash=row.key_hash,
        audience=CredentialAudience(row.audience),
        scopes=tuple(CredentialScope(s) for s in (row.scopes or [])),
        created_at=row.created_at,
        expires_at=row.expires_at,
        revoked_at=row.revoked_at,
        last_used_at=row.last_used_at,
        is_active=row.is_active,
    )


async def create_api_credential(
    session: AsyncSession, entity: domain.ApiCredential
) -> domain.ApiCredential:
    row = models.ApiCredential(
        id=str(entity.id),
        project_id=str(entity.project_id),
        principal_id=str(entity.principal_id) if entity.principal_id else None,
        name=entity.name,
        key_prefix=entity.key_prefix,
        key_hash=entity.key_hash,
        audience=entity.audience.value,
        scopes=[s.value for s in entity.scopes],
        expires_at=entity.expires_at,
        revoked_at=entity.revoked_at,
        last_used_at=entity.last_used_at,
        is_active=entity.is_active,
    )
    session.add(row)
    await session.flush()
    await session.refresh(row)
    return _api_credential_to_domain(row)


async def get_api_credential(
    session: AsyncSession, credential_id: ApiCredentialId
) -> domain.ApiCredential | None:
    row = await session.get(models.ApiCredential, str(credential_id))
    return _api_credential_to_domain(row) if row else None


async def get_api_credential_by_hash(
    session: AsyncSession, key_hash: str
) -> domain.ApiCredential | None:
    result = await session.execute(
        select(models.ApiCredential).where(models.ApiCredential.key_hash == key_hash)
    )
    row = result.scalar_one_or_none()
    return _api_credential_to_domain(row) if row else None


async def get_api_credential_by_name(
    session: AsyncSession, project_id: ProjectId, name: str
) -> domain.ApiCredential | None:
    result = await session.execute(
        select(models.ApiCredential).where(
            models.ApiCredential.project_id == str(project_id),
            models.ApiCredential.name == name,
        )
    )
    row = result.scalar_one_or_none()
    return _api_credential_to_domain(row) if row else None


async def list_api_credentials(
    session: AsyncSession, project_id: ProjectId
) -> list[domain.ApiCredential]:
    result = await session.execute(
        select(models.ApiCredential)
        .where(models.ApiCredential.project_id == str(project_id))
        .order_by(models.ApiCredential.created_at.asc())
    )
    return [_api_credential_to_domain(r) for r in result.scalars().all()]


async def list_api_credentials_paged(
    session: AsyncSession,
    project_id: ProjectId,
    *,
    limit: int = 50,
    offset: int = 0,
) -> list[domain.ApiCredential]:
    stmt = (
        select(models.ApiCredential)
        .where(models.ApiCredential.project_id == str(project_id))
        .order_by(models.ApiCredential.created_at.asc(), models.ApiCredential.id.asc())
        .limit(limit)
        .offset(offset)
    )
    result = await session.execute(stmt)
    return [_api_credential_to_domain(r) for r in result.scalars().all()]


async def count_api_credentials(session: AsyncSession, project_id: ProjectId) -> int:
    stmt = select(func.count()).select_from(models.ApiCredential).where(
        models.ApiCredential.project_id == str(project_id)
    )
    return (await session.execute(stmt)).scalar_one()


async def revoke_api_credential(
    session: AsyncSession, credential_id: ApiCredentialId, revoked_at: datetime
) -> domain.ApiCredential | None:
    row = await session.get(models.ApiCredential, str(credential_id))
    if row is None:
        return None
    # Idempotent: only the first revoke stamps the event; repeated revokes
    # preserve the original timestamp and never move it forward.
    if row.revoked_at is None:
        row.revoked_at = revoked_at
    row.is_active = False
    await session.flush()
    return _api_credential_to_domain(row)


async def update_api_credential_last_used(
    session: AsyncSession, credential_id: ApiCredentialId, used_at: datetime
) -> None:
    row = await session.get(models.ApiCredential, str(credential_id))
    if row is None:
        return
    row.last_used_at = used_at
    await session.flush()


# ---------------------------------------------------------------------------
# RBAC / role assignments
# ---------------------------------------------------------------------------


def _role_assignment_to_domain(row: models.RoleAssignment) -> domain.RoleAssignment:
    return domain.RoleAssignment(
        id=RoleAssignmentId(row.id),
        principal_id=PrincipalId(row.principal_id),
        role=Role(row.role),
        resource_scope_type=ResourceScopeType(row.resource_scope_type),
        resource_id=ProjectId(row.resource_id) if row.resource_id else None,
        created_at=row.created_at,
        created_by=PrincipalId(row.created_by) if row.created_by else None,
        revoked_at=row.revoked_at,
        is_active=row.is_active,
    )


async def create_role_assignment(
    session: AsyncSession, entity: domain.RoleAssignment
) -> domain.RoleAssignment:
    row = models.RoleAssignment(
        id=str(entity.id),
        principal_id=str(entity.principal_id),
        role=entity.role.value,
        resource_scope_type=entity.resource_scope_type.value,
        resource_id=str(entity.resource_id) if entity.resource_id else "",
        created_by=str(entity.created_by) if entity.created_by else None,
        is_active=entity.is_active,
    )
    session.add(row)
    await session.flush()
    return _role_assignment_to_domain(row)


async def get_role_assignment(
    session: AsyncSession, assignment_id: RoleAssignmentId
) -> domain.RoleAssignment | None:
    row = await session.get(models.RoleAssignment, str(assignment_id))
    return _role_assignment_to_domain(row) if row else None


async def list_active_role_assignments(
    session: AsyncSession, principal_id: PrincipalId
) -> list[domain.RoleAssignment]:
    result = await session.execute(
        select(models.RoleAssignment).where(
            models.RoleAssignment.principal_id == str(principal_id),
            models.RoleAssignment.is_active.is_(True),
            models.RoleAssignment.revoked_at.is_(None),
        )
    )
    return [_role_assignment_to_domain(r) for r in result.scalars().all()]


async def find_active_equivalent_assignment(
    session: AsyncSession,
    principal_id: PrincipalId,
    role: Role,
    resource_scope_type: ResourceScopeType,
    resource_id: ProjectId | None,
) -> domain.RoleAssignment | None:
    result = await session.execute(
        select(models.RoleAssignment).where(
            models.RoleAssignment.principal_id == str(principal_id),
            models.RoleAssignment.role == role.value,
            models.RoleAssignment.resource_scope_type == resource_scope_type.value,
            models.RoleAssignment.resource_id == (str(resource_id) if resource_id else ""),
            models.RoleAssignment.is_active.is_(True),
            models.RoleAssignment.revoked_at.is_(None),
        )
    )
    row = result.scalar_one_or_none()
    return _role_assignment_to_domain(row) if row else None


async def revoke_role_assignment(
    session: AsyncSession, assignment_id: RoleAssignmentId, revoked_at: datetime
) -> domain.RoleAssignment | None:
    row = await session.get(models.RoleAssignment, str(assignment_id))
    if row is None:
        return None
    if row.revoked_at is None:
        row.revoked_at = revoked_at
    row.is_active = False
    await session.flush()
    return _role_assignment_to_domain(row)


async def list_role_assignments(
    session: AsyncSession,
    *,
    project_ids: set[ProjectId] | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[domain.RoleAssignment]:
    """List role assignments, filtered to ``project_ids`` when non-None.

    ``None`` means the caller is deployment-wide and sees all assignments; a
    project-scoped caller sees only project-scoped assignments within its
    authorized projects (deployment-scoped assignments are not discoverable).
    """
    stmt = select(models.RoleAssignment)
    if project_ids is not None:
        if not project_ids:
            return []
        stmt = stmt.where(
            models.RoleAssignment.resource_scope_type == ResourceScopeType.PROJECT.value,
            models.RoleAssignment.resource_id.in_([str(p) for p in project_ids]),
        )
    stmt = (
        stmt.order_by(
            models.RoleAssignment.created_at.asc(), models.RoleAssignment.id.asc()
        )
        .limit(limit)
        .offset(offset)
    )
    result = await session.execute(stmt)
    return [_role_assignment_to_domain(r) for r in result.scalars().all()]


async def count_role_assignments(
    session: AsyncSession, *, project_ids: set[ProjectId] | None = None
) -> int:
    stmt = select(func.count()).select_from(models.RoleAssignment)
    if project_ids is not None:
        if not project_ids:
            return 0
        stmt = stmt.where(
            models.RoleAssignment.resource_scope_type == ResourceScopeType.PROJECT.value,
            models.RoleAssignment.resource_id.in_([str(p) for p in project_ids]),
        )
    return (await session.execute(stmt)).scalar_one()


# ---------------------------------------------------------------------------
# Bootstrap state
# ---------------------------------------------------------------------------

BOOTSTRAP_STATE_ID = "bootstrap"


def _bootstrap_state_to_domain(row: models.BootstrapState) -> domain.BootstrapState:
    return domain.BootstrapState(
        id=row.id,
        completed=row.completed,
        completed_at=row.completed_at,
        initial_project_id=ProjectId(row.initial_project_id)
        if row.initial_project_id
        else None,
        initial_admin_principal_id=PrincipalId(row.initial_admin_principal_id)
        if row.initial_admin_principal_id
        else None,
        initial_admin_credential_id=ApiCredentialId(row.initial_admin_credential_id)
        if row.initial_admin_credential_id
        else None,
    )


async def get_bootstrap_state(session: AsyncSession) -> domain.BootstrapState | None:
    row = await session.get(models.BootstrapState, BOOTSTRAP_STATE_ID)
    return _bootstrap_state_to_domain(row) if row else None


async def get_bootstrap_state_for_update(
    session: AsyncSession,
) -> domain.BootstrapState | None:
    result = await session.execute(
        select(models.BootstrapState)
        .where(models.BootstrapState.id == BOOTSTRAP_STATE_ID)
        .with_for_update()
    )
    row = result.scalar_one_or_none()
    return _bootstrap_state_to_domain(row) if row else None


async def create_bootstrap_state(session: AsyncSession) -> domain.BootstrapState:
    row = models.BootstrapState(id=BOOTSTRAP_STATE_ID, completed=False)
    session.add(row)
    await session.flush()
    return _bootstrap_state_to_domain(row)


async def mark_bootstrap_completed(
    session: AsyncSession,
    *,
    project_id: ProjectId,
    principal_id: PrincipalId,
    credential_id: ApiCredentialId,
    completed_at: datetime,
) -> domain.BootstrapState:
    row = await session.get(models.BootstrapState, BOOTSTRAP_STATE_ID)
    assert row is not None  # guaranteed by bootstrap flow
    row.completed = True
    row.completed_at = completed_at
    row.initial_project_id = str(project_id)
    row.initial_admin_principal_id = str(principal_id)
    row.initial_admin_credential_id = str(credential_id)
    await session.flush()
    return _bootstrap_state_to_domain(row)


# ---------------------------------------------------------------------------
# Human OIDC identity: external identities, browser sessions, login states
# ---------------------------------------------------------------------------


def _external_identity_to_domain(
    row: models.ExternalIdentity,
) -> domain.ExternalIdentity:
    return domain.ExternalIdentity(
        id=ExternalIdentityId(row.id),
        principal_id=PrincipalId(row.principal_id),
        issuer=row.issuer,
        subject=row.subject,
        email=row.email,
        display_name=row.display_name,
        created_at=row.created_at,
        last_login_at=row.last_login_at,
        is_active=row.is_active,
    )


async def create_external_identity(
    session: AsyncSession, entity: domain.ExternalIdentity
) -> domain.ExternalIdentity:
    row = models.ExternalIdentity(
        id=str(entity.id),
        principal_id=str(entity.principal_id),
        issuer=entity.issuer,
        subject=entity.subject,
        email=entity.email,
        display_name=entity.display_name,
        last_login_at=entity.last_login_at,
        is_active=entity.is_active,
    )
    session.add(row)
    await session.flush()
    return _external_identity_to_domain(row)


async def get_external_identity(
    session: AsyncSession, issuer: str, subject: str
) -> domain.ExternalIdentity | None:
    result = await session.execute(
        select(models.ExternalIdentity).where(
            models.ExternalIdentity.issuer == issuer,
            models.ExternalIdentity.subject == subject,
        )
    )
    row = result.scalar_one_or_none()
    return _external_identity_to_domain(row) if row else None


async def get_external_identity_by_id(
    session: AsyncSession, identity_id: ExternalIdentityId
) -> domain.ExternalIdentity | None:
    row = await session.get(models.ExternalIdentity, str(identity_id))
    return _external_identity_to_domain(row) if row else None


async def touch_external_identity_login(
    session: AsyncSession, identity_id: ExternalIdentityId, at: datetime
) -> domain.ExternalIdentity | None:
    row = await session.get(models.ExternalIdentity, str(identity_id))
    if row is None:
        return None
    row.last_login_at = at
    await session.flush()
    return _external_identity_to_domain(row)


def _browser_session_to_domain(row: models.BrowserSession) -> domain.BrowserSession:
    return domain.BrowserSession(
        id=BrowserSessionId(row.id),
        principal_id=PrincipalId(row.principal_id),
        session_hash=row.session_hash,
        csrf_token_hash=row.csrf_token_hash,
        created_at=row.created_at,
        last_seen_at=row.last_seen_at,
        idle_expires_at=row.idle_expires_at,
        absolute_expires_at=row.absolute_expires_at,
        revoked_at=row.revoked_at,
        is_active=row.is_active,
    )


async def create_browser_session(
    session: AsyncSession, entity: domain.BrowserSession
) -> domain.BrowserSession:
    row = models.BrowserSession(
        id=str(entity.id),
        principal_id=str(entity.principal_id),
        session_hash=entity.session_hash,
        csrf_token_hash=entity.csrf_token_hash,
        last_seen_at=entity.last_seen_at,
        idle_expires_at=entity.idle_expires_at,
        absolute_expires_at=entity.absolute_expires_at,
        revoked_at=entity.revoked_at,
        is_active=entity.is_active,
    )
    session.add(row)
    await session.flush()
    return _browser_session_to_domain(row)


async def get_browser_session_by_hash(
    session: AsyncSession, session_hash: str
) -> domain.BrowserSession | None:
    result = await session.execute(
        select(models.BrowserSession).where(
            models.BrowserSession.session_hash == session_hash
        )
    )
    row = result.scalar_one_or_none()
    return _browser_session_to_domain(row) if row else None


async def get_browser_session(
    session: AsyncSession, session_id: BrowserSessionId
) -> domain.BrowserSession | None:
    row = await session.get(models.BrowserSession, str(session_id))
    return _browser_session_to_domain(row) if row else None


async def touch_browser_session(
    session: AsyncSession,
    session_id: BrowserSessionId,
    last_seen_at: datetime,
    idle_expires_at: datetime,
) -> domain.BrowserSession | None:
    row = await session.get(models.BrowserSession, str(session_id))
    if row is None:
        return None
    row.last_seen_at = last_seen_at
    row.idle_expires_at = idle_expires_at
    await session.flush()
    return _browser_session_to_domain(row)


async def revoke_browser_session(
    session: AsyncSession, session_id: BrowserSessionId, revoked_at: datetime
) -> domain.BrowserSession | None:
    row = await session.get(models.BrowserSession, str(session_id))
    if row is None:
        return None
    if row.revoked_at is None:
        row.revoked_at = revoked_at
    row.is_active = False
    await session.flush()
    return _browser_session_to_domain(row)


def _oidc_login_state_to_domain(row: models.OidcLoginState) -> domain.OidcLoginState:
    return domain.OidcLoginState(
        id=OidcLoginStateId(row.id),
        state=row.state,
        nonce=row.nonce,
        code_verifier=row.code_verifier,
        code_challenge=row.code_challenge,
        created_at=row.created_at,
        expires_at=row.expires_at,
        consumed_at=row.consumed_at,
    )


async def create_oidc_login_state(
    session: AsyncSession, entity: domain.OidcLoginState
) -> domain.OidcLoginState:
    row = models.OidcLoginState(
        id=str(entity.id),
        state=entity.state,
        nonce=entity.nonce,
        code_verifier=entity.code_verifier,
        code_challenge=entity.code_challenge,
        expires_at=entity.expires_at,
        consumed_at=entity.consumed_at,
    )
    session.add(row)
    await session.flush()
    return _oidc_login_state_to_domain(row)


async def get_oidc_login_state(
    session: AsyncSession, state: str
) -> domain.OidcLoginState | None:
    result = await session.execute(
        select(models.OidcLoginState).where(models.OidcLoginState.state == state)
    )
    row = result.scalar_one_or_none()
    return _oidc_login_state_to_domain(row) if row else None


async def get_oidc_login_state_for_update(
    session: AsyncSession, state: str
) -> domain.OidcLoginState | None:
    result = await session.execute(
        select(models.OidcLoginState)
        .where(models.OidcLoginState.state == state)
        .with_for_update()
    )
    row = result.scalar_one_or_none()
    return _oidc_login_state_to_domain(row) if row else None


async def consume_oidc_login_state(
    session: AsyncSession, state_id: OidcLoginStateId, consumed_at: datetime
) -> domain.OidcLoginState | None:
    row = await session.get(models.OidcLoginState, str(state_id))
    if row is None:
        return None
    if row.consumed_at is None:
        row.consumed_at = consumed_at
    await session.flush()
    return _oidc_login_state_to_domain(row)


# ---------------------------------------------------------------------------
# Audit events
# ---------------------------------------------------------------------------


def _audit_event_to_domain(row: models.AuditEvent) -> domain.AuditEvent:
    return domain.AuditEvent(
        id=AuditEventId(row.id),
        actor_principal_id=PrincipalId(row.actor_principal_id)
        if row.actor_principal_id
        else None,
        project_id=ProjectId(row.project_id) if row.project_id else None,
        action=row.action,
        resource_type=row.resource_type,
        resource_id=row.resource_id,
        occurred_at=row.occurred_at,
        metadata=row.details or {},
    )


async def create_audit_event(
    session: AsyncSession, entity: domain.AuditEvent
) -> domain.AuditEvent:
    row = models.AuditEvent(
        id=str(entity.id),
        actor_principal_id=str(entity.actor_principal_id)
        if entity.actor_principal_id
        else None,
        project_id=str(entity.project_id) if entity.project_id else None,
        action=entity.action,
        resource_type=entity.resource_type,
        resource_id=entity.resource_id,
        occurred_at=entity.occurred_at,
        details=entity.metadata,
    )
    session.add(row)
    await session.flush()
    return _audit_event_to_domain(row)


async def list_audit_events(session: AsyncSession) -> list[domain.AuditEvent]:
    result = await session.execute(
        select(models.AuditEvent).order_by(models.AuditEvent.occurred_at.asc())
    )
    return [_audit_event_to_domain(r) for r in result.scalars().all()]


def _price_policy_to_domain(row: models.PricePolicy) -> domain.PricePolicy:
    return domain.PricePolicy(
        id=PricePolicyId(row.id),
        route_binding_id=RouteBindingId(row.route_binding_id),
        billing_unit=BillingUnit(row.billing_unit),
        currency=row.currency,
        unit_scale=row.unit_scale,
        request_price=row.request_price,
        input_price=row.input_price,
        output_price=row.output_price,
        enabled=row.enabled,
        name=row.name,
    )


async def create_price_policy(
    session: AsyncSession, entity: domain.PricePolicy
) -> domain.PricePolicy:
    if entity.enabled:
        existing = (
            await session.execute(
                select(models.PricePolicy)
                .where(
                    models.PricePolicy.route_binding_id == str(entity.route_binding_id),
                    models.PricePolicy.enabled.is_(True),
                )
                .with_for_update()
            )
        ).scalars().first()
        if existing is not None:
            raise PricePolicyConflictError(str(entity.route_binding_id))

    row = models.PricePolicy(
        id=str(entity.id),
        route_binding_id=str(entity.route_binding_id),
        billing_unit=entity.billing_unit.value,
        currency=entity.currency,
        unit_scale=entity.unit_scale,
        request_price=entity.request_price,
        input_price=entity.input_price,
        output_price=entity.output_price,
        enabled=entity.enabled,
        name=entity.name,
    )
    session.add(row)
    try:
        await session.flush()
    except IntegrityError as exc:
        if _is_price_policy_conflict(exc):
            raise PricePolicyConflictError(str(entity.route_binding_id)) from exc
        raise
    return _price_policy_to_domain(row)


def _is_price_policy_conflict(exc: IntegrityError) -> bool:
    message = str(exc.orig).lower() if exc.orig else ""
    return "uq_price_policies_one_enabled_per_route" in message


async def get_price_policy_for_route_binding(
    session: AsyncSession, route_binding_id: RouteBindingId
) -> domain.PricePolicy | None:
    result = await session.execute(
        select(models.PricePolicy).where(
            models.PricePolicy.route_binding_id == str(route_binding_id)
        )
    )
    row = result.scalar_one_or_none()
    return _price_policy_to_domain(row) if row else None


def _budget_policy_to_domain(row: models.ProjectBudgetPolicy) -> domain.ProjectBudgetPolicy:
    return domain.ProjectBudgetPolicy(
        id=BudgetPolicyId(row.id),
        project_id=ProjectId(row.project_id),
        name=row.name,
        currency=row.currency,
        limit_amount=row.limit_amount,
        window_seconds=row.window_seconds,
        enabled=row.enabled,
    )


async def create_project_budget_policy(
    session: AsyncSession, entity: domain.ProjectBudgetPolicy
) -> domain.ProjectBudgetPolicy:
    row = models.ProjectBudgetPolicy(
        id=str(entity.id),
        project_id=str(entity.project_id),
        name=entity.name,
        currency=entity.currency,
        limit_amount=entity.limit_amount,
        window_seconds=entity.window_seconds,
        enabled=entity.enabled,
    )
    session.add(row)
    await session.flush()
    return _budget_policy_to_domain(row)


async def get_project_budget_policy_by_name(
    session: AsyncSession, project_id: ProjectId, name: str
) -> domain.ProjectBudgetPolicy | None:
    result = await session.execute(
        select(models.ProjectBudgetPolicy).where(
            models.ProjectBudgetPolicy.project_id == str(project_id),
            models.ProjectBudgetPolicy.name == name,
        )
    )
    row = result.scalar_one_or_none()
    return _budget_policy_to_domain(row) if row else None
