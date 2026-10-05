"""OIDC human identity + browser session tests (unit + DB-gated integration)."""

from __future__ import annotations

import time
from datetime import timedelta
from urllib.parse import parse_qs, urlsplit

import httpx
import jwt
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
from aethergate.errors import (
    OidcAuthenticationFailed,
    OidcConfigurationError,
)
from aethergate.identity import admin as admin_service
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
REDIRECT_URI = "http://test/admin/v1/auth/oidc/callback"
BOOTSTRAP_TOKEN = "test-bootstrap-secret-0123456789abcdefghij"
SUBJECT = "dev-user"


def _oidc_settings(**overrides) -> Settings:
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
        "oidc_jit_provisioning": False,
        "oidc_jit_project_name": "default",
        "oidc_session_idle_seconds": 3600,
        "oidc_session_absolute_seconds": 7200,
        "oidc_login_ttl_seconds": 600,
    }
    base.update(overrides)
    return Settings(**base)


# ---------------------------------------------------------------------------
# Unit tests (no DB)
# ---------------------------------------------------------------------------


def test_pkce_s256_generation():
    verifier, challenge = oidc_module.generate_pkce_pair()
    assert 43 <= len(verifier) <= 128
    import base64
    import hashlib

    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    expected = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    assert challenge == expected


def test_state_and_nonce_entropy():
    states = {oidc_module.generate_state() for _ in range(50)}
    nonces = {oidc_module.generate_nonce() for _ in range(50)}
    assert len(states) == 50
    assert len(nonces) == 50
    assert all(len(s) >= 32 for s in states)
    assert all(len(n) >= 32 for n in nonces)


def test_oidc_config_missing_fields():
    with pytest.raises(ValidationError):
        _oidc_settings(oidc_client_id=None)
    with pytest.raises(ValidationError):
        _oidc_settings(oidc_issuer=None)
    with pytest.raises(ValidationError):
        _oidc_settings(oidc_redirect_uri=None)


def test_oidc_openid_scope_always_included():
    settings = _oidc_settings(oidc_scopes="profile,email")
    assert settings.oidc_scope_list[0] == "openid"


def test_oidc_scopes_accept_whitespace_separated():
    settings = _oidc_settings(oidc_scopes="openid profile email")
    assert settings.oidc_scope_list == ["openid", "profile", "email"]


def test_oidc_prod_requires_https_issuer():
    with pytest.raises(ValidationError):
        _oidc_settings(app_env="prod", queue_key="k" * 32)


def test_oidc_dev_allows_http_issuer():
    assert _oidc_settings().oidc_issuer == ISSUER


def test_txn_cookie_secure_in_prod(monkeypatch):
    from aethergate.api import session_auth

    prod = _oidc_settings(
        app_env="prod",
        queue_key="k" * 32,
        oidc_issuer="https://idp.example",
        oidc_redirect_uri="https://aethergate.example/admin/v1/auth/oidc/callback",
    )
    monkeypatch.setattr("aethergate.api.session_auth.get_settings", lambda: prod)
    resp = session_auth.RedirectResponse("https://idp.example/authorize", status_code=302)
    session_auth._set_txn_cookie(resp, "raw-binding-value")
    set_cookie = resp.headers["set-cookie"].lower()
    assert "secure" in set_cookie
    assert "httponly" in set_cookie
    assert "samesite=lax" in set_cookie
    assert "path=/admin/v1/auth/oidc" in set_cookie
    assert f"max-age={prod.oidc_login_ttl_seconds}" in set_cookie


async def _provider_for(idp: DevOidcIdp, settings: Settings):
    transport = httpx.ASGITransport(app=idp.app)
    http = httpx.AsyncClient(transport=transport, base_url=settings.oidc_issuer)
    return oidc_module.OidcProvider(settings=settings, http_client=http), http


async def test_discovery_issuer_mismatch_rejected():
    idp = DevOidcIdp(issuer=ISSUER, client_id=CLIENT_ID)
    settings = _oidc_settings(oidc_issuer="http://other.test")
    provider, http = await _provider_for(idp, settings)
    try:
        with pytest.raises(OidcConfigurationError):
            await provider.begin_login(
                state="s", nonce="n", code_challenge="c"
            )
    finally:
        await http.aclose()


