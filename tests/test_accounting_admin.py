"""Accounting admin API: pricing, budgets, usage/ledger/audit reads, RBAC.

DB-gated. Exercises the ``/admin/v1`` accounting surface introduced in AGV2-017:
route price-policy CRUD, immutable price-snapshot reads, project budget policies,
budget status/headroom, budget reservation reads, usage/ledger reads, and
deployment-vs-project-scoped audit reads.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from aethergate.accounting import admin as accounting_service
from aethergate.accounting import repository as accounting_repo
from aethergate.catalog import admin as catalog_service
from aethergate.config import Settings
from aethergate.domain import entities as domain
from aethergate.domain.enums import (
    BillingUnit,
    CredentialAudience,
    PrincipalKind,
    ResourceScopeType,
    Role,
)
from aethergate.domain.ids import (
    EndpointId,
    ModelAliasId,
    PricePolicyId,
    ProviderAccountId,
    ProviderId,
    RouteBindingId,
)
from aethergate.errors import AdminAuthorizationError, PricePolicyConflictError
from aethergate.identity import admin as admin_service
from aethergate.identity import rbac
from aethergate.main import app
from aethergate.persistence import db as persistence_db
from aethergate.persistence import models, repository
from aethergate.scheduler import repository as sched_repo
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
READ_SCOPE_VALUES = [s.value for s in rbac.READ_PERMISSIONS]
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
async def acct_engine():
    if not TEST_DATABASE_URL:
        pytest.skip("AETHERGATE_TEST_DATABASE_URL not set")
    url = url_for_database(TEST_DATABASE_URL, "aethergate_test_accounting_admin")
    await ensure_database(url)
    engine = create_async_engine(url)
    yield engine
    await engine.dispose()


@pytest.fixture
def acct_factory(acct_engine):
    return async_sessionmaker(acct_engine, expire_on_commit=False)


@pytest.fixture
async def acct_http(acct_engine, acct_factory, monkeypatch):
    await reset_schema(acct_engine)
    monkeypatch.setattr(admin_service, "get_settings", _settings)
    monkeypatch.setattr(catalog_service, "get_settings", _settings)
    monkeypatch.setattr("aethergate.api.admin.get_session_factory", lambda: acct_factory)

    async def _test_session():
        async with acct_factory() as s:
            yield s

    app.dependency_overrides[persistence_db.get_session] = _test_session
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, acct_factory
    app.dependency_overrides.clear()
    await reset_schema(acct_engine)


# --- helpers -------------------------------------------------------------------


async def _http_bootstrap(client) -> str:
    resp = await client.post(
        "/admin/v1/bootstrap",
        headers={"Authorization": f"Bearer {BOOTSTRAP_TOKEN}"},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["raw_key"]


async def _http_make_project_role(
    client, sa_hdr, *, role: str, project_name: str, scopes: list[str]
) -> tuple[str, dict]:
    resp = await client.post("/admin/v1/projects", json={"name": project_name}, headers=sa_hdr)
    assert resp.status_code == 201, resp.text
    project = resp.json()

    resp = await client.post(
        f"/admin/v1/projects/{project['id']}/principals",
        json={"kind": "service_account", "name": f"{project_name}-p"},
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
            "name": f"{project_name}-p-cred",
            "audience": "admin",
            "scopes": scopes,
        },
        headers=sa_hdr,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["raw_key"], project


async def _http_route_seed(client, sa_hdr, alias_name="gpt-4"):
    resp = await client.post(
        "/admin/v1/providers",
        json={"kind": "openai", "name": f"prov-{alias_name}", "capabilities": ["text"]},
        headers=sa_hdr,
    )
    assert resp.status_code == 201, resp.text
    provider = resp.json()

    resp = await client.post(
        "/admin/v1/provider-accounts",
        json={"provider_id": provider["id"], "name": f"acct-{alias_name}"},
        headers=sa_hdr,
    )
    assert resp.status_code == 201, resp.text
    account = resp.json()

    resp = await client.post(
        "/admin/v1/endpoints",
        json={
            "provider_account_id": account["id"],
            "name": f"ep-{alias_name}",
            "base_destination": f"http://{ALLOWED_HOST}",
        },
        headers=sa_hdr,
    )
    assert resp.status_code == 201, resp.text
    endpoint = resp.json()

    resp = await client.post(
        "/admin/v1/model-aliases",
        json={"name": alias_name, "capabilities": ["text"]},
        headers=sa_hdr,
    )
    assert resp.status_code == 201, resp.text
    alias = resp.json()

    resp = await client.post(
        "/admin/v1/route-bindings",
        json={
            "model_alias_id": alias["id"],
            "endpoint_id": endpoint["id"],
            "provider_account_id": account["id"],
            "upstream_model": "m",
        },
        headers=sa_hdr,
    )
    assert resp.status_code == 201, resp.text
    route = resp.json()
    return provider, account, endpoint, alias, route


async def _http_create_request_price_policy(
    client, hdr, route_id, price="0.05", enabled=True, name=None
) -> dict:
    body = {
        "route_binding_id": route_id,
        "billing_unit": "request",
        "currency": "USD",
        "request_price": price,
        "enabled": enabled,
    }
    if name is not None:
        body["name"] = name
    resp = await client.post("/admin/v1/price-policies", json=body, headers=hdr)
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _seed_accounting_graph(session, project_id: str):
    """Seed a full attribution graph and return the relevant IDs."""
    await repository.create_provider(
        session,
        domain.Provider(id=ProviderId("prov"), kind="openai", name="prov"),
    )
    await repository.create_provider_account(
        session,
        domain.ProviderAccount(
            id=ProviderAccountId("acct"), provider_id=ProviderId("prov"), name="acct"
        ),
    )
    await repository.create_endpoint(
        session,
        domain.Endpoint(
            id=EndpointId("ep"),
            provider_account_id=ProviderAccountId("acct"),
            name="ep",
            base_destination=f"http://{ALLOWED_HOST}",
        ),
    )
    await repository.create_model_alias(
        session, domain.ModelAlias(id=ModelAliasId("alias"), name="gpt-4")
    )
    route = await repository.create_route_binding(
        session,
        domain.RouteBinding(
            id=RouteBindingId("rb"),
            model_alias_id=ModelAliasId("alias"),
            endpoint_id=EndpointId("ep"),
            provider_account_id=ProviderAccountId("acct"),
            upstream_model="m",
        ),
    )
    policy = await repository.create_price_policy(
        session,
        domain.PricePolicy(
            id=PricePolicyId("pp"),
            route_binding_id=RouteBindingId("rb"),
            billing_unit=BillingUnit.REQUEST,
            currency="USD",
            request_price=Decimal("0.05"),
        ),
    )
    snapshot = await accounting_repo.create_price_snapshot(
        session,
        snapshot_id="snap",
        source_price_policy_id="pp",
        route_binding_id="rb",
        provider_account_id="acct",
        model_alias_id="alias",
        billing_unit="request",
        currency="USD",
        unit_scale=1,
        request_price=Decimal("0.05"),
        input_price=None,
        output_price=None,
        captured_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    request = models.InferenceRequest(
        id="req",
        project_id=project_id,
        model_alias_id="alias",
        state="succeeded",
        payload_encrypted=b"x",
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    session.add(request)
    await session.flush()
    attempt = models.ExecutionAttempt(
        id="attempt", request_id="req", state="succeeded", fencing_token=1
    )
    session.add(attempt)
    await session.flush()
    usage = models.UsageRecord(
        id="usage",
        request_id="req",
        execution_attempt_id="attempt",
        project_id=project_id,
        model_alias_id="alias",
        route_binding_id="rb",
        provider_account_id="acct",
        price_snapshot_id="snap",
        billing_unit="request",
        input_units=0,
        output_units=0,
        request_units=1,
        amount=Decimal("0.05"),
        currency="USD",
        recorded_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    session.add(usage)
    await session.flush()
    ledger = models.LedgerEntry(
        id="ledger",
        project_id=project_id,
        usage_record_id="usage",
        entry_type="usage_debit",
        amount=Decimal("-0.05"),
        currency="USD",
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        idempotency_key="usage:ledger",
    )
    session.add(ledger)
    await session.flush()
    return {"policy": policy, "route": route, "snapshot": snapshot}


# --- service-layer authorization -----------------------------------------------


async def test_service_layer_accounting_authorization(acct_engine, acct_factory, monkeypatch):
    await reset_schema(acct_engine)
    monkeypatch.setattr(admin_service, "get_settings", _settings)
    async with acct_factory() as s:
        _, sa_raw = await admin_service.bootstrap(s, BOOTSTRAP_TOKEN)

    async with acct_factory() as s:
        async with s.begin():
            sa_ctx = await admin_service.authenticate_admin(s, sa_raw)
            proj = await admin_service.create_project(s, context=sa_ctx, name="acct-auth")
            viewer = await admin_service.create_principal(
                s, context=sa_ctx, project_id=proj.id,
                kind=PrincipalKind.SERVICE_ACCOUNT, name="acct-viewer",
            )
            await admin_service.create_role_assignment(
                s, context=sa_ctx, principal_id=viewer.id, role=Role.PROJECT_VIEWER,
                resource_scope_type=ResourceScopeType.PROJECT, resource_id=proj.id,
            )
            _, viewer_raw = await admin_service.create_credential(
                s, context=sa_ctx, project_id=proj.id, principal_id=viewer.id,
                name="acct-viewer-c", audience=CredentialAudience.ADMIN,
                scopes=tuple(rbac.READ_PERMISSIONS), expires_at=None,
            )

    async with acct_factory() as s:
        async with s.begin():
            viewer_ctx = await admin_service.authenticate_admin(s, viewer_raw)
            # Deployment-scoped pricing is denied to any project-scoped role.
            with pytest.raises(AdminAuthorizationError):
                await accounting_service.list_price_policies(s, context=viewer_ctx)
            with pytest.raises(AdminAuthorizationError):
                await accounting_service.get_price_policy(
                    s, context=viewer_ctx, policy_id=PricePolicyId("any")
                )
            # Project-scoped write denied to a project_viewer.
            with pytest.raises(AdminAuthorizationError):
                await accounting_service.create_project_budget_policy(
                    s, context=viewer_ctx, project_id=proj.id, name="b",
                    currency="USD", limit_amount=Decimal("1"), window_seconds=60,
                )


# --- price policy CRUD ---------------------------------------------------------


async def test_price_policy_crud_and_shape(acct_http):
    client, _ = acct_http
    sa_hdr = {"Authorization": f"Bearer {await _http_bootstrap(client)}"}
    *_, route = await _http_route_seed(client, sa_hdr)

    # Request shape missing request_price => 400.
    resp = await client.post(
        "/admin/v1/price-policies",
        json={"route_binding_id": route["id"], "billing_unit": "request", "currency": "USD"},
        headers=sa_hdr,
    )
    assert resp.status_code == 400

    # Token shape missing input_price => 400.
    resp = await client.post(
        "/admin/v1/price-policies",
        json={
            "route_binding_id": route["id"],
            "billing_unit": "token",
            "currency": "USD",
            "input_price": "0.5",
        },
        headers=sa_hdr,
    )
    assert resp.status_code == 400

    # Float money rejected.
    resp = await client.post(
        "/admin/v1/price-policies",
        json={
            "route_binding_id": route["id"],
            "billing_unit": "request",
            "currency": "USD",
            "request_price": 0.05,
        },
        headers=sa_hdr,
    )
    assert resp.status_code == 400

    # Route binding must exist.
    resp = await client.post(
        "/admin/v1/price-policies",
        json={
            "route_binding_id": "missing",
            "billing_unit": "request",
            "currency": "USD",
            "request_price": "0.05",
        },
        headers=sa_hdr,
    )
    assert resp.status_code == 404

    created = await _http_create_request_price_policy(
        client, sa_hdr, route["id"], name="pp-a"
    )
    assert Decimal(created["request_price"]) == Decimal("0.05")
    assert created["enabled"] is True

    resp = await client.get("/admin/v1/price-policies", headers=sa_hdr)
    assert resp.status_code == 200 and resp.json()["total"] == 1

    resp = await client.get(f"/admin/v1/price-policies/{created['id']}", headers=sa_hdr)
    assert resp.status_code == 200 and resp.json()["name"] == "pp-a"

    # Omitted fields unchanged; explicit null clears name.
    resp = await client.patch(
        f"/admin/v1/price-policies/{created['id']}", json={"name": "pp-b"}, headers=sa_hdr
    )
    assert resp.status_code == 200 and resp.json()["name"] == "pp-b"
    resp = await client.patch(
        f"/admin/v1/price-policies/{created['id']}", json={"name": None}, headers=sa_hdr
    )
    assert resp.status_code == 200 and resp.json()["name"] is None

    # PATCH that would break the resulting request shape => 400.
    resp = await client.patch(
        f"/admin/v1/price-policies/{created['id']}",
        json={"request_price": None},
        headers=sa_hdr,
    )
    assert resp.status_code == 400


async def test_price_policy_one_enabled_per_route(acct_http):
    client, _ = acct_http
    sa_hdr = {"Authorization": f"Bearer {await _http_bootstrap(client)}"}
    *_, route = await _http_route_seed(client, sa_hdr)

    await _http_create_request_price_policy(client, sa_hdr, route["id"])

    # Second enabled policy => 409.
    resp = await client.post(
        "/admin/v1/price-policies",
        json={
            "route_binding_id": route["id"],
            "billing_unit": "request",
            "currency": "USD",
            "request_price": "0.06",
        },
        headers=sa_hdr,
    )
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "price_policy_conflict"

    # Disabled alternate allowed; enabling it conflicts.
    resp = await client.post(
        "/admin/v1/price-policies",
        json={
            "route_binding_id": route["id"],
            "billing_unit": "request",
            "currency": "USD",
            "request_price": "0.06",
            "enabled": False,
        },
        headers=sa_hdr,
    )
    assert resp.status_code == 201
    alternate_id = resp.json()["id"]

    resp = await client.patch(
        f"/admin/v1/price-policies/{alternate_id}", json={"enabled": True}, headers=sa_hdr
    )
    assert resp.status_code == 409


# --- concurrent price-policy race ----------------------------------------------


async def _seed_route_for_race(session) -> str:
    """Seed a provider/account/endpoint/alias/route with no enabled price policy."""
    await repository.create_provider(
        session,
        domain.Provider(id=ProviderId("prov-race"), kind="openai", name="prov-race"),
    )
    await repository.create_provider_account(
        session,
        domain.ProviderAccount(
            id=ProviderAccountId("acct-race"),
            provider_id=ProviderId("prov-race"),
            name="acct-race",
        ),
    )
    await repository.create_endpoint(
        session,
        domain.Endpoint(
            id=EndpointId("ep-race"),
            provider_account_id=ProviderAccountId("acct-race"),
            name="ep-race",
            base_destination=f"http://{ALLOWED_HOST}",
        ),
    )
    await repository.create_model_alias(
        session, domain.ModelAlias(id=ModelAliasId("alias-race"), name="gpt-4")
    )
    await repository.create_route_binding(
        session,
        domain.RouteBinding(
            id=RouteBindingId("rb-race"),
            model_alias_id=ModelAliasId("alias-race"),
            endpoint_id=EndpointId("ep-race"),
            provider_account_id=ProviderAccountId("acct-race"),
            upstream_model="m",
        ),
    )
    return "rb-race"


async def test_concurrent_price_policy_create_race(acct_engine, acct_factory):
    """Two independent sessions race to create the first enabled policy.

    The partial unique index is the authoritative backstop: exactly one winner,
    exactly one typed ``PricePolicyConflictError`` loser, and no raw
    ``IntegrityError`` escapes.
    """
    await reset_schema(acct_engine)
    async with acct_factory() as session:
        async with session.begin():
            route_id = await _seed_route_for_race(session)

    barrier = asyncio.Barrier(2)

    async def attempt(name: str) -> str:
        async with acct_factory() as session:
            async with session.begin():
                await barrier.wait()
                try:
                    await repository.create_price_policy(
                        session,
                        domain.PricePolicy(
                            id=PricePolicyId(f"pp-{name}"),
                            route_binding_id=RouteBindingId(route_id),
                            billing_unit=BillingUnit.REQUEST,
                            currency="USD",
                            request_price=Decimal("0.05"),
                            enabled=True,
                        ),
                    )
                except PricePolicyConflictError:
                    return "conflict"
                return "winner"

    results = await asyncio.gather(attempt("a"), attempt("b"))
    assert sorted(results) == ["conflict", "winner"]

    async with acct_factory() as session:
        async with session.begin():
            enabled = await repository.count_price_policies(
                session, route_binding_id=RouteBindingId(route_id), enabled=True
            )
    assert enabled == 1

    # The loser's aborted transaction did not corrupt subsequent requests: a
    # disabled alternate can still be created.
    async with acct_factory() as session:
        async with session.begin():
            await repository.create_price_policy(
                session,
                domain.PricePolicy(
                    id=PricePolicyId("pp-disabled-after"),
                    route_binding_id=RouteBindingId(route_id),
                    billing_unit=BillingUnit.REQUEST,
                    currency="USD",
                    request_price=Decimal("0.06"),
                    enabled=False,
                ),
            )


async def test_concurrent_price_policy_enable_race(acct_engine, acct_factory):
    """Two disabled policies race to enable; only one may become active."""
    await reset_schema(acct_engine)
    async with acct_factory() as session:
        async with session.begin():
            route_id = await _seed_route_for_race(session)
            await repository.create_price_policy(
                session,
                domain.PricePolicy(
                    id=PricePolicyId("pp-a"),
                    route_binding_id=RouteBindingId(route_id),
                    billing_unit=BillingUnit.REQUEST,
                    currency="USD",
                    request_price=Decimal("0.05"),
                    enabled=False,
                ),
            )
            await repository.create_price_policy(
                session,
                domain.PricePolicy(
                    id=PricePolicyId("pp-b"),
                    route_binding_id=RouteBindingId(route_id),
                    billing_unit=BillingUnit.REQUEST,
                    currency="USD",
                    request_price=Decimal("0.06"),
                    enabled=False,
                ),
            )

    barrier = asyncio.Barrier(2)

    async def enable(policy_id: str, price: str) -> str:
        async with acct_factory() as session:
            async with session.begin():
                await barrier.wait()
                try:
                    await repository.update_price_policy(
                        session,
                        PricePolicyId(policy_id),
                        billing_unit=BillingUnit.REQUEST.value,
                        currency="USD",
                        unit_scale=1,
                        request_price=Decimal(price),
                        input_price=None,
                        output_price=None,
                        enabled=True,
                        name=None,
                    )
                except PricePolicyConflictError:
                    return "conflict"
                return "winner"

    results = await asyncio.gather(enable("pp-a", "0.05"), enable("pp-b", "0.06"))
    assert sorted(results) == ["conflict", "winner"]

    async with acct_factory() as session:
        async with session.begin():
            enabled = await repository.count_price_policies(
                session, route_binding_id=RouteBindingId(route_id), enabled=True
            )
    assert enabled == 1


async def test_price_snapshot_readonly_and_immutable(acct_http):
    client, factory = acct_http
    sa_hdr = {"Authorization": f"Bearer {await _http_bootstrap(client)}"}
    *_, route = await _http_route_seed(client, sa_hdr)
    policy = await _http_create_request_price_policy(client, sa_hdr, route["id"], "0.05")

    async with factory() as s:
        async with s.begin():
            await accounting_repo.create_price_snapshot(
                s,
                snapshot_id="snap",
                source_price_policy_id=policy["id"],
                route_binding_id=route["id"],
                provider_account_id=route["provider_account_id"],
                model_alias_id=route["model_alias_id"],
                billing_unit="request",
                currency="USD",
                unit_scale=1,
                request_price=Decimal("0.05"),
                input_price=None,
                output_price=None,
                captured_at=datetime(2026, 1, 1, tzinfo=UTC),
            )

    # Edit the mutable policy; the snapshot must not change.
    resp = await client.patch(
        f"/admin/v1/price-policies/{policy['id']}",
        json={"request_price": "0.99"},
        headers=sa_hdr,
    )
    assert resp.status_code == 200

    resp = await client.get("/admin/v1/price-snapshots", headers=sa_hdr)
    assert resp.status_code == 200
    snapshots = resp.json()["items"]
    assert len(snapshots) == 1
    assert Decimal(snapshots[0]["request_price"]) == Decimal("0.05")
    assert snapshots[0]["source_price_policy_id"] == policy["id"]

    resp = await client.get(f"/admin/v1/price-snapshots/{snapshots[0]['id']}", headers=sa_hdr)
    assert resp.status_code == 200 and Decimal(resp.json()["request_price"]) == Decimal("0.05")

    # Read-only surface: no POST/PATCH/DELETE.
    resp = await client.post("/admin/v1/price-snapshots", json={}, headers=sa_hdr)
    assert resp.status_code in (404, 405)


# --- project budget policies ---------------------------------------------------


async def test_project_budget_policy_rbac_and_non_enumeration(acct_http):
    client, _ = acct_http
    sa_hdr = {"Authorization": f"Bearer {await _http_bootstrap(client)}"}
    pa_raw, proj_a = await _http_make_project_role(
        client, sa_hdr, role="project_admin", project_name="proj-a",
        scopes=ADMIN_SCOPE_VALUES,
    )
    pb_raw, proj_b = await _http_make_project_role(
        client, sa_hdr, role="project_admin", project_name="proj-b",
        scopes=ADMIN_SCOPE_VALUES,
    )
    pa_hdr = {"Authorization": f"Bearer {pa_raw}"}
    pb_hdr = {"Authorization": f"Bearer {pb_raw}"}

    resp = await client.post(
        "/admin/v1/project-budget-policies",
        json={
            "project_id": proj_a["id"],
            "name": "a-budget",
            "currency": "USD",
            "limit_amount": "10",
            "window_seconds": 3600,
        },
        headers=pa_hdr,
    )
    assert resp.status_code == 201
    policy_id = resp.json()["id"]

    # project_admin sees its own policy.
    resp = await client.get(
        f"/admin/v1/project-budget-policies?project_id={proj_a['id']}", headers=pa_hdr
    )
    assert resp.status_code == 200 and resp.json()["total"] == 1

    # Cross-project enumeration returns an empty page (not an error).
    resp = await client.get(
        f"/admin/v1/project-budget-policies?project_id={proj_b['id']}", headers=pa_hdr
    )
    assert resp.status_code == 200 and resp.json()["total"] == 0

    # system_admin can read any project's policy by ID.
    resp = await client.get(
        f"/admin/v1/project-budget-policies/{policy_id}", headers=sa_hdr
    )
    assert resp.status_code == 200

    # project B admin cannot read project A's policy by ID (non-enumerating 404).
    resp = await client.get(
        f"/admin/v1/project-budget-policies/{policy_id}", headers=pb_hdr
    )
    assert resp.status_code == 404


async def test_budget_policy_immutable_currency_window(acct_http):
    client, _ = acct_http
    sa_hdr = {"Authorization": f"Bearer {await _http_bootstrap(client)}"}
    resp = await client.post("/admin/v1/projects", json={"name": "budget-proj"}, headers=sa_hdr)
    project = resp.json()

    resp = await client.post(
        "/admin/v1/project-budget-policies",
        json={
            "project_id": project["id"],
            "name": "b",
            "currency": "USD",
            "limit_amount": "10",
            "window_seconds": 3600,
        },
        headers=sa_hdr,
    )
    assert resp.status_code == 201
    policy_id = resp.json()["id"]

    # Currency and window_seconds immutable.
    resp = await client.patch(
        f"/admin/v1/project-budget-policies/{policy_id}",
        json={"currency": "EUR"},
        headers=sa_hdr,
    )
    assert resp.status_code == 400
    resp = await client.patch(
        f"/admin/v1/project-budget-policies/{policy_id}",
        json={"window_seconds": 60},
        headers=sa_hdr,
    )
    assert resp.status_code == 400

    # name/limit/enabled mutable.
    resp = await client.patch(
        f"/admin/v1/project-budget-policies/{policy_id}",
        json={"name": "b2", "limit_amount": "20", "enabled": False},
        headers=sa_hdr,
    )
    assert resp.status_code == 200
    assert resp.json()["name"] == "b2"
    assert Decimal(resp.json()["limit_amount"]) == Decimal("20")
    assert resp.json()["enabled"] is False
    assert resp.json()["currency"] == "USD"
    assert resp.json()["window_seconds"] == 3600


async def test_budget_status_zero_state_and_headroom(acct_http):
    client, factory = acct_http
    sa_hdr = {"Authorization": f"Bearer {await _http_bootstrap(client)}"}
    resp = await client.post("/admin/v1/projects", json={"name": "status-proj"}, headers=sa_hdr)
    project = resp.json()

    resp = await client.post(
        "/admin/v1/project-budget-policies",
        json={
            "project_id": project["id"],
            "name": "b",
            "currency": "USD",
            "limit_amount": "10",
            "window_seconds": 3600,
        },
        headers=sa_hdr,
    )
    policy_id = resp.json()["id"]

    # Zero-state: no window row yet -> committed/reserved zero, headroom == limit.
    resp = await client.get(
        f"/admin/v1/projects/{project['id']}/budget-status", headers=sa_hdr
    )
    assert resp.status_code == 200
    status = resp.json()
    assert len(status) == 1
    assert Decimal(status[0]["committed_amount"]) == Decimal("0")
    assert Decimal(status[0]["reserved_amount"]) == Decimal("0")
    assert Decimal(status[0]["headroom"]) == Decimal("10")
    assert Decimal(status[0]["limit_amount"]) == Decimal("10")
    assert status[0]["enabled"] is True

    # Seed a window with committed/reserved; headroom reflects it exactly.
    window_start = sched_repo.fixed_window_start(datetime.now(UTC), 3600)
    async with factory() as s:
        async with s.begin():
            s.add(
                models.BudgetWindow(
                    id=_new_id(),
                    budget_policy_id=policy_id,
                    window_start=window_start,
                    committed_amount=Decimal("3"),
                    reserved_amount=Decimal("1"),
                )
            )
            await s.flush()

    resp = await client.get(
        f"/admin/v1/projects/{project['id']}/budget-status", headers=sa_hdr
    )
    status = resp.json()[0]
    assert Decimal(status["committed_amount"]) == Decimal("3")
    assert Decimal(status["reserved_amount"]) == Decimal("1")
    assert Decimal(status["headroom"]) == Decimal("6")

    # Repeated reads do not create window/reservation history.
    async with factory() as s:
        async with s.begin():
            windows = (await s.execute(models.BudgetWindow.__table__.select())).all()
            reservations = (
                await s.execute(models.BudgetReservation.__table__.select())
            ).all()
    assert len(windows) == 1
    assert len(reservations) == 0


# --- budget reservations -------------------------------------------------------


async def test_budget_reservation_nullable_snapshot(acct_http):
    client, factory = acct_http
    sa_hdr = {"Authorization": f"Bearer {await _http_bootstrap(client)}"}
    resp = await client.post("/admin/v1/projects", json={"name": "resv-proj"}, headers=sa_hdr)
    project = resp.json()

    resp = await client.post(
        "/admin/v1/project-budget-policies",
        json={
            "project_id": project["id"],
            "name": "b",
            "currency": "USD",
            "limit_amount": "10",
            "window_seconds": 3600,
        },
        headers=sa_hdr,
    )
    policy_id = resp.json()["id"]

    async with factory() as s:
        async with s.begin():
            await repository.create_model_alias(
                s, domain.ModelAlias(id=ModelAliasId("alias"), name="gpt-4")
            )
            s.add(
                models.InferenceRequest(
                    id="req-resv",
                    project_id=project["id"],
                    model_alias_id="alias",
                    state="succeeded",
                    payload_encrypted=b"x",
                    expires_at=datetime.now(UTC) + timedelta(hours=1),
                )
            )
            await s.flush()
            s.add(
                models.BudgetReservation(
                    id="resv-1",
                    request_id="req-resv",
                    budget_policy_id=policy_id,
                    price_snapshot_id=None,
                    window_start=sched_repo.fixed_window_start(datetime.now(UTC), 3600),
                    reserved_amount=Decimal("0"),
                    committed_amount=Decimal("0"),
                    state="released",
                )
            )
            await s.flush()

    resp = await client.get("/admin/v1/budget-reservations", headers=sa_hdr)
    assert resp.status_code == 200
    assert resp.json()["total"] == 1
    item = resp.json()["items"][0]
    assert item["state"] == "released"
    assert item["price_snapshot_id"] is None


# --- usage and ledger reads ----------------------------------------------------


async def test_usage_and_ledger_reads(acct_http):
    client, factory = acct_http
    sa_hdr = {"Authorization": f"Bearer {await _http_bootstrap(client)}"}
    resp = await client.post("/admin/v1/projects", json={"name": "usage-proj"}, headers=sa_hdr)
    project = resp.json()

    async with factory() as s:
        async with s.begin():
            await _seed_accounting_graph(s, project["id"])

    resp = await client.get("/admin/v1/usage-records", headers=sa_hdr)
    assert resp.status_code == 200 and resp.json()["total"] == 1
    usage = resp.json()["items"][0]
    assert usage["project_id"] == project["id"]
    assert Decimal(usage["amount"]) == Decimal("0.05")
    assert usage["currency"] == "USD"

    resp = await client.get(
        f"/admin/v1/usage-records?project_id={project['id']}", headers=sa_hdr
    )
    assert resp.status_code == 200 and resp.json()["total"] == 1

    resp = await client.get(f"/admin/v1/usage-records/{usage['id']}", headers=sa_hdr)
    assert resp.status_code == 200

    resp = await client.get("/admin/v1/ledger-entries", headers=sa_hdr)
    assert resp.status_code == 200 and resp.json()["total"] == 1
    ledger = resp.json()["items"][0]
    assert ledger["entry_type"] == "usage_debit"
    assert Decimal(ledger["amount"]) == Decimal("-0.05")  # signed Decimal preserved

    resp = await client.get(
        f"/admin/v1/ledger-entries/{ledger['id']}", headers=sa_hdr
    )
    assert resp.status_code == 200


async def test_usage_ledger_non_enumeration(acct_http):
    client, factory = acct_http
    sa_hdr = {"Authorization": f"Bearer {await _http_bootstrap(client)}"}
    resp = await client.post("/admin/v1/projects", json={"name": "enum-proj-a"}, headers=sa_hdr)
    proj_a = resp.json()
    await client.post("/admin/v1/projects", json={"name": "enum-proj-b"}, headers=sa_hdr)

    async with factory() as s:
        async with s.begin():
            await _seed_accounting_graph(s, proj_a["id"])

    pa_raw, _ = await _http_make_project_role(
        client, sa_hdr, role="project_admin", project_name="enum-proj-c",
        scopes=ADMIN_SCOPE_VALUES,
    )
    pa_hdr = {"Authorization": f"Bearer {pa_raw}"}

    # Project-scoped caller cannot enumerate project A's usage/ledger.
    resp = await client.get("/admin/v1/usage-records", headers=pa_hdr)
    assert resp.status_code == 200 and resp.json()["total"] == 0
    resp = await client.get("/admin/v1/ledger-entries", headers=pa_hdr)
    assert resp.status_code == 200 and resp.json()["total"] == 0

    # Cross-project ID lookup is non-enumerating (404), not a leak.
    resp = await client.get("/admin/v1/usage-records/usage", headers=pa_hdr)
    assert resp.status_code == 404
    resp = await client.get("/admin/v1/ledger-entries/ledger", headers=pa_hdr)
    assert resp.status_code == 404


# --- audit reads ---------------------------------------------------------------


async def test_audit_reads_scoped(acct_http):
    client, _ = acct_http
    sa_hdr = {"Authorization": f"Bearer {await _http_bootstrap(client)}"}
    *_, route = await _http_route_seed(client, sa_hdr)

    pa_raw, proj_a = await _http_make_project_role(
        client, sa_hdr, role="project_admin", project_name="audit-proj",
        scopes=ADMIN_SCOPE_VALUES,
    )
    pa_hdr = {"Authorization": f"Bearer {pa_raw}"}

    # Deployment-scoped price policy audit (system_admin actor).
    await _http_create_request_price_policy(client, sa_hdr, route["id"], name="audit-pp")
    # Project-scoped budget audit.
    resp = await client.post(
        "/admin/v1/project-budget-policies",
        json={
            "project_id": proj_a["id"],
            "name": "b",
            "currency": "USD",
            "limit_amount": "10",
            "window_seconds": 3600,
        },
        headers=pa_hdr,
    )
    assert resp.status_code == 201

    # system_admin sees both deployment and project events.
    resp = await client.get("/admin/v1/audit-events", headers=sa_hdr)
    assert resp.status_code == 200
    actions = {e["action"] for e in resp.json()["items"]}
    assert "price_policy.created" in actions
    assert "project_budget_policy.created" in actions

    # project_admin sees its own project's budget event, not the deployment one.
    resp = await client.get("/admin/v1/audit-events", headers=pa_hdr)
    assert resp.status_code == 200
    actions = {e["action"] for e in resp.json()["items"]}
    assert "project_budget_policy.created" in actions
    assert "price_policy.created" not in actions


async def test_noop_patch_no_audit_and_no_secrets(acct_http):
    client, factory = acct_http
    sa_hdr = {"Authorization": f"Bearer {await _http_bootstrap(client)}"}
    *_, route = await _http_route_seed(client, sa_hdr)
    policy = await _http_create_request_price_policy(
        client, sa_hdr, route["id"], "0.05", name="noop-pp"
    )

    # No-op PATCH emits no update audit event.
    resp = await client.patch(
        f"/admin/v1/price-policies/{policy['id']}", json={"name": "noop-pp"}, headers=sa_hdr
    )
    assert resp.status_code == 200

    async with factory() as s:
        async with s.begin():
            events = await repository.list_audit_events(s)
    updated = [e for e in events if e.action == "price_policy.updated"]
    assert len(updated) == 0
    for e in events:
        blob = str(e.metadata) + e.resource_id + e.action + e.resource_type
        assert BOOTSTRAP_TOKEN not in blob


async def test_bearer_accounting_mutation_no_csrf(acct_http):
    client, _ = acct_http
    sa_hdr = {"Authorization": f"Bearer {await _http_bootstrap(client)}"}
    *_, route = await _http_route_seed(client, sa_hdr)
    resp = await client.post(
        "/admin/v1/price-policies",
        json={
            "route_binding_id": route["id"],
            "billing_unit": "request",
            "currency": "USD",
            "request_price": "0.05",
        },
        headers=sa_hdr,
    )
    assert resp.status_code == 201
