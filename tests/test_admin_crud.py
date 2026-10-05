"""Admin CRUD, cross-project non-enumeration, RBAC delegation, and pagination.

DB-gated. Exercises the /admin/v1 project/principal/role-assignment/credential
CRUD surface and the centralized service-layer authorization, with emphasis on
the anti-enumeration boundary introduced in AGV2-014.
"""

from __future__ import annotations

import httpx
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from aethergate.config import Settings
from aethergate.domain.enums import (
    CredentialAudience,
    CredentialScope,
    PrincipalKind,
    ResourceScopeType,
    Role,
)
from aethergate.errors import AdminAuthorizationError, AdminResourceNotFound
from aethergate.identity import admin as admin_service
from aethergate.identity import rbac
from aethergate.identity.authorization import (
    RESOURCE_CREDENTIAL,
    RESOURCE_PRINCIPAL,
    RESOURCE_ROLE_ASSIGNMENT,
    authorized_project_ids,
    resolve_admin_resource,
)
from aethergate.main import app
from aethergate.persistence import db as persistence_db
from aethergate.persistence import repository
from db_helpers import (
    TEST_DATABASE_URL,
    ensure_database,
    reset_schema,
    url_for_database,
)

pytestmark = pytest.mark.skipif(
    TEST_DATABASE_URL is None, reason="AETHERGATE_TEST_DATABASE_URL not set"
)

BOOTSTRAP_TOKEN = "test-bootstrap-secret"
ADMIN_SCOPE_VALUES = [s.value for s in rbac.ADMIN_PERMISSIONS]
ADMIN_SCOPES = rbac.ADMIN_PERMISSIONS


def _settings() -> Settings:
    return Settings(
        database_url="postgresql+asyncpg://u:p@h/db",
        allow_inference_auth_bypass=False,
        bootstrap_token=BOOTSTRAP_TOKEN,
    )


@pytest.fixture
async def crud_engine():
    if not TEST_DATABASE_URL:
        pytest.skip("AETHERGATE_TEST_DATABASE_URL not set")
    url = url_for_database(TEST_DATABASE_URL, "aethergate_test_crud")
    await ensure_database(url)
    engine = create_async_engine(url)
    yield engine
    await engine.dispose()


@pytest.fixture
def crud_factory(crud_engine):
    return async_sessionmaker(crud_engine, expire_on_commit=False)


@pytest.fixture
async def crud_http(crud_engine, crud_factory, monkeypatch):
    await reset_schema(crud_engine)
    monkeypatch.setattr(admin_service, "get_settings", _settings)
    monkeypatch.setattr("aethergate.api.admin.get_session_factory", lambda: crud_factory)

    async def _test_session():
        async with crud_factory() as s:
            yield s

    app.dependency_overrides[persistence_db.get_session] = _test_session
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, crud_factory
    app.dependency_overrides.clear()
    await reset_schema(crud_engine)


# --- helpers -------------------------------------------------------------------