async def test_id_token_validation_success():
    idp = DevOidcIdp(issuer=ISSUER, client_id=CLIENT_ID, subject=SUBJECT)
    settings = _oidc_settings()
    provider, http = await _provider_for(idp, settings)
    try:
        token = idp.mint_id_token(
            {
                "iss": ISSUER,
                "sub": SUBJECT,
                "aud": CLIENT_ID,
                "iat": int(time.time()),
                "exp": int(time.time()) + 60,
                "nonce": "n1",
            }
        )
        claims = await provider.validate_id_token(token, expected_nonce="n1")
        assert claims["sub"] == SUBJECT
    finally:
        await http.aclose()


async def test_id_token_issuer_validation():
    idp = DevOidcIdp(issuer=ISSUER, client_id=CLIENT_ID)
    settings = _oidc_settings()
    provider, http = await _provider_for(idp, settings)
    try:
        token = idp.mint_id_token(
            {
                "iss": "http://evil.test",
                "sub": SUBJECT,
                "aud": CLIENT_ID,
                "exp": int(time.time()) + 60,
                "nonce": "n1",
            }
        )
        with pytest.raises(OidcAuthenticationFailed):
            await provider.validate_id_token(token, expected_nonce="n1")
    finally:
        await http.aclose()


async def test_id_token_audience_validation():
    idp = DevOidcIdp(issuer=ISSUER, client_id=CLIENT_ID)
    settings = _oidc_settings()
    provider, http = await _provider_for(idp, settings)
    try:
        token = idp.mint_id_token(
            {
                "iss": ISSUER,
                "sub": SUBJECT,
                "aud": "other-client",
                "exp": int(time.time()) + 60,
                "nonce": "n1",
            }
        )
        with pytest.raises(OidcAuthenticationFailed):
            await provider.validate_id_token(token, expected_nonce="n1")
    finally:
        await http.aclose()


async def test_id_token_expiry_validation():
    idp = DevOidcIdp(issuer=ISSUER, client_id=CLIENT_ID)
    settings = _oidc_settings()
    provider, http = await _provider_for(idp, settings)
    try:
        token = idp.mint_id_token(
            {
                "iss": ISSUER,
                "sub": SUBJECT,
                "aud": CLIENT_ID,
                "exp": int(time.time()) - 10,
                "nonce": "n1",
            }
        )
        with pytest.raises(OidcAuthenticationFailed):
            await provider.validate_id_token(token, expected_nonce="n1")
    finally:
        await http.aclose()


async def test_id_token_subject_required():
    idp = DevOidcIdp(issuer=ISSUER, client_id=CLIENT_ID)
    settings = _oidc_settings()
    provider, http = await _provider_for(idp, settings)
    try:
        token = idp.mint_id_token(
            {
                "iss": ISSUER,
                "aud": CLIENT_ID,
                "exp": int(time.time()) + 60,
                "nonce": "n1",
            }
        )
        with pytest.raises(OidcAuthenticationFailed):
            await provider.validate_id_token(token, expected_nonce="n1")
    finally:
        await http.aclose()


async def test_id_token_nonce_validation():
    idp = DevOidcIdp(issuer=ISSUER, client_id=CLIENT_ID)
    settings = _oidc_settings()
    provider, http = await _provider_for(idp, settings)
    try:
        token = idp.mint_id_token(
            {
                "iss": ISSUER,
                "sub": SUBJECT,
                "aud": CLIENT_ID,
                "exp": int(time.time()) + 60,
                "nonce": "expected",
            }
        )
        with pytest.raises(OidcAuthenticationFailed):
            await provider.validate_id_token(token, expected_nonce="wrong")
    finally:
        await http.aclose()


async def test_unsafe_algorithm_rejected():
    idp = DevOidcIdp(issuer=ISSUER, client_id=CLIENT_ID)
    settings = _oidc_settings()
    provider, http = await _provider_for(idp, settings)
    try:
        # HS256 token (unsigned by our RSA key, but header alg is HS256).
        token = jwt.encode(
            {
                "iss": ISSUER,
                "sub": SUBJECT,
                "aud": CLIENT_ID,
                "exp": int(time.time()) + 60,
                "nonce": "n1",
            },
            "secret",
            algorithm="HS256",
        )
        with pytest.raises(OidcAuthenticationFailed):
            await provider.validate_id_token(token, expected_nonce="n1")
    finally:
        await http.aclose()


# ---------------------------------------------------------------------------
# DB-gated integration
# ---------------------------------------------------------------------------


