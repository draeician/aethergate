"""Admin control-plane identity tests (DB-gated): auth, RBAC, bootstrap, audit."""

from __future__ import annotations

import asyncio

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from aethergate.config import Settings
from aethergate.domain import entities as domain
from aethergate.domain.enums import (
    CredentialAudience,
    CredentialScope,
    PrincipalKind,
    ResourceScopeType,
    Role,
)
from aethergate.domain.ids import PrincipalId, ProjectId
from aethergate.errors import (
    AdminAuthenticationRequired,
    AdminAuthorizationError,
    AuthenticationRequired,
    BootstrapAlreadyCompleted,
    BootstrapTokenRejected,
    CredentialLifecycleError,
)
from aethergate.identity import admin as admin_service
from aethergate.identity import rbac
from aethergate.identity import service as identity_service
from aethergate.main import app
from aethergate.persistence import db as persistence_db
from aethergate.persistence import models, repository
from db_helpers import (
    TEST_DATABASE_URL,
    ensure_database,
    reset_schema,
    url_for_database,
)

pytestmark = pytest.mark.skipif(
    TEST_DATABASE_URL is None, reason="AETHERGATE_TEST_DATABASE_URL not set"
)

BOOTSTRAP_TOKEN = "test-bootstrap-secret-0123456789abcdefghij"
ADMIN_SCOPES = rbac.ADMIN_PERMISSIONS


def _settings(token: str = BOOTSTRAP_TOKEN) -> Settings:
    return Settings(
        database_url="postgresql+asyncpg://u:p@h/db",
        allow_inference_auth_bypass=False,
        bootstrap_token=token,
    )


@pytest.fixture
async def admin_engine():
    if not TEST_DATABASE_URL:
        pytest.skip("AETHERGATE_TEST_DATABASE_URL not set")
    url = url_for_database(TEST_DATABASE_URL, "aethergate_test_admin")
    await ensure_database(url)
    engine = create_async_engine(url)
    yield engine
    await engine.dispose()


@pytest.fixture
def admin_factory(admin_engine):
    return async_sessionmaker(admin_engine, expire_on_commit=False)


async def _system_admin_context(factory) -> domain.AdminRequestContext:
    """Bootstrap and authenticate, returning a durable system_admin context."""
    async with factory() as s:
        _, raw = await admin_service.bootstrap(s, BOOTSTRAP_TOKEN)
    async with factory() as s:
        async with s.begin():
            return await admin_service.authenticate_admin(s, raw)


# --- audience/scope coherence -------------------------------------------------


async def test_inference_audience_rejects_admin_scope(admin_engine, admin_factory):
    await reset_schema(admin_engine)
    async with admin_factory() as s:
        async with s.begin():
            project = await repository.create_project(
                s, domain.Project(id=ProjectId("p-coh-1"), name="p-coh-1")
            )
            principal = await repository.create_principal(
                s,
                domain.Principal(
                    id=PrincipalId("pr-coh-1"), project_id=project.id,
                    kind=PrincipalKind.SERVICE_ACCOUNT, name="svc",
                ),
            )
            with pytest.raises(CredentialLifecycleError):
                await identity_service.create_credential(
                    s, project_id=project.id, principal_id=principal.id, name="bad",
                    audience=CredentialAudience.INFERENCE,
                    scopes=(CredentialScope.ADMIN_CREDENTIALS_WRITE,),
                )


async def test_admin_audience_rejects_inference_scope(admin_engine, admin_factory):
    await reset_schema(admin_engine)
    async with admin_factory() as s:
        async with s.begin():
            project = await repository.create_project(
                s, domain.Project(id=ProjectId("p-coh-2"), name="p-coh-2")
            )
            principal = await repository.create_principal(
                s,
                domain.Principal(
                    id=PrincipalId("pr-coh-2"), project_id=project.id,
                    kind=PrincipalKind.SERVICE_ACCOUNT, name="svc",
                ),
            )
            with pytest.raises(CredentialLifecycleError):
                await identity_service.create_credential(
                    s, project_id=project.id, principal_id=principal.id, name="bad",
                    audience=CredentialAudience.ADMIN,
                    scopes=(CredentialScope.INFERENCE_INVOKE,),
                )


