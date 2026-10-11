"""Identity phase 1 tests: API-key generation/verification, Bearer parsing,
authentication, credential lifecycle, and pre-dispatch authorization.

Offline tests (no DB) cover key format/entropy and strict Bearer parsing. The
DB-gated tests cover authentication resolution, lifecycle operations, and
queued-work revalidation.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from aethergate.adapters.base import (
    ChatRequest,
    CompletionResult,
    GenerationParams,
    Message,
    StreamChunk,
    Usage,
)
from aethergate.config import Settings
from aethergate.domain import entities as domain
from aethergate.domain.entities import RequestContext
from aethergate.domain.enums import (
    Capability,
    CredentialAudience,
    CredentialScope,
    PrincipalKind,
)
from aethergate.domain.ids import (
    ApiCredentialId,
    EndpointId,
    ModelAliasId,
    PrincipalId,
    ProjectId,
    ProviderAccountId,
    ProviderId,
    RouteBindingId,
)
from aethergate.errors import AuthenticationRequired, CredentialLifecycleError
from aethergate.identity import keys as identity_keys
from aethergate.identity import service as identity_service
from aethergate.persistence import models, repository
from db_helpers import TEST_DATABASE_URL, reset_schema

pytestmark = pytest.mark.skipif(
    TEST_DATABASE_URL is None, reason="AETHERGATE_TEST_DATABASE_URL not set"
)


# --- offline: key generation / hashing ---------------------------------------


def test_key_generation_format_and_entropy():
    seen = set()
    for _ in range(200):
        raw, prefix = identity_keys.generate_api_key()
        assert raw.startswith("agk_")
        assert prefix.startswith("agk_")
        # secret material carries >= 256 bits (urlsafe base64 of 32 bytes >= 43 chars)
        assert len(raw) >= len("agk_") + 8 + 43
        assert raw not in seen
        seen.add(raw)
        assert raw.startswith(prefix + "_")


def test_hash_raw_key_is_deterministic_sha256():
    raw, _ = identity_keys.generate_api_key()
    assert identity_keys.hash_raw_key(raw) == identity_keys.hash_raw_key(raw)
    digest = identity_keys.hash_raw_key(raw)
    assert len(digest) == 64
    assert int(digest, 16)
    assert digest != raw


def test_raw_key_is_never_the_verifier():
    raw, _ = identity_keys.generate_api_key()
    assert identity_keys.hash_raw_key(raw) != raw


def test_api_credential_read_dto_exposes_no_secret():
    from aethergate.contracts.admin_v1 import ApiCredentialRead

    fields = set(ApiCredentialRead.model_fields)
    for forbidden in ("key_hash", "raw_key", "secret", "verifier", "token"):
        assert forbidden not in fields
    assert "key_prefix" in fields
    assert "audience" in fields
    assert "scopes" in fields


# --- offline: strict Bearer parsing ------------------------------------------


def _parse(values: list[str]):
    return identity_service.parse_bearer_token(values)


def test_bearer_parsing_valid_and_invalid():
    assert _parse(["Bearer agk_abc_xyz"]) == "agk_abc_xyz"
    assert _parse(["bearer agk_abc_xyz"]) == "agk_abc_xyz"
    for bad in (
        [],  # missing
        ["Basic abc"],  # wrong scheme
        ["Bearer"],  # no token
        ["Bearer "],  # empty token
        ["Bearer  two"],  # double space / leading junk
        ["Bearer agk_abc_xyz extra"],  # trailing junk
        ["Bearer agk_abc_xyz "],  # trailing whitespace
        [" agk_abc_xyz"],  # no scheme
        ["BearerX agk_abc_xyz"],  # scheme prefix not "bearer"
        ["Bearer agk_abc_xyz", "Bearer agk_abc_xyz"],  # ambiguous multiple
    ):
        with pytest.raises(AuthenticationRequired):
            _parse(bad)


def test_auth_failures_do_not_emit_raw_material(caplog):
    import logging

    from aethergate.api.openai_errors import to_openai_error

    material = "FAKE_SECRET_MATERIAL_FOR_CANARY_ONLY"
    raw = f"agk_fakeprefix_{material}"
    with caplog.at_level(logging.DEBUG, logger="aethergate"):
        for bad in (["Bearer"], ["Basic abc"], ["Bearer  two"], ["Bearer agk_abc_xyz extra"]):
            with pytest.raises(AuthenticationRequired):
                _parse(bad)
        # a well-formed token parses but must never be logged either
        assert _parse([f"Bearer {raw}"]) == raw
    # the structured 401 response is a fixed message; no token/hash echoes back
    resp = to_openai_error(AuthenticationRequired(), "req-123")
    assert material not in caplog.text
    assert raw not in caplog.text
    assert "Authorization" not in caplog.text
    assert material not in resp.body.decode()
    assert raw not in resp.body.decode()


# --- DB-gated: credential lifecycle + authentication -------------------------


async def _create_identity(
    session,
    *,
    audience=CredentialAudience.INFERENCE,
    scopes=None,
):
    project = await repository.create_project(
        session, domain.Project(id=ProjectId("proj-id"), name="project-id")
    )
    principal = await repository.create_principal(
        session,
        domain.Principal(
            id=PrincipalId("prin-id"),
            project_id=project.id,
            kind=PrincipalKind.SERVICE_ACCOUNT,
            name="svc",
        ),
    )
    credential, raw = await identity_service.create_credential(
        session,
        project_id=project.id,
        principal_id=principal.id,
        name="cred-1",
        audience=audience,
        scopes=scopes,
    )
    return project, principal, credential, raw


async def test_create_returns_raw_once_and_persists_only_verifier(session):
    _, _, credential, raw = await _create_identity(session)
    assert credential.key_hash == identity_keys.hash_raw_key(raw)
    assert credential.key_prefix is not None
    assert credential.key_prefix.startswith("agk_")
    # raw key is not present on the metadata object
    assert raw not in credential.model_dump(mode="json").values()


async def test_authenticate_valid_key_resolves_context(session):
    project, principal, credential, raw = await _create_identity(session)
    ctx = await identity_service.authenticate(session, raw)
    assert ctx.project_id == project.id
    assert ctx.principal_id == principal.id
    assert ctx.api_credential_id == credential.id
    assert ctx.audience == CredentialAudience.INFERENCE
    assert ctx.scopes == (CredentialScope.INFERENCE_INVOKE,)


async def test_authenticate_invalid_key_fails(session):
    await _create_identity(session)
    with pytest.raises(AuthenticationRequired):
        await identity_service.authenticate(session, "agk_deadbeef_notarealkey")


async def test_authenticate_revoked_credential_fails(session):
    _, _, credential, raw = await _create_identity(session)
    await identity_service.revoke_credential(session, credential.id)
    with pytest.raises(AuthenticationRequired):
        await identity_service.authenticate(session, raw)


async def test_authenticate_expired_credential_fails(session):
    project = await repository.create_project(
        session, domain.Project(id=ProjectId("proj-x"), name="project-x")
    )
    principal = await repository.create_principal(
        session,
        domain.Principal(
            id=PrincipalId("prin-x"), project_id=project.id,
            kind=PrincipalKind.SERVICE_ACCOUNT, name="svc-x",
        ),
    )
    credential, raw = await identity_service.create_credential(
        session,
        project_id=project.id,
        principal_id=principal.id,
        name="expired",
        expires_at=datetime.now(UTC) - timedelta(seconds=1),
    )
    assert credential.expires_at is not None
    with pytest.raises(AuthenticationRequired):
        await identity_service.authenticate(session, raw)


async def test_authenticate_inactive_project_fails(session):
    project, _, _, raw = await _create_identity(session)
    await repository.set_project_active(session, project.id, False)
    with pytest.raises(AuthenticationRequired):
        await identity_service.authenticate(session, raw)


async def test_authenticate_inactive_principal_fails(session):
    _, principal, _, raw = await _create_identity(session)
    await repository.set_principal_active(session, principal.id, False)
    with pytest.raises(AuthenticationRequired):
        await identity_service.authenticate(session, raw)


async def test_authenticate_wrong_audience_fails(session):
    _, _, _, raw = await _create_identity(session, audience=CredentialAudience.ADMIN)
    with pytest.raises(AuthenticationRequired):
        await identity_service.authenticate(session, raw)


async def test_authenticate_missing_scope_fails(session):
    _, _, _, raw = await _create_identity(session, scopes=())
    with pytest.raises(AuthenticationRequired):
        await identity_service.authenticate(session, raw)


async def test_authorize_for_dispatch_blocks_inactive_credential(session):
    _, _, credential, _ = await _create_identity(session)
    await identity_service.revoke_credential(session, credential.id)
    with pytest.raises(AuthenticationRequired):
        await identity_service.authorize_for_dispatch(
            session, credential.project_id, credential.principal_id, credential.id
        )


async def test_rotate_revokes_old_and_creates_new(session):
    project, principal, credential, old_raw = await _create_identity(session)
    new_credential, new_raw = await identity_service.rotate_credential(
        session, credential.id
    )
    assert new_credential.id != credential.id
    assert new_credential.project_id == project.id
    assert new_credential.principal_id == principal.id
    assert new_raw != old_raw

    # old key now fails; new key works
    with pytest.raises(AuthenticationRequired):
        await identity_service.authenticate(session, old_raw)
    ctx = await identity_service.authenticate(session, new_raw)
    assert ctx.api_credential_id == new_credential.id

    # old metadata retained as revoked history
    old = await repository.get_api_credential(session, credential.id)
    assert old is not None
    assert old.revoked_at is not None
    assert old.is_active is False


async def test_revoke_is_idempotent_and_durable(session):
    _, _, credential, _ = await _create_identity(session)
    first = await identity_service.revoke_credential(session, credential.id)
    second = await identity_service.revoke_credential(session, credential.id)
    assert first is not None and second is not None
    assert first.revoked_at is not None
    assert second.is_active is False


async def test_list_credentials_has_no_secret(session):
    _, _, _, _ = await _create_identity(session)
    creds = await identity_service.list_credentials(session, ProjectId("proj-id"))
    assert len(creds) == 1
    assert creds[0].key_hash is not None
    # the entity carries the hash (internal), but never the raw key


# --- DB-gated: lifecycle hardening (AGV2-012V) -------------------------------


async def test_create_rejects_missing_project(session):
    project = await repository.create_project(
        session, domain.Project(id=ProjectId("proj-real"), name="proj-real")
    )
    principal = await repository.create_principal(
        session,
        domain.Principal(
            id=PrincipalId("prin-real"), project_id=project.id,
            kind=PrincipalKind.SERVICE_ACCOUNT, name="svc-real",
        ),
    )
    with pytest.raises(CredentialLifecycleError):
        await identity_service.create_credential(
            session, project_id=ProjectId("proj-missing"),
            principal_id=principal.id, name="nope",
        )


async def test_create_rejects_missing_principal(session):
    project = await repository.create_project(
        session, domain.Project(id=ProjectId("proj-noprin"), name="proj-noprin")
    )
    with pytest.raises(CredentialLifecycleError):
        await identity_service.create_credential(
            session, project_id=project.id, principal_id=PrincipalId("prin-missing"),
            name="nope",
        )


async def test_create_rejects_cross_project_principal(session):
    project_a = await repository.create_project(
        session, domain.Project(id=ProjectId("proj-a"), name="proj-a")
    )
    project_b = await repository.create_project(
        session, domain.Project(id=ProjectId("proj-b"), name="proj-b")
    )
    principal_b = await repository.create_principal(
        session,
        domain.Principal(
            id=PrincipalId("prin-b"), project_id=project_b.id,
            kind=PrincipalKind.SERVICE_ACCOUNT, name="svc-b",
        ),
    )
    with pytest.raises(CredentialLifecycleError):
        await identity_service.create_credential(
            session, project_id=project_a.id, principal_id=principal_b.id, name="nope",
        )


async def test_create_rejects_inactive_project(session):
    project = await repository.create_project(
        session, domain.Project(id=ProjectId("proj-off"), name="proj-off")
    )
    principal = await repository.create_principal(
        session,
        domain.Principal(
            id=PrincipalId("prin-off"), project_id=project.id,
            kind=PrincipalKind.SERVICE_ACCOUNT, name="svc-off",
        ),
    )
    await repository.set_project_active(session, project.id, False)
    with pytest.raises(CredentialLifecycleError):
        await identity_service.create_credential(
            session, project_id=project.id, principal_id=principal.id, name="nope",
        )


async def test_create_rejects_inactive_principal(session):
    project = await repository.create_project(
        session, domain.Project(id=ProjectId("proj-poff"), name="proj-poff")
    )
    principal = await repository.create_principal(
        session,
        domain.Principal(
            id=PrincipalId("prin-poff"), project_id=project.id,
            kind=PrincipalKind.SERVICE_ACCOUNT, name="svc-poff",
        ),
    )
    await repository.set_principal_active(session, principal.id, False)
    with pytest.raises(CredentialLifecycleError):
        await identity_service.create_credential(
            session, project_id=project.id, principal_id=principal.id, name="nope",
        )


async def test_create_no_raw_key_on_rejection(session):
    project = await repository.create_project(
        session, domain.Project(id=ProjectId("proj-rj"), name="proj-rj")
    )
    with pytest.raises(CredentialLifecycleError):
        await identity_service.create_credential(
            session, project_id=project.id, principal_id=PrincipalId("prin-missing"),
            name="nope",
        )
    # nothing was persisted for the rejected request
    creds = await identity_service.list_credentials(session, project.id)
    assert creds == []


async def test_rotate_rejects_missing_credential(session):
    with pytest.raises(CredentialLifecycleError):
        await identity_service.rotate_credential(session, ApiCredentialId("cred-missing"))


async def test_rotate_rejects_revoked_credential(session):
    _, _, credential, _ = await _create_identity(session)
    await identity_service.revoke_credential(session, credential.id)
    with pytest.raises(CredentialLifecycleError):
        await identity_service.rotate_credential(session, credential.id)


async def test_rotate_rejects_inactive_credential(session):
    _, _, credential, _ = await _create_identity(session)
    row = await session.get(models.ApiCredential, str(credential.id))
    row.is_active = False
    await session.flush()
    with pytest.raises(CredentialLifecycleError):
        await identity_service.rotate_credential(session, credential.id)


async def test_rotate_rejects_expired_credential(session):
    project = await repository.create_project(
        session, domain.Project(id=ProjectId("proj-exp"), name="proj-exp")
    )
    principal = await repository.create_principal(
        session,
        domain.Principal(
            id=PrincipalId("prin-exp"), project_id=project.id,
            kind=PrincipalKind.SERVICE_ACCOUNT, name="svc-exp",
        ),
    )
    credential, _ = await identity_service.create_credential(
        session, project_id=project.id, principal_id=principal.id, name="exp",
        expires_at=datetime.now(UTC) - timedelta(seconds=1),
    )
    with pytest.raises(CredentialLifecycleError):
        await identity_service.rotate_credential(session, credential.id)


async def test_rotate_rejects_inactive_project(session):
    project, _, credential, _ = await _create_identity(session)
    await repository.set_project_active(session, project.id, False)
    with pytest.raises(CredentialLifecycleError):
        await identity_service.rotate_credential(session, credential.id)


async def test_rotate_rejects_inactive_principal(session):
    _, principal, credential, _ = await _create_identity(session)
    await repository.set_principal_active(session, principal.id, False)
    with pytest.raises(CredentialLifecycleError):
        await identity_service.rotate_credential(session, credential.id)


async def test_failed_rotation_creates_no_replacement(session):
    _, _, credential, _ = await _create_identity(session)
    await identity_service.revoke_credential(session, credential.id)
    with pytest.raises(CredentialLifecycleError):
        await identity_service.rotate_credential(session, credential.id)
    creds = await identity_service.list_credentials(session, ProjectId("proj-id"))
    assert len(creds) == 1


async def test_repeated_revoke_preserves_timestamp(session):
    _, _, credential, _ = await _create_identity(session)
    first = await identity_service.revoke_credential(session, credential.id)
    second = await identity_service.revoke_credential(session, credential.id)
    assert first is not None and first.revoked_at is not None
    assert second is not None and second.revoked_at is not None
    assert second.revoked_at == first.revoked_at
    assert second.is_active is False


async def test_admin_audience_has_no_inference_scope(session):
    _, _, credential, _ = await _create_identity(session, audience=CredentialAudience.ADMIN)
    assert credential.scopes == ()


async def test_incompatible_audience_scope_rejected(session):
    with pytest.raises(CredentialLifecycleError):
        await _create_identity(
            session, audience=CredentialAudience.ADMIN,
            scopes=(CredentialScope.INFERENCE_INVOKE,),
        )


async def test_inference_default_scope_is_invoke(session):
    _, _, credential, _ = await _create_identity(session)
    assert credential.scopes == (CredentialScope.INFERENCE_INVOKE,)


# --- DB-gated: queued revocation blocks dispatch -----------------------------


class MockAdapter:
    async def complete(self, request: ChatRequest, secret: str | None) -> CompletionResult:
        return CompletionResult(
            content="ok", finish_reason="stop",
            usage=Usage(prompt_tokens=1, completion_tokens=1, total_tokens=2),
            upstream_request_id="up-1",
        )

    async def stream(self, request: ChatRequest, secret: str | None):
        yield StreamChunk(content="ok", finish_reason="stop", usage=None)


async def _seed_route(session) -> None:
    await repository.create_provider(
        session,
        domain.Provider(
            id=ProviderId("prov-1"), kind="ollama", name="ollama",
            capabilities=(Capability.TEXT,),
        ),
    )
    await repository.create_provider_account(
        session,
        domain.ProviderAccount(
            id=ProviderAccountId("acct-1"), provider_id=ProviderId("prov-1"), name="acct",
        ),
    )
    await repository.create_endpoint(
        session,
        domain.Endpoint(
            id=EndpointId("ep-1"), provider_account_id=ProviderAccountId("acct-1"),
            name="ep", base_destination="http://192.168.22.50:11434", max_concurrency=1,
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
            id=RouteBindingId("rb-1"), model_alias_id=ModelAliasId("alias-1"),
            endpoint_id=EndpointId("ep-1"), provider_account_id=ProviderAccountId("acct-1"),
            upstream_model="qwen3.8-2b-distill:Q6_K",
        ),
    )


async def test_queued_revocation_blocks_dispatch_without_reservations(sched_engine):
    from aethergate.egress import DestinationPolicy
    from aethergate.encryption import QueueEncryptor
    from aethergate.inference.service import InferenceService
    from aethergate.scheduler.service import SchedulingService
    from aethergate.secrets import EnvSecretResolver

    await reset_schema(sched_engine)
    async with async_sessionmaker(sched_engine, expire_on_commit=False)() as session:
        async with session.begin():
            await _seed_route(session)
            project, principal, credential, _raw = await _create_identity(session)

    settings = Settings(
        database_url="postgresql+asyncpg://u:p@h/db",
        upstream_allowlist="192.168.22.50",
    )
    mock = MockAdapter()
    inference = InferenceService(
        EnvSecretResolver(),
        DestinationPolicy(settings.upstream_allowlist_hosts),
        adapter_factory=lambda kind: mock,
    )
    factory = async_sessionmaker(sched_engine, expire_on_commit=False)
    service = SchedulingService(
        encryptor=QueueEncryptor(QueueEncryptor.generate_key()),
        inference_service=inference,
        session_factory=factory,
        settings=settings,
    )
    context = RequestContext(
        project_id=project.id,
        principal_id=principal.id,
        api_credential_id=credential.id,
        audience=CredentialAudience.INFERENCE,
        scopes=(CredentialScope.INFERENCE_INVOKE,),
    )
    enq = await service.admit_and_enqueue(
        alias_name="gpt-4",
        messages=[Message(role="user", content="hi")],
        params=GenerationParams(),
        stream=False,
        context=context,
    )

    # Revoke the credential while the request is still queued.
    async with factory() as session:
        async with session.begin():
            await identity_service.revoke_credential(session, credential.id)

    outcome = await service.claim_and_reserve("w1")
    assert outcome == "processed"

    async with factory() as session:
        req = (await session.execute(
            select(models.InferenceRequest).where(models.InferenceRequest.id == enq.request_id)
        )).scalar_one()
        assert req.state == "failed"
        assert req.error_code == "authorization_failed"
        assert req.price_snapshot_id is None
        # no reservations / attempts survived
        attempts = (await session.execute(select(models.ExecutionAttempt))).scalars().all()
        assert len(attempts) == 0
        reservations = (await session.execute(
            select(models.Reservation).where(models.Reservation.request_id == enq.request_id)
        )).scalars().all()
        assert len(reservations) == 0
        usage = (await session.execute(select(models.UsageRecord))).scalars().all()
        assert len(usage) == 0
