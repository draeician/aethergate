"""Safe diagnostics bundle: contained fields and canary absence."""

from __future__ import annotations

import json

from aethergate.diagnostics import _redact_bundle


async def test_bundle_contains_expected_safe_sections(monkeypatch):
    # Force the DB-touching collectors to no-ops to keep this offline.
    async def _migration() -> dict:
        return {"expected_head": "0017"}

    async def _health() -> dict:
        return {"database_reachable": True}

    async def _empty() -> dict:
        return {}

    monkeypatch.setattr("aethergate.diagnostics._migration_state", _migration)
    monkeypatch.setattr("aethergate.diagnostics._health", _health)
    monkeypatch.setattr("aethergate.diagnostics._queue_summary", _empty)
    monkeypatch.setattr("aethergate.diagnostics._observability_summary", _empty)
    monkeypatch.setattr("aethergate.diagnostics._config_flags", lambda: {"app_env": "dev"})
    from aethergate.diagnostics import acollect

    bundle = await acollect()
    assert set(bundle) == {
        "version",
        "migration",
        "health",
        "config",
        "queue",
        "observability",
        "recent_logs",
    }


def test_redact_bundle_scrubs_secret_canaries():
    canaries = {
        "db_url": "postgresql+asyncpg://u:secretpw@db:5432/aethergate",
        "agk": "agk_live_abcdef0123456789",
        "ags": "ags_session_abcdef0123456789",
        "provider": "api_key=sk-provider-secret",
        "authorization": "Authorization: Bearer sk-live-secret",
    }
    bundle = _redact_bundle({"leak": dict(canaries)})
    rendered = json.dumps(bundle)
    for value in canaries.values():
        assert value not in rendered
    assert "secretpw" not in rendered


def test_config_flags_never_include_secret_values(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://u:topsecretpw@h/db")
    monkeypatch.setenv("AETHERGATE_QUEUE_KEY", "invalid-but-untouched")
    monkeypatch.setenv("AETHERGATE_UPSTREAM_ALLOWLIST", "api.example.com")
    monkeypatch.setenv("AETHERGATE_ENV", "dev")
    import aethergate.config as config_module

    monkeypatch.setattr(config_module, "_settings", None)
    from aethergate.diagnostics import _config_flags

    flags = _config_flags()
    rendered = json.dumps(flags)
    for canary in ("topsecretpw", "postgresql+asyncpg", "invalid-but-untouched"):
        assert canary not in rendered
    # Only booleans/ints/short safe strings.
    for key, value in flags.items():
        assert not isinstance(value, str) or "://" not in value, key