async def test_admin_audience_defaults_to_no_scopes(admin_engine, admin_factory):
    await reset_schema(admin_engine)
    async with admin_factory() as s:
        async with s.begin():
            project = await repository.create_project(
                s, domain.Project(id=ProjectId("p-coh-3"), name="p-coh-3")
            )
            principal = await repository.create_principal(
                s,
                domain.Principal(
                    id=PrincipalId("pr-coh-3"), project_id=project.id,
                    kind=PrincipalKind.SERVICE_ACCOUNT, name="svc",
                ),
            )
            credential, _ = await identity_service.create_credential(
                s, project_id=project.id, principal_id=principal.id, name="admin",
                audience=CredentialAudience.ADMIN,
            )
            assert credential.scopes == ()


# --- RBAC authorization -------------------------------------------------------


async def test_authorize_system_admin_deployment_wide(admin_engine, admin_factory, monkeypatch):
    await reset_schema(admin_engine)
    monkeypatch.setattr(admin_service, "get_settings", lambda: _settings())
    ctx = await _system_admin_context(admin_factory)
    assert Role.SYSTEM_ADMIN in ctx.roles
    admin_service.authorize_admin(
        ctx, CredentialScope.ADMIN_CREDENTIALS_READ, "project", ProjectId("any")
    )
    admin_service.authorize_admin(
        ctx, CredentialScope.ADMIN_CREDENTIALS_WRITE, "project", ProjectId("any")
    )


async def test_project_admin_isolated_to_own_project(admin_engine, admin_factory, monkeypatch):
    await reset_schema(admin_engine)
    monkeypatch.setattr(admin_service, "get_settings", lambda: _settings())
    sa_ctx = await _system_admin_context(admin_factory)
    async with admin_factory() as s:
        async with s.begin():
            proj_a = await repository.create_project(
                s, domain.Project(id=ProjectId("proj-a"), name="proj-a")
            )
            proj_b = await repository.create_project(
                s, domain.Project(id=ProjectId("proj-b"), name="proj-b")
            )
            pa = await repository.create_principal(
                s, domain.Principal(
                    id=PrincipalId("pa"), project_id=proj_a.id,
                    kind=PrincipalKind.SERVICE_ACCOUNT, name="pa",
                )
            )
            await admin_service.create_role_assignment(
                s, context=sa_ctx, principal_id=pa.id, role=Role.PROJECT_ADMIN,
                resource_scope_type=ResourceScopeType.PROJECT, resource_id=proj_a.id,
            )
            _, pa_raw = await admin_service.create_credential(
                s, context=sa_ctx, project_id=proj_a.id, principal_id=pa.id,
                name="pa-cred", audience=CredentialAudience.ADMIN, scopes=ADMIN_SCOPES,
                expires_at=None,
            )
    async with admin_factory() as s:
        async with s.begin():
            ctx = await admin_service.authenticate_admin(s, pa_raw)
    admin_service.authorize_admin(ctx, CredentialScope.ADMIN_CREDENTIALS_READ, "project", proj_a.id)
    admin_service.authorize_admin(
        ctx, CredentialScope.ADMIN_CREDENTIALS_WRITE, "project", proj_a.id
    )
    with pytest.raises(AdminAuthorizationError):
        admin_service.authorize_admin(
            ctx, CredentialScope.ADMIN_CREDENTIALS_READ, "project", proj_b.id
        )