@pytest.fixture
async def oidc_engine():
    if not TEST_DATABASE_URL:
        pytest.skip("AETHERGATE_TEST_DATABASE_URL not set")
    url = url_for_database(TEST_DATABASE_URL, "aethergate_test_oidc")
    await ensure_database(url)
    engine = create_async_engine(url)
    yield engine
    await engine.dispose()


@pytest.fixture
def oidc_factory(oidc_engine):
    return async_sessionmaker(oidc_engine, expire_on_commit=False)


async def _provision_linked_user(factory, idp: DevOidcIdp, role: Role) -> PrincipalId:
    """Bootstrap, create a USER principal, grant a role, link the OIDC identity."""
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
async def oidc_http(oidc_engine, oidc_factory, monkeypatch):
    await reset_schema(oidc_engine)
    settings = _oidc_settings()
    monkeypatch.setattr("aethergate.identity.session.get_settings", lambda: settings)
    monkeypatch.setattr("aethergate.identity.admin.get_settings", lambda: settings)
    monkeypatch.setattr("aethergate.api.session_auth.get_settings", lambda: settings)
    monkeypatch.setattr("aethergate.api.admin.get_session_factory", lambda: oidc_factory)

    idp = DevOidcIdp(issuer=ISSUER, client_id=CLIENT_ID, subject=SUBJECT, email="h@example.com")
    idp_transport = httpx.ASGITransport(app=idp.app)
    idp_client = httpx.AsyncClient(transport=idp_transport, base_url=ISSUER)
    provider = oidc_module.OidcProvider(settings=settings, http_client=idp_client)
    monkeypatch.setattr("aethergate.identity.oidc.get_oidc_provider", lambda: provider)

    async def _test_session():
        async with oidc_factory() as s:
            yield s

    app.dependency_overrides[persistence_db.get_session] = _test_session
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, idp_client, oidc_factory, idp
    app.dependency_overrides.clear()
    await idp_client.aclose()
    await reset_schema(oidc_engine)


async def _follow_login(client, idp_client) -> tuple[httpx.Response, str, str]:
    """Drive the full OIDC login; return (callback_response, cookie_value, csrf_token)."""
    login = await client.get("/admin/v1/auth/oidc/login", follow_redirects=False)
    assert login.status_code == 302
    txn_cookie = login.cookies.get(session_service.LOGIN_TXN_COOKIE_NAME)
    assert txn_cookie
    authorize_url = login.headers["location"]

    authorize = await idp_client.get(authorize_url, follow_redirects=False)
    assert authorize.status_code == 302
    callback_url = authorize.headers["location"]
    query = parse_qs(urlsplit(callback_url).query)

    callback = await client.get(
        "/admin/v1/auth/oidc/callback",
        params={"code": query["code"][0], "state": query["state"][0]},
        cookies={session_service.LOGIN_TXN_COOKIE_NAME: txn_cookie},
        follow_redirects=False,
    )
    assert callback.status_code == 200
    body = callback.json()
    cookie = callback.cookies.get(session_service.SESSION_COOKIE_NAME)
    return callback, cookie, body["csrf_token"]


@pytest.mark.skipif(TEST_DATABASE_URL is None, reason="AETHERGATE_TEST_DATABASE_URL not set")
async def test_login_happy_path(oidc_http):
    client, idp_client, factory, idp = oidc_http
    await _provision_linked_user(factory, idp, Role.SYSTEM_ADMIN)
    callback, cookie, csrf = await _follow_login(client, idp_client)
    assert callback.json()["session"]["authentication_kind"] == "browser_session"
    assert "system_admin" in callback.json()["session"]["roles"]
    assert csrf

    who = await client.get("/admin/v1/whoami", cookies={"ag_session": cookie})
    assert who.status_code == 200
    assert who.json()["authentication_kind"] == "browser_session"
    assert who.json()["api_credential_id"] is None
    assert "system_admin" in who.json()["roles"]


