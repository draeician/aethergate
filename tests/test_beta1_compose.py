"""Deterministic shape checks for the Beta 1 production Compose profile.

These tests validate the production invariants in ``deploy/beta1/compose.yaml``
without Docker: they parse the YAML and assert that a copied/edited production
profile cannot silently weaken the settled security posture. Negative/invalid-prod
configuration cases are covered in ``tests/test_config_check.py``.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from aethergate.config import Settings
from aethergate.config_check import run_checks
from aethergate.encryption import QueueEncryptor

REPO_ROOT = Path(__file__).resolve().parents[1]
COMPOSE = REPO_ROOT / "deploy" / "beta1" / "compose.yaml"
ENV_EXAMPLE = REPO_ROOT / "deploy" / "beta1" / "env.example"


def _compose() -> dict:
    return yaml.safe_load(COMPOSE.read_text())


def _env(service: str) -> dict:
    raw = _compose()["services"][service]["environment"]
    # Compose allows both mapping and list forms; the beta1 profile uses mappings.
    assert isinstance(raw, dict)
    return raw


def test_beta1_services_are_minimal_and_production_only():
    services = set(_compose()["services"])
    assert services == {"api", "worker", "postgres"}
    # The deterministic dev IdP, fake provider, and web console must never appear.
    assert "dev-oidc-idp" not in services
    assert "fake-ollama" not in services
    assert "web" not in services


def test_beta1_api_and_worker_are_prod_with_strict_invariants():
    for svc in ("api", "worker"):
        env = _env(svc)
        assert env["AETHERGATE_ENV"] == "prod"
        assert env["AETHERGATE_ALLOW_INFERENCE_AUTH_BYPASS"] == "false"
        assert env["AETHERGATE_SECRET_BACKEND"] == "file"
        assert env["AETHERGATE_SECRET_DIRECTORY"] == "/run/secrets/aethergate-provider"
        assert env["AETHERGATE_EGRESS_REQUIRE_LITERAL_IP"] == "true"


def test_beta1_postgres_is_not_host_published():
    postgres = _compose()["services"]["postgres"]
    assert "ports" not in postgres


def test_beta1_api_is_loopback_only_ephemeral():
    ports = _compose()["services"]["api"]["ports"]
    assert ports
    for mapping in ports:
        assert "0.0.0.0" not in mapping
        assert "127.0.0.1::" in mapping


def test_beta1_names_do_not_collide_with_dev_or_nomnom():
    compose = _compose()
    assert compose.get("name") == "aethergate-beta1"
    # No hardcoded container_name: container naming is project-scoped (compose
    # auto-names them ``<project>-<service>-1``), so a disposable acceptance run
    # (``--project-name ag-beta1-smoke``) can never collide with the real
    # ``aethergate-beta1`` deployment or the long-lived ``aethergate-v2`` stack.
    for svc in compose["services"].values():
        assert "container_name" not in svc
    volumes = set(compose.get("volumes", {}))
    assert "aethergate-v2-pgdata" not in volumes
    assert "aethergate-beta1-pgdata" in volumes


def test_beta1_healthchecks_are_present():
    for svc in ("api", "worker", "postgres"):
        assert "healthcheck" in _compose()["services"][svc]


def test_beta1_read_only_rootfs_with_tmpfs():
    for svc in ("api", "worker"):
        service = _compose()["services"][svc]
        assert service.get("read_only") is True
        assert "/tmp" in service.get("tmpfs", [])


def test_beta1_secret_directory_is_read_only_bind_mount():
    for svc in ("api", "worker"):
        volumes = _compose()["services"][svc]["volumes"]
        mounts = [v for v in volumes if isinstance(v, dict)]
        secret_mounts = [
            v for v in mounts if v.get("target") == "/run/secrets/aethergate-provider"
        ]
        assert secret_mounts, f"{svc} is missing the provider-secret mount"
        for mount in secret_mounts:
            assert mount.get("read_only") is True
            assert mount.get("type") == "bind"


def test_beta1_env_example_has_no_live_secret_values():
    text = ENV_EXAMPLE.read_text()
    secret_keys = {
        "POSTGRES_PASSWORD",
        "AETHERGATE_QUEUE_KEY",
        "AETHERGATE_BOOTSTRAP_TOKEN",
        "AETHERGATE_UPSTREAM_ALLOWLIST",
        "AETHERGATE_SECRET_HOST_DIR",
        "AETHERGATE_OIDC_ISSUER",
        "AETHERGATE_OIDC_CLIENT_ID",
        "AETHERGATE_OIDC_CLIENT_SECRET",
        "AETHERGATE_OIDC_REDIRECT_URI",
        "AETHERGATE_OIDC_WEB_CALLBACK_PATH",
        "AETHERGATE_OIDC_DEVICE_CLIENT_ID",
    }
    for line in text.splitlines():
        if line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key in secret_keys:
            assert value == "", f"env.example carries a live value for {key}"


def test_beta1_prod_settings_shape_is_valid(tmp_path):
    settings = Settings(
        _env_file=None,
        database_url="postgresql+asyncpg://u:p@h/db",
        app_env="prod",
        queue_key=QueueEncryptor.generate_key(),
        bootstrap_token="agb_" + "a" * 32,
        upstream_allowlist="192.168.22.50",
        secret_backend="file",
        secret_directory=str(tmp_path),
        egress_require_literal_ip=True,
        allow_inference_auth_bypass=False,
        oidc_enabled=False,
    )
    result = run_checks(settings)
    assert result.ok, result.errors
    assert not result.errors
