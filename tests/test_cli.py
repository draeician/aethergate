"""CLI unit tests (offline): profiles, token store, client errors, output, exit codes."""

from __future__ import annotations

import json

import httpx
import pytest
from click.testing import CliRunner

from aethergate.cli import output, profiles
from aethergate.cli.client import Client
from aethergate.cli.errors import (
    EXIT_AUTH,
    EXIT_CONFLICT,
    EXIT_FORBIDDEN,
    EXIT_GENERIC,
    EXIT_NETWORK,
    EXIT_NOT_FOUND,
    EXIT_SERVER,
    EXIT_USAGE,
    AuthError,
    ConflictError,
    ForbiddenError,
    NetworkError,
    NotFoundError,
    ServerError,
    TokenStoreUnavailable,
    UsageError,
)
from aethergate.cli.main import cli
from aethergate.cli.tokenstore import KeyringTokenStore, MemoryTokenStore

# --- profiles ---------------------------------------------------------------


@pytest.fixture
def tmp_config(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    return tmp_path


def test_profile_crud_and_no_token(tmp_config):
    store = profiles.load()
    assert store.profiles == {}

    profiles.add(
        store,
        "local",
        "http://127.0.0.1:43001",
        ca_bundle=None,
        allow_insecure=True,
        set_default=True,
    )
    assert store.default_profile == "local"

    reloaded = profiles.load()
    assert reloaded.profiles["local"].base_url == "http://127.0.0.1:43001"
    # The config file must contain no token-like secret.
    text = profiles.config_path().read_text(encoding="utf-8")
    assert "Bearer" not in text and "token" not in text

    profiles.set_default(reloaded, "local")
    profiles.delete(reloaded, "local")
    assert reloaded.profiles == {}


def test_profile_https_required_for_remote(tmp_config):
    store = profiles.load()
    with pytest.raises(UsageError):
        profiles.add(
            store,
            "prod",
            "http://example.com",
            ca_bundle=None,
            allow_insecure=False,
            set_default=False,
        )


def test_profile_http_allowed_for_localhost(tmp_config):
    store = profiles.load()
    profiles.add(
        store,
        "local",
        "http://127.0.0.1:8000",
        ca_bundle=None,
        allow_insecure=False,
        set_default=False,
    )
    assert "local" in store.profiles


def test_profile_ca_bundle_verify(tmp_config):
    store = profiles.load()
    p = profiles.add(
        store,
        "ca",
        "https://example.com",
        ca_bundle="/tmp/ca.pem",
        allow_insecure=False,
        set_default=False,
    )
    assert p.verify == "/tmp/ca.pem"


# --- token store ------------------------------------------------------------


def test_memory_token_store():
    ts = MemoryTokenStore()
    ts.set("p", "ags_secret")
    assert ts.get("p") == "ags_secret"
    ts.delete("p")
    assert ts.get("p") is None


def test_keyring_unavailable_fail_safe(monkeypatch):
    class _FailKeyring:
        def set_password(self, service, user, password):
            return None

        def get_password(self, service, user):
            return None

    monkeypatch.setattr("keyring.set_password", _FailKeyring().set_password)
    monkeypatch.setattr("keyring.get_password", _FailKeyring().get_password)
    ts = KeyringTokenStore()
    with pytest.raises(TokenStoreUnavailable):
        ts.set("p", "ags_secret")


def test_keyring_roundtrip(monkeypatch):
    storage: dict = {}

    class _MemKeyring:
        def set_password(self, service, user, password):
            storage[(service, user)] = password

        def get_password(self, service, user):
            return storage.get((service, user))

    monkeypatch.setattr("keyring.set_password", _MemKeyring().set_password)
    monkeypatch.setattr("keyring.get_password", _MemKeyring().get_password)
    ts = KeyringTokenStore()
    ts.set("p", "ags_secret")
    assert ts.get("p") == "ags_secret"


# --- client error mapping ---------------------------------------------------


def _client(handler, token: str | None = "ags_token"):
    transport = httpx.MockTransport(handler)
    return Client(base_url="http://test", token=token, verify=False, transport=transport)


def _error_response(status: int, code: str) -> httpx.Response:
    return httpx.Response(
        status,
        json={"error": {"code": code, "message": "boom"}},
        request=httpx.Request("GET", "http://test/x"),
    )


def test_client_maps_errors():
    cases = [
        (401, "not_authenticated", AuthError, EXIT_AUTH),
        (403, "forbidden", ForbiddenError, EXIT_FORBIDDEN),
        (404, "not_found", NotFoundError, EXIT_NOT_FOUND),
        (409, "resource_conflict", ConflictError, EXIT_CONFLICT),
        (400, "invalid_request", UsageError, EXIT_USAGE),
        (500, "internal", ServerError, EXIT_SERVER),
    ]
    for status, code, exc_type, exit_code in cases:
        c = _client(lambda req, s=status, c=code: _error_response(s, c))
        with c:
            with pytest.raises(exc_type) as excinfo:
                c.get("/admin/v1/x")
            assert excinfo.value.exit_code == exit_code


def test_client_device_terminal_code_maps_to_auth():
    c = _client(lambda req: _error_response(400, "device_expired"))
    with c:
        with pytest.raises(AuthError) as excinfo:
            c.post("/admin/v1/auth/device/poll", {"device_code": "x"})
        assert excinfo.value.exit_code == EXIT_AUTH


def test_client_network_error():
    def handler(req):
        raise httpx.ConnectError("boom", request=req)

    c = _client(handler)
    with c:
        with pytest.raises(NetworkError) as excinfo:
            c.get("/admin/v1/x")
        assert excinfo.value.exit_code == EXIT_NETWORK


def test_client_retries_get_only():
    calls = {"n": 0}

    def handler(req):
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.ConnectError("boom", request=req)
        return httpx.Response(200, json={"ok": True}, request=req)

    c = _client(handler)
    with c:
        data = c.get("/admin/v1/x")
        assert data == {"ok": True}
        assert calls["n"] == 2


def test_client_never_retries_mutation():
    calls = {"n": 0}

    def handler(req):
        calls["n"] += 1
        raise httpx.ConnectError("boom", request=req)

    c = _client(handler)
    with c:
        with pytest.raises(NetworkError):
            c.post("/admin/v1/x", {"a": 1})
        assert calls["n"] == 1


def test_client_injects_authorization():
    seen: dict = {}

    def handler(req):
        seen["auth"] = req.headers.get("authorization")
        return httpx.Response(200, json={"ok": True}, request=req)

    c = _client(handler, token="ags_abc")
    with c:
        c.get("/admin/v1/x")
        assert seen["auth"] == "Bearer ags_abc"


# --- output / exit codes ----------------------------------------------------


def test_output_json_is_pure(capsys):
    output.emit({"a": 1, "items": [], "total": 0}, as_json=True)
    captured = capsys.readouterr()
    json.loads(captured.out)  # raises if not pure JSON


def test_output_human_page(capsys):
    output.emit({"items": [{"id": "x"}], "total": 1}, as_json=False)
    captured = capsys.readouterr()
    assert "total=1" in captured.out


def test_exit_codes_distinct():
    codes = {
        EXIT_GENERIC,
        EXIT_USAGE,
        EXIT_AUTH,
        EXIT_FORBIDDEN,
        EXIT_NOT_FOUND,
        EXIT_CONFLICT,
        EXIT_NETWORK,
        EXIT_SERVER,
    }
    assert len(codes) == 8
    assert 0 not in codes


def _fake_profile(_ctx):
    return profiles.Profile("local", "http://x")


# --- CLI offline behaviors --------------------------------------------------


def test_cli_version_offline():
    res = CliRunner().invoke(cli, ["--version"])
    assert res.exit_code == 0
    assert "0.1.0" in res.output


def test_cli_help_offline():
    res = CliRunner().invoke(cli, ["--help"])
    assert res.exit_code == 0
    assert "auth" in res.output
    assert "profile" in res.output


def test_main_maps_cli_error_to_exit_code(monkeypatch):
    from aethergate.cli import main as main_module

    def boom():
        raise AuthError("nope")

    monkeypatch.setattr(main_module, "cli", boom)
    with pytest.raises(SystemExit) as excinfo:
        main_module.main()
    assert excinfo.value.code == EXIT_AUTH


@pytest.mark.parametrize("shell", ["bash", "zsh", "fish"])
def test_cli_completion_offline(shell):
    res = CliRunner().invoke(cli, ["completion", shell])
    assert res.exit_code == 0
    assert len(res.output) > 50


def test_auth_whoami_json(monkeypatch):
    class _FakeClient:
        def __init__(self, **kwargs):
            self._token = kwargs.get("token")

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def close(self):
            pass

        def get(self, path, params=None):
            return {"principal_id": "p1", "authentication_kind": "cli_session", "roles": []}

    monkeypatch.setattr("aethergate.cli.main.Client", _FakeClient)
    monkeypatch.setattr("aethergate.cli.main._resolve_profile", _fake_profile)
    monkeypatch.setattr("aethergate.cli.main._build_token_store", lambda: MemoryTokenStore())
    monkeypatch.delenv("AETHERGATE_TOKEN", raising=False)

    res = CliRunner().invoke(cli, ["auth", "whoami", "--json"])
    assert res.exit_code == 0
    body = json.loads(res.output)
    assert body["authentication_kind"] == "cli_session"


def test_auth_set_token_stdin(monkeypatch):
    seen: dict = {}

    class _FakeClient:
        def __init__(self, **kwargs):
            self._token = kwargs.get("token")

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def close(self):
            pass

        def get(self, path, params=None):
            seen["token_used"] = self._token
            return {"authentication_kind": "service_credential", "roles": ["system_admin"]}

    monkeypatch.setattr("aethergate.cli.main.Client", _FakeClient)
    monkeypatch.setattr("aethergate.cli.main._resolve_profile", _fake_profile)
    ts = MemoryTokenStore()
    monkeypatch.setattr("aethergate.cli.main._build_token_store", lambda: ts)
    monkeypatch.delenv("AETHERGATE_TOKEN", raising=False)

    res = CliRunner().invoke(cli, ["auth", "set-token", "--stdin"], input="agk_abcdef")
    assert res.exit_code == 0
    assert seen["token_used"] == "agk_abcdef"
    assert ts.get("local") == "agk_abcdef"
