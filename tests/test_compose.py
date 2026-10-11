"""Static checks over the v2 Compose file and Dockerfile (no Docker required)."""

from __future__ import annotations

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
COMPOSE = REPO_ROOT / "deploy" / "v2" / "compose.yaml"
DOCKERFILE = REPO_ROOT / "deploy" / "v2" / "Dockerfile"


def _compose() -> dict:
    return yaml.safe_load(COMPOSE.read_text())


def test_compose_does_not_publish_postgres_to_host():
    postgres = _compose()["services"]["postgres"]
    assert "ports" not in postgres


def test_compose_api_uses_ephemeral_loopback_port():
    ports = _compose()["services"]["api"]["ports"]
    for mapping in ports:
        assert "0.0.0.0" not in mapping
        assert "127.0.0.1::" in mapping


def test_compose_has_no_fixed_host_port():
    raw = COMPOSE.read_text()
    assert "8000:8000" not in raw


def test_compose_names_do_not_collide_with_legacy():
    compose = _compose()
    containers = {
        svc["container_name"]
        for svc in compose["services"].values()
        if "container_name" in svc
    }
    assert "aethergate-api" not in containers
    assert "aethergate-frontend" not in containers
    assert compose.get("name") == "aethergate-v2"


def test_dockerfile_runs_as_non_root():
    text = DOCKERFILE.read_text()
    assert "USER root" not in text
    assert "USER aethergate" in text