async def test_project_viewer_read_only(admin_engine, admin_factory, monkeypatch):
    await reset_schema(admin_engine)
    monkeypatch.setattr(admin_service, "get_settings", lambda: _settings())
    sa_ctx = await _system_admin_context(admin_factory)
    async with admin_factory() as s:
        async with s.begin():
            proj_a = await repository.create_project(
                s, domain.Project(id=ProjectId("proj-a"), name="proj-a")
            )
            pv = await repository.create_principal(
                s, domain.Principal(
                    id=PrincipalId("pv"), project_id=proj_a.id,
                    kind=PrincipalKind.SERVICE_ACCOUNT, name="pv",
                )
            )
            await admin_service.create_role_assignment(
                s, context=sa_ctx, principal_id=pv.id, role=Role.PROJECT_VIEWER,
                resource_scope_type=ResourceScopeType.PROJECT, resource_id=proj_a.id,
            )
            _, pv_raw = await admin_service.create_credential(
                s, context=sa_ctx, project_id=proj_a.id, principal_id=pv.id,
                name="pv-cred", audience=CredentialAudience.ADMIN, scopes=ADMIN_SCOPES,
                expires_at=None,
            )
    async with admin_factory() as s:
        async with s.begin():
            ctx = await admin_service.authenticate_admin(s, pv_raw)
    admin_service.authorize_admin(ctx, CredentialScope.ADMIN_CREDENTIALS_READ, "project", proj_a.id)
    with pytest.raises(AdminAuthorizationError):
        admin_service.authorize_admin(
            ctx, CredentialScope.ADMIN_CREDENTIALS_WRITE, "project", proj_a.id
        )


async def test_duplicate_active_assignment_prevented(admin_engine, admin_factory, monkeypatch):
    await reset_schema(admin_engine)
    monkeypatch.setattr(admin_service, "get_settings", lambda: _settings())
    sa_ctx = await _system_admin_context(admin_factory)
    async with admin_factory() as s:
        async with s.begin():
            proj = await repository.create_project(
                s, domain.Project(id=ProjectId("proj-x"), name="proj-x")
            )
            principal = await repository.create_principal(
                s, domain.Principal(
                    id=PrincipalId("px"), project_id=proj.id,
                    kind=PrincipalKind.SERVICE_ACCOUNT, name="px",
                )
            )
            first = await admin_service.create_role_assignment(
                s, context=sa_ctx, principal_id=principal.id, role=Role.PROJECT_ADMIN,
                resource_scope_type=ResourceScopeType.PROJECT, resource_id=proj.id,
            )
            second = await admin_service.create_role_assignment(
                s, context=sa_ctx, principal_id=principal.id, role=Role.PROJECT_ADMIN,
                resource_scope_type=ResourceScopeType.PROJECT, resource_id=proj.id,
            )
            assert first.id == second.id


async def test_concurrent_duplicate_assignment_idempotent(
    admin_engine, admin_factory, monkeypatch
):
    await reset_schema(admin_engine)
    monkeypatch.setattr(admin_service, "get_settings", lambda: _settings())
    sa_ctx = await _system_admin_context(admin_factory)
    async with admin_factory() as s:
        async with s.begin():
            proj = await repository.create_project(
                s, domain.Project(id=ProjectId("proj-conc"), name="proj-conc")
            )
            principal = await repository.create_principal(
                s, domain.Principal(
                    id=PrincipalId("pconc"), project_id=proj.id,
                    kind=PrincipalKind.SERVICE_ACCOUNT, name="pconc",
                )
            )

    async def attempt() -> domain.RoleAssignment:
        async with admin_factory() as s:
            async with s.begin():
                return await admin_service.create_role_assignment(
                    s, context=sa_ctx, principal_id=principal.id,
                    role=Role.PROJECT_ADMIN,
                    resource_scope_type=ResourceScopeType.PROJECT,
                    resource_id=proj.id,
                )

    results = await asyncio.gather(*(attempt() for _ in range(6)))
    ids = {r.id for r in results}
    assert len(ids) == 1

    async with admin_factory() as s:
        async with s.begin():
            active = await repository.list_active_role_assignments(s, principal.id)
            events = await repository.list_audit_events(s)
    assert len(active) == 1
    created = [e for e in events if e.action == "role_assignment.created"]
    assert len(created) == 1
    assert created[0].resource_id == str(next(iter(ids)))


