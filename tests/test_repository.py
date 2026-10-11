"""Repository create/read behavior for the core persisted entities (DB-gated)."""

from __future__ import annotations

from datetime import UTC, datetime

from aethergate.domain import entities as domain
from aethergate.domain.enums import Capability, PrincipalKind
from aethergate.domain.ids import (
    ModelAliasId,
    PrincipalId,
    ProjectId,
    ProviderId,
    SecretRefId,
)
from aethergate.persistence import repository


async def test_project_round_trip(session):
    created = await repository.create_project(
        session, domain.Project(id=ProjectId("proj-1"), name="Example")
    )
    assert created.name == "Example"

    fetched = await repository.get_project(session, ProjectId("proj-1"))
    assert fetched is not None
    assert fetched.id == ProjectId("proj-1")
    assert fetched.is_active is True


async def test_provider_round_trip(session):
    await repository.create_provider(
        session,
        domain.Provider(
            id=ProviderId("prov-1"),
            kind="openai",
            name="OpenAI",
            capabilities=(Capability.TEXT, Capability.EMBEDDING),
        ),
    )
    fetched = await repository.get_provider(session, ProviderId("prov-1"))
    assert fetched is not None
    assert fetched.capabilities == (Capability.TEXT, Capability.EMBEDDING)


async def test_secret_ref_stores_metadata_only(session):
    created = await repository.create_secret_ref(
        session,
        domain.SecretRef(
            id=SecretRefId("sr-1"),
            name="openai-key",
            created_at=datetime(2026, 10, 3, tzinfo=UTC),
        ),
    )
    assert created.created_at is not None
    fetched = await repository.get_secret_ref(session, SecretRefId("sr-1"))
    assert fetched is not None
    assert fetched.name == "openai-key"


async def test_model_alias_round_trip(session):
    await repository.create_model_alias(
        session,
        domain.ModelAlias(
            id=ModelAliasId("alias-1"),
            name="gpt-4",
            capabilities=(Capability.TEXT,),
        ),
    )
    fetched = await repository.get_model_alias_by_name(session, "gpt-4")
    assert fetched is not None
    assert fetched.id == ModelAliasId("alias-1")


async def test_principal_round_trip(session):
    await repository.create_project(
        session, domain.Project(id=ProjectId("proj-1"), name="Example")
    )
    await repository.create_principal(
        session,
        domain.Principal(
            id=PrincipalId("user-1"),
            project_id=ProjectId("proj-1"),
            kind=PrincipalKind.USER,
            name="alice",
        ),
    )
    fetched = await repository.get_principal(session, PrincipalId("user-1"))
    assert fetched is not None
    assert fetched.kind is PrincipalKind.USER
