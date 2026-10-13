"""Tests for the secret-reference resolution foundation."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from aethergate.config import Settings
from aethergate.domain.entities import SecretRef
from aethergate.domain.ids import SecretRefId
from aethergate.errors import SecretResolutionError
from aethergate.secrets import (
    MAX_SECRET_FILE_BYTES,
    EnvSecretResolver,
    FileSecretResolver,
    build_secret_resolver,
)


def _ref(name: str, ref_id: str | None = None) -> SecretRef:
    return SecretRef(
        id=SecretRefId(ref_id or f"sr-{name}"),
        name=name,
        created_at=datetime(2026, 10, 3, tzinfo=UTC),
    )


def test_env_resolver_returns_material(monkeypatch):
    monkeypatch.setenv("AETHERGATE_SECRET_OPENAI_KEY", "material-value")
    resolver = EnvSecretResolver()
    assert resolver.resolve(_ref("openai-key")) == "material-value"


def test_env_resolver_raises_when_missing():
    resolver = EnvSecretResolver()
    with pytest.raises(SecretResolutionError):
        resolver.resolve(_ref("does-not-exist"))


def test_resolver_never_returns_value_in_repr():
    resolver = EnvSecretResolver()
    assert "material-value" not in repr(resolver)


# --- FileSecretResolver ----------------------------------------------------


def _file_resolver(tmp_path: Path, **kwargs) -> FileSecretResolver:
    return FileSecretResolver(str(tmp_path), **kwargs)


def test_file_resolver_reads_by_stable_id(tmp_path):
    (tmp_path / "sr-openai-key").write_text("material-value", encoding="utf-8")
    resolver = _file_resolver(tmp_path)
    assert resolver.resolve(_ref("display-name", "sr-openai-key")) == "material-value"


def test_file_resolver_uses_id_not_display_name(tmp_path):
    (tmp_path / "sr-id").write_text("by-id", encoding="utf-8")
    (tmp_path / "display-name").write_text("by-name", encoding="utf-8")
    resolver = _file_resolver(tmp_path)
    assert resolver.resolve(_ref("display-name", "sr-id")) == "by-id"


def test_file_resolver_strips_single_trailing_lf(tmp_path):
    (tmp_path / "sr-1").write_bytes(b"secret\n")
    assert _file_resolver(tmp_path).resolve(_ref("x", "sr-1")) == "secret"


def test_file_resolver_strips_single_trailing_crlf(tmp_path):
    (tmp_path / "sr-1").write_bytes(b"secret\r\n")
    assert _file_resolver(tmp_path).resolve(_ref("x", "sr-1")) == "secret"


def test_file_resolver_preserves_internal_newlines_and_whitespace(tmp_path):
    (tmp_path / "sr-1").write_bytes(b"line1\nline2\n\n")
    # Only ONE trailing newline is removed; the rest is material.
    assert _file_resolver(tmp_path).resolve(_ref("x", "sr-1")) == "line1\nline2\n"


def test_file_resolver_preserves_leading_and_internal_spaces(tmp_path):
    (tmp_path / "sr-1").write_bytes(b"  spaced value  \n")
    assert _file_resolver(tmp_path).resolve(_ref("x", "sr-1")) == "  spaced value  "


def test_file_resolver_rejects_empty_file(tmp_path):
    (tmp_path / "sr-1").write_bytes(b"")
    with pytest.raises(SecretResolutionError):
        _file_resolver(tmp_path).resolve(_ref("x", "sr-1"))


def test_file_resolver_rejects_newline_only_file(tmp_path):
    (tmp_path / "sr-1").write_bytes(b"\n")
    with pytest.raises(SecretResolutionError):
        _file_resolver(tmp_path).resolve(_ref("x", "sr-1"))


def test_file_resolver_rejects_nul(tmp_path):
    (tmp_path / "sr-1").write_bytes(b"sec\x00ret")
    with pytest.raises(SecretResolutionError):
        _file_resolver(tmp_path).resolve(_ref("x", "sr-1"))


def test_file_resolver_rejects_non_utf8(tmp_path):
    (tmp_path / "sr-1").write_bytes(b"\xff\xfe\x00\x01")
    with pytest.raises(SecretResolutionError):
        _file_resolver(tmp_path).resolve(_ref("x", "sr-1"))


def test_file_resolver_rejects_oversize(tmp_path):
    (tmp_path / "sr-1").write_bytes(b"a" * (MAX_SECRET_FILE_BYTES + 1))
    with pytest.raises(SecretResolutionError):
        _file_resolver(tmp_path).resolve(_ref("x", "sr-1"))


def test_file_resolver_rejects_path_separator_in_id(tmp_path):
    resolver = _file_resolver(tmp_path)
    with pytest.raises(SecretResolutionError):
        resolver.resolve(_ref("x", "../../etc/passwd"))


def test_file_resolver_rejects_directory_target(tmp_path):
    (tmp_path / "sr-1").mkdir()
    with pytest.raises(SecretResolutionError):
        _file_resolver(tmp_path).resolve(_ref("x", "sr-1"))


def test_file_resolver_rejects_symlink_escape(tmp_path):
    outside = tmp_path.parent / "outside-secret"
    outside.write_text("outside", encoding="utf-8")
    root = tmp_path / "root"
    root.mkdir()
    (root / "sr-1").symlink_to(outside)
    with pytest.raises(SecretResolutionError):
        FileSecretResolver(str(root)).resolve(_ref("x", "sr-1"))


def test_file_resolver_allows_symlink_inside_root(tmp_path):
    root = tmp_path / "root"
    target_dir = root / "..data"
    target_dir.mkdir(parents=True)
    (target_dir / "sr-1").write_text("projected", encoding="utf-8")
    (root / "sr-1").symlink_to(target_dir / "sr-1")
    assert FileSecretResolver(str(root)).resolve(_ref("x", "sr-1")) == "projected"


def test_file_resolver_requires_absolute_directory():
    with pytest.raises(ValueError):
        FileSecretResolver("relative/secrets")


def test_file_resolver_requires_existing_directory(tmp_path):
    with pytest.raises(ValueError):
        FileSecretResolver(str(tmp_path / "missing"))


def test_file_resolver_error_never_contains_file_contents(tmp_path):
    (tmp_path / "sr-1").write_bytes(b"topsecretvalue\x00")
    with pytest.raises(SecretResolutionError) as excinfo:
        _file_resolver(tmp_path).resolve(_ref("x", "sr-1"))
    assert "topsecretvalue" not in str(excinfo.value)


# --- build_secret_resolver -------------------------------------------------


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


def test_factory_dev_env_selects_env_resolver():
    resolver = build_secret_resolver(_settings(app_env="dev", secret_backend="env"))
    assert isinstance(resolver, EnvSecretResolver)


def test_factory_prod_env_is_rejected():
    with pytest.raises(SecretResolutionError):
        build_secret_resolver(
            _settings(app_env="prod", secret_backend="env", queue_key="a" * 20)
        )


def test_factory_prod_file_selects_file_resolver(tmp_path):
    resolver = build_secret_resolver(
        _settings(
            app_env="prod",
            secret_backend="file",
            secret_directory=str(tmp_path),
            queue_key="a" * 20,
        )
    )
    assert isinstance(resolver, FileSecretResolver)


def test_factory_file_requires_directory():
    with pytest.raises(SecretResolutionError):
        build_secret_resolver(_settings(secret_backend="file", secret_directory=None))


def test_factory_file_rejects_relative_directory():
    with pytest.raises(SecretResolutionError):
        build_secret_resolver(
            _settings(secret_backend="file", secret_directory="relative/dir")
        )


def test_file_backend_never_falls_back_to_env(tmp_path, monkeypatch):
    # A canary in the env must not rescue a missing file.
    monkeypatch.setenv("AETHERGATE_SECRET_OPENAI_KEY", "env-canary")
    resolver = build_secret_resolver(
        _settings(secret_backend="file", secret_directory=str(tmp_path))
    )
    with pytest.raises(SecretResolutionError):
        resolver.resolve(_ref("openai-key", "sr-missing"))
