"""Egress / destination guard for upstream provider dispatch.

The upstream destination comes only from administrator-controlled persisted
configuration (an endpoint's ``base_destination``), never from an inference
request. This module enforces a minimal explicit policy before dispatch so the
first real inference path cannot become an arbitrary SSRF primitive.

Redirect-following hardening (so a redirect cannot escape an allowed host) is a
deferred enterprise-hardening item; this milestone's primary control is the
explicit allowlist applied to the configured destination.
"""

from __future__ import annotations

import ipaddress
from urllib.parse import urlsplit

from aethergate.errors import DestinationDenied

_SUPPORTED_SCHEMES = ("http", "https")

_METADATA_HOSTS = {
    "metadata.google.internal",
    "metadata",
}


def _is_forbidden_host(host: str) -> bool:
    normalized = host.rstrip(".").lower()
    if normalized in _METADATA_HOSTS:
        return True
    try:
        ip = ipaddress.ip_address(normalized)
    except ValueError:
        return False
    return (
        ip.is_link_local
        or ip.is_loopback
        or ip.is_multicast
        or ip.is_unspecified
        or ip.is_reserved
    )


class DestinationPolicy:
    """Explicit allowlist gate over upstream destination URLs."""

    def __init__(self, allowed_hosts: set[str]) -> None:
        self._allowed = {host.strip().lower() for host in allowed_hosts if host.strip()}

    def validate(self, url: str) -> None:
        """Raise :class:`DestinationDenied` unless ``url`` is allowed."""
        parts = urlsplit(url)
        if parts.scheme not in _SUPPORTED_SCHEMES:
            raise DestinationDenied(f"unsupported scheme {parts.scheme!r}")
        if parts.username is not None or parts.password is not None:
            raise DestinationDenied("URL userinfo is not permitted")
        host = (parts.hostname or "").lower()
        if not host:
            raise DestinationDenied("destination has no host")
        if _is_forbidden_host(host):
            raise DestinationDenied("metadata/link-local destination is not permitted")
        if host not in self._allowed:
            raise DestinationDenied(f"host {host!r} is not allowlisted")