async def test_duplicate_insert_recovers_via_integrity_translation(
    admin_engine, admin_factory, monkeypatch
):
    """A duplicate that races past the pre-check is translated idempotently."""
    await reset_schema(admin_engine)
    monkeypatch.setattr(admin_service, "get_settings", lambda: _settings())
    sa_ctx = await _system_admin_context(admin_factory)
    async with admin_factory() as s:
        async with s.begin():
            proj = await repository.create_project(
                s, domain.Project(id=ProjectId("proj-dup2"), name="proj-dup2")
            )
            principal = await repository.create_principal(
                s, domain.Principal(
                    id=PrincipalId("pdup2"), project_id=proj.id,
                    kind=PrincipalKind.SERVICE_ACCOUNT, name="pdup2",
                )
            )
    async with admin_factory() as s:
        async with s.begin():
            winner = await admin_service.create_role_assignment(
                s, context=sa_ctx, principal_id=principal.id, role=Role.PROJECT_ADMIN,
                resource_scope_type=ResourceScopeType.PROJECT, resource_id=proj.id,
            )

    real_find = repository.find_active_equivalent_assignment
    forced = {"miss": True}

    async def _fake_find(session, principal_id, role, scope, resource_id):
        if forced["miss"]:
            forced["miss"] = False
            return None
        return await real_find(session, principal_id, role, scope, resource_id)

    monkeypatch.setattr(repository, "find_active_equivalent_assignment", _fake_find)

    async with admin_factory() as s:
        async with s.begin():
            result = await admin_service.create_role_assignment(
                s, context=sa_ctx, principal_id=principal.id, role=Role.PROJECT_ADMIN,
                resource_scope_type=ResourceScopeType.PROJECT, resource_id=proj.id,
            )
    assert result.id == winner.id

    async with admin_factory() as s:
        async with s.begin():
            active = await repository.list_active_role_assignments(s, principal.id)
            events = await repository.list_audit_events(s)
    assert len(active) == 1
    assert len([e for e in events if e.action == "role_assignment.created"]) == 1


async def test_unrelated_assignment_integrity_error_not_swallowed(
    admin_engine, admin_factory, monkeypatch
):
    await reset_schema(admin_engine)
    monkeypatch.setattr(admin_service, "get_settings", lambda: _settings())
    sa_ctx = await _system_admin_context(admin_factory)
    async with admin_factory() as s:
        async with s.begin():
            proj = await repository.create_project(
                s, domain.Project(id=ProjectId("proj-fk"), name="proj-fk")
            )

    ghost = domain.Principal(
        id=PrincipalId("ghost-principal"), project_id=proj.id,
        kind=PrincipalKind.SERVICE_ACCOUNT, name="ghost",
    )
    real_get_principal = repository.get_principal

    async def _fake_get_principal(session, principal_id):
        if str(principal_id) == "ghost-principal":
            return ghost
        return await real_get_principal(session, principal_id)

    monkeypatch.setattr(repository, "get_principal", _fake_get_principal)

    async with admin_factory() as s:
        async with s.begin():
            with pytest.raises(IntegrityError):
                await admin_service.create_role_assignment(
                    s, context=sa_ctx, principal_id=ghost.id, role=Role.PROJECT_ADMIN,
                    resource_scope_type=ResourceScopeType.PROJECT, resource_id=proj.id,
                )


async def test_revoked_assignment_denied(admin_engine, admin_factory, monkeypatch):
    await reset_schema(admin_engine)
    monkeypatch.setattr(admin_service, "get_settings", lambda: _settings())
    sa_ctx = await _system_admin_context(admin_factory)
    async with admin_factory() as s:
        async with s.begin():
            proj = await repository.create_project(
                s, domain.Project(id=ProjectId("proj-y"), name="proj-y")
            )
            principal = await repository.create_principal(
                s, domain.Principal(
                    id=PrincipalId("py"), project_id=proj.id,
                    kind=PrincipalKind.SERVICE_ACCOUNT, name="py",
                )
            )
            assignment = await admin_service.create_role_assignment(
                s, context=sa_ctx, principal_id=principal.id, role=Role.PROJECT_ADMIN,
                resource_scope_type=ResourceScopeType.PROJECT, resource_id=proj.id,
            )
            _, raw = await admin_service.create_credential(
                s, context=sa_ctx, project_id=proj.id, principal_id=principal.id,
                name="py-cred", audience=CredentialAudience.ADMIN, scopes=ADMIN_SCOPES,
                expires_at=None,
            )
    async with admin_factory() as s:
        async with s.begin():
            ctx = await admin_service.authenticate_admin(s, raw)
    admin_service.authorize_admin(ctx, CredentialScope.ADMIN_CREDENTIALS_READ, "project", proj.id)
    async with admin_factory() as s:
        async with s.begin():
            await admin_service.revoke_role_assignment(
                s, context=sa_ctx, assignment_id=assignment.id
            )
    async with admin_factory() as s:
        async with s.begin():
            ctx2 = await admin_service.authenticate_admin(s, raw)
    assert ctx2.roles == ()
    with pytest.raises(AdminAuthorizationError):
        admin_service.authorize_admin(
            ctx2, CredentialScope.ADMIN_CREDENTIALS_READ, "project", proj.id
        )


