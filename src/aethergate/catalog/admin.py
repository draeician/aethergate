"""Catalog/routing administration service.

The deployment-wide catalog control plane. Catalog resources (providers, provider
accounts, endpoints, quota groups/limits, model aliases, route bindings, and
secret-reference metadata) are deployment infrastructure, not project-owned
resources, so every operation here requires deployment-scoped authority
(``system_admin``) plus the matching ``admin:catalog:*`` permission.

Mutation services accept a typed :class:`AdminRequestContext` and authorize
internally, so an internal caller cannot bypass RBAC by invoking a service method
with a forged actor ID. Read/list services authorize the same way. Routers remain
thin and never compare opaque resource IDs to project IDs directly.

Endpoint destinations are validated against the same :class:`DestinationPolicy`
used before dispatch, so the control plane can never persist a destination the
inference path would reject. Audit events record only safe metadata (never a
secret name/value, upstream URL, or Authorization/CSRF material).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from aethergate.config import get_settings
from aethergate.domain import entities as domain
from aethergate.domain.enums import Capability, CredentialScope, QuotaMetric
from aethergate.domain.ids import (
    AuditEventId,
    EndpointId,
    ModelAliasId,
    PrincipalId,
    ProviderAccountId,
    ProviderId,
    QuotaGroupId,
    QuotaLimitId,
    RouteBindingId,
    SecretRefId,
)
from aethergate.egress import DestinationPolicy
from aethergate.errors import (
    AdminResourceNotFound,
    AdminValidationError,
    CatalogDestinationDenied,
    CatalogParentMismatchError,
    DestinationDenied,
)
from aethergate.identity.authorization import RESOURCE_DEPLOYMENT, authorize_admin
from aethergate.persistence import repository

# Audit action names (stable strings; safe metadata only).
AUDIT_PROVIDER_CREATED = "provider.created"
AUDIT_PROVIDER_UPDATED = "provider.updated"
AUDIT_SECRET_REF_CREATED = "secret_ref.created"
AUDIT_PROVIDER_ACCOUNT_CREATED = "provider_account.created"
AUDIT_PROVIDER_ACCOUNT_UPDATED = "provider_account.updated"
AUDIT_ENDPOINT_CREATED = "endpoint.created"
AUDIT_ENDPOINT_UPDATED = "endpoint.updated"
AUDIT_QUOTA_GROUP_CREATED = "quota_group.created"
AUDIT_QUOTA_GROUP_UPDATED = "quota_group.updated"
AUDIT_QUOTA_LIMIT_CREATED = "quota_limit.created"
AUDIT_QUOTA_LIMIT_UPDATED = "quota_limit.updated"
AUDIT_MODEL_ALIAS_CREATED = "model_alias.created"
AUDIT_MODEL_ALIAS_UPDATED = "model_alias.updated"
AUDIT_ROUTE_BINDING_CREATED = "route_binding.created"
AUDIT_ROUTE_BINDING_UPDATED = "route_binding.updated"


def utcnow() -> datetime:
    return datetime.now(UTC)


def _new_id() -> str:
    return uuid.uuid4().hex


def _authorize_read(context: domain.AdminRequestContext) -> None:
    authorize_admin(context, CredentialScope.ADMIN_CATALOG_READ, RESOURCE_DEPLOYMENT, None)


def _authorize_write(context: domain.AdminRequestContext) -> None:
    authorize_admin(context, CredentialScope.ADMIN_CATALOG_WRITE, RESOURCE_DEPLOYMENT, None)


def _validate_destination(base_destination: str) -> None:
    """Validate an endpoint destination against the runtime egress policy."""
    try:
        DestinationPolicy(get_settings().upstream_allowlist_hosts).validate(base_destination)
    except DestinationDenied as exc:
        raise CatalogDestinationDenied(exc.reason) from exc


async def _write_audit(
    session: AsyncSession,
    *,
    actor_principal_id: PrincipalId,
    action: str,
    resource_type: str,
    resource_id: str,
    metadata: dict | None = None,
) -> None:
    await repository.create_audit_event(
        session,
        domain.AuditEvent(
            id=AuditEventId(_new_id()),
            actor_principal_id=actor_principal_id,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            occurred_at=utcnow(),
            metadata=metadata or {},
        ),
    )


# ---------------------------------------------------------------------------
# Providers
# ---------------------------------------------------------------------------


async def create_provider(
    session: AsyncSession,
    *,
    context: domain.AdminRequestContext,
    kind: str,
    name: str,
    capabilities: tuple[Capability, ...] = (),
    is_active: bool = True,
) -> domain.Provider:
    _authorize_write(context)
    if not kind.strip():
        raise AdminValidationError("provider kind must not be empty")
    if not name.strip():
        raise AdminValidationError("provider name must not be empty")
    provider = await repository.create_provider(
        session,
        domain.Provider(
            id=ProviderId(_new_id()),
            kind=kind,
            name=name,
            capabilities=capabilities,
            is_active=is_active,
        ),
    )
    await _write_audit(
        session,
        actor_principal_id=context.principal_id,
        action=AUDIT_PROVIDER_CREATED,
        resource_type="provider",
        resource_id=str(provider.id),
    )
    return provider


async def update_provider(
    session: AsyncSession,
    *,
    context: domain.AdminRequestContext,
    provider_id: ProviderId,
    kind: str | None = None,
    name: str | None = None,
    capabilities: tuple[Capability, ...] | None = None,
    is_active: bool | None = None,
) -> domain.Provider:
    _authorize_write(context)
    existing = await repository.get_provider(session, provider_id)
    if existing is None:
        raise AdminResourceNotFound()
    if kind is not None and not kind.strip():
        raise AdminValidationError("provider kind must not be empty")
    if name is not None and not name.strip():
        raise AdminValidationError("provider name must not be empty")
    updated = await repository.update_provider(
        session,
        provider_id,
        kind=kind,
        name=name,
        capabilities=capabilities,
        is_active=is_active,
    )
    assert updated is not None
    changed = (
        (kind is not None and kind != existing.kind)
        or (name is not None and name != existing.name)
        or (capabilities is not None and capabilities != existing.capabilities)
        or (is_active is not None and is_active != existing.is_active)
    )
    if changed:
        await _write_audit(
            session,
            actor_principal_id=context.principal_id,
            action=AUDIT_PROVIDER_UPDATED,
            resource_type="provider",
            resource_id=str(updated.id),
        )
    return updated


async def list_providers(
    session: AsyncSession,
    *,
    context: domain.AdminRequestContext,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[domain.Provider], int]:
    _authorize_read(context)
    items = await repository.list_providers(session, limit=limit, offset=offset)
    total = await repository.count_providers(session)
    return items, total


async def get_provider(
    session: AsyncSession, *, context: domain.AdminRequestContext, provider_id: ProviderId
) -> domain.Provider:
    _authorize_read(context)
    provider = await repository.get_provider(session, provider_id)
    if provider is None:
        raise AdminResourceNotFound()
    return provider


# ---------------------------------------------------------------------------
# Secret references (metadata only)
# ---------------------------------------------------------------------------


async def create_secret_ref(
    session: AsyncSession, *, context: domain.AdminRequestContext, name: str
) -> domain.SecretRef:
    _authorize_write(context)
    ref = await repository.create_secret_ref(
        session,
        domain.SecretRef(id=SecretRefId(_new_id()), name=name, created_at=utcnow()),
    )
    await _write_audit(
        session,
        actor_principal_id=context.principal_id,
        action=AUDIT_SECRET_REF_CREATED,
        resource_type="secret_ref",
        resource_id=str(ref.id),
    )
    return ref


async def list_secret_refs(
    session: AsyncSession,
    *,
    context: domain.AdminRequestContext,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[domain.SecretRef], int]:
    _authorize_read(context)
    items = await repository.list_secret_refs(session, limit=limit, offset=offset)
    total = await repository.count_secret_refs(session)
    return items, total


async def get_secret_ref(
    session: AsyncSession, *, context: domain.AdminRequestContext, ref_id: SecretRefId
) -> domain.SecretRef:
    _authorize_read(context)
    ref = await repository.get_secret_ref(session, ref_id)
    if ref is None:
        raise AdminResourceNotFound()
    return ref


# ---------------------------------------------------------------------------
# Provider accounts
# ---------------------------------------------------------------------------


async def create_provider_account(
    session: AsyncSession,
    *,
    context: domain.AdminRequestContext,
    provider_id: ProviderId,
    name: str,
    external_account_id: str | None = None,
    secret_ref_id: SecretRefId | None = None,
    is_active: bool = True,
) -> domain.ProviderAccount:
    _authorize_write(context)
    provider = await repository.get_provider(session, provider_id)
    if provider is None:
        raise AdminResourceNotFound()
    if secret_ref_id is not None:
        if await repository.get_secret_ref(session, secret_ref_id) is None:
            raise AdminResourceNotFound()
    account = await repository.create_provider_account(
        session,
        domain.ProviderAccount(
            id=ProviderAccountId(_new_id()),
            provider_id=provider_id,
            name=name,
            external_account_id=external_account_id,
            secret_ref_id=secret_ref_id,
            is_active=is_active,
        ),
    )
    await _write_audit(
        session,
        actor_principal_id=context.principal_id,
        action=AUDIT_PROVIDER_ACCOUNT_CREATED,
        resource_type="provider_account",
        resource_id=str(account.id),
    )
    return account


async def update_provider_account(
    session: AsyncSession,
    *,
    context: domain.AdminRequestContext,
    account_id: ProviderAccountId,
    name: str | None = None,
    external_account_id: str | None = None,
    clear_external_account_id: bool = False,
    secret_ref_id: SecretRefId | None = None,
    clear_secret_ref_id: bool = False,
    is_active: bool | None = None,
) -> domain.ProviderAccount:
    _authorize_write(context)
    existing = await repository.get_provider_account(session, account_id)
    if existing is None:
        raise AdminResourceNotFound()
    if secret_ref_id is not None:
        if await repository.get_secret_ref(session, secret_ref_id) is None:
            raise AdminResourceNotFound()
    updated = await repository.update_provider_account(
        session,
        account_id,
        name=name,
        external_account_id=external_account_id,
        clear_external_account_id=clear_external_account_id,
        secret_ref_id=secret_ref_id,
        clear_secret_ref_id=clear_secret_ref_id,
        is_active=is_active,
    )
    assert updated is not None
    changed = (
        (name is not None and name != existing.name)
        or (external_account_id is not None and external_account_id != existing.external_account_id)
        or (clear_external_account_id and existing.external_account_id is not None)
        or (secret_ref_id is not None and secret_ref_id != existing.secret_ref_id)
        or (clear_secret_ref_id and existing.secret_ref_id is not None)
        or (is_active is not None and is_active != existing.is_active)
    )
    if changed:
        await _write_audit(
            session,
            actor_principal_id=context.principal_id,
            action=AUDIT_PROVIDER_ACCOUNT_UPDATED,
            resource_type="provider_account",
            resource_id=str(updated.id),
        )
    return updated


async def list_provider_accounts(
    session: AsyncSession,
    *,
    context: domain.AdminRequestContext,
    provider_id: ProviderId | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[domain.ProviderAccount], int]:
    _authorize_read(context)
    items = await repository.list_provider_accounts(
        session, provider_id=provider_id, limit=limit, offset=offset
    )
    total = await repository.count_provider_accounts(session, provider_id=provider_id)
    return items, total


async def get_provider_account(
    session: AsyncSession,
    *,
    context: domain.AdminRequestContext,
    account_id: ProviderAccountId,
) -> domain.ProviderAccount:
    _authorize_read(context)
    account = await repository.get_provider_account(session, account_id)
    if account is None:
        raise AdminResourceNotFound()
    return account


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


async def create_endpoint(
    session: AsyncSession,
    *,
    context: domain.AdminRequestContext,
    provider_account_id: ProviderAccountId,
    name: str,
    base_destination: str,
    max_concurrency: int = 1,
    is_active: bool = True,
) -> domain.Endpoint:
    _authorize_write(context)
    account = await repository.get_provider_account(session, provider_account_id)
    if account is None:
        raise AdminResourceNotFound()
    _validate_destination(base_destination)
    endpoint = await repository.create_endpoint(
        session,
        domain.Endpoint(
            id=EndpointId(_new_id()),
            provider_account_id=provider_account_id,
            name=name,
            base_destination=base_destination,
            max_concurrency=max_concurrency,
            is_active=is_active,
        ),
    )
    await _write_audit(
        session,
        actor_principal_id=context.principal_id,
        action=AUDIT_ENDPOINT_CREATED,
        resource_type="endpoint",
        resource_id=str(endpoint.id),
    )
    return endpoint


async def update_endpoint(
    session: AsyncSession,
    *,
    context: domain.AdminRequestContext,
    endpoint_id: EndpointId,
    name: str | None = None,
    base_destination: str | None = None,
    max_concurrency: int | None = None,
    is_active: bool | None = None,
) -> domain.Endpoint:
    _authorize_write(context)
    existing = await repository.get_endpoint(session, endpoint_id)
    if existing is None:
        raise AdminResourceNotFound()
    if base_destination is not None:
        _validate_destination(base_destination)
    updated = await repository.update_endpoint(
        session,
        endpoint_id,
        name=name,
        base_destination=base_destination,
        max_concurrency=max_concurrency,
        is_active=is_active,
    )
    assert updated is not None
    changed = (
        (name is not None and name != existing.name)
        or (base_destination is not None and base_destination != existing.base_destination)
        or (max_concurrency is not None and max_concurrency != existing.max_concurrency)
        or (is_active is not None and is_active != existing.is_active)
    )
    if changed:
        await _write_audit(
            session,
            actor_principal_id=context.principal_id,
            action=AUDIT_ENDPOINT_UPDATED,
            resource_type="endpoint",
            resource_id=str(updated.id),
        )
    return updated


async def list_endpoints(
    session: AsyncSession,
    *,
    context: domain.AdminRequestContext,
    provider_account_id: ProviderAccountId | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[domain.Endpoint], int]:
    _authorize_read(context)
    items = await repository.list_endpoints(
        session, provider_account_id=provider_account_id, limit=limit, offset=offset
    )
    total = await repository.count_endpoints(session, provider_account_id=provider_account_id)
    return items, total


async def get_endpoint(
    session: AsyncSession, *, context: domain.AdminRequestContext, endpoint_id: EndpointId
) -> domain.Endpoint:
    _authorize_read(context)
    endpoint = await repository.get_endpoint(session, endpoint_id)
    if endpoint is None:
        raise AdminResourceNotFound()
    return endpoint


# ---------------------------------------------------------------------------
# Quota groups
# ---------------------------------------------------------------------------


async def create_quota_group(
    session: AsyncSession,
    *,
    context: domain.AdminRequestContext,
    provider_account_id: ProviderAccountId,
    name: str,
    description: str | None = None,
) -> domain.QuotaGroup:
    _authorize_write(context)
    account = await repository.get_provider_account(session, provider_account_id)
    if account is None:
        raise AdminResourceNotFound()
    group = await repository.create_quota_group(
        session,
        domain.QuotaGroup(
            id=QuotaGroupId(_new_id()),
            provider_account_id=provider_account_id,
            name=name,
            description=description,
        ),
    )
    await _write_audit(
        session,
        actor_principal_id=context.principal_id,
        action=AUDIT_QUOTA_GROUP_CREATED,
        resource_type="quota_group",
        resource_id=str(group.id),
    )
    return group


async def update_quota_group(
    session: AsyncSession,
    *,
    context: domain.AdminRequestContext,
    group_id: QuotaGroupId,
    name: str | None = None,
    description: str | None = None,
    clear_description: bool = False,
) -> domain.QuotaGroup:
    _authorize_write(context)
    existing = await repository.get_quota_group(session, group_id)
    if existing is None:
        raise AdminResourceNotFound()
    updated = await repository.update_quota_group(
        session,
        group_id,
        name=name,
        description=description,
        clear_description=clear_description,
    )
    assert updated is not None
    changed = (
        (name is not None and name != existing.name)
        or (description is not None and description != existing.description)
        or (clear_description and existing.description is not None)
    )
    if changed:
        await _write_audit(
            session,
            actor_principal_id=context.principal_id,
            action=AUDIT_QUOTA_GROUP_UPDATED,
            resource_type="quota_group",
            resource_id=str(updated.id),
        )
    return updated


async def list_quota_groups(
    session: AsyncSession,
    *,
    context: domain.AdminRequestContext,
    provider_account_id: ProviderAccountId | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[domain.QuotaGroup], int]:
    _authorize_read(context)
    items = await repository.list_quota_groups(
        session, provider_account_id=provider_account_id, limit=limit, offset=offset
    )
    total = await repository.count_quota_groups(
        session, provider_account_id=provider_account_id
    )
    return items, total


async def get_quota_group(
    session: AsyncSession, *, context: domain.AdminRequestContext, group_id: QuotaGroupId
) -> domain.QuotaGroup:
    _authorize_read(context)
    group = await repository.get_quota_group(session, group_id)
    if group is None:
        raise AdminResourceNotFound()
    return group


# ---------------------------------------------------------------------------
# Quota limits
# ---------------------------------------------------------------------------


async def create_quota_limit(
    session: AsyncSession,
    *,
    context: domain.AdminRequestContext,
    quota_group_id: QuotaGroupId,
    metric: QuotaMetric,
    limit_units: int,
    window_seconds: int,
    enabled: bool = True,
    name: str | None = None,
) -> domain.QuotaLimit:
    _authorize_write(context)
    group = await repository.get_quota_group(session, quota_group_id)
    if group is None:
        raise AdminResourceNotFound()
    limit = await repository.create_quota_limit(
        session,
        domain.QuotaLimit(
            id=QuotaLimitId(_new_id()),
            quota_group_id=quota_group_id,
            metric=metric,
            limit_units=limit_units,
            window_seconds=window_seconds,
            enabled=enabled,
            name=name,
        ),
    )
    await _write_audit(
        session,
        actor_principal_id=context.principal_id,
        action=AUDIT_QUOTA_LIMIT_CREATED,
        resource_type="quota_limit",
        resource_id=str(limit.id),
    )
    return limit


async def update_quota_limit(
    session: AsyncSession,
    *,
    context: domain.AdminRequestContext,
    limit_id: QuotaLimitId,
    limit_units: int | None = None,
    window_seconds: int | None = None,
    enabled: bool | None = None,
    name: str | None = None,
    clear_name: bool = False,
) -> domain.QuotaLimit:
    _authorize_write(context)
    existing = await repository.get_quota_limit(session, limit_id)
    if existing is None:
        raise AdminResourceNotFound()
    updated = await repository.update_quota_limit(
        session,
        limit_id,
        limit_units=limit_units,
        window_seconds=window_seconds,
        enabled=enabled,
        name=name,
        clear_name=clear_name,
    )
    assert updated is not None
    changed = (
        (limit_units is not None and limit_units != existing.limit_units)
        or (window_seconds is not None and window_seconds != existing.window_seconds)
        or (enabled is not None and enabled != existing.enabled)
        or (name is not None and name != existing.name)
        or (clear_name and existing.name is not None)
    )
    if changed:
        await _write_audit(
            session,
            actor_principal_id=context.principal_id,
            action=AUDIT_QUOTA_LIMIT_UPDATED,
            resource_type="quota_limit",
            resource_id=str(updated.id),
        )
    return updated


async def list_quota_limits(
    session: AsyncSession,
    *,
    context: domain.AdminRequestContext,
    quota_group_id: QuotaGroupId | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[domain.QuotaLimit], int]:
    _authorize_read(context)
    items = await repository.list_quota_limits(
        session, quota_group_id=quota_group_id, limit=limit, offset=offset
    )
    total = await repository.count_quota_limits(session, quota_group_id=quota_group_id)
    return items, total


async def get_quota_limit(
    session: AsyncSession, *, context: domain.AdminRequestContext, limit_id: QuotaLimitId
) -> domain.QuotaLimit:
    _authorize_read(context)
    limit = await repository.get_quota_limit(session, limit_id)
    if limit is None:
        raise AdminResourceNotFound()
    return limit


# ---------------------------------------------------------------------------
# Model aliases
# ---------------------------------------------------------------------------


async def create_model_alias(
    session: AsyncSession,
    *,
    context: domain.AdminRequestContext,
    name: str,
    capabilities: tuple[Capability, ...] = (),
    is_active: bool = True,
) -> domain.ModelAlias:
    _authorize_write(context)
    alias = await repository.create_model_alias(
        session,
        domain.ModelAlias(
            id=ModelAliasId(_new_id()),
            name=name,
            capabilities=capabilities,
            is_active=is_active,
        ),
    )
    await _write_audit(
        session,
        actor_principal_id=context.principal_id,
        action=AUDIT_MODEL_ALIAS_CREATED,
        resource_type="model_alias",
        resource_id=str(alias.id),
    )
    return alias


async def update_model_alias(
    session: AsyncSession,
    *,
    context: domain.AdminRequestContext,
    alias_id: ModelAliasId,
    name: str | None = None,
    capabilities: tuple[Capability, ...] | None = None,
    is_active: bool | None = None,
) -> domain.ModelAlias:
    _authorize_write(context)
    existing = await repository.get_model_alias(session, alias_id)
    if existing is None:
        raise AdminResourceNotFound()
    updated = await repository.update_model_alias(
        session,
        alias_id,
        name=name,
        capabilities=capabilities,
        is_active=is_active,
    )
    assert updated is not None
    changed = (
        (name is not None and name != existing.name)
        or (capabilities is not None and capabilities != existing.capabilities)
        or (is_active is not None and is_active != existing.is_active)
    )
    if changed:
        await _write_audit(
            session,
            actor_principal_id=context.principal_id,
            action=AUDIT_MODEL_ALIAS_UPDATED,
            resource_type="model_alias",
            resource_id=str(updated.id),
        )
    return updated


async def list_model_aliases(
    session: AsyncSession,
    *,
    context: domain.AdminRequestContext,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[domain.ModelAlias], int]:
    _authorize_read(context)
    items = await repository.list_model_aliases(session, limit=limit, offset=offset)
    total = await repository.count_model_aliases(session)
    return items, total


async def get_model_alias(
    session: AsyncSession, *, context: domain.AdminRequestContext, alias_id: ModelAliasId
) -> domain.ModelAlias:
    _authorize_read(context)
    alias = await repository.get_model_alias(session, alias_id)
    if alias is None:
        raise AdminResourceNotFound()
    return alias


# ---------------------------------------------------------------------------
# Route bindings
# ---------------------------------------------------------------------------


async def create_route_binding(
    session: AsyncSession,
    *,
    context: domain.AdminRequestContext,
    model_alias_id: ModelAliasId,
    endpoint_id: EndpointId,
    provider_account_id: ProviderAccountId,
    upstream_model: str | None = None,
    quota_group_id: QuotaGroupId | None = None,
    default_output_tokens: int | None = None,
    is_active: bool = True,
) -> domain.RouteBinding:
    _authorize_write(context)
    if await repository.get_model_alias(session, model_alias_id) is None:
        raise AdminResourceNotFound()
    endpoint = await repository.get_endpoint(session, endpoint_id)
    if endpoint is None:
        raise AdminResourceNotFound()
    if await repository.get_provider_account(session, provider_account_id) is None:
        raise AdminResourceNotFound()
    if endpoint.provider_account_id != provider_account_id:
        raise CatalogParentMismatchError(
            "route provider_account_id must equal its endpoint's provider_account_id"
        )
    if quota_group_id is not None:
        group = await repository.get_quota_group(session, quota_group_id)
        if group is None:
            raise AdminResourceNotFound()
        if group.provider_account_id != provider_account_id:
            raise CatalogParentMismatchError(
                "route quota group must belong to the route's provider account"
            )
    binding = await repository.create_route_binding(
        session,
        domain.RouteBinding(
            id=RouteBindingId(_new_id()),
            model_alias_id=model_alias_id,
            endpoint_id=endpoint_id,
            provider_account_id=provider_account_id,
            upstream_model=upstream_model,
            quota_group_id=quota_group_id,
            default_output_tokens=default_output_tokens,
            is_active=is_active,
        ),
    )
    await _write_audit(
        session,
        actor_principal_id=context.principal_id,
        action=AUDIT_ROUTE_BINDING_CREATED,
        resource_type="route_binding",
        resource_id=str(binding.id),
    )
    return binding


async def update_route_binding(
    session: AsyncSession,
    *,
    context: domain.AdminRequestContext,
    binding_id: RouteBindingId,
    upstream_model: str | None = None,
    clear_upstream_model: bool = False,
    quota_group_id: QuotaGroupId | None = None,
    clear_quota_group_id: bool = False,
    default_output_tokens: int | None = None,
    clear_default_output_tokens: bool = False,
    is_active: bool | None = None,
) -> domain.RouteBinding:
    _authorize_write(context)
    existing = await repository.get_route_binding(session, binding_id)
    if existing is None:
        raise AdminResourceNotFound()
    if quota_group_id is not None:
        group = await repository.get_quota_group(session, quota_group_id)
        if group is None:
            raise AdminResourceNotFound()
        if group.provider_account_id != existing.provider_account_id:
            raise CatalogParentMismatchError(
                "route quota group must belong to the route's provider account"
            )
    updated = await repository.update_route_binding(
        session,
        binding_id,
        upstream_model=upstream_model,
        clear_upstream_model=clear_upstream_model,
        quota_group_id=quota_group_id,
        clear_quota_group_id=clear_quota_group_id,
        default_output_tokens=default_output_tokens,
        clear_default_output_tokens=clear_default_output_tokens,
        is_active=is_active,
    )
    assert updated is not None
    changed = (
        (upstream_model is not None and upstream_model != existing.upstream_model)
        or (clear_upstream_model and existing.upstream_model is not None)
        or (quota_group_id is not None and quota_group_id != existing.quota_group_id)
        or (clear_quota_group_id and existing.quota_group_id is not None)
        or (
            default_output_tokens is not None
            and default_output_tokens != existing.default_output_tokens
        )
        or (clear_default_output_tokens and existing.default_output_tokens is not None)
        or (is_active is not None and is_active != existing.is_active)
    )
    if changed:
        await _write_audit(
            session,
            actor_principal_id=context.principal_id,
            action=AUDIT_ROUTE_BINDING_UPDATED,
            resource_type="route_binding",
            resource_id=str(updated.id),
        )
    return updated


async def list_route_bindings(
    session: AsyncSession,
    *,
    context: domain.AdminRequestContext,
    model_alias_id: ModelAliasId | None = None,
    provider_account_id: ProviderAccountId | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[domain.RouteBinding], int]:
    _authorize_read(context)
    items = await repository.list_route_bindings_paged(
        session,
        model_alias_id=model_alias_id,
        provider_account_id=provider_account_id,
        limit=limit,
        offset=offset,
    )
    total = await repository.count_route_bindings(
        session,
        model_alias_id=model_alias_id,
        provider_account_id=provider_account_id,
    )
    return items, total


async def get_route_binding(
    session: AsyncSession,
    *,
    context: domain.AdminRequestContext,
    binding_id: RouteBindingId,
) -> domain.RouteBinding:
    _authorize_read(context)
    binding = await repository.get_route_binding(session, binding_id)
    if binding is None:
        raise AdminResourceNotFound()
    return binding


__all__ = [
    "create_provider",
    "update_provider",
    "list_providers",
    "get_provider",
    "create_secret_ref",
    "list_secret_refs",
    "get_secret_ref",
    "create_provider_account",
    "update_provider_account",
    "list_provider_accounts",
    "get_provider_account",
    "create_endpoint",
    "update_endpoint",
    "list_endpoints",
    "get_endpoint",
    "create_quota_group",
    "update_quota_group",
    "list_quota_groups",
    "get_quota_group",
    "create_quota_limit",
    "update_quota_limit",
    "list_quota_limits",
    "get_quota_limit",
    "create_model_alias",
    "update_model_alias",
    "list_model_aliases",
    "get_model_alias",
    "create_route_binding",
    "update_route_binding",
    "list_route_bindings",
    "get_route_binding",
]
