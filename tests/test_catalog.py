"""Model-alias route resolution (DB-gated)."""

from __future__ import annotations

import pytest

from aethergate.catalog.service import resolve_model_alias
from aethergate.domain import entities as domain
from aethergate.domain.enums import Capability
from aethergate.domain.ids import (
    EndpointId,
    ModelAliasId,
    ProviderAccountId,
    ProviderId,
    RouteBindingId,
)
from aethergate.errors import (
    AmbiguousRoute,
    ModelAliasNotFound,
    ResourceInactive,
    RouteUnresolved,
)
from aethergate.persistence import repository


async def _seed_base(session):
    await repository.create_provider(
        session,
        domain.Provider(
            id=ProviderId("prov-1"),
            kind="openai",
            name="OpenAI",
            capabilities=(Capability.TEXT,),
        ),
    )
    await repository.create_provider_account(
        session,
        domain.ProviderAccount(
            id=ProviderAccountId("acct-1"), provider_id=ProviderId("prov-1"), name="acct"
        ),
    )
    await repository.create_endpoint(
        session,
        domain.Endpoint(
            id=EndpointId("ep-1"),
            provider_account_id=ProviderAccountId("acct-1"),
            name="primary",
            base_destination="https://api.example.com",
        ),
    )
    await repository.create_model_alias(
        session,
        domain.ModelAlias(
            id=ModelAliasId("alias-1"), name="gpt-4", capabilities=(Capability.TEXT,)
        ),
    )
    await repository.create_route_binding(
        session,
        domain.RouteBinding(
            id=RouteBindingId("rb-1"),
            model_alias_id=ModelAliasId("alias-1"),
            endpoint_id=EndpointId("ep-1"),
            provider_account_id=ProviderAccountId("acct-1"),
            upstream_model="gpt-4-upstream",
        ),
    )


async def test_resolve_active_alias(session):
    await _seed_base(session)
    resolved = await resolve_model_alias(session, "gpt-4")
    assert resolved.alias.name == "gpt-4"
    assert resolved.provider.id == ProviderId("prov-1")
    assert resolved.endpoint.base_destination == "https://api.example.com"
    assert resolved.upstream_model == "gpt-4-upstream"


async def test_resolve_rejects_missing_upstream_model(session):
    await _seed_base(session)
    await repository.create_model_alias(
        session, domain.ModelAlias(id=ModelAliasId("alias-noup"), name="no-upstream")
    )
    await repository.create_route_binding(
        session,
        domain.RouteBinding(
            id=RouteBindingId("rb-noup"),
            model_alias_id=ModelAliasId("alias-noup"),
            endpoint_id=EndpointId("ep-1"),
            provider_account_id=ProviderAccountId("acct-1"),
            upstream_model=None,
        ),
    )
    with pytest.raises(RouteUnresolved):
        await resolve_model_alias(session, "no-upstream")


async def test_unknown_alias_rejected(session):
    await _seed_base(session)
    with pytest.raises(ModelAliasNotFound):
        await resolve_model_alias(session, "does-not-exist")


async def test_inactive_alias_rejected(session):
    await _seed_base(session)
    await repository.create_model_alias(
        session,
        domain.ModelAlias(id=ModelAliasId("alias-off"), name="off", is_active=False),
    )
    with pytest.raises(ResourceInactive):
        await resolve_model_alias(session, "off")


async def test_inactive_endpoint_rejected(session):
    await _seed_base(session)
    await repository.create_endpoint(
        session,
        domain.Endpoint(
            id=EndpointId("ep-off"),
            provider_account_id=ProviderAccountId("acct-1"),
            name="off",
            base_destination="https://off.example.com",
            is_active=False,
        ),
    )
    await repository.create_model_alias(
        session, domain.ModelAlias(id=ModelAliasId("alias-2"), name="m2")
    )
    await repository.create_route_binding(
        session,
        domain.RouteBinding(
            id=RouteBindingId("rb-m2"),
            model_alias_id=ModelAliasId("alias-2"),
            endpoint_id=EndpointId("ep-off"),
            provider_account_id=ProviderAccountId("acct-1"),
            upstream_model="m2-upstream",
        ),
    )
    with pytest.raises(ResourceInactive):
        await resolve_model_alias(session, "m2")


async def test_inactive_provider_rejected(session):
    await _seed_base(session)
    await repository.create_provider(
        session,
        domain.Provider(
            id=ProviderId("prov-off"), kind="openai", name="Off", is_active=False
        ),
    )
    await repository.create_provider_account(
        session,
        domain.ProviderAccount(
            id=ProviderAccountId("acct-off"),
            provider_id=ProviderId("prov-off"),
            name="acct-off",
        ),
    )
    await repository.create_endpoint(
        session,
        domain.Endpoint(
            id=EndpointId("ep-off2"),
            provider_account_id=ProviderAccountId("acct-off"),
            name="off",
            base_destination="https://off.example.com",
        ),
    )
    await repository.create_model_alias(
        session, domain.ModelAlias(id=ModelAliasId("alias-3"), name="m3")
    )
    await repository.create_route_binding(
        session,
        domain.RouteBinding(
            id=RouteBindingId("rb-m3"),
            model_alias_id=ModelAliasId("alias-3"),
            endpoint_id=EndpointId("ep-off2"),
            provider_account_id=ProviderAccountId("acct-off"),
            upstream_model="m3-upstream",
        ),
    )
    with pytest.raises(ResourceInactive):
        await resolve_model_alias(session, "m3")


async def test_ambiguous_multi_route_rejected(session):
    await _seed_base(session)
    await repository.create_route_binding(
        session,
        domain.RouteBinding(
            id=RouteBindingId("rb-2"),
            model_alias_id=ModelAliasId("alias-1"),
            endpoint_id=EndpointId("ep-1"),
            provider_account_id=ProviderAccountId("acct-1"),
            upstream_model="gpt-4-upstream-2",
        ),
    )
    with pytest.raises(AmbiguousRoute):
        await resolve_model_alias(session, "gpt-4")
