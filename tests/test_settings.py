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
    assert Settings(app_env="prod", database_url="postgresql://x").app_env == "prod"
    assert Settings(database_url="postgresql://x").app_env == "dev"


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
