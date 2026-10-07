"""Tests for the typed runtime settings layer."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from aethergate.config import Settings


@pytest.fixture(autouse=True)
def _clear_db_env(monkeypatch):
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
    ):
        monkeypatch.delenv(name, raising=False)


def test_database_url_takes_precedence():
    settings = Settings(database_url="postgresql+asyncpg://u:p@db:5432/app")
    assert settings.resolved_database_url == "postgresql+asyncpg://u:p@db:5432/app"


def test_assembles_url_from_parts():
    pw = "p" + "@" + "ss word"
    settings = Settings(
        postgres_host="db",
        postgres_user="u",
        postgres_password=pw,
        postgres_db="app",
    )
    assert settings.resolved_database_url == (
        "postgresql+asyncpg://u:p%40ss+word@db:5432/app"
    )


def test_missing_database_config_raises():
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_secret_masked_in_repr():
    settings = Settings(database_url="postgresql+asyncpg://u:hunter2@db:5432/app")
    assert "hunter2" not in repr(settings)
    assert "hunter2" not in str(settings)


def test_explicit_environment_mode():
    assert (
        Settings(
            app_env="prod",
            database_url="postgresql://x",
            queue_key="x" * 32,
        ).app_env
        == "prod"
    )
    assert Settings(database_url="postgresql://x").app_env == "dev"


def test_queue_key_required_in_prod():
    with pytest.raises(ValidationError):
        Settings(app_env="prod", database_url="postgresql://x")


def test_queue_key_optional_in_dev():
    assert Settings(app_env="dev", database_url="postgresql://x").queue_key is None


def test_inference_auth_bypass_forbidden_in_prod():
    with pytest.raises(ValidationError):
        Settings(
            app_env="prod",
            database_url="postgresql://x",
            allow_inference_auth_bypass=True,
        )


def test_inference_auth_bypass_allowed_in_dev():
    settings = Settings(
        app_env="dev",
        database_url="postgresql://x",
        allow_inference_auth_bypass=True,
    )
    assert settings.allow_inference_auth_bypass is True


def test_upstream_allowlist_parsing():
    settings = Settings(
        database_url="postgresql://x",
        upstream_allowlist="HostA, hostb,,127.0.0.1",
    )
    assert settings.upstream_allowlist_hosts == {"hosta", "hostb", "127.0.0.1"}
    assert Settings(database_url="postgresql://x").upstream_allowlist_hosts == set()


def test_heartbeat_must_be_below_lease():
    with pytest.raises(ValidationError):
        Settings(
            database_url="postgresql://x",
            worker_lease_seconds=10.0,
            worker_heartbeat_seconds=10.0,
        )
    with pytest.raises(ValidationError):
        Settings(
            database_url="postgresql://x",
            worker_lease_seconds=10.0,
            worker_heartbeat_seconds=20.0,
        )


def test_heartbeat_and_lease_must_be_positive():
    for field, value in (
        ("worker_lease_seconds", 0.0),
        ("worker_heartbeat_seconds", 0.0),
        ("queue_max_wait_seconds", 0.0),
        ("queue_total_lifetime_seconds", 0.0),
    ):
        with pytest.raises(ValidationError):
            Settings(database_url="postgresql://x", **{field: value})


def test_default_scheduler_timing_is_valid():
    settings = Settings(database_url="postgresql://x")
    assert settings.worker_heartbeat_seconds < settings.worker_lease_seconds


# --- bootstrap token strength guard -------------------------------------------


def test_bootstrap_token_unset_disables_bootstrap():
    settings = Settings(database_url="postgresql://x")
    assert settings.bootstrap_token is None


def test_bootstrap_token_generated_dev_format_accepted():
    import secrets

    token = "agb_" + secrets.token_urlsafe(32)
    settings = Settings(database_url="postgresql://x", bootstrap_token=token)
    assert settings.bootstrap_token is not None
    assert settings.bootstrap_token.get_secret_value() == token


def test_bootstrap_token_short_arbitrary_rejected():
    with pytest.raises(ValidationError):
        Settings(database_url="postgresql://x", bootstrap_token="short")


def test_bootstrap_token_placeholder_rejected():
    for placeholder in ("changeme", "CHANGE-ME", "replaceme", "bootstrap"):
        with pytest.raises(ValidationError):
            Settings(database_url="postgresql://x", bootstrap_token=placeholder)


def test_bootstrap_token_never_appears_in_output():
    token = "agb_" + "x" * 32
    settings = Settings(database_url="postgresql://x", bootstrap_token=token)
    assert token not in repr(settings)
    assert token not in str(settings)


def test_bootstrap_token_whitespace_only_disables():
    settings = Settings(database_url="postgresql://x", bootstrap_token="   ")
    assert settings.bootstrap_token is None