# --- admin credential authentication / audience separation --------------------


async def test_inference_credential_rejected_by_admin_auth(
    admin_engine, admin_factory, monkeypatch
):
    await reset_schema(admin_engine)
    monkeypatch.setattr(admin_service, "get_settings", lambda: _settings())
    async with admin_factory() as s:
        async with s.begin():
            project = await repository.create_project(
                s, domain.Project(id=ProjectId("proj-i"), name="proj-i")
            )
            principal = await repository.create_principal(
                s, domain.Principal(
                    id=PrincipalId("pri"), project_id=project.id,
                    kind=PrincipalKind.SERVICE_ACCOUNT, name="pri",
                )
            )
            _, inf_raw = await identity_service.create_credential(
                s, project_id=project.id, principal_id=principal.id, name="inf",
            )
    async with admin_factory() as s:
        async with s.begin():
            with pytest.raises(AdminAuthenticationRequired):
                await admin_service.authenticate_admin(s, inf_raw)


async def test_admin_credential_rejected_by_inference_auth(
    admin_engine, admin_factory, monkeypatch
):
    await reset_schema(admin_engine)
    monkeypatch.setattr(admin_service, "get_settings", lambda: _settings())
    async with admin_factory() as s:
        _, admin_raw = await admin_service.bootstrap(s, BOOTSTRAP_TOKEN)
    async with admin_factory() as s:
        async with s.begin():
            with pytest.raises(AuthenticationRequired):
                await identity_service.authenticate(s, admin_raw)


# --- bootstrap -----------------------------------------------------------------


async def test_bootstrap_missing_token_rejected(admin_engine, admin_factory, monkeypatch):
    await reset_schema(admin_engine)
    monkeypatch.setattr(admin_service, "get_settings", lambda: _settings())
    async with admin_factory() as s:
        with pytest.raises(BootstrapTokenRejected):
            await admin_service.bootstrap(s, None)


async def test_bootstrap_wrong_token_rejected(admin_engine, admin_factory, monkeypatch):
    await reset_schema(admin_engine)
    monkeypatch.setattr(admin_service, "get_settings", lambda: _settings())
    async with admin_factory() as s:
        with pytest.raises(BootstrapTokenRejected):
            await admin_service.bootstrap(s, "wrong-token")


async def test_bootstrap_correct_then_second_use_rejected(
    admin_engine, admin_factory, monkeypatch
):
    await reset_schema(admin_engine)
    monkeypatch.setattr(admin_service, "get_settings", lambda: _settings())
    async with admin_factory() as s:
        credential, raw = await admin_service.bootstrap(s, BOOTSTRAP_TOKEN)
        assert credential.audience == CredentialAudience.ADMIN
        assert credential.scopes == ADMIN_SCOPES
    async with admin_factory() as s:
        with pytest.raises(BootstrapAlreadyCompleted):
            await admin_service.bootstrap(s, BOOTSTRAP_TOKEN)


async def test_bootstrap_survives_new_session(admin_engine, admin_factory, monkeypatch):
    await reset_schema(admin_engine)
    monkeypatch.setattr(admin_service, "get_settings", lambda: _settings())
    async with admin_factory() as s:
        await admin_service.bootstrap(s, BOOTSTRAP_TOKEN)
    async with admin_factory() as s:
        async with s.begin():
            state = await admin_service.get_bootstrap_status(s)
            assert state is not None and state.completed is True