@pytest.mark.skipif(TEST_DATABASE_URL is None, reason="AETHERGATE_TEST_DATABASE_URL not set")
async def test_unknown_identity_denied_jit_off(oidc_http):
    client, idp_client, factory, idp = oidc_http
    login = await client.get("/admin/v1/auth/oidc/login", follow_redirects=False)
    txn_cookie = login.cookies.get(session_service.LOGIN_TXN_COOKIE_NAME)
    authorize = await idp_client.get(login.headers["location"], follow_redirects=False)
    query = parse_qs(urlsplit(authorize.headers["location"]).query)
    callback = await client.get(
        "/admin/v1/auth/oidc/callback",
        params={"code": query["code"][0], "state": query["state"][0]},
        cookies={session_service.LOGIN_TXN_COOKIE_NAME: txn_cookie},
    )
    assert callback.status_code == 401
    assert callback.json()["error"]["code"] == "oidc_authentication_failed"
    assert callback.cookies.get("ag_session") is None


@pytest.mark.skipif(TEST_DATABASE_URL is None, reason="AETHERGATE_TEST_DATABASE_URL not set")
async def test_wrong_state_fails(oidc_http):
    client, idp_client, factory, idp = oidc_http
    await _provision_linked_user(factory, idp, Role.SYSTEM_ADMIN)
    login = await client.get("/admin/v1/auth/oidc/login", follow_redirects=False)
    txn_cookie = login.cookies.get(session_service.LOGIN_TXN_COOKIE_NAME)
    authorize = await idp_client.get(login.headers["location"], follow_redirects=False)
    query = parse_qs(urlsplit(authorize.headers["location"]).query)
    callback = await client.get(
        "/admin/v1/auth/oidc/callback",
        params={"code": query["code"][0], "state": "wrong-state"},
        cookies={session_service.LOGIN_TXN_COOKIE_NAME: txn_cookie},
    )
    assert callback.status_code == 400
    assert callback.json()["error"]["code"] == "invalid_login_state"


@pytest.mark.skipif(TEST_DATABASE_URL is None, reason="AETHERGATE_TEST_DATABASE_URL not set")
async def test_callback_replay_rejected(oidc_http):
    client, idp_client, factory, idp = oidc_http
    await _provision_linked_user(factory, idp, Role.SYSTEM_ADMIN)
    login = await client.get("/admin/v1/auth/oidc/login", follow_redirects=False)
    txn_cookie = login.cookies.get(session_service.LOGIN_TXN_COOKIE_NAME)
    authorize = await idp_client.get(login.headers["location"], follow_redirects=False)
    query = parse_qs(urlsplit(authorize.headers["location"]).query)
    params = {"code": query["code"][0], "state": query["state"][0]}
    cookies = {session_service.LOGIN_TXN_COOKIE_NAME: txn_cookie}
    first = await client.get("/admin/v1/auth/oidc/callback", params=params, cookies=cookies)
    assert first.status_code == 200
    second = await client.get("/admin/v1/auth/oidc/callback", params=params, cookies=cookies)
    assert second.status_code == 400


@pytest.mark.skipif(TEST_DATABASE_URL is None, reason="AETHERGATE_TEST_DATABASE_URL not set")
async def test_login_transaction_cookie_binding(oidc_http):
    client, idp_client, factory, idp = oidc_http
    await _provision_linked_user(factory, idp, Role.SYSTEM_ADMIN)
    login = await client.get("/admin/v1/auth/oidc/login", follow_redirects=False)
    authorize = await idp_client.get(login.headers["location"], follow_redirects=False)
    query = parse_qs(urlsplit(authorize.headers["location"]).query)
    params = {"code": query["code"][0], "state": query["state"][0]}

    # A separate client (a different browser with no transaction cookie) must not
    # be able to complete the login, with or without a forged cookie value.
    attacker_transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=attacker_transport, base_url="http://test"
    ) as attacker:
        missing = await attacker.get("/admin/v1/auth/oidc/callback", params=params)
        assert missing.status_code == 400

        wrong = await attacker.get(
            "/admin/v1/auth/oidc/callback",
            params=params,
            cookies={session_service.LOGIN_TXN_COOKIE_NAME: "attacker-cookie"},
        )
        assert wrong.status_code == 400


@pytest.mark.skipif(TEST_DATABASE_URL is None, reason="AETHERGATE_TEST_DATABASE_URL not set")
async def test_login_state_deleted_on_consume(oidc_http):
    client, idp_client, factory, idp = oidc_http
    await _provision_linked_user(factory, idp, Role.SYSTEM_ADMIN)
    _, cookie, _ = await _follow_login(client, idp_client)
    async with factory() as s:
        rows = (await s.execute(select(models.OidcLoginState))).scalars().all()
        assert rows == []


