"""Repositories mapping ORM rows to domain entities (and back).

These functions accept/return domain entities from ``aethergate.domain``; ORM
objects never escape this module.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from aethergate.domain import entities as domain
from aethergate.domain.enums import (
    BillingUnit,
    BudgetReservationState,
    Capability,
    CredentialAudience,
    CredentialScope,
    EndpointOperationalState,
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
    BudgetWindowId,
    CliSessionId,
    DeviceAuthorizationId,
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
    RoleAssignmentId,
    RouteBindingId,
    SecretRefId,
    UsageRecordId,
)
from aethergate.errors import (
    ActiveRouteConflictError,
    CatalogConflictError,
    CatalogParentMismatchError,
    PricePolicyConflictError,
)
from aethergate.persistence import models


def _capabilities(values: list | None) -> tuple[Capability, ...]:
    return tuple(Capability(v) for v in (values or []))


def _is_unique_violation(exc: IntegrityError, constraint: str) -> bool:
    """Return True if an ``IntegrityError`` is a unique violation on ``constraint``.

    Matches the asyncpg/PostgreSQL message against the constraint name substring
    so conflicts are translated to stable domain errors, never surfaced as raw
    SQL text.
    """
    message = str(exc.orig).lower() if exc.orig else ""
    return constraint.lower() in message


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
    try:
        await session.flush()
    except IntegrityError as exc:
        if _is_unique_violation(exc, "secret_refs_name_key"):
            raise CatalogConflictError("secret ref", "name", entity.name) from exc
        raise
    await session.refresh(row)
    return _secret_ref_to_domain(row)


async def get_secret_ref(
    session: AsyncSession, ref_id: SecretRefId
) -> domain.SecretRef | None:
    row = await session.get(models.SecretRef, str(ref_id))
    return _secret_ref_to_domain(row) if row else None


async def get_secret_ref_by_name(session: AsyncSession, name: str) -> domain.SecretRef | None:
    result = await session.execute(
        select(models.SecretRef).where(models.SecretRef.name == name)
    )
    row = result.scalar_one_or_none()
    return _secret_ref_to_domain(row) if row else None


async def list_secret_refs(
    session: AsyncSession, *, limit: int = 50, offset: int = 0
) -> list[domain.SecretRef]:
    stmt = (
        select(models.SecretRef)
        .order_by(models.SecretRef.created_at.asc(), models.SecretRef.id.asc())
        .limit(limit)
        .offset(offset)
    )
    result = await session.execute(stmt)
    return [_secret_ref_to_domain(r) for r in result.scalars().all()]


async def count_secret_refs(session: AsyncSession) -> int:
    return (await session.execute(select(func.count()).select_from(models.SecretRef))).scalar_one()


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
    try:
        await session.flush()
    except IntegrityError as exc:
        if _is_unique_violation(exc, "providers_name_key"):
            raise CatalogConflictError("provider", "name", entity.name) from exc
        raise
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


async def list_providers(
    session: AsyncSession, *, limit: int = 50, offset: int = 0
) -> list[domain.Provider]:
    stmt = (
        select(models.Provider)
        .order_by(models.Provider.created_at.asc(), models.Provider.id.asc())
        .limit(limit)
        .offset(offset)
    )
    result = await session.execute(stmt)
    return [_provider_to_domain(r) for r in result.scalars().all()]


async def count_providers(session: AsyncSession) -> int:
    return (await session.execute(select(func.count()).select_from(models.Provider))).scalar_one()


async def update_provider(
    session: AsyncSession,
    provider_id: ProviderId,
    *,
    kind: str | None = None,
    name: str | None = None,
    capabilities: tuple[Capability, ...] | None = None,
    is_active: bool | None = None,
) -> domain.Provider | None:
    row = await session.get(models.Provider, str(provider_id))
    if row is None:
        return None
    if kind is not None:
        row.kind = kind
    if name is not None:
        row.name = name
    if capabilities is not None:
        row.capabilities = [c.value for c in capabilities]
    if is_active is not None:
        row.is_active = is_active
    try:
        await session.flush()
    except IntegrityError as exc:
        if _is_unique_violation(exc, "providers_name_key"):
            raise CatalogConflictError("provider", "name", name or "") from exc
        raise
    return _provider_to_domain(row)


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


async def list_provider_accounts(
    session: AsyncSession,
    *,
    provider_id: ProviderId | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[domain.ProviderAccount]:
    stmt = select(models.ProviderAccount)
    if provider_id is not None:
        stmt = stmt.where(models.ProviderAccount.provider_id == str(provider_id))
    stmt = (
        stmt.order_by(
            models.ProviderAccount.created_at.asc(), models.ProviderAccount.id.asc()
        )
        .limit(limit)
        .offset(offset)
    )
    result = await session.execute(stmt)
    return [_provider_account_to_domain(r) for r in result.scalars().all()]


async def count_provider_accounts(
    session: AsyncSession, *, provider_id: ProviderId | None = None
) -> int:
    stmt = select(func.count()).select_from(models.ProviderAccount)
    if provider_id is not None:
        stmt = stmt.where(models.ProviderAccount.provider_id == str(provider_id))
    return (await session.execute(stmt)).scalar_one()


async def update_provider_account(
    session: AsyncSession,
    account_id: ProviderAccountId,
    *,
    name: str | None = None,
    external_account_id: str | None = None,
    clear_external_account_id: bool = False,
    secret_ref_id: SecretRefId | None = None,
    clear_secret_ref_id: bool = False,
    is_active: bool | None = None,
) -> domain.ProviderAccount | None:
    row = await session.get(models.ProviderAccount, str(account_id))
    if row is None:
        return None
    if name is not None:
        row.name = name
    if external_account_id is not None:
        row.external_account_id = external_account_id
    elif clear_external_account_id:
        row.external_account_id = None
    if secret_ref_id is not None:
        row.secret_ref_id = str(secret_ref_id)
    elif clear_secret_ref_id:
        row.secret_ref_id = None
    if is_active is not None:
        row.is_active = is_active
    await session.flush()
    return _provider_account_to_domain(row)


def _endpoint_to_domain(row: models.Endpoint) -> domain.Endpoint:
    return domain.Endpoint(
        id=EndpointId(row.id),
        provider_account_id=ProviderAccountId(row.provider_account_id),
        name=row.name,
        base_destination=row.base_destination,
        max_concurrency=row.max_concurrency,
        is_active=row.is_active,
        operational_state=EndpointOperationalState(row.operational_state),
    )


async def create_endpoint(session: AsyncSession, entity: domain.Endpoint) -> domain.Endpoint:
    row = models.Endpoint(
        id=str(entity.id),
        provider_account_id=str(entity.provider_account_id),
        name=entity.name,
        base_destination=entity.base_destination,
        max_concurrency=entity.max_concurrency,
        is_active=entity.is_active,
        operational_state=entity.operational_state.value,
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


async def list_endpoints(
    session: AsyncSession,
    *,
    provider_account_id: ProviderAccountId | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[domain.Endpoint]:
    stmt = select(models.Endpoint)
    if provider_account_id is not None:
        stmt = stmt.where(
            models.Endpoint.provider_account_id == str(provider_account_id)
        )
    stmt = (
        stmt.order_by(models.Endpoint.created_at.asc(), models.Endpoint.id.asc())
        .limit(limit)
        .offset(offset)
    )
    result = await session.execute(stmt)
    return [_endpoint_to_domain(r) for r in result.scalars().all()]


async def count_endpoints(
    session: AsyncSession, *, provider_account_id: ProviderAccountId | None = None
) -> int:
    stmt = select(func.count()).select_from(models.Endpoint)
    if provider_account_id is not None:
        stmt = stmt.where(
            models.Endpoint.provider_account_id == str(provider_account_id)
        )
    return (await session.execute(stmt)).scalar_one()


async def update_endpoint(
    session: AsyncSession,
    endpoint_id: EndpointId,
    *,
    name: str | None = None,
    base_destination: str | None = None,
    max_concurrency: int | None = None,
    is_active: bool | None = None,
) -> domain.Endpoint | None:
    row = await session.get(models.Endpoint, str(endpoint_id))
    if row is None:
        return None
    if name is not None:
        row.name = name
    if base_destination is not None:
        row.base_destination = base_destination
    if max_concurrency is not None:
        row.max_concurrency = max_concurrency
    if is_active is not None:
        row.is_active = is_active
    await session.flush()
    return _endpoint_to_domain(row)


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
    try:
        await session.flush()
    except IntegrityError as exc:
        if _is_unique_violation(exc, "quota_groups_name_key"):
            raise CatalogConflictError("quota group", "name", entity.name) from exc
        raise
    return _quota_group_to_domain(row)


async def get_quota_group_by_name(
    session: AsyncSession, name: str
) -> domain.QuotaGroup | None:
    result = await session.execute(
        select(models.QuotaGroup).where(models.QuotaGroup.name == name)
    )
    row = result.scalar_one_or_none()
    return _quota_group_to_domain(row) if row else None


async def get_quota_group(
    session: AsyncSession, group_id: QuotaGroupId
) -> domain.QuotaGroup | None:
    row = await session.get(models.QuotaGroup, str(group_id))
    return _quota_group_to_domain(row) if row else None


async def list_quota_groups(
    session: AsyncSession,
    *,
    provider_account_id: ProviderAccountId | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[domain.QuotaGroup]:
    stmt = select(models.QuotaGroup)
    if provider_account_id is not None:
        stmt = stmt.where(
            models.QuotaGroup.provider_account_id == str(provider_account_id)
        )
    stmt = (
        stmt.order_by(models.QuotaGroup.created_at.asc(), models.QuotaGroup.id.asc())
        .limit(limit)
        .offset(offset)
    )
    result = await session.execute(stmt)
    return [_quota_group_to_domain(r) for r in result.scalars().all()]


async def count_quota_groups(
    session: AsyncSession, *, provider_account_id: ProviderAccountId | None = None
) -> int:
    stmt = select(func.count()).select_from(models.QuotaGroup)
    if provider_account_id is not None:
        stmt = stmt.where(
            models.QuotaGroup.provider_account_id == str(provider_account_id)
        )
    return (await session.execute(stmt)).scalar_one()


async def update_quota_group(
    session: AsyncSession,
    group_id: QuotaGroupId,
    *,
    name: str | None = None,
    description: str | None = None,
    clear_description: bool = False,
) -> domain.QuotaGroup | None:
    row = await session.get(models.QuotaGroup, str(group_id))
    if row is None:
        return None
    if name is not None:
        row.name = name
    if description is not None:
        row.description = description
    elif clear_description:
        row.description = None
    try:
        await session.flush()
    except IntegrityError as exc:
        if _is_unique_violation(exc, "quota_groups_name_key"):
            raise CatalogConflictError("quota group", "name", name or "") from exc
        raise
    return _quota_group_to_domain(row)


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


async def get_quota_limit(
    session: AsyncSession, limit_id: QuotaLimitId
) -> domain.QuotaLimit | None:
    row = await session.get(models.QuotaLimit, str(limit_id))
    return _quota_limit_to_domain(row) if row else None


async def list_quota_limits(
    session: AsyncSession,
    *,
    quota_group_id: QuotaGroupId | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[domain.QuotaLimit]:
    stmt = select(models.QuotaLimit)
    if quota_group_id is not None:
        stmt = stmt.where(models.QuotaLimit.quota_group_id == str(quota_group_id))
    stmt = (
        stmt.order_by(models.QuotaLimit.created_at.asc(), models.QuotaLimit.id.asc())
        .limit(limit)
        .offset(offset)
    )
    result = await session.execute(stmt)
    return [_quota_limit_to_domain(r) for r in result.scalars().all()]


async def count_quota_limits(
    session: AsyncSession, *, quota_group_id: QuotaGroupId | None = None
) -> int:
    stmt = select(func.count()).select_from(models.QuotaLimit)
    if quota_group_id is not None:
        stmt = stmt.where(models.QuotaLimit.quota_group_id == str(quota_group_id))
    return (await session.execute(stmt)).scalar_one()


async def update_quota_limit(
    session: AsyncSession,
    limit_id: QuotaLimitId,
    *,
    limit_units: int | None = None,
    window_seconds: int | None = None,
    enabled: bool | None = None,
    name: str | None = None,
    clear_name: bool = False,
) -> domain.QuotaLimit | None:
    row = await session.get(models.QuotaLimit, str(limit_id))
    if row is None:
        return None
    if limit_units is not None:
        row.limit_units = limit_units
    if window_seconds is not None:
        row.window_seconds = window_seconds
    if enabled is not None:
        row.enabled = enabled
    if name is not None:
        row.name = name
    elif clear_name:
        row.name = None
    await session.flush()
    return _quota_limit_to_domain(row)


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
    try:
        await session.flush()
    except IntegrityError as exc:
        if _is_unique_violation(exc, "model_aliases_name_key"):
            raise CatalogConflictError("model alias", "name", entity.name) from exc
        raise
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


async def list_model_aliases(
    session: AsyncSession, *, limit: int = 50, offset: int = 0
) -> list[domain.ModelAlias]:
    stmt = (
        select(models.ModelAlias)
        .order_by(models.ModelAlias.created_at.asc(), models.ModelAlias.id.asc())
        .limit(limit)
        .offset(offset)
    )
    result = await session.execute(stmt)
    return [_model_alias_to_domain(r) for r in result.scalars().all()]


async def count_model_aliases(session: AsyncSession) -> int:
    return (await session.execute(select(func.count()).select_from(models.ModelAlias))).scalar_one()


async def update_model_alias(
    session: AsyncSession,
    alias_id: ModelAliasId,
    *,
    name: str | None = None,
    capabilities: tuple[Capability, ...] | None = None,
    is_active: bool | None = None,
) -> domain.ModelAlias | None:
    row = await session.get(models.ModelAlias, str(alias_id))
    if row is None:
        return None
    if name is not None:
        row.name = name
    if capabilities is not None:
        row.capabilities = [c.value for c in capabilities]
    if is_active is not None:
        row.is_active = is_active
    try:
        await session.flush()
    except IntegrityError as exc:
        if _is_unique_violation(exc, "model_aliases_name_key"):
            raise CatalogConflictError("model alias", "name", name or "") from exc
        raise
    return _model_alias_to_domain(row)


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
    endpoint = await session.get(models.Endpoint, str(entity.endpoint_id))
    if endpoint is None:
        raise ValueError(f"endpoint {entity.endpoint_id!s} not found")
    if endpoint.provider_account_id != str(entity.provider_account_id):
        raise CatalogParentMismatchError(
            "route provider_account_id must equal its endpoint's provider_account_id"
        )
    if entity.quota_group_id is not None:
        group = await session.get(models.QuotaGroup, str(entity.quota_group_id))
        if group is None:
            raise ValueError(f"quota group {entity.quota_group_id!s} not found")
        if group.provider_account_id != str(entity.provider_account_id):
            raise CatalogParentMismatchError(
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
    try:
        await session.flush()
    except IntegrityError as exc:
        if _is_unique_violation(exc, "uq_route_bindings_one_active_per_alias"):
            raise ActiveRouteConflictError(str(entity.model_alias_id)) from exc
        raise
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


async def get_route_binding(
    session: AsyncSession, binding_id: RouteBindingId
) -> domain.RouteBinding | None:
    row = await session.get(models.RouteBinding, str(binding_id))
    return _route_binding_to_domain(row) if row else None


async def list_route_bindings_paged(
    session: AsyncSession,
    *,
    model_alias_id: ModelAliasId | None = None,
    provider_account_id: ProviderAccountId | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[domain.RouteBinding]:
    stmt = select(models.RouteBinding)
    if model_alias_id is not None:
        stmt = stmt.where(models.RouteBinding.model_alias_id == str(model_alias_id))
    if provider_account_id is not None:
        stmt = stmt.where(
            models.RouteBinding.provider_account_id == str(provider_account_id)
        )
    stmt = (
        stmt.order_by(models.RouteBinding.created_at.asc(), models.RouteBinding.id.asc())
        .limit(limit)
        .offset(offset)
    )
    result = await session.execute(stmt)
    return [_route_binding_to_domain(r) for r in result.scalars().all()]


async def count_route_bindings(
    session: AsyncSession,
    *,
    model_alias_id: ModelAliasId | None = None,
    provider_account_id: ProviderAccountId | None = None,
) -> int:
    stmt = select(func.count()).select_from(models.RouteBinding)
    if model_alias_id is not None:
        stmt = stmt.where(models.RouteBinding.model_alias_id == str(model_alias_id))
    if provider_account_id is not None:
        stmt = stmt.where(
            models.RouteBinding.provider_account_id == str(provider_account_id)
        )
    return (await session.execute(stmt)).scalar_one()


async def update_route_binding(
    session: AsyncSession,
    binding_id: RouteBindingId,
    *,
    upstream_model: str | None = None,
    clear_upstream_model: bool = False,
    quota_group_id: QuotaGroupId | None = None,
    clear_quota_group_id: bool = False,
    default_output_tokens: int | None = None,
    clear_default_output_tokens: bool = False,
    is_active: bool | None = None,
) -> domain.RouteBinding | None:
    row = await session.get(models.RouteBinding, str(binding_id))
    if row is None:
        return None
    model_alias_id = row.model_alias_id
    if upstream_model is not None:
        row.upstream_model = upstream_model
    elif clear_upstream_model:
        row.upstream_model = None
    if quota_group_id is not None:
        row.quota_group_id = str(quota_group_id)
    elif clear_quota_group_id:
        row.quota_group_id = None
    if default_output_tokens is not None:
        row.default_output_tokens = default_output_tokens
    elif clear_default_output_tokens:
        row.default_output_tokens = None
    if is_active is not None:
        row.is_active = is_active
    try:
        await session.flush()
    except IntegrityError as exc:
        if _is_unique_violation(exc, "uq_route_bindings_one_active_per_alias"):
            raise ActiveRouteConflictError(model_alias_id) from exc
        raise
    return _route_binding_to_domain(row)


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
        txn_cookie_hash=row.txn_cookie_hash,
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
        txn_cookie_hash=entity.txn_cookie_hash,
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


async def delete_oidc_login_state(
    session: AsyncSession, state_id: OidcLoginStateId
) -> None:
    row = await session.get(models.OidcLoginState, str(state_id))
    if row is not None:
        await session.delete(row)
        await session.flush()


async def delete_expired_oidc_login_states(
    session: AsyncSession, now: datetime
) -> int:
    """Delete expired login transactions; returns the number of rows removed."""
    result = await session.execute(
        delete(models.OidcLoginState).where(models.OidcLoginState.expires_at <= now)
    )
    return result.rowcount or 0


# ---------------------------------------------------------------------------
# Device authorization transactions
# ---------------------------------------------------------------------------


def _device_authorization_to_domain(
    row: models.DeviceAuthorization,
) -> domain.DeviceAuthorization:
    return domain.DeviceAuthorization(
        id=DeviceAuthorizationId(row.id),
        device_code_hash=row.device_code_hash,
        user_code=row.user_code,
        verification_uri=row.verification_uri,
        verification_uri_complete=row.verification_uri_complete,
        created_at=row.created_at,
        expires_at=row.expires_at,
        poll_interval_seconds=row.poll_interval_seconds,
        last_poll_at=row.last_poll_at,
        consumed_at=row.consumed_at,
    )


async def create_device_authorization(
    session: AsyncSession, entity: domain.DeviceAuthorization
) -> domain.DeviceAuthorization:
    row = models.DeviceAuthorization(
        id=str(entity.id),
        device_code_hash=entity.device_code_hash,
        user_code=entity.user_code,
        verification_uri=entity.verification_uri,
        verification_uri_complete=entity.verification_uri_complete,
        expires_at=entity.expires_at,
        poll_interval_seconds=entity.poll_interval_seconds,
        last_poll_at=entity.last_poll_at,
        consumed_at=entity.consumed_at,
    )
    session.add(row)
    await session.flush()
    return _device_authorization_to_domain(row)


async def get_device_authorization_by_code_hash(
    session: AsyncSession, device_code_hash: str
) -> domain.DeviceAuthorization | None:
    result = await session.execute(
        select(models.DeviceAuthorization).where(
            models.DeviceAuthorization.device_code_hash == device_code_hash
        )
    )
    row = result.scalar_one_or_none()
    return _device_authorization_to_domain(row) if row else None


async def get_device_authorization_by_code_hash_for_update(
    session: AsyncSession, device_code_hash: str
) -> domain.DeviceAuthorization | None:
    result = await session.execute(
        select(models.DeviceAuthorization)
        .where(models.DeviceAuthorization.device_code_hash == device_code_hash)
        .with_for_update()
    )
    row = result.scalar_one_or_none()
    return _device_authorization_to_domain(row) if row else None


async def touch_device_authorization_poll(
    session: AsyncSession, device_auth_id: DeviceAuthorizationId, polled_at: datetime
) -> domain.DeviceAuthorization | None:
    row = await session.get(models.DeviceAuthorization, str(device_auth_id))
    if row is None:
        return None
    row.last_poll_at = polled_at
    await session.flush()
    return _device_authorization_to_domain(row)


async def consume_device_authorization(
    session: AsyncSession, device_auth_id: DeviceAuthorizationId, consumed_at: datetime
) -> domain.DeviceAuthorization | None:
    row = await session.get(models.DeviceAuthorization, str(device_auth_id))
    if row is None:
        return None
    row.consumed_at = consumed_at
    await session.flush()
    return _device_authorization_to_domain(row)


async def update_device_authorization_poll_interval(
    session: AsyncSession, device_auth_id: DeviceAuthorizationId, poll_interval_seconds: int
) -> domain.DeviceAuthorization | None:
    row = await session.get(models.DeviceAuthorization, str(device_auth_id))
    if row is None:
        return None
    row.poll_interval_seconds = poll_interval_seconds
    await session.flush()
    return _device_authorization_to_domain(row)


async def delete_expired_device_authorizations(
    session: AsyncSession, now: datetime
) -> int:
    result = await session.execute(
        delete(models.DeviceAuthorization).where(
            models.DeviceAuthorization.expires_at <= now
        )
    )
    return result.rowcount or 0


# ---------------------------------------------------------------------------
# CLI sessions
# ---------------------------------------------------------------------------


def _cli_session_to_domain(row: models.CliSession) -> domain.CliSession:
    return domain.CliSession(
        id=CliSessionId(row.id),
        principal_id=PrincipalId(row.principal_id),
        token_hash=row.token_hash,
        created_at=row.created_at,
        expires_at=row.expires_at,
        last_seen_at=row.last_seen_at,
        revoked_at=row.revoked_at,
        is_active=row.is_active,
    )


async def create_cli_session(
    session: AsyncSession, entity: domain.CliSession
) -> domain.CliSession:
    row = models.CliSession(
        id=str(entity.id),
        principal_id=str(entity.principal_id),
        token_hash=entity.token_hash,
        expires_at=entity.expires_at,
        last_seen_at=entity.last_seen_at,
        revoked_at=entity.revoked_at,
        is_active=entity.is_active,
    )
    session.add(row)
    await session.flush()
    return _cli_session_to_domain(row)


async def get_cli_session_by_hash(
    session: AsyncSession, token_hash: str
) -> domain.CliSession | None:
    result = await session.execute(
        select(models.CliSession).where(models.CliSession.token_hash == token_hash)
    )
    row = result.scalar_one_or_none()
    return _cli_session_to_domain(row) if row else None


async def get_cli_session(
    session: AsyncSession, cli_session_id: CliSessionId
) -> domain.CliSession | None:
    row = await session.get(models.CliSession, str(cli_session_id))
    return _cli_session_to_domain(row) if row else None


async def touch_cli_session(
    session: AsyncSession, cli_session_id: CliSessionId, last_seen_at: datetime
) -> domain.CliSession | None:
    row = await session.get(models.CliSession, str(cli_session_id))
    if row is None:
        return None
    row.last_seen_at = last_seen_at
    await session.flush()
    return _cli_session_to_domain(row)


async def revoke_cli_session(
    session: AsyncSession, cli_session_id: CliSessionId, revoked_at: datetime
) -> domain.CliSession | None:
    row = await session.get(models.CliSession, str(cli_session_id))
    if row is None:
        return None
    if row.revoked_at is None:
        row.revoked_at = revoked_at
    row.is_active = False
    await session.flush()
    return _cli_session_to_domain(row)


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


async def get_project_budget_policy(
    session: AsyncSession, policy_id: BudgetPolicyId
) -> domain.ProjectBudgetPolicy | None:
    row = await session.get(models.ProjectBudgetPolicy, str(policy_id))
    return _budget_policy_to_domain(row) if row else None


async def get_project_budget_policy_for_update(
    session: AsyncSession, policy_id: BudgetPolicyId
) -> domain.ProjectBudgetPolicy | None:
    row = (
        await session.execute(
            select(models.ProjectBudgetPolicy)
            .where(models.ProjectBudgetPolicy.id == str(policy_id))
            .with_for_update()
        )
    ).scalar_one_or_none()
    return _budget_policy_to_domain(row) if row else None


async def list_project_budget_policies(
    session: AsyncSession,
    *,
    project_ids: set[ProjectId] | None = None,
    enabled: bool | None = None,
    currency: str | None = None,
    limit: int | None = 50,
    offset: int = 0,
) -> list[domain.ProjectBudgetPolicy]:
    stmt = select(models.ProjectBudgetPolicy)
    if project_ids is not None:
        if not project_ids:
            return []
        stmt = stmt.where(
            models.ProjectBudgetPolicy.project_id.in_([str(p) for p in project_ids])
        )
    if enabled is not None:
        stmt = stmt.where(models.ProjectBudgetPolicy.enabled.is_(enabled))
    if currency is not None:
        stmt = stmt.where(models.ProjectBudgetPolicy.currency == currency)
    stmt = stmt.order_by(
        models.ProjectBudgetPolicy.created_at.asc(),
        models.ProjectBudgetPolicy.id.asc(),
    )
    if limit is not None:
        stmt = stmt.limit(limit)
    stmt = stmt.offset(offset)
    result = await session.execute(stmt)
    return [_budget_policy_to_domain(r) for r in result.scalars().all()]


async def count_project_budget_policies(
    session: AsyncSession,
    *,
    project_ids: set[ProjectId] | None = None,
    enabled: bool | None = None,
    currency: str | None = None,
) -> int:
    stmt = select(func.count()).select_from(models.ProjectBudgetPolicy)
    if project_ids is not None:
        if not project_ids:
            return 0
        stmt = stmt.where(
            models.ProjectBudgetPolicy.project_id.in_([str(p) for p in project_ids])
        )
    if enabled is not None:
        stmt = stmt.where(models.ProjectBudgetPolicy.enabled.is_(enabled))
    if currency is not None:
        stmt = stmt.where(models.ProjectBudgetPolicy.currency == currency)
    return (await session.execute(stmt)).scalar_one()


async def update_project_budget_policy(
    session: AsyncSession,
    policy_id: BudgetPolicyId,
    *,
    name: str | None = None,
    limit_amount: Decimal | None = None,
    enabled: bool | None = None,
) -> domain.ProjectBudgetPolicy | None:
    """Update mutable budget fields only (name/limit/enabled).

    ``currency`` and ``window_seconds`` are immutable and never touched here; the
    service layer rejects any attempt to change them.
    """
    row = await session.get(models.ProjectBudgetPolicy, str(policy_id))
    if row is None:
        return None
    if name is not None:
        row.name = name
    if limit_amount is not None:
        row.limit_amount = limit_amount
    if enabled is not None:
        row.enabled = enabled
    await session.flush()
    return _budget_policy_to_domain(row)


def _price_snapshot_to_domain(row: models.PriceSnapshot) -> domain.PriceSnapshot:
    return domain.PriceSnapshot(
        id=PriceSnapshotId(row.id),
        source_price_policy_id=PricePolicyId(row.source_price_policy_id),
        route_binding_id=RouteBindingId(row.route_binding_id),
        provider_account_id=ProviderAccountId(row.provider_account_id),
        model_alias_id=ModelAliasId(row.model_alias_id),
        billing_unit=BillingUnit(row.billing_unit),
        currency=row.currency,
        unit_scale=row.unit_scale,
        request_price=row.request_price,
        input_price=row.input_price,
        output_price=row.output_price,
        captured_at=row.captured_at,
    )


async def get_price_snapshot(
    session: AsyncSession, snapshot_id: PriceSnapshotId
) -> domain.PriceSnapshot | None:
    row = await session.get(models.PriceSnapshot, str(snapshot_id))
    return _price_snapshot_to_domain(row) if row else None


async def list_price_snapshots(
    session: AsyncSession,
    *,
    route_binding_id: RouteBindingId | None = None,
    model_alias_id: ModelAliasId | None = None,
    provider_account_id: ProviderAccountId | None = None,
    source_price_policy_id: PricePolicyId | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[domain.PriceSnapshot]:
    stmt = select(models.PriceSnapshot)
    if route_binding_id is not None:
        stmt = stmt.where(models.PriceSnapshot.route_binding_id == str(route_binding_id))
    if model_alias_id is not None:
        stmt = stmt.where(models.PriceSnapshot.model_alias_id == str(model_alias_id))
    if provider_account_id is not None:
        stmt = stmt.where(
            models.PriceSnapshot.provider_account_id == str(provider_account_id)
        )
    if source_price_policy_id is not None:
        stmt = stmt.where(
            models.PriceSnapshot.source_price_policy_id == str(source_price_policy_id)
        )
    stmt = (
        stmt.order_by(models.PriceSnapshot.captured_at.asc(), models.PriceSnapshot.id.asc())
        .limit(limit)
        .offset(offset)
    )
    result = await session.execute(stmt)
    return [_price_snapshot_to_domain(r) for r in result.scalars().all()]


async def count_price_snapshots(
    session: AsyncSession,
    *,
    route_binding_id: RouteBindingId | None = None,
    model_alias_id: ModelAliasId | None = None,
    provider_account_id: ProviderAccountId | None = None,
    source_price_policy_id: PricePolicyId | None = None,
) -> int:
    stmt = select(func.count()).select_from(models.PriceSnapshot)
    if route_binding_id is not None:
        stmt = stmt.where(models.PriceSnapshot.route_binding_id == str(route_binding_id))
    if model_alias_id is not None:
        stmt = stmt.where(models.PriceSnapshot.model_alias_id == str(model_alias_id))
    if provider_account_id is not None:
        stmt = stmt.where(
            models.PriceSnapshot.provider_account_id == str(provider_account_id)
        )
    if source_price_policy_id is not None:
        stmt = stmt.where(
            models.PriceSnapshot.source_price_policy_id == str(source_price_policy_id)
        )
    return (await session.execute(stmt)).scalar_one()


async def get_price_policy(
    session: AsyncSession, policy_id: PricePolicyId
) -> domain.PricePolicy | None:
    row = await session.get(models.PricePolicy, str(policy_id))
    return _price_policy_to_domain(row) if row else None


async def list_price_policies(
    session: AsyncSession,
    *,
    route_binding_id: RouteBindingId | None = None,
    enabled: bool | None = None,
    billing_unit: BillingUnit | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[domain.PricePolicy]:
    stmt = select(models.PricePolicy)
    if route_binding_id is not None:
        stmt = stmt.where(models.PricePolicy.route_binding_id == str(route_binding_id))
    if enabled is not None:
        stmt = stmt.where(models.PricePolicy.enabled.is_(enabled))
    if billing_unit is not None:
        stmt = stmt.where(models.PricePolicy.billing_unit == billing_unit.value)
    stmt = (
        stmt.order_by(models.PricePolicy.created_at.asc(), models.PricePolicy.id.asc())
        .limit(limit)
        .offset(offset)
    )
    result = await session.execute(stmt)
    return [_price_policy_to_domain(r) for r in result.scalars().all()]


async def count_price_policies(
    session: AsyncSession,
    *,
    route_binding_id: RouteBindingId | None = None,
    enabled: bool | None = None,
    billing_unit: BillingUnit | None = None,
) -> int:
    stmt = select(func.count()).select_from(models.PricePolicy)
    if route_binding_id is not None:
        stmt = stmt.where(models.PricePolicy.route_binding_id == str(route_binding_id))
    if enabled is not None:
        stmt = stmt.where(models.PricePolicy.enabled.is_(enabled))
    if billing_unit is not None:
        stmt = stmt.where(models.PricePolicy.billing_unit == billing_unit.value)
    return (await session.execute(stmt)).scalar_one()


async def update_price_policy(
    session: AsyncSession,
    policy_id: PricePolicyId,
    *,
    billing_unit: str,
    currency: str,
    unit_scale: int,
    request_price: Decimal | None,
    input_price: Decimal | None,
    output_price: Decimal | None,
    enabled: bool,
    name: str | None,
) -> domain.PricePolicy | None:
    """Apply a fully-validated resulting price-policy shape under a row lock.

    The service layer validates the complete resulting shape (including the
    one-enabled-per-route invariant's pre-check) before calling this. The row
    lock serializes edits against scheduler admission; the partial unique index
    remains the authoritative backstop for concurrent enable races.
    """
    row = (
        await session.execute(
            select(models.PricePolicy)
            .where(models.PricePolicy.id == str(policy_id))
            .with_for_update()
        )
    ).scalar_one_or_none()
    if row is None:
        return None
    if enabled and not row.enabled:
        existing = (
            await session.execute(
                select(models.PricePolicy)
                .where(
                    models.PricePolicy.route_binding_id == row.route_binding_id,
                    models.PricePolicy.enabled.is_(True),
                    models.PricePolicy.id != row.id,
                )
                .with_for_update()
            )
        ).scalars().first()
        if existing is not None:
            raise PricePolicyConflictError(row.route_binding_id)
    row.billing_unit = billing_unit
    row.currency = currency
    row.unit_scale = unit_scale
    row.request_price = request_price
    row.input_price = input_price
    row.output_price = output_price
    row.enabled = enabled
    row.name = name
    try:
        await session.flush()
    except IntegrityError as exc:
        if _is_price_policy_conflict(exc):
            raise PricePolicyConflictError(row.route_binding_id) from exc
        raise
    return _price_policy_to_domain(row)


def _budget_window_to_domain(row: models.BudgetWindow) -> domain.BudgetWindow:
    return domain.BudgetWindow(
        id=BudgetWindowId(row.id),
        budget_policy_id=BudgetPolicyId(row.budget_policy_id),
        window_start=row.window_start,
        committed_amount=row.committed_amount,
        reserved_amount=row.reserved_amount,
    )


async def get_budget_window(
    session: AsyncSession, *, budget_policy_id: BudgetPolicyId, window_start: datetime
) -> domain.BudgetWindow | None:
    row = (
        await session.execute(
            select(models.BudgetWindow).where(
                models.BudgetWindow.budget_policy_id == str(budget_policy_id),
                models.BudgetWindow.window_start == window_start,
            )
        )
    ).scalar_one_or_none()
    return _budget_window_to_domain(row) if row else None


def _budget_reservation_to_domain(
    row: models.BudgetReservation,
) -> domain.BudgetReservation:
    return domain.BudgetReservation(
        id=BudgetReservationId(row.id),
        request_id=RequestId(row.request_id),
        budget_policy_id=BudgetPolicyId(row.budget_policy_id),
        price_snapshot_id=PriceSnapshotId(row.price_snapshot_id)
        if row.price_snapshot_id
        else None,
        window_start=row.window_start,
        reserved_amount=row.reserved_amount,
        committed_amount=row.committed_amount,
        state=BudgetReservationState(row.state),
        settlement_reason=row.settlement_reason,
    )


async def get_budget_reservation(
    session: AsyncSession, reservation_id: BudgetReservationId
) -> domain.BudgetReservation | None:
    row = await session.get(models.BudgetReservation, str(reservation_id))
    return _budget_reservation_to_domain(row) if row else None


async def list_budget_reservations(
    session: AsyncSession,
    *,
    project_ids: set[ProjectId] | None = None,
    request_id: RequestId | None = None,
    budget_policy_id: BudgetPolicyId | None = None,
    state: BudgetReservationState | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[domain.BudgetReservation]:
    stmt = select(models.BudgetReservation)
    if project_ids is not None:
        if not project_ids:
            return []
        stmt = stmt.join(
            models.ProjectBudgetPolicy,
            models.ProjectBudgetPolicy.id == models.BudgetReservation.budget_policy_id,
        ).where(
            models.ProjectBudgetPolicy.project_id.in_([str(p) for p in project_ids])
        )
    if request_id is not None:
        stmt = stmt.where(models.BudgetReservation.request_id == str(request_id))
    if budget_policy_id is not None:
        stmt = stmt.where(
            models.BudgetReservation.budget_policy_id == str(budget_policy_id)
        )
    if state is not None:
        stmt = stmt.where(models.BudgetReservation.state == state.value)
    stmt = (
        stmt.order_by(
            models.BudgetReservation.created_at.asc(),
            models.BudgetReservation.id.asc(),
        )
        .limit(limit)
        .offset(offset)
    )
    result = await session.execute(stmt)
    return [_budget_reservation_to_domain(r) for r in result.scalars().all()]


async def count_budget_reservations(
    session: AsyncSession,
    *,
    project_ids: set[ProjectId] | None = None,
    request_id: RequestId | None = None,
    budget_policy_id: BudgetPolicyId | None = None,
    state: BudgetReservationState | None = None,
) -> int:
    stmt = select(func.count()).select_from(models.BudgetReservation)
    if project_ids is not None:
        if not project_ids:
            return 0
        stmt = stmt.join(
            models.ProjectBudgetPolicy,
            models.ProjectBudgetPolicy.id == models.BudgetReservation.budget_policy_id,
        ).where(
            models.ProjectBudgetPolicy.project_id.in_([str(p) for p in project_ids])
        )
    if request_id is not None:
        stmt = stmt.where(models.BudgetReservation.request_id == str(request_id))
    if budget_policy_id is not None:
        stmt = stmt.where(
            models.BudgetReservation.budget_policy_id == str(budget_policy_id)
        )
    if state is not None:
        stmt = stmt.where(models.BudgetReservation.state == state.value)
    return (await session.execute(stmt)).scalar_one()


def _usage_record_to_domain(row: models.UsageRecord) -> domain.UsageRecord:
    return domain.UsageRecord(
        id=UsageRecordId(row.id),
        request_id=RequestId(row.request_id),
        execution_attempt_id=ExecutionAttemptId(row.execution_attempt_id),
        project_id=ProjectId(row.project_id),
        principal_id=PrincipalId(row.principal_id) if row.principal_id else None,
        api_credential_id=ApiCredentialId(row.api_credential_id)
        if row.api_credential_id
        else None,
        model_alias_id=ModelAliasId(row.model_alias_id),
        route_binding_id=RouteBindingId(row.route_binding_id),
        provider_account_id=ProviderAccountId(row.provider_account_id),
        price_snapshot_id=PriceSnapshotId(row.price_snapshot_id),
        billing_unit=BillingUnit(row.billing_unit),
        input_units=row.input_units,
        output_units=row.output_units,
        request_units=row.request_units,
        amount=row.amount,
        currency=row.currency,
        recorded_at=row.recorded_at,
        upstream_request_id=row.upstream_request_id,
    )


async def get_usage_record(
    session: AsyncSession, record_id: UsageRecordId
) -> domain.UsageRecord | None:
    row = await session.get(models.UsageRecord, str(record_id))
    return _usage_record_to_domain(row) if row else None


async def list_usage_records(
    session: AsyncSession,
    *,
    project_ids: set[ProjectId] | None = None,
    request_id: RequestId | None = None,
    principal_id: PrincipalId | None = None,
    api_credential_id: ApiCredentialId | None = None,
    model_alias_id: ModelAliasId | None = None,
    route_binding_id: RouteBindingId | None = None,
    provider_account_id: ProviderAccountId | None = None,
    billing_unit: BillingUnit | None = None,
    currency: str | None = None,
    recorded_from: datetime | None = None,
    recorded_to: datetime | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[domain.UsageRecord]:
    stmt = select(models.UsageRecord)
    if project_ids is not None:
        if not project_ids:
            return []
        stmt = stmt.where(models.UsageRecord.project_id.in_([str(p) for p in project_ids]))
    if request_id is not None:
        stmt = stmt.where(models.UsageRecord.request_id == str(request_id))
    if principal_id is not None:
        stmt = stmt.where(models.UsageRecord.principal_id == str(principal_id))
    if api_credential_id is not None:
        stmt = stmt.where(models.UsageRecord.api_credential_id == str(api_credential_id))
    if model_alias_id is not None:
        stmt = stmt.where(models.UsageRecord.model_alias_id == str(model_alias_id))
    if route_binding_id is not None:
        stmt = stmt.where(models.UsageRecord.route_binding_id == str(route_binding_id))
    if provider_account_id is not None:
        stmt = stmt.where(
            models.UsageRecord.provider_account_id == str(provider_account_id)
        )
    if billing_unit is not None:
        stmt = stmt.where(models.UsageRecord.billing_unit == billing_unit.value)
    if currency is not None:
        stmt = stmt.where(models.UsageRecord.currency == currency)
    if recorded_from is not None:
        stmt = stmt.where(models.UsageRecord.recorded_at >= recorded_from)
    if recorded_to is not None:
        stmt = stmt.where(models.UsageRecord.recorded_at <= recorded_to)
    stmt = (
        stmt.order_by(models.UsageRecord.recorded_at.asc(), models.UsageRecord.id.asc())
        .limit(limit)
        .offset(offset)
    )
    result = await session.execute(stmt)
    return [_usage_record_to_domain(r) for r in result.scalars().all()]


async def count_usage_records(
    session: AsyncSession,
    *,
    project_ids: set[ProjectId] | None = None,
    request_id: RequestId | None = None,
    principal_id: PrincipalId | None = None,
    api_credential_id: ApiCredentialId | None = None,
    model_alias_id: ModelAliasId | None = None,
    route_binding_id: RouteBindingId | None = None,
    provider_account_id: ProviderAccountId | None = None,
    billing_unit: BillingUnit | None = None,
    currency: str | None = None,
    recorded_from: datetime | None = None,
    recorded_to: datetime | None = None,
) -> int:
    stmt = select(func.count()).select_from(models.UsageRecord)
    if project_ids is not None:
        if not project_ids:
            return 0
        stmt = stmt.where(models.UsageRecord.project_id.in_([str(p) for p in project_ids]))
    if request_id is not None:
        stmt = stmt.where(models.UsageRecord.request_id == str(request_id))
    if principal_id is not None:
        stmt = stmt.where(models.UsageRecord.principal_id == str(principal_id))
    if api_credential_id is not None:
        stmt = stmt.where(models.UsageRecord.api_credential_id == str(api_credential_id))
    if model_alias_id is not None:
        stmt = stmt.where(models.UsageRecord.model_alias_id == str(model_alias_id))
    if route_binding_id is not None:
        stmt = stmt.where(models.UsageRecord.route_binding_id == str(route_binding_id))
    if provider_account_id is not None:
        stmt = stmt.where(
            models.UsageRecord.provider_account_id == str(provider_account_id)
        )
    if billing_unit is not None:
        stmt = stmt.where(models.UsageRecord.billing_unit == billing_unit.value)
    if currency is not None:
        stmt = stmt.where(models.UsageRecord.currency == currency)
    if recorded_from is not None:
        stmt = stmt.where(models.UsageRecord.recorded_at >= recorded_from)
    if recorded_to is not None:
        stmt = stmt.where(models.UsageRecord.recorded_at <= recorded_to)
    return (await session.execute(stmt)).scalar_one()


def _ledger_entry_to_domain(row: models.LedgerEntry) -> domain.LedgerEntry:
    return domain.LedgerEntry(
        id=LedgerEntryId(row.id),
        project_id=ProjectId(row.project_id),
        usage_record_id=UsageRecordId(row.usage_record_id)
        if row.usage_record_id
        else None,
        entry_type=LedgerEntryType(row.entry_type),
        amount=row.amount,
        currency=row.currency,
        created_at=row.created_at,
        idempotency_key=row.idempotency_key,
        reason=row.reason,
    )


async def get_ledger_entry(
    session: AsyncSession, entry_id: LedgerEntryId
) -> domain.LedgerEntry | None:
    row = await session.get(models.LedgerEntry, str(entry_id))
    return _ledger_entry_to_domain(row) if row else None


async def list_ledger_entries(
    session: AsyncSession,
    *,
    project_ids: set[ProjectId] | None = None,
    usage_record_id: UsageRecordId | None = None,
    entry_type: LedgerEntryType | None = None,
    currency: str | None = None,
    created_from: datetime | None = None,
    created_to: datetime | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[domain.LedgerEntry]:
    stmt = select(models.LedgerEntry)
    if project_ids is not None:
        if not project_ids:
            return []
        stmt = stmt.where(models.LedgerEntry.project_id.in_([str(p) for p in project_ids]))
    if usage_record_id is not None:
        stmt = stmt.where(models.LedgerEntry.usage_record_id == str(usage_record_id))
    if entry_type is not None:
        stmt = stmt.where(models.LedgerEntry.entry_type == entry_type.value)
    if currency is not None:
        stmt = stmt.where(models.LedgerEntry.currency == currency)
    if created_from is not None:
        stmt = stmt.where(models.LedgerEntry.created_at >= created_from)
    if created_to is not None:
        stmt = stmt.where(models.LedgerEntry.created_at <= created_to)
    stmt = (
        stmt.order_by(models.LedgerEntry.created_at.asc(), models.LedgerEntry.id.asc())
        .limit(limit)
        .offset(offset)
    )
    result = await session.execute(stmt)
    return [_ledger_entry_to_domain(r) for r in result.scalars().all()]


async def count_ledger_entries(
    session: AsyncSession,
    *,
    project_ids: set[ProjectId] | None = None,
    usage_record_id: UsageRecordId | None = None,
    entry_type: LedgerEntryType | None = None,
    currency: str | None = None,
    created_from: datetime | None = None,
    created_to: datetime | None = None,
) -> int:
    stmt = select(func.count()).select_from(models.LedgerEntry)
    if project_ids is not None:
        if not project_ids:
            return 0
        stmt = stmt.where(models.LedgerEntry.project_id.in_([str(p) for p in project_ids]))
    if usage_record_id is not None:
        stmt = stmt.where(models.LedgerEntry.usage_record_id == str(usage_record_id))
    if entry_type is not None:
        stmt = stmt.where(models.LedgerEntry.entry_type == entry_type.value)
    if currency is not None:
        stmt = stmt.where(models.LedgerEntry.currency == currency)
    if created_from is not None:
        stmt = stmt.where(models.LedgerEntry.created_at >= created_from)
    if created_to is not None:
        stmt = stmt.where(models.LedgerEntry.created_at <= created_to)
    return (await session.execute(stmt)).scalar_one()


async def get_audit_event(
    session: AsyncSession, event_id: AuditEventId
) -> domain.AuditEvent | None:
    row = await session.get(models.AuditEvent, str(event_id))
    return _audit_event_to_domain(row) if row else None


async def list_audit_events_paged(
    session: AsyncSession,
    *,
    project_ids: set[ProjectId] | None = None,
    actor_principal_id: PrincipalId | None = None,
    action: str | None = None,
    resource_type: str | None = None,
    resource_id: str | None = None,
    occurred_from: datetime | None = None,
    occurred_to: datetime | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[domain.AuditEvent]:
    """List audit events, scoped to ``project_ids`` when non-None.

    ``None`` means the caller is deployment-wide (``system_admin``) and sees all
    events including deployment-scoped (``project_id`` NULL) ones; a set restricts
    to project-scoped events within those projects (NULL project_id is excluded).
    """
    stmt = select(models.AuditEvent)
    if project_ids is not None:
        if not project_ids:
            return []
        stmt = stmt.where(
            models.AuditEvent.project_id.in_([str(p) for p in project_ids])
        )
    if actor_principal_id is not None:
        stmt = stmt.where(models.AuditEvent.actor_principal_id == str(actor_principal_id))
    if action is not None:
        stmt = stmt.where(models.AuditEvent.action == action)
    if resource_type is not None:
        stmt = stmt.where(models.AuditEvent.resource_type == resource_type)
    if resource_id is not None:
        stmt = stmt.where(models.AuditEvent.resource_id == resource_id)
    if occurred_from is not None:
        stmt = stmt.where(models.AuditEvent.occurred_at >= occurred_from)
    if occurred_to is not None:
        stmt = stmt.where(models.AuditEvent.occurred_at <= occurred_to)
    stmt = (
        stmt.order_by(models.AuditEvent.occurred_at.asc(), models.AuditEvent.id.asc())
        .limit(limit)
        .offset(offset)
    )
    result = await session.execute(stmt)
    return [_audit_event_to_domain(r) for r in result.scalars().all()]


async def count_audit_events(
    session: AsyncSession,
    *,
    project_ids: set[ProjectId] | None = None,
    actor_principal_id: PrincipalId | None = None,
    action: str | None = None,
    resource_type: str | None = None,
    resource_id: str | None = None,
    occurred_from: datetime | None = None,
    occurred_to: datetime | None = None,
) -> int:
    stmt = select(func.count()).select_from(models.AuditEvent)
    if project_ids is not None:
        if not project_ids:
            return 0
        stmt = stmt.where(
            models.AuditEvent.project_id.in_([str(p) for p in project_ids])
        )
    if actor_principal_id is not None:
        stmt = stmt.where(models.AuditEvent.actor_principal_id == str(actor_principal_id))
    if action is not None:
        stmt = stmt.where(models.AuditEvent.action == action)
    if resource_type is not None:
        stmt = stmt.where(models.AuditEvent.resource_type == resource_type)
    if resource_id is not None:
        stmt = stmt.where(models.AuditEvent.resource_id == resource_id)
    if occurred_from is not None:
        stmt = stmt.where(models.AuditEvent.occurred_at >= occurred_from)
    if occurred_to is not None:
        stmt = stmt.where(models.AuditEvent.occurred_at <= occurred_to)
    return (await session.execute(stmt)).scalar_one()
