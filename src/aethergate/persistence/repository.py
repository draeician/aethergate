"""Repositories mapping ORM rows to domain entities (and back).

These functions accept/return domain entities from ``aethergate.domain``; ORM
objects never escape this module.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
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
)
from aethergate.domain.ids import (
    ApiCredentialId,
    BudgetPolicyId,
    EndpointId,
    ModelAliasId,
    PricePolicyId,
    PrincipalId,
    ProjectId,
    ProviderAccountId,
    ProviderId,
    QuotaGroupId,
    QuotaLimitId,
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


async def revoke_api_credential(
    session: AsyncSession, credential_id: ApiCredentialId, revoked_at: datetime
) -> domain.ApiCredential | None:
    row = await session.get(models.ApiCredential, str(credential_id))
    if row is None:
        return None
    row.is_active = False
    row.revoked_at = revoked_at
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
