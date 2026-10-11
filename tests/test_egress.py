"""Egress / destination policy tests."""

from __future__ import annotations

import pytest

from aethergate.egress import DestinationPolicy
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
