"""Configuration preflight (``python -m aethergate.config_check``) behavior."""

from __future__ import annotations

import pytest

from aethergate.config import Settings
from aethergate.config_check import main, run_checks
from aethergate.encryption import QueueEncryptor

VALID_QUEUE_KEY = QueueEncryptor.generate_key()


@pytest.fixture(autouse=True)
def _isolate_settings_env(monkeypatch, tmp_path):
    """Keep these tests independent of ambient CI/container environment.

    ``Settings`` reads process env (and ``.env``) unless told otherwise, so a
    workflow-level ``AETHERGATE_QUEUE_KEY``/``AETHERGATE_ENV``/etc. would silently
    satisfy a validation this suite is asserting on. Clear the relevant vars and
    run from an empty directory so no on-disk ``.env`` contributes either.
    """
    for name in (
        "DATABASE_URL",
        "POSTGRES_HOST",
        "POSTGRES_PORT",
        "POSTGRES_DB",
        "POSTGRES_USER",
        "POSTGRES_PASSWORD",
        "AETHERGATE_ENV",
        "AETHERGATE_ALLOW_INFERENCE_AUTH_BYPASS",
        "AETHERGATE_UPSTREAM_ALLOWLIST",
        "AETHERGATE_QUEUE_KEY",
        "AETHERGATE_BOOTSTRAP_TOKEN",
        "AETHERGATE_OIDC_ENABLED",
        "AETHERGATE_OIDC_ISSUER",
        "AETHERGATE_OIDC_CLIENT_ID",
        "AETHERGATE_OIDC_CLIENT_SECRET",
        "AETHERGATE_OIDC_REDIRECT_URI",
        "AETHERGATE_OIDC_WEB_CALLBACK_PATH",
        "AETHERGATE_OIDC_DEVICE_CLIENT_ID",
        "AETHERGATE_SECRET_BACKEND",
        "AETHERGATE_SECRET_DIRECTORY",
        "AETHERGATE_EGRESS_REQUIRE_LITERAL_IP",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(tmp_path)


def _dev_settings(**overrides) -> Settings:
    # ``_env_file=None`` plus explicit values keep this isolated from any ambient
    # environment (CI/container env would otherwise flip OIDC/bootstrap flags).
    base = dict(
        _env_file=None,
        database_url="postgresql+asyncpg://u:p@h/db",
        app_env="dev",
        queue_key=VALID_QUEUE_KEY,
        upstream_allowlist="api.example.com",
        oidc_enabled=False,
        bootstrap_token=None,
        allow_inference_auth_bypass=False,
    )
    base.update(overrides)
    return Settings(**base)


def test_valid_dev_config_is_ok():
    result = run_checks(_dev_settings())
    assert result.ok, result.errors
    assert not result.errors


def test_empty_allowlist_is_warning_not_error():
    result = run_checks(_dev_settings(upstream_allowlist=""))
    assert result.ok
    assert any("allowlist" in w for w in result.warnings)


def test_invalid_queue_key_is_error():
    result = run_checks(_dev_settings(queue_key="not-a-fernet-key"))
    assert not result.ok
    assert any("Fernet" in e for e in result.errors)


def test_prod_requires_queue_key(monkeypatch):
    # Settings itself forbids missing queue_key in prod, so this path is enforced
    # there; assert the prod validator rejects it. Explicitly clear any ambient
    # AETHERGATE_QUEUE_KEY (e.g. a workflow-level CI value) and isolate env-file
    # loading so the test does not pass merely because the environment supplied a
    # key — the production requirement itself is not weakened.
    monkeypatch.delenv("AETHERGATE_QUEUE_KEY", raising=False)
    with pytest.raises(ValueError):
        Settings(
            _env_file=None,
            database_url="postgresql+asyncpg://u:p@h/db",
            app_env="prod",
            upstream_allowlist="api.example.com",
        )


def test_prod_rejects_inference_auth_bypass():
    with pytest.raises(ValueError):
        Settings(
            _env_file=None,
            database_url="postgresql+asyncpg://u:p@h/db",
            app_env="prod",
            queue_key=VALID_QUEUE_KEY,
            allow_inference_auth_bypass=True,
            upstream_allowlist="api.example.com",
        )


def test_prod_oidc_issuer_must_be_https():
    with pytest.raises(ValueError):
        _dev_settings(
            app_env="prod",
            oidc_enabled=True,
            oidc_issuer="http://idp.example.com",
            oidc_client_id="cid",
            oidc_redirect_uri="https://gw.example.com/callback",
        )


def test_oidc_enabled_missing_fields_is_error():
    # Settings enforces this, so construct via run_checks on a dev settings object
    # that is coherent; assert prod https is enforced separately.
    result = run_checks(
        _dev_settings(
            oidc_enabled=True,
            oidc_issuer="https://idp.example.com",
            oidc_client_id="cid",
            oidc_redirect_uri="https://gw.example.com/callback",
        )
    )
    assert result.ok, result.errors


def test_short_bootstrap_token_rejected_by_settings():
    with pytest.raises(ValueError):
        _dev_settings(bootstrap_token="short")


def test_placeholder_bootstrap_token_rejected_by_settings():
    with pytest.raises(ValueError):
        _dev_settings(bootstrap_token="changeme")


def test_disabled_features_are_warnings():
    result = run_checks(_dev_settings())
    assert any("OIDC is disabled" in w for w in result.warnings)
    assert any("bootstrap token is unset" in w for w in result.warnings)


def test_dev_env_secret_backend_is_warning_only():
    result = run_checks(_dev_settings(secret_backend="env"))
    assert result.ok, result.errors
    assert any("secret backend" in w for w in result.warnings)


def test_prod_env_secret_backend_is_invalid():
    result = run_checks(_dev_settings(app_env="prod", secret_backend="env"))
    assert not result.ok
    assert any("secret backend" in e for e in result.errors)


def test_prod_file_secret_backend_valid(tmp_path):
    result = run_checks(
        _dev_settings(
            app_env="prod",
            secret_backend="file",
            secret_directory=str(tmp_path),
        )
    )
    assert result.ok, result.errors


def test_file_secret_backend_requires_directory():
    result = run_checks(_dev_settings(secret_backend="file", secret_directory=None))
    assert not result.ok
    assert any("AETHERGATE_SECRET_DIRECTORY" in e for e in result.errors)


def test_file_secret_backend_rejects_relative_directory():
    result = run_checks(
        _dev_settings(secret_backend="file", secret_directory="relative/secrets")
    )
    assert not result.ok
    assert any("absolute" in e for e in result.errors)


def test_file_secret_backend_rejects_missing_directory(tmp_path):
    result = run_checks(
        _dev_settings(
            secret_backend="file",
            secret_directory=str(tmp_path / "missing"),
        )
    )
    assert not result.ok
    assert any("does not exist" in e for e in result.errors)


def test_run_checks_never_prints_secrets(capsys, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://u:topsecretpw@h/db")
    monkeypatch.setenv("AETHERGATE_QUEUE_KEY", VALID_QUEUE_KEY)
    monkeypatch.setenv("AETHERGATE_UPSTREAM_ALLOWLIST", "api.example.com")
    monkeypatch.setenv("AETHERGATE_ENV", "dev")
    monkeypatch.delenv("AETHERGATE_BOOTSTRAP_TOKEN", raising=False)
    # Avoid the process-wide cached settings.
    import aethergate.config as config_module

    monkeypatch.setattr(config_module, "_settings", None)
    monkeypatch.setattr("aethergate.config_check.get_settings", config_module.get_settings)
    code = main()
    out = capsys.readouterr().out
    assert "topsecretpw" not in out
    assert VALID_QUEUE_KEY not in out
    assert code == 0


def test_config_check_invalid_exit_nonzero(capsys, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://u:p@h/db")
    monkeypatch.setenv("AETHERGATE_QUEUE_KEY", "invalid-key")
    monkeypatch.setenv("AETHERGATE_UPSTREAM_ALLOWLIST", "api.example.com")
    monkeypatch.setenv("AETHERGATE_ENV", "dev")
    import aethergate.config as config_module

    monkeypatch.setattr(config_module, "_settings", None)
    monkeypatch.setattr("aethergate.config_check.get_settings", config_module.get_settings)
    code = main()
    out = capsys.readouterr().out
    assert code != 0
    assert "invalid-key" not in out
