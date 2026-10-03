"""Tests for the secret-reference resolution foundation."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from aethergate.domain.entities import SecretRef
from aethergate.domain.ids import SecretRefId
from aethergate.errors import SecretResolutionError
from aethergate.secrets import EnvSecretResolver


def _ref(name: str) -> SecretRef:
    return SecretRef(
        id=SecretRefId(f"sr-{name}"),
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