@pytest.mark.skipif(TEST_DATABASE_URL is None, reason="AETHERGATE_TEST_DATABASE_URL not set")
async def test_login_state_expiry_cleanup(oidc_http):
    client, idp_client, factory, idp = oidc_http
    async with factory() as s:
        async with s.begin():
            await session_service.create_login_transaction(s)
            await session_service.create_login_transaction(s)

    async with factory() as s:
        async with s.begin():
            rows = (await s.execute(select(models.OidcLoginState))).scalars().all()
            assert len(rows) == 2
            for row in rows:
                row.expires_at = session_service.utcnow() - timedelta(seconds=1)
        async with s.begin():
            await session_service.create_login_transaction(s)
        rows = (await s.execute(select(models.OidcLoginState))).scalars().all()
        assert len(rows) == 1


@pytest.mark.skipif(TEST_DATABASE_URL is None, reason="AETHERGATE_TEST_DATABASE_URL not set")
async def test_audit_failed_login_no_raw_state(oidc_http):
    client, idp_client, factory, idp = oidc_http
    await _provision_linked_user(factory, idp, Role.SYSTEM_ADMIN)
    login = await client.get("/admin/v1/auth/oidc/login", follow_redirects=False)
    txn_cookie = login.cookies.get(session_service.LOGIN_TXN_COOKIE_NAME)
    authorize = await idp_client.get(login.headers["location"], follow_redirects=False)
    query = parse_qs(urlsplit(authorize.headers["location"]).query)
    raw_state = query["state"][0]

    callback = await client.get(
        "/admin/v1/auth/oidc/callback",
        params={"code": "bogus-code", "state": raw_state},
        cookies={session_service.LOGIN_TXN_COOKIE_NAME: txn_cookie},
    )
    assert callback.status_code == 401

    async with factory() as s:
        events = (await s.execute(select(models.AuditEvent))).scalars().all()
        failed = [e for e in events if e.action == "human.login_failed"]
        assert failed
        for e in events:
            blob = str(e.details) + e.resource_id + e.action
            assert raw_state not in blob
            assert e.resource_id != raw_state
            assert txn_cookie not in blob


@pytest.mark.skipif(TEST_DATABASE_URL is None, reason="AETHERGATE_TEST_DATABASE_URL not set")
async def test_login_txn_cookie_attributes(oidc_http):
    client, idp_client, factory, idp = oidc_http
    login = await client.get("/admin/v1/auth/oidc/login", follow_redirects=False)
    set_cookie = login.headers["set-cookie"].lower()
    assert session_service.LOGIN_TXN_COOKIE_NAME + "=" in set_cookie
    assert "httponly" in set_cookie
    assert "samesite=lax" in set_cookie
    assert "path=/admin/v1/auth/oidc" in set_cookie
    assert "max-age=600" in set_cookie


@pytest.mark.skipif(TEST_DATABASE_URL is None, reason="AETHERGATE_TEST_DATABASE_URL not set")
async def test_login_txn_cookie_cleared_on_success(oidc_http):
    client, idp_client, factory, idp = oidc_http
    await _provision_linked_user(factory, idp, Role.SYSTEM_ADMIN)
    callback, cookie, csrf = await _follow_login(client, idp_client)
    assert callback.status_code == 200
    cleared = any(
        session_service.LOGIN_TXN_COOKIE_NAME in sc
        for sc in callback.headers.get_list("set-cookie")
    )
    assert cleared
    # The binding cookie must be gone (deleted), not re-set to a new value.
    txn_headers = [
        sc for sc in callback.headers.get_list("set-cookie")
        if session_service.LOGIN_TXN_COOKIE_NAME in sc
    ]
    assert all("max-age=0" in sc.lower() or '=""' in sc for sc in txn_headers)


@pytest.mark.skipif(TEST_DATABASE_URL is None, reason="AETHERGATE_TEST_DATABASE_URL not set")
async def test_login_txn_cookie_cleared_on_failure(oidc_http):
    client, idp_client, factory, idp = oidc_http
    await _provision_linked_user(factory, idp, Role.SYSTEM_ADMIN)
    login = await client.get("/admin/v1/auth/oidc/login", follow_redirects=False)
    txn_cookie = login.cookies.get(session_service.LOGIN_TXN_COOKIE_NAME)
    authorize = await idp_client.get(login.headers["location"], follow_redirects=False)
    query = parse_qs(urlsplit(authorize.headers["location"]).query)
    callback = await client.get(
        "/admin/v1/auth/oidc/callback",
        params={"code": "bogus-code", "state": query["state"][0]},
        cookies={session_service.LOGIN_TXN_COOKIE_NAME: txn_cookie},
    )
    assert callback.status_code == 401
    txn_headers = [
        sc for sc in callback.headers.get_list("set-cookie")
        if session_service.LOGIN_TXN_COOKIE_NAME in sc
    ]
    assert txn_headers
    assert all("max-age=0" in sc.lower() or '=""' in sc for sc in txn_headers)


