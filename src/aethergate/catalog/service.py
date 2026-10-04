"""Resolve an active public model alias to its active route, endpoint, account, provider."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from aethergate.domain import entities as domain
from aethergate.errors import (
    AmbiguousRoute,
    ModelAliasNotFound,
    ResourceInactive,
    RouteUnresolved,
)
from aethergate.persistence import repository


@dataclass(frozen=True)
class ResolvedRoute:
    """A fully resolved, active route from alias to provider."""

    alias: domain.ModelAlias
    route_binding: domain.RouteBinding
    endpoint: domain.Endpoint
    provider_account: domain.ProviderAccount
    provider: domain.Provider
    upstream_model: str


async def resolve_model_alias(session: AsyncSession, alias_name: str) -> ResolvedRoute:
    """Resolve ``alias_name`` to its single active route.

    Raises:
        ModelAliasNotFound: unknown alias.
        ResourceInactive: an inactive alias/route/endpoint/account/provider.
        AmbiguousRoute: multiple active routes with no selection policy.
        RouteUnresolved: the single active route has no upstream model configured.
    """
    alias = await repository.get_model_alias_by_name(session, alias_name)
    if alias is None:
        raise ModelAliasNotFound(alias_name)
    if not alias.is_active:
        raise ResourceInactive("model alias", alias_name)

    bindings = await repository.list_route_bindings(session, alias.id)
    active_bindings = [b for b in bindings if b.is_active]
    if not active_bindings:
        raise ResourceInactive("route", str(alias.id))
    if len(active_bindings) > 1:
        raise AmbiguousRoute(alias_name, [str(b.id) for b in active_bindings])

    binding = active_bindings[0]
    if not binding.upstream_model or not binding.upstream_model.strip():
        raise RouteUnresolved(str(binding.id))

    endpoint = await repository.get_endpoint(session, binding.endpoint_id)
    if endpoint is None or not endpoint.is_active:
        raise ResourceInactive("endpoint", str(binding.endpoint_id))

    account = await repository.get_provider_account(session, binding.provider_account_id)
    if account is None or not account.is_active:
        raise ResourceInactive("provider account", str(binding.provider_account_id))

    provider = await repository.get_provider(session, account.provider_id)
    if provider is None or not provider.is_active:
        raise ResourceInactive("provider", str(account.provider_id))

    return ResolvedRoute(
        alias=alias,
        route_binding=binding,
        endpoint=endpoint,
        provider_account=account,
        provider=provider,
        upstream_model=binding.upstream_model,
    )
