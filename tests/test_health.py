"""Tests for liveness/readiness endpoints."""

from __future__ import annotations

from fastapi.testclient import TestClient

from aethergate.main import app

client = TestClient(app)


def test_liveness_returns_ok():
    response = client.get("/health/live")
    assert response.status_code == 200
    assert response.json() == {"status": "alive"}


def test_readiness_success(monkeypatch):
    async def _ok():
        return True

    async def _current():
        return True

    monkeypatch.setattr("aethergate.persistence.db.check_database_connection", _ok)
    monkeypatch.setattr("aethergate.api.health.schema_is_current", _current)
    response = client.get("/health/ready")
    assert response.status_code == 200
    assert response.json() == {"status": "ready"}


def test_readiness_failure_returns_503_without_leaking(monkeypatch):
    async def _down():
        return False

    monkeypatch.setattr("aethergate.persistence.db.check_database_connection", _down)
    response = client.get("/health/ready")
    assert response.status_code == 503
    assert response.json() == {"status": "not_ready", "reason": "database_unreachable"}


def test_readiness_schema_not_current_returns_503(monkeypatch):
    async def _ok():
        return True

    async def _stale():
        return False

    monkeypatch.setattr("aethergate.persistence.db.check_database_connection", _ok)
    monkeypatch.setattr("aethergate.api.health.schema_is_current", _stale)
    response = client.get("/health/ready")
    assert response.status_code == 503
    assert response.json() == {"status": "not_ready", "reason": "schema_not_current"}