@pytest.mark.skipif(TEST_DATABASE_URL is None, reason="AETHERGATE_TEST_DATABASE_URL not set")
async def test_login_binding_raw_not_persisted(oidc_http):
    client, idp_client, factory, idp = oidc_http
    async with factory() as s:
        async with s.begin():
            entity, raw_binding = await session_service.create_login_transaction(s)
            state = entity.state
    async with factory() as s:
        row = (await s.execute(
            select(models.OidcLoginState).where(models.OidcLoginState.state == state)
        )).scalar_one()
        assert row.txn_cookie_hash == session_service.hash_verifier(raw_binding)
        assert raw_binding not in row.txn_cookie_hash
        for col in ("id", "state", "nonce", "code_verifier", "code_challenge", "txn_cookie_hash"):
            assert raw_binding not in str(getattr(row, col))


@pytest.mark.skipif(TEST_DATABASE_URL is None, reason="AETHERGATE_TEST_DATABASE_URL not set")
async def test_session_fixation_replaced(oidc_http):
    client, idp_client, factory, idp = oidc_http
    await _provision_linked_user(factory, idp, Role.SYSTEM_ADMIN)
    _, first_cookie, _ = await _follow_login(client, idp_client)
    _, second_cookie, _ = await _follow_login(client, idp_client)
    assert first_cookie != second_cookie
    who = await client.get("/admin/v1/whoami", cookies={"ag_session": second_cookie})
    assert who.status_code == 200
    assert who.json()["authentication_kind"] == "browser_session"


@pytest.mark.skipif(TEST_DATABASE_URL is None, reason="AETHERGATE_TEST_DATABASE_URL not set")
async def test_idp_rejects_bad_pkce_verifier():
    idp = DevOidcIdp(issuer=ISSUER, client_id=CLIENT_ID)
    transport = httpx.ASGITransport(app=idp.app)
    http = httpx.AsyncClient(transport=transport, base_url=ISSUER)
    try:
        verifier, challenge = oidc_module.generate_pkce_pair()
        auth = await http.get(
            "/authorize",
            params={
                "response_type": "code",
                "client_id": CLIENT_ID,
                "redirect_uri": REDIRECT_URI,
                "scope": "openid",
                "state": "s1",
                "nonce": "n1",
                "code_challenge": challenge,
                "code_challenge_method": "S256",
            },
            follow_redirects=False,
        )
        code = parse_qs(urlsplit(auth.headers["location"]).query)["code"][0]

        bad = await http.post(
            "/token",
            data={
                "grant_type": "authorization_code",
                "code": code,
                "code_verifier": "wrong-verifier",
                "client_id": CLIENT_ID,
                "redirect_uri": REDIRECT_URI,
            },
        )
        assert bad.status_code == 400

        ok = await http.post(
            "/token",
            data={
                "grant_type": "authorization_code",
                "code": code,
                "code_verifier": verifier,
                "client_id": CLIENT_ID,
                "redirect_uri": REDIRECT_URI,
            },
        )
        assert ok.status_code == 200
        assert "id_token" in ok.json()
    finally:
        await http.aclose()


@pytest.mark.skipif(TEST_DATABASE_URL is None, reason="AETHERGATE_TEST_DATABASE_URL not set")
async def test_session_cookie_attributes(oidc_http):
    client, idp_client, factory, idp = oidc_http
    await _provision_linked_user(factory, idp, Role.SYSTEM_ADMIN)
    callback, cookie, csrf = await _follow_login(client, idp_client)
    set_cookie = callback.headers.get_list("set-cookie")[0].lower()
    assert "httponly" in set_cookie
    assert "samesite=lax" in set_cookie
    assert "path=/admin" in set_cookie


