"""Human CLI (OAuth device flow) + CLI session tests (unit + DB-gated integration)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import pytest
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from aethergate.config import Settings
from aethergate.dev_oidc_idp import DevOidcIdp
from aethergate.domain.enums import (
    AdminAuthenticationKind,
    PrincipalKind,
    ResourceScopeType,
    Role,
)
from aethergate.domain.ids import PrincipalId
from aethergate.identity import admin as admin_service
from aethergate.identity import cli_session as cli_session_service
from aethergate.identity import oidc as oidc_module
from aethergate.identity import session as session_service
from aethergate.main import app
from aethergate.persistence import db as persistence_db
from aethergate.persistence import models, repository
from db_helpers import (
    TEST_DATABASE_URL,
    ensure_database,
    reset_schema,
    url_for_database,
)

ISSUER = "http://idp.test"
CLIENT_ID = "ag-client"
DEVICE_CLIENT_ID = "ag-device-client"
REDIRECT_URI = "http://test/admin/v1/auth/oidc/callback"
BOOTSTRAP_TOKEN = "test-bootstrap-secret-0123456789abcdefghij"
SUBJECT = "dev-user"


class _Clock:
    """A controllable clock for driving gateway poll-interval enforcement."""

    def __init__(self) -> None:
        self._now = datetime.now(UTC)

    def now(self) -> datetime:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now += timedelta(seconds=seconds)


def _settings(**overrides) -> Settings:
    base: dict = {
        "database_url": "postgresql+asyncpg://u:p@h/db",
        "app_env": "dev",
        "bootstrap_token": BOOTSTRAP_TOKEN,
        "oidc_enabled": True,
        "oidc_issuer": ISSUER,
        "oidc_client_id": CLIENT_ID,
        "oidc_client_secret": None,
        "oidc_redirect_uri": REDIRECT_URI,
        "oidc_scopes": "openid",
        "oidc_device_client_id": DEVICE_CLIENT_ID,
        "oidc_device_scopes": "openid",
        "oidc_jit_provisioning": False,
        "oidc_jit_project_name": "default",
        "oidc_session_idle_seconds": 3600,
        "oidc_session_absolute_seconds": 7200,
        "oidc_login_ttl_seconds": 600,
        "cli_session_ttl_seconds": 43200,
    }
    base.update(overrides)
    return Settings(**base)


# ---------------------------------------------------------------------------
# Unit tests (no DB)
# ---------------------------------------------------------------------------


def test_cli_session_token_format():
    tokens = {cli_session_service.generate_cli_session_token() for _ in range(20)}
    assert len(tokens) == 20
    for token in tokens:
        assert token.startswith(cli_session_service.CLI_SESSION_PREFIX)
        assert token != cli_session_service.CLI_SESSION_PREFIX
        # ags_<8-hex>_<43-char base64url secret> => 256 bits of CSPRNG entropy.
        assert len(token) >= len(cli_session_service.CLI_SESSION_PREFIX) + 8 + 1 + 43


def test_device_flow_enabled_flag():
    assert _settings().device_flow_enabled is True
    assert _settings(oidc_device_client_id=None).device_flow_enabled is False
    assert _settings(oidc_enabled=False).device_flow_enabled is False


def test_device_scope_list_openid_first():
    assert _settings().oidc_device_scope_list == ["openid"]
    assert _settings(oidc_device_scopes="profile email").oidc_device_scope_list == [
        "openid",
        "profile",
        "email",
    ]


def test_cli_session_ttl_positive():
    with pytest.raises(ValidationError):
        _settings(cli_session_ttl_seconds=0)


# --- deterministic IdP device grant (no DB) ------------------------------------


def _idp_http(idp: DevOidcIdp) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=idp.app), base_url=ISSUER)


async def _start_device(http: httpx.AsyncClient) -> dict:
    resp = await http.post(
        "/device_authorization",
        data={"client_id": DEVICE_CLIENT_ID, "scope": "openid"},
    )
    assert resp.status_code == 200
    return resp.json()


def _poll_body(device_code: str) -> dict:
    return {
        "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
        "device_code": device_code,
        "client_id": DEVICE_CLIENT_ID,
    }


@pytest.mark.skipif(TEST_DATABASE_URL is None, reason="AETHERGATE_TEST_DATABASE_URL not set")
async def test_idp_device_pending_then_approve():
    idp = DevOidcIdp(issuer=ISSUER, client_id=CLIENT_ID, device_client_id=DEVICE_CLIENT_ID)
    http = _idp_http(idp)
    try:
        started = await _start_device(http)
        assert started["device_code"]
        assert started["user_code"]
        assert started["verification_uri"]

        pending = await http.post("/token", data=_poll_body(started["device_code"]))
        assert pending.status_code == 400
        assert pending.json()["error"] == "authorization_pending"

        approve = await http.post("/device/approve", json={"user_code": started["user_code"]})
        assert approve.status_code == 200

        ok = await http.post("/token", data=_poll_body(started["device_code"]))
        assert ok.status_code == 200
        assert ok.json()["access_token"]
        assert ok.json()["id_token"]
    finally:
        await http.aclose()


@pytest.mark.skipif(TEST_DATABASE_URL is None, reason="AETHERGATE_TEST_DATABASE_URL not set")
async def test_idp_device_denied_and_replay():
    idp = DevOidcIdp(issuer=ISSUER, client_id=CLIENT_ID, device_client_id=DEVICE_CLIENT_ID)
    http = _idp_http(idp)
    try:
        started = await _start_device(http)
        await http.post("/device/deny", json={"user_code": started["user_code"]})
        denied = await http.post("/token", data=_poll_body(started["device_code"]))
        assert denied.status_code == 400
        assert denied.json()["error"] == "access_denied"

        # An approved-then-consumed code rejects a replay as invalid_grant.
        started2 = await _start_device(http)
        await http.post("/device/approve", json={"user_code": started2["user_code"]})
        ok = await http.post("/token", data=_poll_body(started2["device_code"]))
        assert ok.status_code == 200
        replay = await http.post("/token", data=_poll_body(started2["device_code"]))
        assert replay.status_code == 400
        assert replay.json()["error"] == "invalid_grant"
    finally:
        await http.aclose()


@pytest.mark.skipif(TEST_DATABASE_URL is None, reason="AETHERGATE_TEST_DATABASE_URL not set")
async def test_idp_device_expired():
    idp = DevOidcIdp(issuer=ISSUER, client_id=CLIENT_ID, device_client_id=DEVICE_CLIENT_ID)
    http = _idp_http(idp)
    try:
        started = await _start_device(http)
        await http.post("/device/expire", json={"user_code": started["user_code"]})
        expired = await http.post("/token", data=_poll_body(started["device_code"]))
        assert expired.status_code == 400
        assert expired.json()["error"] == "expired_token"
    finally:
        await http.aclose()


# ---------------------------------------------------------------------------
# DB-gated integration
# ---------------------------------------------------------------------------


@pytest.fixture
async def cli_engine():
    if not TEST_DATABASE_URL:
        pytest.skip("AETHERGATE_TEST_DATABASE_URL not set")
    url = url_for_database(TEST_DATABASE_URL, "aethergate_test_cli")
    await ensure_database(url)
    engine = create_async_engine(url)
    yield engine
    await engine.dispose()


@pytest.fixture
def cli_factory(cli_engine):
    return async_sessionmaker(cli_engine, expire_on_commit=False)


async def _provision_linked_user(factory, idp: DevOidcIdp, role: Role) -> PrincipalId:
    async with factory() as s:
        _, raw = await admin_service.bootstrap(s, BOOTSTRAP_TOKEN)
    async with factory() as s:
        async with s.begin():
            ctx = await admin_service.authenticate_admin(s, raw)
            project = await repository.get_project(s, ctx.project_id)
            assert project is not None
            principal = await admin_service.create_principal(
                s, context=ctx, project_id=project.id, kind=PrincipalKind.USER, name="human-a"
            )
            await admin_service.create_role_assignment(
                s,
                context=ctx,
                principal_id=principal.id,
                role=role,
                resource_scope_type=(
                    ResourceScopeType.DEPLOYMENT
                    if role is Role.SYSTEM_ADMIN
                    else ResourceScopeType.PROJECT
                ),
                resource_id=None if role is Role.SYSTEM_ADMIN else project.id,
            )
            await session_service.link_external_identity(
                s,
                principal_id=principal.id,
                issuer=ISSUER,
                subject=idp.subject,
                now=session_service.utcnow(),
            )
            return principal.id


@pytest.fixture
async def cli_http(cli_engine, cli_factory, monkeypatch):
    await reset_schema(cli_engine)
    settings = _settings()
    monkeypatch.setattr("aethergate.identity.session.get_settings", lambda: settings)
    monkeypatch.setattr("aethergate.identity.admin.get_settings", lambda: settings)
    monkeypatch.setattr("aethergate.identity.cli_session.get_settings", lambda: settings)
    monkeypatch.setattr("aethergate.api.session_auth.get_settings", lambda: settings)
    monkeypatch.setattr("aethergate.api.cli_auth.get_settings", lambda: settings)
    monkeypatch.setattr("aethergate.api.admin.get_session_factory", lambda: cli_factory)

    idp = DevOidcIdp(
        issuer=ISSUER,
        client_id=CLIENT_ID,
        device_client_id=DEVICE_CLIENT_ID,
        subject=SUBJECT,
        email="h@example.com",
        device_interval=1,
    )
    idp_transport = httpx.ASGITransport(app=idp.app)
    idp_client = httpx.AsyncClient(transport=idp_transport, base_url=ISSUER)
    provider = oidc_module.OidcProvider(settings=settings, http_client=idp_client)
    monkeypatch.setattr("aethergate.identity.oidc.get_oidc_provider", lambda: provider)

    clock = _Clock()
    monkeypatch.setattr("aethergate.identity.cli_session.utcnow", clock.now)

    async def _test_session():
        async with cli_factory() as s:
            yield s

    app.dependency_overrides[persistence_db.get_session] = _test_session
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, idp_client, cli_factory, idp, clock
    app.dependency_overrides.clear()
    await idp_client.aclose()
    await reset_schema(cli_engine)


async def _device_start(client) -> dict:
    resp = await client.post("/admin/v1/auth/device/start")
    assert resp.status_code == 200
    return resp.json()


def _device_poll(client, device_code: str) -> httpx.Response:
    return client.post(
        "/admin/v1/auth/device/poll", json={"device_code": device_code}
    )


async def _device_login(client, idp_client, clock: _Clock) -> tuple[dict, str, str]:
    """Drive start -> pending -> approve -> success; return (success_body, token, device_code)."""
    started = await _device_start(client)
    device_code = started["device_code"]

    pending = await _device_poll(client, device_code)
    assert pending.status_code == 200
    assert pending.json()["status"] == "pending"

    approve = await idp_client.post(
        "/device/approve", json={"user_code": started["user_code"]}
    )
    assert approve.status_code == 200

    # Respect the provider polling interval before the success poll.
    clock.advance(2)

    success = await _device_poll(client, device_code)
    assert success.status_code == 200
    body = success.json()
    assert body["status"] == "success"
    return body, body["token"], device_code


@pytest.mark.skipif(TEST_DATABASE_URL is None, reason="AETHERGATE_TEST_DATABASE_URL not set")
async def test_device_flow_happy_path(cli_http):
    client, idp_client, factory, idp, clock = cli_http
    await _provision_linked_user(factory, idp, Role.SYSTEM_ADMIN)
    body, token, _ = await _device_login(client, idp_client, clock)

    assert body["session"]["authentication_kind"] == "cli_session"
    assert "system_admin" in body["session"]["roles"]
    assert token.startswith(cli_session_service.CLI_SESSION_PREFIX)

    who = await client.get("/admin/v1/whoami", headers={"Authorization": f"Bearer {token}"})
    assert who.status_code == 200
    assert who.json()["authentication_kind"] == "cli_session"
    assert who.json()["api_credential_id"] is None
    assert who.json()["cli_session_id"] is not None
    assert "system_admin" in who.json()["roles"]


@pytest.mark.skipif(TEST_DATABASE_URL is None, reason="AETHERGATE_TEST_DATABASE_URL not set")
async def test_device_flow_unavailable_without_client(cli_http, cli_factory, monkeypatch):
    client, idp_client, factory, idp, clock = cli_http
    settings = _settings(oidc_device_client_id=None)
    monkeypatch.setattr("aethergate.api.cli_auth.get_settings", lambda: settings)
    resp = await client.post("/admin/v1/auth/device/start")
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "device_flow_unavailable"


@pytest.mark.skipif(TEST_DATABASE_URL is None, reason="AETHERGATE_TEST_DATABASE_URL not set")
async def test_device_code_invalid_unknown(cli_http):
    client, idp_client, factory, idp, clock = cli_http
    resp = await _device_poll(client, "bogus-device-code")
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "device_code_invalid"


@pytest.mark.skipif(TEST_DATABASE_URL is None, reason="AETHERGATE_TEST_DATABASE_URL not set")
async def test_device_replay_rejected(cli_http):
    client, idp_client, factory, idp, clock = cli_http
    await _provision_linked_user(factory, idp, Role.SYSTEM_ADMIN)
    _, _, device_code = await _device_login(client, idp_client, clock)

    replay = await _device_poll(client, device_code)
    assert replay.status_code == 400
    assert replay.json()["error"]["code"] == "device_code_invalid"


@pytest.mark.skipif(TEST_DATABASE_URL is None, reason="AETHERGATE_TEST_DATABASE_URL not set")
async def test_device_expired(cli_http):
    client, idp_client, factory, idp, clock = cli_http
    await _provision_linked_user(factory, idp, Role.SYSTEM_ADMIN)
    started = await _device_start(client)
    await idp_client.post("/device/expire", json={"user_code": started["user_code"]})

    resp = await _device_poll(client, started["device_code"])
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "device_expired"


@pytest.mark.skipif(TEST_DATABASE_URL is None, reason="AETHERGATE_TEST_DATABASE_URL not set")
async def test_device_denied(cli_http):
    client, idp_client, factory, idp, clock = cli_http
    await _provision_linked_user(factory, idp, Role.SYSTEM_ADMIN)
    started = await _device_start(client)
    await idp_client.post("/device/deny", json={"user_code": started["user_code"]})

    resp = await _device_poll(client, started["device_code"])
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "device_access_denied"


@pytest.mark.skipif(TEST_DATABASE_URL is None, reason="AETHERGATE_TEST_DATABASE_URL not set")
async def test_device_slow_down_enforced(cli_http):
    client, idp_client, factory, idp, clock = cli_http
    await _provision_linked_user(factory, idp, Role.SYSTEM_ADMIN)
    started = await _device_start(client)

    first = await _device_poll(client, started["device_code"])
    assert first.json()["status"] == "pending"

    # A second poll immediately (within the interval) is throttled by the gateway.
    fast = await _device_poll(client, started["device_code"])
    assert fast.status_code == 200
    assert fast.json()["status"] == "slow_down"


@pytest.mark.skipif(TEST_DATABASE_URL is None, reason="AETHERGATE_TEST_DATABASE_URL not set")
async def test_role_revocation_reflected_next_request(cli_http):
    client, idp_client, factory, idp, clock = cli_http
    principal_id = await _provision_linked_user(factory, idp, Role.SYSTEM_ADMIN)
    _, token, _ = await _device_login(client, idp_client, clock)

    who = await client.get("/admin/v1/whoami", headers={"Authorization": f"Bearer {token}"})
    assert "system_admin" in who.json()["roles"]

    async with factory() as s:
        async with s.begin():
            assignments = await repository.list_active_role_assignments(s, principal_id)
            await repository.revoke_role_assignment(
                s, assignments[0].id, session_service.utcnow()
            )

    who2 = await client.get("/admin/v1/whoami", headers={"Authorization": f"Bearer {token}"})
    assert who2.status_code == 200
    assert "system_admin" not in who2.json()["roles"]


@pytest.mark.skipif(TEST_DATABASE_URL is None, reason="AETHERGATE_TEST_DATABASE_URL not set")
async def test_cli_logout_revokes(cli_http):
    client, idp_client, factory, idp, clock = cli_http
    await _provision_linked_user(factory, idp, Role.SYSTEM_ADMIN)
    _, token, _ = await _device_login(client, idp_client, clock)

    hdr = {"Authorization": f"Bearer {token}"}
    logout = await client.post("/admin/v1/auth/cli/logout", headers=hdr)
    assert logout.status_code == 200
    assert logout.json()["revoked"] is True

    who = await client.get("/admin/v1/whoami", headers=hdr)
    assert who.status_code == 401


@pytest.mark.skipif(TEST_DATABASE_URL is None, reason="AETHERGATE_TEST_DATABASE_URL not set")
async def test_cli_session_token_not_persisted(cli_http):
    client, idp_client, factory, idp, clock = cli_http
    await _provision_linked_user(factory, idp, Role.SYSTEM_ADMIN)
    _, token, device_code = await _device_login(client, idp_client, clock)

    async with factory() as s:
        rows = (await s.execute(select(models.CliSession))).scalars().all()
        assert len(rows) == 1
        assert rows[0].token_hash != token
        assert rows[0].token_hash == session_service.hash_verifier(token)

    async with factory() as s:
        dev_rows = (await s.execute(select(models.DeviceAuthorization))).scalars().all()
        assert len(dev_rows) == 1
        assert dev_rows[0].device_code_hash == session_service.hash_verifier(device_code)
        assert device_code not in dev_rows[0].device_code_hash


@pytest.mark.skipif(TEST_DATABASE_URL is None, reason="AETHERGATE_TEST_DATABASE_URL not set")
async def test_cli_session_context_has_no_credential(cli_http):
    client, idp_client, factory, idp, clock = cli_http
    await _provision_linked_user(factory, idp, Role.SYSTEM_ADMIN)
    _, token, _ = await _device_login(client, idp_client, clock)

    async with factory() as s:
        async with s.begin():
            ctx = await admin_service.authenticate_cli_session(s, token)
            assert ctx.authentication_kind is AdminAuthenticationKind.CLI_SESSION
            assert ctx.api_credential_id is None
            assert ctx.cli_session_id is not None
            assert ctx.scopes == ()
            assert ctx.audience is None


@pytest.mark.skipif(TEST_DATABASE_URL is None, reason="AETHERGATE_TEST_DATABASE_URL not set")
async def test_cli_session_invalid_token_rejected(cli_http):
    client, idp_client, factory, idp, clock = cli_http
    await _provision_linked_user(factory, idp, Role.SYSTEM_ADMIN)
    resp = await client.get(
        "/admin/v1/whoami", headers={"Authorization": "Bearer ags_deadbeef_invalid"}
    )
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "not_authenticated"


@pytest.mark.skipif(TEST_DATABASE_URL is None, reason="AETHERGATE_TEST_DATABASE_URL not set")
async def test_cli_session_cannot_authenticate_inference(cli_http):
    client, idp_client, factory, idp, clock = cli_http
    await _provision_linked_user(factory, idp, Role.SYSTEM_ADMIN)
    _, token, _ = await _device_login(client, idp_client, clock)
    # The CLI session token is admin-audience only and must not work on /v1.
    resp = await client.get("/v1/models", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 401
