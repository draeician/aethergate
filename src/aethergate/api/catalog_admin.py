"""Protected catalog/routing admin HTTP surface.

Deployment-scoped catalog resources exposed over ``/admin/v1``. Routers are thin:
authorization and validation live in :mod:`aethergate.catalog.admin`, which
accepts a typed ``AdminRequestContext`` and authorizes internally. Every catalog
list/read/mutation requires deployment-scoped authority (``system_admin``) plus
the matching ``admin:catalog:*`` permission; a project-scoped role is denied
regardless of the credential's scopes.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query

from aethergate.api.admin import (
    DEFAULT_LIMIT,
    AdminContextDep,
    LimitQuery,
    OffsetQuery,
    SessionDep,
)
from aethergate.catalog import admin as catalog_service
from aethergate.contracts.admin_v1 import (
    EndpointCreate,
    EndpointRead,
    EndpointUpdate,
    ModelAliasCreate,
    ModelAliasRead,
    ModelAliasUpdate,
    ProviderAccountCreate,
    ProviderAccountRead,
    ProviderAccountUpdate,
    ProviderCreate,
    ProviderRead,
    ProviderUpdate,
    QuotaGroupCreate,
    QuotaGroupRead,
    QuotaGroupUpdate,
    QuotaLimitCreate,
    QuotaLimitRead,
    QuotaLimitUpdate,
    RouteBindingCreate,
    RouteBindingRead,
    RouteBindingUpdate,
    SecretRefCreate,
    SecretRefRead,
)
from aethergate.contracts.common import Page
from aethergate.domain import entities as domain
from aethergate.domain.ids import (
    EndpointId,
    ModelAliasId,
    ProviderAccountId,
    ProviderId,
    QuotaGroupId,
    QuotaLimitId,
    RouteBindingId,
    SecretRefId,
)

router = APIRouter(prefix="/admin/v1", tags=["admin-catalog"])

ProviderIdQuery = Annotated[ProviderId | None, Query()]
ProviderAccountIdQuery = Annotated[ProviderAccountId | None, Query()]
QuotaGroupIdQuery = Annotated[QuotaGroupId | None, Query()]
ModelAliasIdQuery = Annotated[ModelAliasId | None, Query()]


def _to_provider(provider: domain.Provider) -> ProviderRead:
    return ProviderRead(
        id=provider.id,
        kind=provider.kind,
        name=provider.name,
        capabilities=provider.capabilities,
        is_active=provider.is_active,
    )


def _to_secret_ref(ref: domain.SecretRef) -> SecretRefRead:
    return SecretRefRead(id=ref.id, name=ref.name, created_at=ref.created_at)


def _to_provider_account(account: domain.ProviderAccount) -> ProviderAccountRead:
    return ProviderAccountRead(
        id=account.id,
        provider_id=account.provider_id,
        name=account.name,
        external_account_id=account.external_account_id,
        secret_ref_id=account.secret_ref_id,
        is_active=account.is_active,
    )


def _to_endpoint(endpoint: domain.Endpoint) -> EndpointRead:
    return EndpointRead(
        id=endpoint.id,
        provider_account_id=endpoint.provider_account_id,
        name=endpoint.name,
        base_destination=endpoint.base_destination,
        max_concurrency=endpoint.max_concurrency,
        is_active=endpoint.is_active,
    )


def _to_quota_group(group: domain.QuotaGroup) -> QuotaGroupRead:
    return QuotaGroupRead(
        id=group.id,
        provider_account_id=group.provider_account_id,
        name=group.name,
        description=group.description,
    )


def _to_quota_limit(limit: domain.QuotaLimit) -> QuotaLimitRead:
    return QuotaLimitRead(
        id=limit.id,
        quota_group_id=limit.quota_group_id,
        metric=limit.metric,
        limit_units=limit.limit_units,
        window_seconds=limit.window_seconds,
        enabled=limit.enabled,
        name=limit.name,
    )


def _to_model_alias(alias: domain.ModelAlias) -> ModelAliasRead:
    return ModelAliasRead(
        id=alias.id,
        name=alias.name,
        capabilities=alias.capabilities,
        is_active=alias.is_active,
    )


def _to_route_binding(binding: domain.RouteBinding) -> RouteBindingRead:
    return RouteBindingRead(
        id=binding.id,
        model_alias_id=binding.model_alias_id,
        endpoint_id=binding.endpoint_id,
        provider_account_id=binding.provider_account_id,
        upstream_model=binding.upstream_model,
        quota_group_id=binding.quota_group_id,
        default_output_tokens=binding.default_output_tokens,
        is_active=binding.is_active,
    )


# --- providers -----------------------------------------------------------------


@router.post("/providers", response_model=ProviderRead, status_code=201)
async def create_provider(
    body: ProviderCreate, context: AdminContextDep, session: SessionDep
) -> ProviderRead:
    async with session.begin():
        provider = await catalog_service.create_provider(
            session,
            context=context,
            kind=body.kind,
            name=body.name,
            capabilities=body.capabilities,
            is_active=body.is_active,
        )
    return _to_provider(provider)


@router.get("/providers", response_model=Page[ProviderRead])
async def list_providers(
    context: AdminContextDep,
    session: SessionDep,
    limit: LimitQuery = DEFAULT_LIMIT,
    offset: OffsetQuery = 0,
) -> Page[ProviderRead]:
    items, total = await catalog_service.list_providers(
        session, context=context, limit=limit, offset=offset
    )
    return Page(
        items=[_to_provider(p) for p in items], limit=limit, offset=offset, total=total
    )


@router.get("/providers/{provider_id}", response_model=ProviderRead)
async def get_provider(
    provider_id: ProviderId, context: AdminContextDep, session: SessionDep
) -> ProviderRead:
    return _to_provider(
        await catalog_service.get_provider(session, context=context, provider_id=provider_id)
    )


@router.patch("/providers/{provider_id}", response_model=ProviderRead)
async def update_provider(
    provider_id: ProviderId,
    body: ProviderUpdate,
    context: AdminContextDep,
    session: SessionDep,
) -> ProviderRead:
    async with session.begin():
        provider = await catalog_service.update_provider(
            session,
            context=context,
            provider_id=provider_id,
            kind=body.kind,
            name=body.name,
            capabilities=body.capabilities,
            is_active=body.is_active,
        )
    return _to_provider(provider)


# --- secret references (metadata only) -----------------------------------------


@router.post("/secret-refs", response_model=SecretRefRead, status_code=201)
async def create_secret_ref(
    body: SecretRefCreate, context: AdminContextDep, session: SessionDep
) -> SecretRefRead:
    async with session.begin():
        ref = await catalog_service.create_secret_ref(
            session, context=context, name=body.name
        )
    return _to_secret_ref(ref)


@router.get("/secret-refs", response_model=Page[SecretRefRead])
async def list_secret_refs(
    context: AdminContextDep,
    session: SessionDep,
    limit: LimitQuery = DEFAULT_LIMIT,
    offset: OffsetQuery = 0,
) -> Page[SecretRefRead]:
    items, total = await catalog_service.list_secret_refs(
        session, context=context, limit=limit, offset=offset
    )
    return Page(
        items=[_to_secret_ref(r) for r in items], limit=limit, offset=offset, total=total
    )


@router.get("/secret-refs/{ref_id}", response_model=SecretRefRead)
async def get_secret_ref(
    ref_id: SecretRefId, context: AdminContextDep, session: SessionDep
) -> SecretRefRead:
    return _to_secret_ref(
        await catalog_service.get_secret_ref(session, context=context, ref_id=ref_id)
    )


# --- provider accounts ----------------------------------------------------------


@router.post("/provider-accounts", response_model=ProviderAccountRead, status_code=201)
async def create_provider_account(
    body: ProviderAccountCreate, context: AdminContextDep, session: SessionDep
) -> ProviderAccountRead:
    async with session.begin():
        account = await catalog_service.create_provider_account(
            session,
            context=context,
            provider_id=body.provider_id,
            name=body.name,
            external_account_id=body.external_account_id,
            secret_ref_id=body.secret_ref_id,
        )
    return _to_provider_account(account)


@router.get("/provider-accounts", response_model=Page[ProviderAccountRead])
async def list_provider_accounts(
    context: AdminContextDep,
    session: SessionDep,
    provider_id: ProviderIdQuery = None,
    limit: LimitQuery = DEFAULT_LIMIT,
    offset: OffsetQuery = 0,
) -> Page[ProviderAccountRead]:
    items, total = await catalog_service.list_provider_accounts(
        session, context=context, provider_id=provider_id, limit=limit, offset=offset
    )
    return Page(
        items=[_to_provider_account(a) for a in items],
        limit=limit,
        offset=offset,
        total=total,
    )


@router.get("/provider-accounts/{account_id}", response_model=ProviderAccountRead)
async def get_provider_account(
    account_id: ProviderAccountId, context: AdminContextDep, session: SessionDep
) -> ProviderAccountRead:
    return _to_provider_account(
        await catalog_service.get_provider_account(
            session, context=context, account_id=account_id
        )
    )


@router.patch("/provider-accounts/{account_id}", response_model=ProviderAccountRead)
async def update_provider_account(
    account_id: ProviderAccountId,
    body: ProviderAccountUpdate,
    context: AdminContextDep,
    session: SessionDep,
) -> ProviderAccountRead:
    fields = body.model_fields_set
    clear_external = "external_account_id" in fields and body.external_account_id is None
    clear_secret_ref = "secret_ref_id" in fields and body.secret_ref_id is None
    async with session.begin():
        account = await catalog_service.update_provider_account(
            session,
            context=context,
            account_id=account_id,
            name=body.name,
            external_account_id=None if clear_external else body.external_account_id,
            clear_external_account_id=clear_external,
            secret_ref_id=None if clear_secret_ref else body.secret_ref_id,
            clear_secret_ref_id=clear_secret_ref,
            is_active=body.is_active,
        )
    return _to_provider_account(account)


# --- endpoints ------------------------------------------------------------------


@router.post("/endpoints", response_model=EndpointRead, status_code=201)
async def create_endpoint(
    body: EndpointCreate, context: AdminContextDep, session: SessionDep
) -> EndpointRead:
    async with session.begin():
        endpoint = await catalog_service.create_endpoint(
            session,
            context=context,
            provider_account_id=body.provider_account_id,
            name=body.name,
            base_destination=body.base_destination,
            max_concurrency=body.max_concurrency,
        )
    return _to_endpoint(endpoint)


@router.get("/endpoints", response_model=Page[EndpointRead])
async def list_endpoints(
    context: AdminContextDep,
    session: SessionDep,
    provider_account_id: ProviderAccountIdQuery = None,
    limit: LimitQuery = DEFAULT_LIMIT,
    offset: OffsetQuery = 0,
) -> Page[EndpointRead]:
    items, total = await catalog_service.list_endpoints(
        session,
        context=context,
        provider_account_id=provider_account_id,
        limit=limit,
        offset=offset,
    )
    return Page(
        items=[_to_endpoint(e) for e in items], limit=limit, offset=offset, total=total
    )


@router.get("/endpoints/{endpoint_id}", response_model=EndpointRead)
async def get_endpoint(
    endpoint_id: EndpointId, context: AdminContextDep, session: SessionDep
) -> EndpointRead:
    return _to_endpoint(
        await catalog_service.get_endpoint(session, context=context, endpoint_id=endpoint_id)
    )


@router.patch("/endpoints/{endpoint_id}", response_model=EndpointRead)
async def update_endpoint(
    endpoint_id: EndpointId,
    body: EndpointUpdate,
    context: AdminContextDep,
    session: SessionDep,
) -> EndpointRead:
    async with session.begin():
        endpoint = await catalog_service.update_endpoint(
            session,
            context=context,
            endpoint_id=endpoint_id,
            name=body.name,
            base_destination=body.base_destination,
            max_concurrency=body.max_concurrency,
            is_active=body.is_active,
        )
    return _to_endpoint(endpoint)


# --- quota groups ---------------------------------------------------------------


@router.post("/quota-groups", response_model=QuotaGroupRead, status_code=201)
async def create_quota_group(
    body: QuotaGroupCreate, context: AdminContextDep, session: SessionDep
) -> QuotaGroupRead:
    async with session.begin():
        group = await catalog_service.create_quota_group(
            session,
            context=context,
            provider_account_id=body.provider_account_id,
            name=body.name,
            description=body.description,
        )
    return _to_quota_group(group)


@router.get("/quota-groups", response_model=Page[QuotaGroupRead])
async def list_quota_groups(
    context: AdminContextDep,
    session: SessionDep,
    provider_account_id: ProviderAccountIdQuery = None,
    limit: LimitQuery = DEFAULT_LIMIT,
    offset: OffsetQuery = 0,
) -> Page[QuotaGroupRead]:
    items, total = await catalog_service.list_quota_groups(
        session,
        context=context,
        provider_account_id=provider_account_id,
        limit=limit,
        offset=offset,
    )
    return Page(
        items=[_to_quota_group(g) for g in items], limit=limit, offset=offset, total=total
    )


@router.get("/quota-groups/{group_id}", response_model=QuotaGroupRead)
async def get_quota_group(
    group_id: QuotaGroupId, context: AdminContextDep, session: SessionDep
) -> QuotaGroupRead:
    return _to_quota_group(
        await catalog_service.get_quota_group(session, context=context, group_id=group_id)
    )


@router.patch("/quota-groups/{group_id}", response_model=QuotaGroupRead)
async def update_quota_group(
    group_id: QuotaGroupId,
    body: QuotaGroupUpdate,
    context: AdminContextDep,
    session: SessionDep,
) -> QuotaGroupRead:
    fields = body.model_fields_set
    clear_description = "description" in fields and body.description is None
    async with session.begin():
        group = await catalog_service.update_quota_group(
            session,
            context=context,
            group_id=group_id,
            name=body.name,
            description=None if clear_description else body.description,
            clear_description=clear_description,
        )
    return _to_quota_group(group)


# --- quota limits ---------------------------------------------------------------


@router.post("/quota-limits", response_model=QuotaLimitRead, status_code=201)
async def create_quota_limit(
    body: QuotaLimitCreate, context: AdminContextDep, session: SessionDep
) -> QuotaLimitRead:
    async with session.begin():
        limit = await catalog_service.create_quota_limit(
            session,
            context=context,
            quota_group_id=body.quota_group_id,
            metric=body.metric,
            limit_units=body.limit_units,
            window_seconds=body.window_seconds,
            enabled=body.enabled,
            name=body.name,
        )
    return _to_quota_limit(limit)


@router.get("/quota-limits", response_model=Page[QuotaLimitRead])
async def list_quota_limits(
    context: AdminContextDep,
    session: SessionDep,
    quota_group_id: QuotaGroupIdQuery = None,
    limit: LimitQuery = DEFAULT_LIMIT,
    offset: OffsetQuery = 0,
) -> Page[QuotaLimitRead]:
    items, total = await catalog_service.list_quota_limits(
        session,
        context=context,
        quota_group_id=quota_group_id,
        limit=limit,
        offset=offset,
    )
    return Page(
        items=[_to_quota_limit(item) for item in items],
        limit=limit,
        offset=offset,
        total=total,
    )


@router.get("/quota-limits/{limit_id}", response_model=QuotaLimitRead)
async def get_quota_limit(
    limit_id: QuotaLimitId, context: AdminContextDep, session: SessionDep
) -> QuotaLimitRead:
    return _to_quota_limit(
        await catalog_service.get_quota_limit(session, context=context, limit_id=limit_id)
    )


@router.patch("/quota-limits/{limit_id}", response_model=QuotaLimitRead)
async def update_quota_limit(
    limit_id: QuotaLimitId,
    body: QuotaLimitUpdate,
    context: AdminContextDep,
    session: SessionDep,
) -> QuotaLimitRead:
    fields = body.model_fields_set
    clear_name = "name" in fields and body.name is None
    async with session.begin():
        limit = await catalog_service.update_quota_limit(
            session,
            context=context,
            limit_id=limit_id,
            limit_units=body.limit_units,
            window_seconds=body.window_seconds,
            enabled=body.enabled,
            name=None if clear_name else body.name,
            clear_name=clear_name,
        )
    return _to_quota_limit(limit)


# --- model aliases --------------------------------------------------------------


@router.post("/model-aliases", response_model=ModelAliasRead, status_code=201)
async def create_model_alias(
    body: ModelAliasCreate, context: AdminContextDep, session: SessionDep
) -> ModelAliasRead:
    async with session.begin():
        alias = await catalog_service.create_model_alias(
            session,
            context=context,
            name=body.name,
            capabilities=body.capabilities,
            is_active=body.is_active,
        )
    return _to_model_alias(alias)


@router.get("/model-aliases", response_model=Page[ModelAliasRead])
async def list_model_aliases(
    context: AdminContextDep,
    session: SessionDep,
    limit: LimitQuery = DEFAULT_LIMIT,
    offset: OffsetQuery = 0,
) -> Page[ModelAliasRead]:
    items, total = await catalog_service.list_model_aliases(
        session, context=context, limit=limit, offset=offset
    )
    return Page(
        items=[_to_model_alias(a) for a in items], limit=limit, offset=offset, total=total
    )


@router.get("/model-aliases/{alias_id}", response_model=ModelAliasRead)
async def get_model_alias(
    alias_id: ModelAliasId, context: AdminContextDep, session: SessionDep
) -> ModelAliasRead:
    return _to_model_alias(
        await catalog_service.get_model_alias(session, context=context, alias_id=alias_id)
    )


@router.patch("/model-aliases/{alias_id}", response_model=ModelAliasRead)
async def update_model_alias(
    alias_id: ModelAliasId,
    body: ModelAliasUpdate,
    context: AdminContextDep,
    session: SessionDep,
) -> ModelAliasRead:
    async with session.begin():
        alias = await catalog_service.update_model_alias(
            session,
            context=context,
            alias_id=alias_id,
            name=body.name,
            capabilities=body.capabilities,
            is_active=body.is_active,
        )
    return _to_model_alias(alias)


# --- route bindings -------------------------------------------------------------


@router.post("/route-bindings", response_model=RouteBindingRead, status_code=201)
async def create_route_binding(
    body: RouteBindingCreate, context: AdminContextDep, session: SessionDep
) -> RouteBindingRead:
    async with session.begin():
        binding = await catalog_service.create_route_binding(
            session,
            context=context,
            model_alias_id=body.model_alias_id,
            endpoint_id=body.endpoint_id,
            provider_account_id=body.provider_account_id,
            upstream_model=body.upstream_model,
            quota_group_id=body.quota_group_id,
            default_output_tokens=body.default_output_tokens,
            is_active=body.is_active,
        )
    return _to_route_binding(binding)


@router.get("/route-bindings", response_model=Page[RouteBindingRead])
async def list_route_bindings(
    context: AdminContextDep,
    session: SessionDep,
    model_alias_id: ModelAliasIdQuery = None,
    provider_account_id: ProviderAccountIdQuery = None,
    limit: LimitQuery = DEFAULT_LIMIT,
    offset: OffsetQuery = 0,
) -> Page[RouteBindingRead]:
    items, total = await catalog_service.list_route_bindings(
        session,
        context=context,
        model_alias_id=model_alias_id,
        provider_account_id=provider_account_id,
        limit=limit,
        offset=offset,
    )
    return Page(
        items=[_to_route_binding(b) for b in items],
        limit=limit,
        offset=offset,
        total=total,
    )


@router.get("/route-bindings/{binding_id}", response_model=RouteBindingRead)
async def get_route_binding(
    binding_id: RouteBindingId, context: AdminContextDep, session: SessionDep
) -> RouteBindingRead:
    return _to_route_binding(
        await catalog_service.get_route_binding(
            session, context=context, binding_id=binding_id
        )
    )


@router.patch("/route-bindings/{binding_id}", response_model=RouteBindingRead)
async def update_route_binding(
    binding_id: RouteBindingId,
    body: RouteBindingUpdate,
    context: AdminContextDep,
    session: SessionDep,
) -> RouteBindingRead:
    fields = body.model_fields_set
    clear_upstream = "upstream_model" in fields and body.upstream_model is None
    clear_quota = "quota_group_id" in fields and body.quota_group_id is None
    clear_default = "default_output_tokens" in fields and body.default_output_tokens is None
    async with session.begin():
        binding = await catalog_service.update_route_binding(
            session,
            context=context,
            binding_id=binding_id,
            upstream_model=None if clear_upstream else body.upstream_model,
            clear_upstream_model=clear_upstream,
            quota_group_id=None if clear_quota else body.quota_group_id,
            clear_quota_group_id=clear_quota,
            default_output_tokens=None if clear_default else body.default_output_tokens,
            clear_default_output_tokens=clear_default,
            is_active=body.is_active,
        )
    return _to_route_binding(binding)


__all__ = ["router"]