@pytest.mark.skipif(TEST_DATABASE_URL is None, reason="AETHERGATE_TEST_DATABASE_URL not set")
async def test_session_raw_cookie_not_persisted(oidc_http):
    client, idp_client, factory, idp = oidc_http
    await _provision_linked_user(factory, idp, Role.SYSTEM_ADMIN)
    _, cookie, _ = await _follow_login(client, idp_client)
    async with factory() as s:
        rows = (await s.execute(select(models.BrowserSession))).scalars().all()
        assert len(rows) == 1
        assert rows[0].session_hash != cookie
        assert rows[0].session_hash == session_service.hash_verifier(cookie)


@pytest.mark.skipif(TEST_DATABASE_URL is None, reason="AETHERGATE_TEST_DATABASE_URL not set")
async def test_revoked_session_denied(oidc_http):
    client, idp_client, factory, idp = oidc_http
    await _provision_linked_user(factory, idp, Role.SYSTEM_ADMIN)
    _, cookie, csrf = await _follow_login(client, idp_client)
    # logout
    logout = await client.post(
        "/admin/v1/auth/logout",
        cookies={"ag_session": cookie},
        headers={"X-CSRF-Token": csrf},
    )
    assert logout.status_code == 200
    who = await client.get("/admin/v1/whoami", cookies={"ag_session": cookie})
    assert who.status_code == 401


@pytest.mark.skipif(TEST_DATABASE_URL is None, reason="AETHERGATE_TEST_DATABASE_URL not set")
async def test_csrf_required_for_mutation(oidc_http):
    client, idp_client, factory, idp = oidc_http
    await _provision_linked_user(factory, idp, Role.SYSTEM_ADMIN)
    _, cookie, csrf = await _follow_login(client, idp_client)

    # GET works without CSRF
    who = await client.get("/admin/v1/whoami", cookies={"ag_session": cookie})
    assert who.status_code == 200

    # missing CSRF -> 403
    no_csrf = await client.post(
        "/admin/v1/projects",
        cookies={"ag_session": cookie},
        json={"name": "no-csrf"},
    )
    assert no_csrf.status_code == 403
    assert no_csrf.json()["error"]["code"] == "invalid_csrf_token"

    # wrong CSRF -> 403
    wrong = await client.post(
        "/admin/v1/projects",
        cookies={"ag_session": cookie},
        headers={"X-CSRF-Token": "wrong"},
        json={"name": "wrong-csrf"},
    )
    assert wrong.status_code == 403

    # correct CSRF -> 201
    ok = await client.post(
        "/admin/v1/projects",
        cookies={"ag_session": cookie},
        headers={"X-CSRF-Token": csrf},
        json={"name": "good-csrf"},
    )
    assert ok.status_code == 201


@pytest.mark.skipif(TEST_DATABASE_URL is None, reason="AETHERGATE_TEST_DATABASE_URL not set")
async def test_bearer_mutation_no_csrf(oidc_http):
    client, idp_client, factory, idp = oidc_http
    async with factory() as s:
        _, raw = await admin_service.bootstrap(s, BOOTSTRAP_TOKEN)
    resp = await client.post(
        "/admin/v1/projects",
        headers={"Authorization": f"Bearer {raw}"},
        json={"name": "bearer-project"},
    )
    assert resp.status_code == 201


@pytest.mark.skipif(TEST_DATABASE_URL is None, reason="AETHERGATE_TEST_DATABASE_URL not set")
async def test_invalid_authorization_no_cookie_fallback(oidc_http):
    client, idp_client, factory, idp = oidc_http
    await _provision_linked_user(factory, idp, Role.SYSTEM_ADMIN)
    _, cookie, _ = await _follow_login(client, idp_client)
    resp = await client.get(
        "/admin/v1/whoami",
        headers={"Authorization": "Bearer agk_bad_bad"},
        cookies={"ag_session": cookie},
    )
    assert resp.status_code == 401


@pytest.mark.skipif(TEST_DATABASE_URL is None, reason="AETHERGATE_TEST_DATABASE_URL not set")
async def test_role_revocation_reflected_next_request(oidc_http):
    client, idp_client, factory, idp = oidc_http
    principal_id = await _provision_linked_user(factory, idp, Role.SYSTEM_ADMIN)
    _, cookie, csrf = await _follow_login(client, idp_client)

    who = await client.get("/admin/v1/whoami", cookies={"ag_session": cookie})
    assert "system_admin" in who.json()["roles"]

    # revoke the role assignment
    async with factory() as s:
        async with s.begin():
            assignments = await repository.list_active_role_assignments(s, principal_id)
            await repository.revoke_role_assignment(
                s, assignments[0].id, session_service.utcnow()
            )

    who2 = await client.get("/admin/v1/whoami", cookies={"ag_session": cookie})
    assert "system_admin" not in who2.json()["roles"]