async def test_bootstrap_raw_secrets_never_persisted(admin_engine, admin_factory, monkeypatch):
    await reset_schema(admin_engine)
    monkeypatch.setattr(admin_service, "get_settings", lambda: _settings())
    async with admin_factory() as s:
        _, raw = await admin_service.bootstrap(s, BOOTSTRAP_TOKEN)
    async with admin_factory() as s:
        async with s.begin():
            rows = (await s.execute(select(models.ApiCredential))).scalars().all()
            states = (await s.execute(select(models.BootstrapState))).scalars().all()
            assert raw not in [r.key_hash or "" for r in rows]
            assert raw not in [r.key_prefix or "" for r in rows]
            for st in states:
                assert BOOTSTRAP_TOKEN not in str(st.__dict__)


async def test_concurrent_bootstrap_exactly_one_succeeds(
    admin_engine, admin_factory, monkeypatch
):
    await reset_schema(admin_engine)
    monkeypatch.setattr(admin_service, "get_settings", lambda: _settings())

    async def run() -> bool:
        async with admin_factory() as s:
            try:
                await admin_service.bootstrap(s, BOOTSTRAP_TOKEN)
                return True
            except BootstrapAlreadyCompleted:
                return False

    results = await asyncio.gather(run(), run())
    assert results.count(True) == 1


# --- audit events --------------------------------------------------------------


async def test_audit_events_for_bootstrap_role_credential(
    admin_engine, admin_factory, monkeypatch
):
    await reset_schema(admin_engine)
    monkeypatch.setattr(admin_service, "get_settings", lambda: _settings())
    sa_ctx = await _system_admin_context(admin_factory)
    async with admin_factory() as s:
        async with s.begin():
            proj = await repository.create_project(
                s, domain.Project(id=ProjectId("proj-aud"), name="proj-aud")
            )
            principal = await repository.create_principal(
                s, domain.Principal(
                    id=PrincipalId("paud"), project_id=proj.id,
                    kind=PrincipalKind.SERVICE_ACCOUNT, name="paud",
                )
            )
            assignment = await admin_service.create_role_assignment(
                s, context=sa_ctx, principal_id=principal.id, role=Role.PROJECT_ADMIN,
                resource_scope_type=ResourceScopeType.PROJECT, resource_id=proj.id,
            )
            cred2, raw = await admin_service.create_credential(
                s, context=sa_ctx, project_id=proj.id, principal_id=principal.id,
                name="aud-cred", audience=CredentialAudience.ADMIN, scopes=ADMIN_SCOPES,
                expires_at=None,
            )
            rotated, _ = await admin_service.rotate_credential(
                s, context=sa_ctx, credential_id=cred2.id
            )
            await admin_service.revoke_credential(
                s, context=sa_ctx, credential_id=rotated.id
            )
            await admin_service.revoke_role_assignment(
                s, context=sa_ctx, assignment_id=assignment.id
            )
    async with admin_factory() as s:
        async with s.begin():
            events = await repository.list_audit_events(s)
    actions = {e.action for e in events}
    assert "bootstrap.completed" in actions
    assert "role_assignment.created" in actions
    assert "role_assignment.revoked" in actions
    assert "credential.created" in actions
    assert "credential.rotated" in actions
    assert "credential.revoked" in actions
    for e in events:
        blob = str(e.metadata) + e.resource_id + e.action
        assert BOOTSTRAP_TOKEN not in blob
        assert raw not in blob


# --- HTTP surface --------------------------------------------------------------


@pytest.fixture
async def admin_http(admin_engine, admin_factory, monkeypatch):
    await reset_schema(admin_engine)
    monkeypatch.setattr(admin_service, "get_settings", lambda: _settings())
    monkeypatch.setattr("aethergate.api.admin.get_session_factory", lambda: admin_factory)

    async def _test_session():
        async with admin_factory() as s:
            yield s

    app.dependency_overrides[persistence_db.get_session] = _test_session
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, admin_factory
    app.dependency_overrides.clear()
    await reset_schema(admin_engine)


