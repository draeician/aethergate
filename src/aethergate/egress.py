"""Egress / destination guard for upstream provider dispatch.

The upstream destination comes only from administrator-controlled persisted
configuration (an endpoint's ``base_destination``), never from an inference
request. This module enforces an explicit policy before dispatch so the
inference path cannot become an arbitrary SSRF primitive.

Two independent controls are provided:

- :class:`DestinationPolicy` — an explicit host allowlist that rejects
  unsupported schemes, URL userinfo, and literal metadata/link-local/loopback/
  multicast/unspecified/reserved destinations. It can additionally require the
  configured host to be a **literal IP address**, which closes the DNS-rebinding
  class for the current supported Ollama deployment: the address that is
  validated is the address that is connected to, so DNS cannot be swapped
  between validation and connect.
- :func:`install_redirect_safety` — forces the pinned LiteLLM transport to
  never follow HTTP redirects. Empirically, pinned LiteLLM 1.104.0 constructs
  its httpx clients with ``follow_redirects=True``, so a provider ``3xx`` could
  steer the client to a different destination ("redirect escape"). Forcing
  ``follow_redirects=False`` makes every ``301/302/303/307/308`` surface as a
  provider failure instead of a second request, so the ``Location`` target is
  never contacted.
"""

from __future__ import annotations

import ipaddress
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

from aethergate.errors import DestinationDenied

if TYPE_CHECKING:  # pragma: no cover - typing only
    from aethergate.config import Settings

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


def _is_ip_literal(host: str) -> bool:
    try:
        ipaddress.ip_address(host.rstrip(".").lower())
    except ValueError:
        return False
    return True


class DestinationPolicy:
    """Explicit allowlist gate over upstream destination URLs.

    ``require_literal_ip`` is the narrow Beta production policy for the current
    supported Ollama upstream: DNS names are rejected so a resolved address
    cannot be swapped between validation and connect. Authorized private-LAN
    literal IPs remain allowed when allowlisted; RFC1918 is not rejected
    generally.
    """

    def __init__(self, allowed_hosts: set[str], *, require_literal_ip: bool = False) -> None:
        self._allowed = {host.strip().lower() for host in allowed_hosts if host.strip()}
        self._require_literal_ip = require_literal_ip

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
        if self._require_literal_ip and not _is_ip_literal(host):
            raise DestinationDenied(
                "a literal IP upstream is required (DNS names are disabled by the "
                "production egress policy)"
            )


def build_destination_policy(settings: Settings) -> DestinationPolicy:
    """Build the environment-aware destination policy from typed settings.

    Production requires literal-IP upstreams until transport-level pinning
    exists; dev/test may use service DNS names for deterministic Compose CI.
    The same policy object is used by catalog validation and dispatch.
    """
    require_literal_ip = settings.egress_require_literal_ip or settings.app_env == "prod"
    return DestinationPolicy(
        settings.upstream_allowlist_hosts,
        require_literal_ip=require_literal_ip,
    )


_REDIRECT_GUARD_INSTALLED = False


def _force_no_redirect(handler: object) -> None:
    if handler is None or not hasattr(handler, "follow_redirects"):
        return
    try:
        handler.follow_redirects = False  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001 - best-effort hardening
        pass
    inner = getattr(handler, "client", None)
    if inner is not None and hasattr(inner, "follow_redirects"):
        try:
            inner.follow_redirects = False
        except Exception:  # noqa: BLE001 - best-effort hardening
            pass


def install_redirect_safety() -> None:
    """Force the pinned LiteLLM transport never to follow HTTP redirects.

    Patches ``AsyncHTTPHandler`` so every future client is created with
    ``follow_redirects=False`` and mutates already-created module-level/cached
    handlers so their in-process httpx clients also stop following redirects.
    Idempotent.
    """
    global _REDIRECT_GUARD_INSTALLED
    if _REDIRECT_GUARD_INSTALLED:
        return

    import litellm
    from litellm.llms.custom_httpx import http_handler as _http_handler

    original_init = _http_handler.AsyncHTTPHandler.__init__
    if not getattr(original_init, "_aethergate_redirect_safe", False):

        def _init(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            kwargs["follow_redirects"] = False
            original_init(self, *args, **kwargs)

        _init._aethergate_redirect_safe = True  # type: ignore[attr-defined]
        _http_handler.AsyncHTTPHandler.__init__ = _init  # type: ignore[method-assign]

    _force_no_redirect(getattr(litellm, "module_level_aclient", None))
    _force_no_redirect(getattr(litellm, "module_level_client", None))
    cache = getattr(litellm, "in_memory_llm_clients_cache", None)
    data = getattr(cache, "cache_dict", None)
    if isinstance(data, dict):
        for entry in list(data.values()):
            _force_no_redirect(getattr(entry, "value", entry))

    _REDIRECT_GUARD_INSTALLED = True
