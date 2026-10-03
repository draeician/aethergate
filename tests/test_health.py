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

    monkeypatch.setattr("aethergate.persistence.db.check_database_connection", _ok)
    response = client.get("/health/ready")
    assert response.status_code == 200
    assert response.json() == {"status": "ready"}


def test_readiness_failure_returns_503_without_leaking(monkeypatch):
    async def _down():
        return False

    monkeypatch.setattr("aethergate.persistence.db.check_database_connection", _down)
    response = client.get("/health/ready")
    assert response.status_code == 503
    assert response.json() == {"status": "not_ready"}