async def test_http_bootstrap_then_whoami(admin_http):
    client, _ = admin_http
    resp = await client.post("/admin/v1/bootstrap")
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "bootstrap_token_rejected"

    resp = await client.post(
        "/admin/v1/bootstrap",
        headers={"Authorization": f"Bearer {BOOTSTRAP_TOKEN}"},
    )
    assert resp.status_code == 201
    body = resp.json()
    raw = body["raw_key"]
    assert raw.startswith("agk_")

    resp = await client.post(
        "/admin/v1/bootstrap",
        headers={"Authorization": f"Bearer {BOOTSTRAP_TOKEN}"},
    )
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "bootstrap_already_completed"

    resp = await client.get(
        "/admin/v1/whoami", headers={"Authorization": f"Bearer {raw}"}
    )
    assert resp.status_code == 200
    who = resp.json()
    assert who["audience"] == "admin"
    assert "system_admin" in who["roles"]
    assert "raw_key" not in who and "key_hash" not in who


async def test_http_inference_key_rejected_on_admin(admin_http):
    client, factory = admin_http
    async with factory() as s:
        async with s.begin():
            project = await repository.create_project(
                s, domain.Project(id=ProjectId("proj-http"), name="proj-http")
            )
            principal = await repository.create_principal(
                s, domain.Principal(
                    id=PrincipalId("prh"), project_id=project.id,
                    kind=PrincipalKind.SERVICE_ACCOUNT, name="prh",
                )
            )
            _, inf_raw = await identity_service.create_credential(
                s, project_id=project.id, principal_id=principal.id, name="inf-http",
            )
    resp = await client.get(
        "/admin/v1/whoami", headers={"Authorization": f"Bearer {inf_raw}"}
    )
    assert resp.status_code == 401


async def test_http_admin_key_rejected_on_inference(admin_http):
    client, factory = admin_http
    async with factory() as s:
        _, admin_raw = await admin_service.bootstrap(s, BOOTSTRAP_TOKEN)
    resp = await client.get(
        "/v1/models", headers={"Authorization": f"Bearer {admin_raw}"}
    )
    assert resp.status_code == 401


async def test_http_credential_lifecycle_and_rbac(admin_http):
    client, factory = admin_http
    async with factory() as s:
        sa_cred, sa_raw = await admin_service.bootstrap(s, BOOTSTRAP_TOKEN)
    default_project = sa_cred.project_id
    async with factory() as s:
        async with s.begin():
            proj_b = await repository.create_project(
                s, domain.Project(id=ProjectId("proj-b"), name="proj-b")
            )
            pa = await repository.create_principal(
                s, domain.Principal(
                    id=PrincipalId("pa-h"), project_id=default_project,
                    kind=PrincipalKind.SERVICE_ACCOUNT, name="pa-h",
                )
            )
            sa_ctx = await admin_service.authenticate_admin(s, sa_raw)
            await admin_service.create_role_assignment(
                s, context=sa_ctx, principal_id=pa.id, role=Role.PROJECT_ADMIN,
                resource_scope_type=ResourceScopeType.PROJECT,
                resource_id=default_project,
            )
            _, pa_raw = await admin_service.create_credential(
                s, context=sa_ctx, project_id=default_project, principal_id=pa.id,
                name="pa-h-cred", audience=CredentialAudience.ADMIN, scopes=ADMIN_SCOPES,
                expires_at=None,
            )

    hdr = {"Authorization": f"Bearer {sa_raw}"}
    resp = await client.get(
        f"/admin/v1/projects/{default_project}/credentials", headers=hdr
    )
    assert resp.status_code == 200

    pa_hdr = {"Authorization": f"Bearer {pa_raw}"}
    resp = await client.get(
        f"/admin/v1/projects/{default_project}/credentials", headers=pa_hdr
    )
    assert resp.status_code == 200

    resp = await client.get(
        f"/admin/v1/projects/{proj_b.id}/credentials", headers=pa_hdr
    )
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "not_found"
