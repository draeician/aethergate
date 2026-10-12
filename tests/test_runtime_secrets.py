"""Runtime resolver/policy selection (API and worker use the same factory)."""

from __future__ import annotations

import pytest

from aethergate.config import Settings
from aethergate.errors import SecretResolutionError
from aethergate.scheduler import runtime
from aethergate.secrets import EnvSecretResolver, FileSecretResolver


def _settings(**overrides) -> Settings:
    base = dict(
        _env_file=None,
        database_url="postgresql+asyncpg://u:p@h/db",
        app_env="dev",
        upstream_allowlist="api.example.com",
        oidc_enabled=False,
        allow_inference_auth_bypass=False,
        bootstrap_token=None,
    )
    base.update(overrides)
    return Settings(**base)


def test_runtime_dev_selects_env_resolver(monkeypatch):
    monkeypatch.setattr(
        runtime, "get_settings", lambda: _settings(app_env="dev", secret_backend="env")
    )
    service = runtime.build_inference_service()
    assert isinstance(service._resolver, EnvSecretResolver)


def test_runtime_prod_file_selects_file_resolver(monkeypatch, tmp_path):
    monkeypatch.setattr(
        runtime,
        "get_settings",
        lambda: _settings(
            app_env="prod",
            secret_backend="file",
            secret_directory=str(tmp_path),
            queue_key="a" * 20,
        ),
    )
    service = runtime.build_inference_service()
    assert isinstance(service._resolver, FileSecretResolver)


def test_runtime_prod_env_is_rejected(monkeypatch):
    monkeypatch.setattr(
        runtime,
        "get_settings",
        lambda: _settings(
            app_env="prod",
            secret_backend="env",
            queue_key="a" * 20,
        ),
    )
    with pytest.raises(SecretResolutionError):
        runtime.build_inference_service()


def test_runtime_prod_policy_requires_literal_ip(monkeypatch):
    from aethergate.errors import DestinationDenied

    monkeypatch.setattr(
        runtime,
        "get_settings",
        lambda: _settings(
            app_env="prod",
            secret_backend="file",
            secret_directory="/tmp",
            queue_key="a" * 20,
            upstream_allowlist="ollama.internal",
        ),
    )
    service = runtime.build_inference_service()
    with pytest.raises(DestinationDenied):
        service._policy.validate("http://ollama.internal:11434")
