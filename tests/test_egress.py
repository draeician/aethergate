"""Egress / destination policy tests."""

from __future__ import annotations

import pytest

from aethergate.config import Settings
from aethergate.egress import DestinationPolicy, build_destination_policy
from aethergate.errors import DestinationDenied


def _policy(*hosts: str) -> DestinationPolicy:
    return DestinationPolicy(set(hosts))


def test_allows_allowlisted_https_host():
    _policy("api.example.com").validate("https://api.example.com/v1")


def test_allows_allowlisted_host_with_port():
    _policy("192.168.22.50").validate("http://192.168.22.50:11434")


def test_rejects_unlisted_host():
    with pytest.raises(DestinationDenied):
        _policy("api.example.com").validate("http://evil.example.com")


def test_rejects_unsupported_scheme():
    with pytest.raises(DestinationDenied):
        _policy("api.example.com").validate("ftp://api.example.com")


def test_rejects_url_userinfo():
    with pytest.raises(DestinationDenied):
        _policy("api.example.com").validate("http://user:pass@api.example.com")


def test_rejects_metadata_host():
    with pytest.raises(DestinationDenied):
        _policy("169.254.169.254").validate("http://169.254.169.254/latest")


def test_rejects_link_local():
    with pytest.raises(DestinationDenied):
        _policy("169.254.1.1").validate("http://169.254.1.1")


def test_rejects_loopback_even_if_allowlisted():
    with pytest.raises(DestinationDenied):
        _policy("127.0.0.1").validate("http://127.0.0.1:11434")


def test_empty_allowlist_denies_all():
    with pytest.raises(DestinationDenied):
        DestinationPolicy(set()).validate("http://api.example.com")


def test_allowlist_is_case_insensitive():
    _policy("api.example.com").validate("http://API.EXAMPLE.COM")


# --- literal-IP production policy (DNS-rebinding closure) ------------------


def _settings(**overrides) -> Settings:
    base = dict(
        _env_file=None,
        database_url="postgresql+asyncpg://u:p@h/db",
        app_env="dev",
        upstream_allowlist="192.168.22.50",
        oidc_enabled=False,
        allow_inference_auth_bypass=False,
        bootstrap_token=None,
    )
    base.update(overrides)
    return Settings(**base)


def test_literal_ip_policy_allows_allowlisted_private_lan_ip():
    policy = DestinationPolicy({"192.168.22.50"}, require_literal_ip=True)
    policy.validate("http://192.168.22.50:11434")


def test_literal_ip_policy_allows_allowlisted_ipv6():
    policy = DestinationPolicy({"2001:db8::1"}, require_literal_ip=True)
    policy.validate("http://[2001:db8::1]:11434")


def test_literal_ip_policy_rejects_allowlisted_hostname():
    policy = DestinationPolicy({"ollama.internal"}, require_literal_ip=True)
    with pytest.raises(DestinationDenied):
        policy.validate("http://ollama.internal:11434")


def test_literal_ip_policy_still_rejects_metadata_and_link_local():
    policy = DestinationPolicy({"169.254.169.254", "169.254.1.1"}, require_literal_ip=True)
    with pytest.raises(DestinationDenied):
        policy.validate("http://169.254.169.254/latest")
    with pytest.raises(DestinationDenied):
        policy.validate("http://169.254.1.1")


def test_literal_ip_policy_still_requires_allowlist():
    policy = DestinationPolicy({"192.168.22.50"}, require_literal_ip=True)
    with pytest.raises(DestinationDenied):
        policy.validate("http://192.168.22.51")


def test_build_policy_prod_requires_literal_ip():
    policy = build_destination_policy(
        _settings(
            app_env="prod",
            upstream_allowlist="ollama.internal,192.168.22.50",
            queue_key="a" * 20,
        )
    )
    # Private-LAN literal IP still allowed in prod.
    policy.validate("http://192.168.22.50:11434")
    # DNS name rejected in prod even when allowlisted.
    with pytest.raises(DestinationDenied):
        policy.validate("http://ollama.internal:11434")


def test_build_policy_dev_allows_service_dns():
    policy = build_destination_policy(_settings(upstream_allowlist="fake-ollama"))
    policy.validate("http://fake-ollama:11434")


def test_build_policy_dev_can_opt_into_literal_ip():
    policy = build_destination_policy(
        _settings(
            upstream_allowlist="fake-ollama",
            egress_require_literal_ip=True,
        )
    )
    with pytest.raises(DestinationDenied):
        policy.validate("http://fake-ollama:11434")