async def _http_bootstrap(client) -> str:
    resp = await client.post(
        "/admin/v1/bootstrap",
        headers={"Authorization": f"Bearer {BOOTSTRAP_TOKEN}"},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["raw_key"]


async def _http_create_project(client, hdr, name: str) -> dict:
    resp = await client.post("/admin/v1/projects", json={"name": name}, headers=hdr)
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _http_make_principal(
    client, sa_hdr, project_id: str, principal_name: str, role: str
) -> str:
    """Create a principal in ``project_id``, grant ``role``, return a raw key."""
    resp = await client.post(
        f"/admin/v1/projects/{project_id}/principals",
        json={"kind": "service_account", "name": principal_name},
        headers=sa_hdr,
    )
    assert resp.status_code == 201, resp.text
    pid = resp.json()["id"]

    resp = await client.post(
        "/admin/v1/role-assignments",
        json={
            "principal_id": pid,
            "role": role,
            "resource_scope_type": "project",
            "resource_id": project_id,
        },
        headers=sa_hdr,
    )
    assert resp.status_code == 201, resp.text

    resp = await client.post(
        "/admin/v1/credentials",
        json={
            "project_id": project_id,
            "principal_id": pid,
            "name": f"{principal_name}-cred",
            "audience": "admin",
            "scopes": ADMIN_SCOPE_VALUES,
        },
        headers=sa_hdr,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["raw_key"]


# --- resource-scope resolver ---------------------------------------------------


async def test_resource_scope_resolver_maps_entities(crud_engine, crud_factory, monkeypatch):
    await reset_schema(crud_engine)
    monkeypatch.setattr(admin_service, "get_settings", _settings)
    async with crud_factory() as s:
        _, sa_raw = await admin_service.bootstrap(s, BOOTSTRAP_TOKEN)
    async with crud_factory() as s:
        async with s.begin():
            sa_ctx = await admin_service.authenticate_admin(s, sa_raw)
            proj = await admin_service.create_project(s, context=sa_ctx, name="resolver-p")
            principal = await admin_service.create_principal(
                s, context=sa_ctx, project_id=proj.id,
                kind=PrincipalKind.SERVICE_ACCOUNT, name="resolver-pr",
            )
            cred, _ = await admin_service.create_credential(
                s, context=sa_ctx, project_id=proj.id, principal_id=principal.id,
                name="resolver-c", audience=CredentialAudience.ADMIN,
                scopes=ADMIN_SCOPES, expires_at=None,
            )
            assignment = await admin_service.create_role_assignment(
                s, context=sa_ctx, principal_id=principal.id, role=Role.PROJECT_ADMIN,
                resource_scope_type=ResourceScopeType.PROJECT, resource_id=proj.id,
            )

            # system_admin resolves all three by opaque ID.
            assert (await resolve_admin_resource(
                s, sa_ctx, CredentialScope.ADMIN_CREDENTIALS_READ,
                RESOURCE_CREDENTIAL, str(cred.id),
            )).id == cred.id
            assert (await resolve_admin_resource(
                s, sa_ctx, CredentialScope.ADMIN_PRINCIPALS_READ,
                RESOURCE_PRINCIPAL, str(principal.id),
            )).id == principal.id
            assert (await resolve_admin_resource(
                s, sa_ctx, CredentialScope.ADMIN_PRINCIPALS_READ,
                RESOURCE_ROLE_ASSIGNMENT, str(assignment.id),
            )).id == assignment.id
            assert authorized_project_ids(sa_ctx) is None


async def test_project_scoped_resolver_non_enumeration(crud_engine, crud_factory, monkeypatch):
    await reset_schema(crud_engine)
    monkeypatch.setattr(admin_service, "get_settings", _settings)
    async with crud_factory() as s:
        _, sa_raw = await admin_service.bootstrap(s, BOOTSTRAP_TOKEN)
    async with crud_factory() as s:
        async with s.begin():
            sa_ctx = await admin_service.authenticate_admin(s, sa_raw)
            proj_a = await admin_service.create_project(s, context=sa_ctx, name="res-a")
            proj_b = await admin_service.create_project(s, context=sa_ctx, name="res-b")
            pa = await admin_service.create_principal(
                s, context=sa_ctx, project_id=proj_a.id,
                kind=PrincipalKind.SERVICE_ACCOUNT, name="res-pa",
            )
            await admin_service.create_role_assignment(
                s, context=sa_ctx, principal_id=pa.id, role=Role.PROJECT_ADMIN,
                resource_scope_type=ResourceScopeType.PROJECT, resource_id=proj_a.id,
            )
            _, pa_raw = await admin_service.create_credential(
                s, context=sa_ctx, project_id=proj_a.id, principal_id=pa.id,
                name="res-pa-c", audience=CredentialAudience.ADMIN,
                scopes=ADMIN_SCOPES, expires_at=None,
            )
            pb = await admin_service.create_principal(
                s, context=sa_ctx, project_id=proj_b.id,
                kind=PrincipalKind.SERVICE_ACCOUNT, name="res-pb",
            )
            cred_b, _ = await admin_service.create_credential(
                s, context=sa_ctx, project_id=proj_b.id, principal_id=pb.id,
                name="res-pb-c", audience=CredentialAudience.ADMIN,
                scopes=ADMIN_SCOPES, expires_at=None,
            )
    async with crud_factory() as s:
        async with s.begin():
            pa_ctx = await admin_service.authenticate_admin(s, pa_raw)
            assert authorized_project_ids(pa_ctx) == {proj_a.id}

            # Cross-project credential resolves as not-found (non-enumerating).
            with pytest.raises(AdminResourceNotFound):
                await resolve_admin_resource(
                    s, pa_ctx, CredentialScope.ADMIN_CREDENTIALS_READ,
                    RESOURCE_CREDENTIAL, str(cred_b.id),
                )
            with pytest.raises(AdminResourceNotFound):
                await resolve_admin_resource(
                    s, pa_ctx, CredentialScope.ADMIN_CREDENTIALS_READ,
                    RESOURCE_CREDENTIAL, "nonexistent",
                )
            # In-scope write on a project_admin succeeds at resolution.
            assert (await resolve_admin_resource(
                s, pa_ctx, CredentialScope.ADMIN_PRINCIPALS_READ,
                RESOURCE_PRINCIPAL, str(pa.id),
            )).id == pa.id


# --- service-layer escalation defense ------------------------------------------


async def test_service_layer_rejects_escalation(crud_engine, crud_factory, monkeypatch):
    await reset_schema(crud_engine)
    monkeypatch.setattr(admin_service, "get_settings", _settings)
    async with crud_factory() as s:
        _, sa_raw = await admin_service.bootstrap(s, BOOTSTRAP_TOKEN)
    async with crud_factory() as s:
        async with s.begin():
            sa_ctx = await admin_service.authenticate_admin(s, sa_raw)
            proj_a = await admin_service.create_project(s, context=sa_ctx, name="esc-a")
            proj_b = await admin_service.create_project(s, context=sa_ctx, name="esc-b")
            pa = await admin_service.create_principal(
                s, context=sa_ctx, project_id=proj_a.id,
                kind=PrincipalKind.SERVICE_ACCOUNT, name="esc-pa",
            )
            await admin_service.create_role_assignment(
                s, context=sa_ctx, principal_id=pa.id, role=Role.PROJECT_ADMIN,
                resource_scope_type=ResourceScopeType.PROJECT, resource_id=proj_a.id,
            )
            _, pa_raw = await admin_service.create_credential(
                s, context=sa_ctx, project_id=proj_a.id, principal_id=pa.id,
                name="esc-pa-c", audience=CredentialAudience.ADMIN,
                scopes=ADMIN_SCOPES, expires_at=None,
            )
            target = await admin_service.create_principal(
                s, context=sa_ctx, project_id=proj_a.id,
                kind=PrincipalKind.SERVICE_ACCOUNT, name="esc-target",
            )
    async with crud_factory() as s:
        async with s.begin():
            pa_ctx = await admin_service.authenticate_admin(s, pa_raw)
            # A project_admin cannot grant system_admin, even directly at the
            # service layer (no router involved).
            with pytest.raises(AdminAuthorizationError):
                await admin_service.create_role_assignment(
                    s, context=pa_ctx, principal_id=target.id, role=Role.SYSTEM_ADMIN,
                    resource_scope_type=ResourceScopeType.DEPLOYMENT, resource_id=None,
                )
            # A project_admin cannot grant any role outside its own project.
            with pytest.raises(AdminAuthorizationError):
                await admin_service.create_role_assignment(
                    s, context=pa_ctx, principal_id=target.id, role=Role.PROJECT_ADMIN,
                    resource_scope_type=ResourceScopeType.PROJECT, resource_id=proj_b.id,
                )


# --- project CRUD --------------------------------------------------------------


async def test_project_crud_and_pagination(crud_http):
    client, _ = crud_http
    sa_raw = await _http_bootstrap(client)
    sa_hdr = {"Authorization": f"Bearer {sa_raw}"}

    created = []
    for i in range(3):
        p = await _http_create_project(client, sa_hdr, f"proj-{i}")
        created.append(p)

    resp = await client.get("/admin/v1/projects?limit=2&offset=0", headers=sa_hdr)
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["items"]) == 2
    # 3 created + 1 bootstrap "default" project.
    assert body["limit"] == 2 and body["offset"] == 0 and body["total"] == 4

    first = created[0]
    resp = await client.get(f"/admin/v1/projects/{first['id']}", headers=sa_hdr)
    assert resp.status_code == 200 and resp.json()["name"] == "proj-0"

    resp = await client.patch(
        f"/admin/v1/projects/{first['id']}",
        json={"name": "proj-0-renamed", "is_active": False},
        headers=sa_hdr,
    )
    assert resp.status_code == 200
    assert resp.json()["name"] == "proj-0-renamed"
    assert resp.json()["is_active"] is False


async def test_project_scoped_access_isolated(crud_http):
    client, _ = crud_http
    sa_raw = await _http_bootstrap(client)
    sa_hdr = {"Authorization": f"Bearer {sa_raw}"}

    a = await _http_create_project(client, sa_hdr, "iso-a")
    b = await _http_create_project(client, sa_hdr, "iso-b")

    pa_raw = await _http_make_principal(client, sa_hdr, a["id"], "iso-pa", "project_admin")
    pa_hdr = {"Authorization": f"Bearer {pa_raw}"}

    # project_admin(A) can read and update A.
    resp = await client.get(f"/admin/v1/projects/{a['id']}", headers=pa_hdr)
    assert resp.status_code == 200
    resp = await client.patch(
        f"/admin/v1/projects/{a['id']}", json={"name": "iso-a2"}, headers=pa_hdr
    )
    assert resp.status_code == 200

    # but cannot read/update B (indistinguishable from nonexistent: 404).
    resp = await client.get(f"/admin/v1/projects/{b['id']}", headers=pa_hdr)
    assert resp.status_code == 404
    resp = await client.get("/admin/v1/projects/nonexistent", headers=pa_hdr)
    assert resp.status_code == 404
    resp = await client.patch(
        f"/admin/v1/projects/{b['id']}", json={"name": "x"}, headers=pa_hdr
    )
    assert resp.status_code == 404

    # project_admin cannot create projects.
    resp = await client.post("/admin/v1/projects", json={"name": "nope"}, headers=pa_hdr)
    assert resp.status_code == 403


async def test_project_viewer_read_only(crud_http):
    client, _ = crud_http
    sa_raw = await _http_bootstrap(client)
    sa_hdr = {"Authorization": f"Bearer {sa_raw}"}
    a = await _http_create_project(client, sa_hdr, "view-a")

    pv_raw = await _http_make_principal(client, sa_hdr, a["id"], "view-pv", "project_viewer")
    pv_hdr = {"Authorization": f"Bearer {pv_raw}"}

    assert (await client.get(f"/admin/v1/projects/{a['id']}", headers=pv_hdr)).status_code == 200
    resp = await client.patch(
        f"/admin/v1/projects/{a['id']}", json={"name": "x"}, headers=pv_hdr
    )
    assert resp.status_code == 403


# --- principal CRUD ------------------------------------------------------------


async def test_principal_crud_and_non_enumeration(crud_http):
    client, factory = crud_http
    sa_raw = await _http_bootstrap(client)
    sa_hdr = {"Authorization": f"Bearer {sa_raw}"}
    a = await _http_create_project(client, sa_hdr, "prin-a")
    b = await _http_create_project(client, sa_hdr, "prin-b")

    resp = await client.post(
        f"/admin/v1/projects/{a['id']}/principals",
        json={"kind": "service_account", "name": "prin-1"},
        headers=sa_hdr,
    )
    assert resp.status_code == 201
    pa_principal = resp.json()

    resp = await client.post(
        f"/admin/v1/projects/{b['id']}/principals",
        json={"kind": "service_account", "name": "prin-2"},
        headers=sa_hdr,
    )
    b_principal = resp.json()

    resp = await client.get(f"/admin/v1/projects/{a['id']}/principals", headers=sa_hdr)
    assert resp.status_code == 200
    assert resp.json()["total"] == 1

    pa_raw = await _http_make_principal(client, sa_hdr, a["id"], "prin-pa", "project_admin")
    pa_hdr = {"Authorization": f"Bearer {pa_raw}"}

    # project_admin(A) can read its own principal, but not B's (404 both ways).
    assert (
        await client.get(f"/admin/v1/principals/{pa_principal['id']}", headers=pa_hdr)
    ).status_code == 200
    resp = await client.get(f"/admin/v1/principals/{b_principal['id']}", headers=pa_hdr)
    assert resp.status_code == 404
    resp = await client.get("/admin/v1/principals/nonexistent", headers=pa_hdr)
    assert resp.status_code == 404


async def test_principal_deactivation_invalidates_credentials(crud_http):
    client, _ = crud_http
    sa_raw = await _http_bootstrap(client)
    sa_hdr = {"Authorization": f"Bearer {sa_raw}"}
    a = await _http_create_project(client, sa_hdr, "deact-p")

    resp = await client.post(
        f"/admin/v1/projects/{a['id']}/principals",
        json={"kind": "service_account", "name": "deact-pr"},
        headers=sa_hdr,
    )
    pid = resp.json()["id"]
    resp = await client.post(
        "/admin/v1/role-assignments",
        json={
            "principal_id": pid,
            "role": "project_admin",
            "resource_scope_type": "project",
            "resource_id": a["id"],
        },
        headers=sa_hdr,
    )
    assert resp.status_code == 201
    resp = await client.post(
        "/admin/v1/credentials",
        json={
            "project_id": a["id"],
            "principal_id": pid,
            "name": "deact-cred",
            "audience": "admin",
            "scopes": ADMIN_SCOPE_VALUES,
        },
        headers=sa_hdr,
    )
    raw = resp.json()["raw_key"]
    hdr = {"Authorization": f"Bearer {raw}"}
    assert (await client.get("/admin/v1/whoami", headers=hdr)).status_code == 200

    resp = await client.patch(
        f"/admin/v1/principals/{pid}", json={"is_active": False}, headers=sa_hdr
    )
    assert resp.status_code == 200
    assert (await client.get("/admin/v1/whoami", headers=hdr)).status_code == 401


# --- role assignment CRUD + delegation ----------------------------------------


async def test_role_assignment_crud_and_delegation(crud_http):
    client, factory = crud_http
    sa_raw = await _http_bootstrap(client)
    sa_hdr = {"Authorization": f"Bearer {sa_raw}"}
    a = await _http_create_project(client, sa_hdr, "role-a")
    b = await _http_create_project(client, sa_hdr, "role-b")

    # system_admin grants project_admin(A).
    pa_raw = await _http_make_principal(client, sa_hdr, a["id"], "role-pa", "project_admin")
    pa_hdr = {"Authorization": f"Bearer {pa_raw}"}

    # Create a target principal in A.
    resp = await client.post(
        f"/admin/v1/projects/{a['id']}/principals",
        json={"kind": "service_account", "name": "role-target"},
        headers=sa_hdr,
    )
    target = resp.json()["id"]

    # project_admin(A) may grant a project role within A.
    resp = await client.post(
        "/admin/v1/role-assignments",
        json={
            "principal_id": target,
            "role": "project_viewer",
            "resource_scope_type": "project",
            "resource_id": a["id"],
        },
        headers=pa_hdr,
    )
    assert resp.status_code == 201
    assignment_id = resp.json()["id"]

    # list/read within A.
    resp = await client.get("/admin/v1/role-assignments", headers=pa_hdr)
    assert resp.status_code == 200
    resp = await client.get(f"/admin/v1/role-assignments/{assignment_id}", headers=pa_hdr)
    assert resp.status_code == 200

    # cannot grant system_admin.
    resp = await client.post(
        "/admin/v1/role-assignments",
        json={
            "principal_id": target,
            "role": "system_admin",
            "resource_scope_type": "deployment",
        },
        headers=pa_hdr,
    )
    assert resp.status_code == 403

    # cannot grant for project B.
    resp = await client.post(
        "/admin/v1/role-assignments",
        json={
            "principal_id": target,
            "role": "project_admin",
            "resource_scope_type": "project",
            "resource_id": b["id"],
        },
        headers=pa_hdr,
    )
    assert resp.status_code == 403

    # viewer cannot mutate roles.
    pv_raw = await _http_make_principal(client, sa_hdr, a["id"], "role-pv", "project_viewer")
    pv_hdr = {"Authorization": f"Bearer {pv_raw}"}
    resp = await client.post(
        "/admin/v1/role-assignments",
        json={
            "principal_id": target,
            "role": "project_viewer",
            "resource_scope_type": "project",
            "resource_id": a["id"],
        },
        headers=pv_hdr,
    )
    assert resp.status_code == 403


async def test_role_assignment_revoke_and_non_enumeration(crud_http):
    client, factory = crud_http
    sa_raw = await _http_bootstrap(client)
    sa_hdr = {"Authorization": f"Bearer {sa_raw}"}
    a = await _http_create_project(client, sa_hdr, "rev-a")
    b = await _http_create_project(client, sa_hdr, "rev-b")

    # system_admin creates a project_admin assignment in B (deployment authority).
    resp = await client.post(
        f"/admin/v1/projects/{b['id']}/principals",
        json={"kind": "service_account", "name": "rev-pb"},
        headers=sa_hdr,
    )
    pid_b = resp.json()["id"]
    resp = await client.post(
        "/admin/v1/role-assignments",
        json={
            "principal_id": pid_b,
            "role": "project_admin",
            "resource_scope_type": "project",
            "resource_id": b["id"],
        },
        headers=sa_hdr,
    )
    assert resp.status_code == 201
    b_assignment = resp.json()["id"]

    pa_raw = await _http_make_principal(client, sa_hdr, a["id"], "rev-pa", "project_admin")
    pa_hdr = {"Authorization": f"Bearer {pa_raw}"}

    # project_admin(A) cannot see B's assignment (404), indistinguishable from
    # a nonexistent assignment.
    resp = await client.get(f"/admin/v1/role-assignments/{b_assignment}", headers=pa_hdr)
    assert resp.status_code == 404
    resp = await client.get("/admin/v1/role-assignments/nonexistent", headers=pa_hdr)
    assert resp.status_code == 404
    resp = await client.post(
        f"/admin/v1/role-assignments/{b_assignment}/revoke", headers=pa_hdr
    )
    assert resp.status_code == 404


# --- generic credential lifecycle + audience separation ------------------------


async def test_credential_audience_separation_via_endpoint(crud_http):
    client, factory = crud_http
    sa_raw = await _http_bootstrap(client)
    sa_hdr = {"Authorization": f"Bearer {sa_raw}"}
    a = await _http_create_project(client, sa_hdr, "sep-a")

    resp = await client.post(
        f"/admin/v1/projects/{a['id']}/principals",
        json={"kind": "service_account", "name": "sep-pr"},
        headers=sa_hdr,
    )
    pid = resp.json()["id"]

    # provision an inference credential through the generic endpoint.
    resp = await client.post(
        "/admin/v1/credentials",
        json={
            "project_id": a["id"],
            "principal_id": pid,
            "name": "sep-inf",
            "audience": "inference",
            "scopes": ["inference:invoke"],
        },
        headers=sa_hdr,
    )
    assert resp.status_code == 201
    inf_raw = resp.json()["raw_key"]

    # provision an admin credential through the same endpoint.
    resp = await client.post(
        "/admin/v1/credentials",
        json={
            "project_id": a["id"],
            "principal_id": pid,
            "name": "sep-admin",
            "audience": "admin",
            "scopes": ADMIN_SCOPE_VALUES,
        },
        headers=sa_hdr,
    )
    assert resp.status_code == 201
    admin_raw = resp.json()["raw_key"]

    inf_hdr = {"Authorization": f"Bearer {inf_raw}"}
    admin_hdr = {"Authorization": f"Bearer {admin_raw}"}

    # inference key rejected on admin, admin key rejected on inference.
    assert (await client.get("/admin/v1/whoami", headers=inf_hdr)).status_code == 401
    assert (await client.get("/v1/models", headers=admin_hdr)).status_code == 401


async def test_credential_cross_project_non_enumeration(crud_http):
    client, factory = crud_http
    sa_raw = await _http_bootstrap(client)
    sa_hdr = {"Authorization": f"Bearer {sa_raw}"}
    a = await _http_create_project(client, sa_hdr, "enum-a")
    b = await _http_create_project(client, sa_hdr, "enum-b")

    # Create a credential in B (owned by B).
    resp = await client.post(
        f"/admin/v1/projects/{b['id']}/principals",
        json={"kind": "service_account", "name": "enum-pb"},
        headers=sa_hdr,
    )
    pid_b = resp.json()["id"]
    resp = await client.post(
        "/admin/v1/credentials",
        json={
            "project_id": b["id"],
            "principal_id": pid_b,
            "name": "enum-b-cred",
            "audience": "admin",
            "scopes": ADMIN_SCOPE_VALUES,
        },
        headers=sa_hdr,
    )
    assert resp.status_code == 201
    cred_b = resp.json()["credential"]["id"]

    pa_raw = await _http_make_principal(client, sa_hdr, a["id"], "enum-pa", "project_admin")
    pa_hdr = {"Authorization": f"Bearer {pa_raw}"}

    # get/rotate/revoke of B's credential vs a nonexistent ID are indistinguishable.
    for path in (f"/admin/v1/credentials/{cred_b}", "/admin/v1/credentials/nonexistent"):
        resp = await client.get(path, headers=pa_hdr)
        assert resp.status_code == 404
        assert resp.json()["error"]["code"] == "not_found"

    for path in (
        f"/admin/v1/credentials/{cred_b}/rotate",
        "/admin/v1/credentials/nonexistent/rotate",
    ):
        resp = await client.post(path, headers=pa_hdr)
        assert resp.status_code == 404
        assert resp.json()["error"]["code"] == "not_found"

    for path in (
        f"/admin/v1/credentials/{cred_b}/revoke",
        "/admin/v1/credentials/nonexistent/revoke",
    ):
        resp = await client.post(path, headers=pa_hdr)
        assert resp.status_code == 404
        assert resp.json()["error"]["code"] == "not_found"

    # B's credential remains active.
    resp = await client.get(f"/admin/v1/credentials/{cred_b}", headers=sa_hdr)
    assert resp.status_code == 200
    assert resp.json()["is_active"] is True


# --- audit idempotency + no secret material -----------------------------------


async def test_idempotent_revoke_emits_single_audit_event(crud_http):
    client, factory = crud_http
    sa_raw = await _http_bootstrap(client)
    sa_hdr = {"Authorization": f"Bearer {sa_raw}"}
    a = await _http_create_project(client, sa_hdr, "audit-a")

    resp = await client.post(
        f"/admin/v1/projects/{a['id']}/principals",
        json={"kind": "service_account", "name": "audit-pr"},
        headers=sa_hdr,
    )
    pid = resp.json()["id"]
    resp = await client.post(
        "/admin/v1/credentials",
        json={
            "project_id": a["id"],
            "principal_id": pid,
            "name": "audit-cred",
            "audience": "admin",
            "scopes": ADMIN_SCOPE_VALUES,
        },
        headers=sa_hdr,
    )
    cred_id = resp.json()["credential"]["id"]
    raw = resp.json()["raw_key"]

    # revoke twice.
    rev1 = await client.post(f"/admin/v1/credentials/{cred_id}/revoke", headers=sa_hdr)
    assert rev1.status_code == 200
    rev2 = await client.post(f"/admin/v1/credentials/{cred_id}/revoke", headers=sa_hdr)
    assert rev2.status_code == 200

    async with factory() as s:
        async with s.begin():
            events = await repository.list_audit_events(s)
    revoked = [e for e in events if e.action == "credential.revoked" and e.resource_id == cred_id]
    assert len(revoked) == 1
    for e in events:
        blob = str(e.metadata) + e.resource_id + e.action
        assert raw not in blob
        assert BOOTSTRAP_TOKEN not in blob
        assert "key_hash" not in str(e.metadata)


async def test_duplicate_assignment_emits_single_audit_event(crud_http):
    client, factory = crud_http
    sa_raw = await _http_bootstrap(client)
    sa_hdr = {"Authorization": f"Bearer {sa_raw}"}
    a = await _http_create_project(client, sa_hdr, "dup-a")

    resp = await client.post(
        f"/admin/v1/projects/{a['id']}/principals",
        json={"kind": "service_account", "name": "dup-pr"},
        headers=sa_hdr,
    )
    pid = resp.json()["id"]

    body = {
        "principal_id": pid,
        "role": "project_admin",
        "resource_scope_type": "project",
        "resource_id": a["id"],
    }
    resp = await client.post("/admin/v1/role-assignments", json=body, headers=sa_hdr)
    assert resp.status_code == 201
    resp = await client.post("/admin/v1/role-assignments", json=body, headers=sa_hdr)
    assert resp.status_code == 201

    async with factory() as s:
        async with s.begin():
            events = await repository.list_audit_events(s)
    created = [e for e in events if e.action == "role_assignment.created"]
    assert len(created) == 1


# --- project deactivation auth impact -----------------------------------------


async def test_project_deactivation_invalidates_credentials(crud_http):
    client, _ = crud_http
    sa_raw = await _http_bootstrap(client)
    sa_hdr = {"Authorization": f"Bearer {sa_raw}"}
    a = await _http_create_project(client, sa_hdr, "proj-deact")

    pa_raw = await _http_make_principal(client, sa_hdr, a["id"], "proj-deact-pa", "project_admin")
    pa_hdr = {"Authorization": f"Bearer {pa_raw}"}
    assert (await client.get("/admin/v1/whoami", headers=pa_hdr)).status_code == 200

    resp = await client.patch(
        f"/admin/v1/projects/{a['id']}", json={"is_active": False}, headers=sa_hdr
    )
    assert resp.status_code == 200

    assert (await client.get("/admin/v1/whoami", headers=pa_hdr)).status_code == 401

    # reactivate.
    resp = await client.patch(
        f"/admin/v1/projects/{a['id']}", json={"is_active": True}, headers=sa_hdr
    )
    assert resp.status_code == 200
    assert (await client.get("/admin/v1/whoami", headers=pa_hdr)).status_code == 200
