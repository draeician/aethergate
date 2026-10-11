"""Catalog/routing admin API: deployment-scope authorization, CRUD, invariants.

DB-gated. Exercises the /admin/v1 catalog surface (providers, secret-refs,
provider accounts, endpoints, quota groups/limits, model aliases, route
bindings) and the deployment-scoped authorization model introduced in AGV2-016.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime

import httpx
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from aethergate.catalog import admin as catalog_service
from aethergate.config import Settings
from aethergate.domain import entities as domain
from aethergate.domain.enums import (
    CredentialAudience,
    PrincipalKind,
    ResourceScopeType,
    Role,
)
from aethergate.domain.ids import (
    EndpointId,
    ModelAliasId,
    ProviderAccountId,
    ProviderId,
    RouteBindingId,
)
from aethergate.errors import ActiveRouteConflictError, AdminAuthorizationError
from aethergate.identity import admin as admin_service
from aethergate.identity import rbac
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
ADMIN_SCOPE_VALUES = [s.value for s in ADMIN_SCOPES]
ALLOWED_HOST = "api.example.com"


def _settings() -> Settings:
    return Settings(
        database_url="postgresql+asyncpg://u:p@h/db",
        allow_inference_auth_bypass=False,
        bootstrap_token=BOOTSTRAP_TOKEN,
        upstream_allowlist=ALLOWED_HOST,
    )


def _new_id() -> str:
    return uuid.uuid4().hex


@pytest.fixture
async def catalog_engine():
    if not TEST_DATABASE_URL:
        pytest.skip("AETHERGATE_TEST_DATABASE_URL not set")
    url = url_for_database(TEST_DATABASE_URL, "aethergate_test_catalog_admin")
    await ensure_database(url)
    engine = create_async_engine(url)
    yield engine
    await engine.dispose()


@pytest.fixture
def catalog_factory(catalog_engine):
    return async_sessionmaker(catalog_engine, expire_on_commit=False)


@pytest.fixture
async def catalog_http(catalog_engine, catalog_factory, monkeypatch):
    await reset_schema(catalog_engine)
    monkeypatch.setattr(admin_service, "get_settings", _settings)
    monkeypatch.setattr(catalog_service, "get_settings", _settings)
    monkeypatch.setattr("aethergate.api.admin.get_session_factory", lambda: catalog_factory)

    async def _test_session():
        async with catalog_factory() as s:
            yield s

    app.dependency_overrides[persistence_db.get_session] = _test_session
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, catalog_factory
    app.dependency_overrides.clear()
    await reset_schema(catalog_engine)


# --- helpers -------------------------------------------------------------------


async def _http_bootstrap(client) -> str:
    resp = await client.post(
        "/admin/v1/bootstrap",
        headers={"Authorization": f"Bearer {BOOTSTRAP_TOKEN}"},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["raw_key"]


async def _http_create_provider(client, hdr, name="provider-a", kind="openai") -> dict:
    resp = await client.post(
        "/admin/v1/providers",
        json={"kind": kind, "name": name, "capabilities": ["text"]},
        headers=hdr,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _http_create_secret_ref(client, hdr, name="secret-a") -> dict:
    resp = await client.post(
        "/admin/v1/secret-refs", json={"name": name}, headers=hdr
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _http_create_account(client, hdr, provider_id, name="account-a") -> dict:
    resp = await client.post(
        "/admin/v1/provider-accounts",
        json={"provider_id": provider_id, "name": name},
        headers=hdr,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _http_create_endpoint(client, hdr, account_id, destination=None, name="ep-a") -> dict:
    resp = await client.post(
        "/admin/v1/endpoints",
        json={
            "provider_account_id": account_id,
            "name": name,
            "base_destination": destination or f"http://{ALLOWED_HOST}",
            "max_concurrency": 1,
        },
        headers=hdr,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _http_create_alias(client, hdr, name="gpt-4", active=True) -> dict:
    resp = await client.post(
        "/admin/v1/model-aliases",
        json={"name": name, "capabilities": ["text"], "is_active": active},
        headers=hdr,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _http_make_project_admin(client, sa_hdr, project_name="cat-proj") -> tuple[str, dict]:
    resp = await client.post("/admin/v1/projects", json={"name": project_name}, headers=sa_hdr)
    assert resp.status_code == 201, resp.text
    project = resp.json()

    resp = await client.post(
        f"/admin/v1/projects/{project['id']}/principals",
        json={"kind": "service_account", "name": f"{project_name}-pa"},
        headers=sa_hdr,
    )
    assert resp.status_code == 201, resp.text
    pid = resp.json()["id"]

    resp = await client.post(
        "/admin/v1/role-assignments",
        json={
            "principal_id": pid,
            "role": "project_admin",
            "resource_scope_type": "project",
            "resource_id": project["id"],
        },
        headers=sa_hdr,
    )
    assert resp.status_code == 201, resp.text

    resp = await client.post(
        "/admin/v1/credentials",
        json={
            "project_id": project["id"],
            "principal_id": pid,
            "name": f"{project_name}-pa-cred",
            "audience": "admin",
            "scopes": ADMIN_SCOPE_VALUES,
        },
        headers=sa_hdr,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["raw_key"], project


# --- service-layer deployment authorization ------------------------------------


async def test_service_layer_deployment_authorization(catalog_engine, catalog_factory, monkeypatch):
    await reset_schema(catalog_engine)
    monkeypatch.setattr(admin_service, "get_settings", _settings)
    monkeypatch.setattr(catalog_service, "get_settings", _settings)
    async with catalog_factory() as s:
        _, sa_raw = await admin_service.bootstrap(s, BOOTSTRAP_TOKEN)

    async with catalog_factory() as s:
        async with s.begin():
            sa_ctx = await admin_service.authenticate_admin(s, sa_raw)
            proj = await admin_service.create_project(s, context=sa_ctx, name="cat-auth")
            pa = await admin_service.create_principal(
                s, context=sa_ctx, project_id=proj.id,
                kind=PrincipalKind.SERVICE_ACCOUNT, name="cat-auth-pa",
            )
            await admin_service.create_role_assignment(
                s, context=sa_ctx, principal_id=pa.id, role=Role.PROJECT_ADMIN,
                resource_scope_type=ResourceScopeType.PROJECT, resource_id=proj.id,
            )
            _, pa_raw = await admin_service.create_credential(
                s, context=sa_ctx, project_id=proj.id, principal_id=pa.id,
                name="cat-auth-pa-c", audience=CredentialAudience.ADMIN,
                scopes=ADMIN_SCOPES, expires_at=None,
            )

    async with catalog_factory() as s:
        async with s.begin():
            pa_ctx = await admin_service.authenticate_admin(s, pa_raw)
            # A project_admin (project-scoped) cannot mutate or read catalog.
            with pytest.raises(AdminAuthorizationError):
                await catalog_service.create_provider(
                    s, context=pa_ctx, kind="openai", name="nope"
                )
            with pytest.raises(AdminAuthorizationError):
                await catalog_service.list_providers(s, context=pa_ctx)


async def test_project_admin_denied_catalog_http(catalog_http):
    client, _ = catalog_http
    sa_raw = await _http_bootstrap(client)
    sa_hdr = {"Authorization": f"Bearer {sa_raw}"}
    pa_raw, _ = await _http_make_project_admin(client, sa_hdr)
    pa_hdr = {"Authorization": f"Bearer {pa_raw}"}

    assert (await client.get("/admin/v1/providers", headers=pa_hdr)).status_code == 403
    resp = await client.post(
        "/admin/v1/providers",
        json={"kind": "openai", "name": "denied"},
        headers=pa_hdr,
    )
    assert resp.status_code == 403


# --- provider CRUD --------------------------------------------------------------


async def test_provider_crud_and_pagination(catalog_http):
    client, _ = catalog_http
    sa_hdr = {"Authorization": f"Bearer {await _http_bootstrap(client)}"}

    for i in range(3):
        await _http_create_provider(client, sa_hdr, name=f"prov-{i}")

    resp = await client.get("/admin/v1/providers?limit=2&offset=0", headers=sa_hdr)
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["items"]) == 2
    assert body["limit"] == 2 and body["offset"] == 0 and body["total"] == 3

    first = (await client.get("/admin/v1/providers", headers=sa_hdr)).json()["items"][0]
    assert first["name"] in {"prov-0", "prov-1", "prov-2"}

    resp = await client.get(f"/admin/v1/providers/{first['id']}", headers=sa_hdr)
    assert resp.status_code == 200 and resp.json()["name"] == first["name"]

    resp = await client.patch(
        f"/admin/v1/providers/{first['id']}",
        json={"name": "prov-0-renamed", "is_active": False},
        headers=sa_hdr,
    )
    assert resp.status_code == 200
    assert resp.json()["name"] == "prov-0-renamed"
    assert resp.json()["is_active"] is False


async def test_provider_duplicate_name_conflict(catalog_http):
    client, _ = catalog_http
    sa_hdr = {"Authorization": f"Bearer {await _http_bootstrap(client)}"}
    await _http_create_provider(client, sa_hdr, name="dup")
    resp = await client.post(
        "/admin/v1/providers",
        json={"kind": "openai", "name": "dup"},
        headers=sa_hdr,
    )
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "resource_conflict"


# --- secret refs (metadata only) ------------------------------------------------


async def test_secret_ref_metadata_only(catalog_http):
    client, factory = catalog_http
    sa_hdr = {"Authorization": f"Bearer {await _http_bootstrap(client)}"}
    ref = await _http_create_secret_ref(client, sa_hdr, name="my-secret")

    assert set(ref.keys()) == {"id", "name", "created_at"}
    assert ref["name"] == "my-secret"

    resp = await client.get("/admin/v1/secret-refs", headers=sa_hdr)
    assert resp.status_code == 200 and resp.json()["total"] == 1

    # Duplicate name is a deterministic conflict.
    resp = await client.post(
        "/admin/v1/secret-refs", json={"name": "my-secret"}, headers=sa_hdr
    )
    assert resp.status_code == 409

    # No raw value is ever persisted (only metadata).
    async with factory() as s:
        async with s.begin():
            rows = await repository.list_secret_refs(s)
    assert all(not hasattr(r, "value") for r in rows)


# --- provider accounts ----------------------------------------------------------


async def test_provider_account_parent_validation(catalog_http):
    client, _ = catalog_http
    sa_hdr = {"Authorization": f"Bearer {await _http_bootstrap(client)}"}
    resp = await client.post(
        "/admin/v1/provider-accounts",
        json={"provider_id": "missing", "name": "acct"},
        headers=sa_hdr,
    )
    assert resp.status_code == 404

    provider = await _http_create_provider(client, sa_hdr)
    resp = await client.post(
        "/admin/v1/provider-accounts",
        json={"provider_id": provider["id"], "name": "acct", "secret_ref_id": "missing"},
        headers=sa_hdr,
    )
    assert resp.status_code == 404


async def test_provider_account_omitted_vs_null_patch(catalog_http):
    client, _ = catalog_http
    sa_hdr = {"Authorization": f"Bearer {await _http_bootstrap(client)}"}
    provider = await _http_create_provider(client, sa_hdr)
    ref = await _http_create_secret_ref(client, sa_hdr, name="sec")

    acct = await client.post(
        "/admin/v1/provider-accounts",
        json={
            "provider_id": provider["id"],
            "name": "acct",
            "external_account_id": "ext-123",
            "secret_ref_id": ref["id"],
        },
        headers=sa_hdr,
    )
    assert acct.status_code == 201
    acct_id = acct.json()["id"]

    # Omitted fields leave values unchanged.
    resp = await client.patch(
        f"/admin/v1/provider-accounts/{acct_id}", json={"name": "acct-2"}, headers=sa_hdr
    )
    assert resp.status_code == 200
    assert resp.json()["external_account_id"] == "ext-123"
    assert resp.json()["secret_ref_id"] == ref["id"]

    # Explicit null clears them.
    resp = await client.patch(
        f"/admin/v1/provider-accounts/{acct_id}",
        json={"external_account_id": None, "secret_ref_id": None},
        headers=sa_hdr,
    )
    assert resp.status_code == 200
    assert resp.json()["external_account_id"] is None
    assert resp.json()["secret_ref_id"] is None


# --- endpoints ------------------------------------------------------------------


async def test_endpoint_parent_validation_and_egress(catalog_http):
    client, _ = catalog_http
    sa_hdr = {"Authorization": f"Bearer {await _http_bootstrap(client)}"}
    provider = await _http_create_provider(client, sa_hdr)
    account = await _http_create_account(client, sa_hdr, provider["id"])

    resp = await client.post(
        "/admin/v1/endpoints",
        json={"provider_account_id": "missing", "name": "ep", "base_destination": f"http://{ALLOWED_HOST}"},
        headers=sa_hdr,
    )
    assert resp.status_code == 404

    # Non-allowlisted host rejected.
    resp = await client.post(
        "/admin/v1/endpoints",
        json={
            "provider_account_id": account["id"],
            "name": "ep",
            "base_destination": "http://evil.example.com",
        },
        headers=sa_hdr,
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "destination_denied"

    # URL userinfo rejected.
    resp = await client.post(
        "/admin/v1/endpoints",
        json={
            "provider_account_id": account["id"],
            "name": "ep",
            "base_destination": f"http://user:pass@{ALLOWED_HOST}",
        },
        headers=sa_hdr,
    )
    assert resp.status_code == 400

    # Metadata host rejected.
    resp = await client.post(
        "/admin/v1/endpoints",
        json={
            "provider_account_id": account["id"],
            "name": "ep",
            "base_destination": "http://metadata.google.internal",
        },
        headers=sa_hdr,
    )
    assert resp.status_code == 400

    # Allowlisted host accepted.
    resp = await client.post(
        "/admin/v1/endpoints",
        json={
            "provider_account_id": account["id"],
            "name": "ep",
            "base_destination": f"http://{ALLOWED_HOST}",
            "max_concurrency": 3,
        },
        headers=sa_hdr,
    )
    assert resp.status_code == 201
    assert resp.json()["max_concurrency"] == 3


async def test_endpoint_max_concurrency_update(catalog_http):
    client, _ = catalog_http
    sa_hdr = {"Authorization": f"Bearer {await _http_bootstrap(client)}"}
    provider = await _http_create_provider(client, sa_hdr)
    account = await _http_create_account(client, sa_hdr, provider["id"])
    ep = await _http_create_endpoint(client, sa_hdr, account["id"])

    resp = await client.patch(
        f"/admin/v1/endpoints/{ep['id']}", json={"max_concurrency": 5}, headers=sa_hdr
    )
    assert resp.status_code == 200 and resp.json()["max_concurrency"] == 5

    resp = await client.patch(
        f"/admin/v1/endpoints/{ep['id']}", json={"max_concurrency": 0}, headers=sa_hdr
    )
    assert resp.status_code == 400


# --- quota groups ---------------------------------------------------------------


async def test_quota_group_crud_and_parent(catalog_http):
    client, _ = catalog_http
    sa_hdr = {"Authorization": f"Bearer {await _http_bootstrap(client)}"}
    provider = await _http_create_provider(client, sa_hdr)
    account = await _http_create_account(client, sa_hdr, provider["id"])

    resp = await client.post(
        "/admin/v1/quota-groups",
        json={"provider_account_id": "missing", "name": "qg"},
        headers=sa_hdr,
    )
    assert resp.status_code == 404

    resp = await client.post(
        "/admin/v1/quota-groups",
        json={"provider_account_id": account["id"], "name": "qg"},
        headers=sa_hdr,
    )
    assert resp.status_code == 201
    group_id = resp.json()["id"]

    resp = await client.get(
        f"/admin/v1/quota-groups?provider_account_id={account['id']}", headers=sa_hdr
    )
    assert resp.status_code == 200 and resp.json()["total"] == 1

    # Duplicate name conflict.
    resp = await client.post(
        "/admin/v1/quota-groups",
        json={"provider_account_id": account["id"], "name": "qg"},
        headers=sa_hdr,
    )
    assert resp.status_code == 409

    resp = await client.patch(
        f"/admin/v1/quota-groups/{group_id}",
        json={"description": "desc"},
        headers=sa_hdr,
    )
    assert resp.status_code == 200 and resp.json()["description"] == "desc"


# --- quota limits ---------------------------------------------------------------


async def test_quota_limit_validation_and_update(catalog_http):
    client, _ = catalog_http
    sa_hdr = {"Authorization": f"Bearer {await _http_bootstrap(client)}"}
    provider = await _http_create_provider(client, sa_hdr)
    account = await _http_create_account(client, sa_hdr, provider["id"])
    resp = await client.post(
        "/admin/v1/quota-groups",
        json={"provider_account_id": account["id"], "name": "qg"},
        headers=sa_hdr,
    )
    group_id = resp.json()["id"]

    resp = await client.post(
        "/admin/v1/quota-limits",
        json={
            "quota_group_id": group_id,
            "metric": "requests",
            "limit_units": 10,
            "window_seconds": 60,
        },
        headers=sa_hdr,
    )
    assert resp.status_code == 201
    limit_id = resp.json()["id"]

    resp = await client.patch(
        f"/admin/v1/quota-limits/{limit_id}",
        json={"limit_units": 20, "enabled": False},
        headers=sa_hdr,
    )
    assert resp.status_code == 200
    assert resp.json()["limit_units"] == 20
    assert resp.json()["enabled"] is False

    # Positive validation at DTO boundary.
    resp = await client.post(
        "/admin/v1/quota-limits",
        json={
            "quota_group_id": group_id,
            "metric": "requests",
            "limit_units": 0,
            "window_seconds": 60,
        },
        headers=sa_hdr,
    )
    assert resp.status_code == 400


async def test_quota_edit_does_not_mutate_history(catalog_http):
    client, factory = catalog_http
    sa_hdr = {"Authorization": f"Bearer {await _http_bootstrap(client)}"}
    provider = await _http_create_provider(client, sa_hdr)
    account = await _http_create_account(client, sa_hdr, provider["id"])
    resp = await client.post(
        "/admin/v1/quota-groups",
        json={"provider_account_id": account["id"], "name": "qg"},
        headers=sa_hdr,
    )
    group_id = resp.json()["id"]
    resp = await client.post(
        "/admin/v1/quota-limits",
        json={
            "quota_group_id": group_id,
            "metric": "requests",
            "limit_units": 10,
            "window_seconds": 60,
        },
        headers=sa_hdr,
    )
    limit_id = resp.json()["id"]

    # Insert a historical window row.
    async with factory() as s:
        async with s.begin():
            s.add(
                models.QuotaWindow(
                    id=_new_id(),
                    quota_limit_id=limit_id,
                    window_start=datetime(2026, 1, 1, tzinfo=UTC),
                    committed_units=5,
                    reserved_units=1,
                )
            )
            await s.flush()

    resp = await client.patch(
        f"/admin/v1/quota-limits/{limit_id}", json={"limit_units": 99}, headers=sa_hdr
    )
    assert resp.status_code == 200

    async with factory() as s:
        async with s.begin():
            rows = await repository.list_quota_limits(s, quota_group_id=group_id)
            windows = (
                await s.execute(
                    models.QuotaWindow.__table__.select().where(
                        models.QuotaWindow.quota_limit_id == limit_id
                    )
                )
            ).all()
    assert len(rows) == 1
    assert windows[0].committed_units == 5
    assert windows[0].reserved_units == 1


# --- model aliases --------------------------------------------------------------


async def test_model_alias_crud_and_active_state(catalog_http):
    client, factory = catalog_http
    sa_hdr = {"Authorization": f"Bearer {await _http_bootstrap(client)}"}
    alias = await _http_create_alias(client, sa_hdr, name="gpt-4")

    resp = await client.post(
        "/admin/v1/model-aliases",
        json={"name": "gpt-4"},
        headers=sa_hdr,
    )
    assert resp.status_code == 409

    async with factory() as s:
        async with s.begin():
            active = await repository.list_active_model_aliases(s)
    assert "gpt-4" in [a.name for a in active]

    resp = await client.patch(
        f"/admin/v1/model-aliases/{alias['id']}", json={"is_active": False}, headers=sa_hdr
    )
    assert resp.status_code == 200

    async with factory() as s:
        async with s.begin():
            active = await repository.list_active_model_aliases(s)
    assert "gpt-4" not in [a.name for a in active]

    resp = await client.patch(
        f"/admin/v1/model-aliases/{alias['id']}", json={"is_active": True}, headers=sa_hdr
    )
    assert resp.status_code == 200


# --- route bindings -------------------------------------------------------------


async def _route_seed(client, sa_hdr, alias_name="gpt-4"):
    provider = await _http_create_provider(client, sa_hdr, name="prov")
    account = await _http_create_account(client, sa_hdr, provider["id"])
    endpoint = await _http_create_endpoint(client, sa_hdr, account["id"])
    alias = await _http_create_alias(client, sa_hdr, name=alias_name)
    return provider, account, endpoint, alias


async def test_route_parent_and_consistency_validation(catalog_http):
    client, _ = catalog_http
    sa_hdr = {"Authorization": f"Bearer {await _http_bootstrap(client)}"}
    provider, account, endpoint, alias = await _route_seed(client, sa_hdr)

    # Second provider account (account B).
    account_b = await _http_create_account(client, sa_hdr, provider["id"], name="account-b")

    # Route using endpoint A but provider_account B => rejected (parent mismatch).
    resp = await client.post(
        "/admin/v1/route-bindings",
        json={
            "model_alias_id": alias["id"],
            "endpoint_id": endpoint["id"],
            "provider_account_id": account_b["id"],
            "upstream_model": "m",
        },
        headers=sa_hdr,
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "parent_mismatch"

    # Quota group from account B attached to route for account A => rejected.
    resp = await client.post(
        "/admin/v1/quota-groups",
        json={"provider_account_id": account_b["id"], "name": "qg-b"},
        headers=sa_hdr,
    )
    qg_b = resp.json()["id"]
    resp = await client.post(
        "/admin/v1/route-bindings",
        json={
            "model_alias_id": alias["id"],
            "endpoint_id": endpoint["id"],
            "provider_account_id": account["id"],
            "quota_group_id": qg_b,
            "upstream_model": "m",
        },
        headers=sa_hdr,
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "parent_mismatch"

    # No invalid row persisted.
    resp = await client.get("/admin/v1/route-bindings", headers=sa_hdr)
    assert resp.json()["total"] == 0


async def test_one_active_route_per_alias(catalog_http):
    client, _ = catalog_http
    sa_hdr = {"Authorization": f"Bearer {await _http_bootstrap(client)}"}
    provider, account, endpoint, alias = await _route_seed(client, sa_hdr)
    endpoint_b = await _http_create_endpoint(client, sa_hdr, account["id"], name="ep-b")

    body = {
        "model_alias_id": alias["id"],
        "endpoint_id": endpoint["id"],
        "provider_account_id": account["id"],
        "upstream_model": "m",
    }
    first = await client.post("/admin/v1/route-bindings", json=body, headers=sa_hdr)
    assert first.status_code == 201

    # Second active route for the same alias => 409.
    second = await client.post(
        "/admin/v1/route-bindings",
        json={**body, "endpoint_id": endpoint_b["id"]},
        headers=sa_hdr,
    )
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "active_route_conflict"

    # An inactive alternate is allowed.
    inactive = await client.post(
        "/admin/v1/route-bindings",
        json={**body, "endpoint_id": endpoint_b["id"], "is_active": False},
        headers=sa_hdr,
    )
    assert inactive.status_code == 201
    inactive_id = inactive.json()["id"]

    # Activating the inactive alternate while another is active => 409.
    resp = await client.patch(
        f"/admin/v1/route-bindings/{inactive_id}", json={"is_active": True}, headers=sa_hdr
    )
    assert resp.status_code == 409

    # Deactivate the active one, then activate the alternate => succeeds.
    resp = await client.patch(
        f"/admin/v1/route-bindings/{first.json()['id']}", json={"is_active": False}, headers=sa_hdr
    )
    assert resp.status_code == 200
    resp = await client.patch(
        f"/admin/v1/route-bindings/{inactive_id}", json={"is_active": True}, headers=sa_hdr
    )
    assert resp.status_code == 200


async def test_route_omitted_vs_null_patch(catalog_http):
    client, _ = catalog_http
    sa_hdr = {"Authorization": f"Bearer {await _http_bootstrap(client)}"}
    provider, account, endpoint, alias = await _route_seed(client, sa_hdr)

    resp = await client.post(
        "/admin/v1/route-bindings",
        json={
            "model_alias_id": alias["id"],
            "endpoint_id": endpoint["id"],
            "provider_account_id": account["id"],
            "upstream_model": "m",
            "default_output_tokens": 128,
        },
        headers=sa_hdr,
    )
    binding_id = resp.json()["id"]

    # Omitted leaves upstream_model and default_output_tokens unchanged.
    resp = await client.patch(
        f"/admin/v1/route-bindings/{binding_id}", json={"is_active": False}, headers=sa_hdr
    )
    assert resp.status_code == 200
    assert resp.json()["upstream_model"] == "m"
    assert resp.json()["default_output_tokens"] == 128

    # Explicit null clears them.
    resp = await client.patch(
        f"/admin/v1/route-bindings/{binding_id}",
        json={"upstream_model": None, "default_output_tokens": None},
        headers=sa_hdr,
    )
    assert resp.status_code == 200
    assert resp.json()["upstream_model"] is None
    assert resp.json()["default_output_tokens"] is None


async def test_concurrent_route_activation_single_winner(
    catalog_engine, catalog_factory, monkeypatch
):
    await reset_schema(catalog_engine)
    monkeypatch.setattr(catalog_service, "get_settings", _settings)

    async with catalog_factory() as s:
        async with s.begin():
            provider = await repository.create_provider(
                s, domain.Provider(id=ProviderId(_new_id()), kind="openai", name="prov")
            )
            account = await repository.create_provider_account(
                s,
                domain.ProviderAccount(
                    id=ProviderAccountId(_new_id()), provider_id=provider.id, name="acct"
                ),
            )
            ep1 = await repository.create_endpoint(
                s,
                domain.Endpoint(
                    id=EndpointId(_new_id()),
                    provider_account_id=account.id,
                    name="ep1",
                    base_destination=f"http://{ALLOWED_HOST}",
                ),
            )
            ep2 = await repository.create_endpoint(
                s,
                domain.Endpoint(
                    id=EndpointId(_new_id()),
                    provider_account_id=account.id,
                    name="ep2",
                    base_destination=f"http://{ALLOWED_HOST}",
                ),
            )
            alias = await repository.create_model_alias(
                s, domain.ModelAlias(id=ModelAliasId(_new_id()), name="gpt-4")
            )
            alias_id = alias.id
            acct_id = account.id
            ep1_id = ep1.id
            ep2_id = ep2.id

    async def _create(endpoint_id):
        async with catalog_factory() as s:
            async with s.begin():
                return await repository.create_route_binding(
                    s,
                    domain.RouteBinding(
                        id=RouteBindingId(_new_id()),
                        model_alias_id=alias_id,
                        endpoint_id=endpoint_id,
                        provider_account_id=acct_id,
                        upstream_model="m",
                        is_active=True,
                    ),
                )

    results = await asyncio.gather(
        _create(ep1_id), _create(ep2_id), return_exceptions=True
    )
    successes = [r for r in results if not isinstance(r, BaseException)]
    conflicts = [r for r in results if isinstance(r, ActiveRouteConflictError)]
    assert len(successes) == 1
    assert len(conflicts) == 1


# --- audit ----------------------------------------------------------------------


async def test_audit_no_secret_material_and_noop_no_event(catalog_http):
    client, factory = catalog_http
    sa_hdr = {"Authorization": f"Bearer {await _http_bootstrap(client)}"}
    provider = await _http_create_provider(client, sa_hdr, name="audit-prov")
    await _http_create_secret_ref(client, sa_hdr, name="top-secret-name")

    resp = await client.patch(
        f"/admin/v1/providers/{provider['id']}", json={}, headers=sa_hdr
    )
    assert resp.status_code == 200

    async with factory() as s:
        async with s.begin():
            events = await repository.list_audit_events(s)
    provider_updated = [
        e for e in events if e.action == "provider.updated"
        and e.resource_id == provider["id"]
    ]
    assert len(provider_updated) == 0

    for e in events:
        blob = str(e.metadata) + e.resource_id + e.action + e.resource_type
        assert "top-secret-name" not in blob
        assert BOOTSTRAP_TOKEN not in blob


# --- bearer auth (no CSRF) ------------------------------------------------------


async def test_bearer_admin_mutation_no_csrf(catalog_http):
    client, _ = catalog_http
    sa_hdr = {"Authorization": f"Bearer {await _http_bootstrap(client)}"}

    # Bearer admin mutation without a CSRF header succeeds; CSRF only guards
    # browser (session-cookie) requests, which are covered by test_oidc_session.
    resp = await client.post(
        "/admin/v1/providers",
        json={"kind": "openai", "name": "via-bearer"},
        headers=sa_hdr,
    )
    assert resp.status_code == 201


async def test_catalog_read_by_id_not_found(catalog_http):
    client, _ = catalog_http
    sa_hdr = {"Authorization": f"Bearer {await _http_bootstrap(client)}"}
    resp = await client.get("/admin/v1/providers/nonexistent", headers=sa_hdr)
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "not_found"


async def test_list_filters_bounded_and_stable(catalog_http):
    client, _ = catalog_http
    sa_hdr = {"Authorization": f"Bearer {await _http_bootstrap(client)}"}
    provider = await _http_create_provider(client, sa_hdr)
    account = await _http_create_account(client, sa_hdr, provider["id"])
    account_b = await _http_create_account(client, sa_hdr, provider["id"], name="acct-b")
    await _http_create_endpoint(client, sa_hdr, account["id"], name="ep-a")
    await _http_create_endpoint(client, sa_hdr, account_b["id"], name="ep-b")

    resp = await client.get(
        f"/admin/v1/endpoints?provider_account_id={account['id']}", headers=sa_hdr
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["name"] == "ep-a"

    # limit bounds enforced.
    resp = await client.get("/admin/v1/endpoints?limit=1000", headers=sa_hdr)
    assert resp.status_code == 400
