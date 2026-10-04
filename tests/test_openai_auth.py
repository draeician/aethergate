"""OpenAI-compatible surface authentication tests (DB-gated, real Bearer auth).

Runs with the development auth bypass disabled so a genuine ``agk_...`` Bearer
credential must authenticate inference. Verifies structured 401 responses,
``WWW-Authenticate: Bearer``, attribution, and the dev-bypass escape hatch.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from aethergate.adapters.base import ChatRequest, CompletionResult, StreamChunk, Usage
from aethergate.api import deps as api_deps
from aethergate.config import Settings
from aethergate.domain import entities as domain
from aethergate.domain.enums import Capability, CredentialAudience, CredentialScope, PrincipalKind
from aethergate.domain.ids import (
    EndpointId,
    ModelAliasId,
    PrincipalId,
    ProjectId,
    ProviderAccountId,
    ProviderId,
    RouteBindingId,
)
from aethergate.egress import DestinationPolicy
from aethergate.encryption import QueueEncryptor
from aethergate.errors import AuthenticationRequired
from aethergate.identity import service as identity_service
from aethergate.inference.service import InferenceService
from aethergate.main import app
from aethergate.persistence import db as persistence_db
from aethergate.persistence import models, repository
from aethergate.scheduler.service import ClaimedWork, SchedulingService
from aethergate.secrets import EnvSecretResolver
from db_helpers import TEST_DATABASE_URL, reset_schema

pytestmark = pytest.mark.skipif(
    TEST_DATABASE_URL is None, reason="AETHERGATE_TEST_DATABASE_URL not set"
)


class MockAdapter:
    async def complete(self, request: ChatRequest, secret: str | None) -> CompletionResult:
        return CompletionResult(
            content="Hello from mock",
            finish_reason="stop",
            usage=Usage(prompt_tokens=1, completion_tokens=2, total_tokens=3),
            upstream_request_id="up-1",
        )

    async def stream(self, request: ChatRequest, secret: str | None) -> AsyncIterator[StreamChunk]:
        yield StreamChunk(content="Hello", finish_reason=None, usage=None)
        yield StreamChunk(
            content=" from mock", finish_reason="stop",
            usage=Usage(prompt_tokens=1, completion_tokens=2, total_tokens=3),
        )


async def _seed_route(session) -> None:
    await repository.create_provider(
        session,
        domain.Provider(id=ProviderId("prov-1"), kind="ollama", name="ollama",
                        capabilities=(Capability.TEXT,)),
    )
    await repository.create_provider_account(
        session,
        domain.ProviderAccount(id=ProviderAccountId("acct-1"), provider_id=ProviderId("prov-1"),
                               name="acct"),
    )
    await repository.create_endpoint(
        session,
        domain.Endpoint(id=EndpointId("ep-1"), provider_account_id=ProviderAccountId("acct-1"),
                        name="ep", base_destination="http://192.168.22.50:11434",
                        max_concurrency=2),
    )
    await repository.create_model_alias(
        session,
        domain.ModelAlias(
            id=ModelAliasId("alias-1"), name="gpt-4", capabilities=(Capability.TEXT,)
        ),
    )
    await repository.create_route_binding(
        session,
        domain.RouteBinding(id=RouteBindingId("rb-1"), model_alias_id=ModelAliasId("alias-1"),
                            endpoint_id=EndpointId("ep-1"),
                            provider_account_id=ProviderAccountId("acct-1"),
                            upstream_model="qwen3.8-2b-distill:Q6_K"),
    )


async def _drain(service: SchedulingService, worker_id: str) -> None:
    while True:
        outcome = await service.claim_and_reserve(worker_id)
        if isinstance(outcome, ClaimedWork):
            if outcome.stream:
                await service.run_stream(outcome)
            else:
                await service.run_complete(outcome)
        else:
            await asyncio.sleep(service.poll_interval)


@pytest.fixture
async def auth_client(sched_engine, monkeypatch):
    await reset_schema(sched_engine)
    raw_key = None
    credential_id = None
    project_id = None
    principal_id = None
    async with async_sessionmaker(sched_engine, expire_on_commit=False)() as session:
        async with session.begin():
            await _seed_route(session)
            project = await repository.create_project(
                session, domain.Project(id=ProjectId("proj-1"), name="proj-1"),
            )
            principal = await repository.create_principal(
                session,
                domain.Principal(id=PrincipalId("prin-1"), project_id=project.id,
                                 kind=PrincipalKind.SERVICE_ACCOUNT, name="svc"),
            )
            credential, raw_key = await identity_service.create_credential(
                session, project_id=project.id, principal_id=principal.id, name="key-1",
            )
            credential_id = credential.id
            project_id = project.id
            principal_id = principal.id

    mock = MockAdapter()
    encryptor = QueueEncryptor(QueueEncryptor.generate_key())
    settings = Settings(
        database_url="postgresql+asyncpg://u:p@h/db",
        allow_inference_auth_bypass=False,
        upstream_allowlist="192.168.22.50",
    )
    factory = async_sessionmaker(sched_engine, expire_on_commit=False)
    inference = InferenceService(
        EnvSecretResolver(),
        DestinationPolicy(settings.upstream_allowlist_hosts),
        adapter_factory=lambda kind: mock,
    )
    service = SchedulingService(
        encryptor=encryptor, inference_service=inference,
        session_factory=factory, settings=settings,
    )

    monkeypatch.setattr("aethergate.api.deps.get_settings", lambda: settings)
    monkeypatch.setattr("aethergate.api.deps.get_session_factory", lambda: factory)
    app.dependency_overrides[api_deps.scheduler_service] = lambda: service

    async def _test_session():
        async with factory() as s:
            yield s

    app.dependency_overrides[persistence_db.get_session] = _test_session

    worker = asyncio.create_task(_drain(service, "w-test"))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, mock, raw_key, credential_id, project_id, principal_id
    worker.cancel()
    try:
        await worker
    except asyncio.CancelledError:
        pass
    app.dependency_overrides.clear()
    await reset_schema(sched_engine)


async def test_missing_key_returns_401(auth_client):
    client, *_ = auth_client
    resp = await client.post(
        "/v1/chat/completions",
        json={"model": "gpt-4", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "not_authenticated"
    assert resp.headers.get("www-authenticate") == "Bearer"


async def test_invalid_key_returns_401(auth_client):
    client, *_ = auth_client
    bogus = "agk_deadbeef_bogus"
    resp = await client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {bogus}"},
        json={"model": "gpt-4", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "not_authenticated"
    assert bogus not in resp.text


async def test_wrong_scheme_and_malformed_401(auth_client):
    client, *_ = auth_client
    for header in (
        "Basic abcdef",
        "Bearer",
        "Bearer  double",
        "Bearer agk_abc_xyz extra",
    ):
        resp = await client.post(
            "/v1/chat/completions",
            headers={"Authorization": header},
            json={"model": "gpt-4", "messages": [{"role": "user", "content": "hi"}]},
        )
        assert resp.status_code == 401


async def test_valid_key_non_stream_and_attribution(auth_client, sched_engine):
    client, _mock, raw_key, credential_id, project_id, principal_id = auth_client
    resp = await client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {raw_key}"},
        json={"model": "gpt-4", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert resp.status_code == 200
    assert resp.json()["choices"][0]["message"]["content"] == "Hello from mock"

    # attribution persisted to the durable request record (usage records are the
    # accounting workstream's concern and require a price policy to materialise)
    async with async_sessionmaker(sched_engine, expire_on_commit=False)() as session:
        req = (await session.execute(select(models.InferenceRequest))).scalars().first()
        assert req is not None
        assert req.project_id == str(project_id)
        assert req.principal_id == str(principal_id)
        assert req.api_credential_id == str(credential_id)


async def test_valid_key_stream(auth_client):
    client, _mock, raw_key, *_ = auth_client
    async with client.stream(
        "POST",
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {raw_key}"},
        json={"model": "gpt-4", "messages": [{"role": "user", "content": "hi"}], "stream": True},
    ) as resp:
        assert resp.status_code == 200
        body = b""
        async for chunk in resp.aiter_bytes():
            body += chunk
    assert "data: [DONE]" in body.decode()


async def test_revoked_key_returns_401(auth_client, sched_engine):
    client, _mock, raw_key, credential_id, *_ = auth_client
    async with async_sessionmaker(sched_engine, expire_on_commit=False)() as session:
        async with session.begin():
            await identity_service.revoke_credential(session, credential_id)
    resp = await client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {raw_key}"},
        json={"model": "gpt-4", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert resp.status_code == 401


# --- dev bypass escape hatch -------------------------------------------------


class _FakeHeaders:
    def __init__(self, values: list[str]) -> None:
        self._values = values

    def getlist(self, key: str) -> list[str]:
        return self._values if key.lower() == "authorization" else []


class _FakeRequest:
    def __init__(self, values: list[str]) -> None:
        self.headers = _FakeHeaders(values)


async def test_dev_bypass_absent_header_resolves_dev_identity(sched_engine, monkeypatch):
    await reset_schema(sched_engine)
    settings = Settings(
        database_url="postgresql+asyncpg://u:p@h/db", allow_inference_auth_bypass=True
    )
    monkeypatch.setattr("aethergate.api.deps.get_settings", lambda: settings)
    monkeypatch.setattr(
        "aethergate.api.deps.get_session_factory",
        lambda: async_sessionmaker(sched_engine, expire_on_commit=False),
    )
    ctx = await api_deps.request_context(_FakeRequest([]))
    assert ctx.audience == CredentialAudience.INFERENCE
    assert ctx.scopes == (CredentialScope.INFERENCE_INVOKE,)
    await reset_schema(sched_engine)


async def test_dev_bypass_invalid_key_never_falls_through(sched_engine, monkeypatch):
    await reset_schema(sched_engine)
    settings = Settings(
        database_url="postgresql+asyncpg://u:p@h/db", allow_inference_auth_bypass=True
    )
    monkeypatch.setattr("aethergate.api.deps.get_settings", lambda: settings)
    monkeypatch.setattr(
        "aethergate.api.deps.get_session_factory",
        lambda: async_sessionmaker(sched_engine, expire_on_commit=False),
    )
    with pytest.raises(AuthenticationRequired):
        await api_deps.request_context(_FakeRequest(["Bearer agk_deadbeef_bogus"]))
    await reset_schema(sched_engine)