@pytest.mark.skipif(TEST_DATABASE_URL is None, reason="AETHERGATE_TEST_DATABASE_URL not set")
async def test_principal_deactivation_reflected(oidc_http):
    client, idp_client, factory, idp = oidc_http
    principal_id = await _provision_linked_user(factory, idp, Role.SYSTEM_ADMIN)
    _, cookie, _ = await _follow_login(client, idp_client)
    assert (await client.get("/admin/v1/whoami", cookies={"ag_session": cookie})).status_code == 200

    async with factory() as s:
        async with s.begin():
            await repository.set_principal_active(s, principal_id, False)

    assert (await client.get("/admin/v1/whoami", cookies={"ag_session": cookie})).status_code == 401


@pytest.mark.skipif(TEST_DATABASE_URL is None, reason="AETHERGATE_TEST_DATABASE_URL not set")
async def test_logout_idempotent(oidc_http):
    client, idp_client, factory, idp = oidc_http
    await _provision_linked_user(factory, idp, Role.SYSTEM_ADMIN)
    _, cookie, csrf = await _follow_login(client, idp_client)
    first = await client.post(
        "/admin/v1/auth/logout", cookies={"ag_session": cookie}, headers={"X-CSRF-Token": csrf}
    )
    assert first.status_code == 200
    assert first.json()["revoked"] is True
    second = await client.post(
        "/admin/v1/auth/logout", cookies={"ag_session": cookie}, headers={"X-CSRF-Token": csrf}
    )
    assert second.status_code == 200


@pytest.mark.skipif(TEST_DATABASE_URL is None, reason="AETHERGATE_TEST_DATABASE_URL not set")
async def test_audit_contains_no_secrets(oidc_http):
    client, idp_client, factory, idp = oidc_http
    await _provision_linked_user(factory, idp, Role.SYSTEM_ADMIN)
    _, cookie, csrf = await _follow_login(client, idp_client)
    async with factory() as s:
        events = (await s.execute(select(models.AuditEvent))).scalars().all()
        assert any(e.action == "human.login_success" for e in events)
        for e in events:
            blob = str(e.details) + e.resource_id + e.action
            assert csrf not in blob
            assert cookie not in blob
            assert "code_verifier" not in blob


@pytest.mark.skipif(TEST_DATABASE_URL is None, reason="AETHERGATE_TEST_DATABASE_URL not set")
async def test_browser_session_context_has_no_credential(oidc_http):
    client, idp_client, factory, idp = oidc_http
    await _provision_linked_user(factory, idp, Role.SYSTEM_ADMIN)
    _, cookie, _ = await _follow_login(client, idp_client)
    async with factory() as s:
        async with s.begin():
            ctx, _ = await admin_service.authenticate_browser_session(s, cookie)
            assert ctx.authentication_kind is AdminAuthenticationKind.BROWSER_SESSION
            assert ctx.api_credential_id is None
            assert ctx.browser_session_id is not None
            assert ctx.scopes == ()


@pytest.mark.skipif(TEST_DATABASE_URL is None, reason="AETHERGATE_TEST_DATABASE_URL not set")
async def test_unique_issuer_subject_not_email(oidc_http):
    client, idp_client, factory, idp = oidc_http
    principal_id = await _provision_linked_user(factory, idp, Role.SYSTEM_ADMIN)
    async with factory() as s:
        async with s.begin():
            # same email, different subject -> allowed (email is not the key)
            await session_service.link_external_identity(
                s,
                principal_id=principal_id,
                issuer=ISSUER,
                subject="different-subject",
                now=session_service.utcnow(),
            )
        # same issuer+subject again -> duplicate rejected
        async with s.begin():
            from aethergate.errors import AdminValidationError

            with pytest.raises(AdminValidationError):
                await session_service.link_external_identity(
                    s,
                    principal_id=principal_id,
                    issuer=ISSUER,
                    subject=idp.subject,
                    now=session_service.utcnow(),
                )
