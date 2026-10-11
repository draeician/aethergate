"""OIDC provider client: discovery, JWKS, token exchange, ID-token validation.

This module implements the standards-aware parts of the OIDC authorization-code
flow used by the admin browser-session login. It never implements cryptographic
signature verification by hand — that is delegated to ``PyJWT`` (RS/ES/PS
signatures verified against the provider's JWKS).

Security invariants:

- Discovery and JWKS URLs are derived only from the configured issuer; no
  user-controlled discovery/JWKS/token endpoint is ever honored.
- Only asymmetric algorithms (RS*/ES*/PS*) are accepted; ``none`` and symmetric
  ``HS*`` are rejected regardless of provider metadata.
- The ID token's ``iss``, ``aud`` (client ID), ``exp``/``nbf``, and ``nonce`` are
  all validated; a missing/empty ``sub`` is rejected.
- The authorization code, ID token, access token, and client secret are never
  logged or persisted.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
import time
from typing import Any

import httpx
import jwt
from jwt import PyJWKSet

from aethergate.config import Settings, get_settings
from aethergate.errors import OidcAuthenticationFailed, OidcConfigurationError

# Allowed asymmetric signing algorithms. ``none`` and symmetric ``HS*`` are
# deliberately absent and never accepted.
ALLOWED_ALGORITHMS: frozenset[str] = frozenset(
    {
        "RS256",
        "RS384",
        "RS512",
        "ES256",
        "ES384",
        "ES512",
        "PS256",
        "PS384",
        "PS512",
    }
)

DISCOVERY_PATH = "/.well-known/openid-configuration"
PKCE_METHOD = "S256"

_DISCOVERY_TTL_SECONDS = 300.0
_JWKS_TTL_SECONDS = 300.0
_REQUEST_TIMEOUT_SECONDS = 10.0


def generate_pkce_pair() -> tuple[str, str]:
    """Return ``(code_verifier, code_challenge)`` using the S256 method.

    ``code_verifier`` is 64 URL-safe characters (48 bytes = 384 bits of CSPRNG
    entropy); ``code_challenge`` is its unpadded base64url SHA-256 digest.
    """
    verifier = secrets.token_urlsafe(48)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


def generate_state() -> str:
    """Return a cryptographically random login ``state`` (32 bytes -> 43 chars)."""
    return secrets.token_urlsafe(32)


def generate_nonce() -> str:
    """Return a cryptographically random login ``nonce`` (32 bytes -> 43 chars)."""
    return secrets.token_urlsafe(32)


class OidcProvider:
    """A configured OIDC provider with bounded, in-memory discovery/JWKS caching.

    Construct with an explicit ``http_client`` to inject a mock/ASGI transport in
    tests; otherwise a shared client is created lazily. The application should
    use :func:`get_oidc_provider` for the process-wide singleton.
    """

    def __init__(
        self,
        settings: Settings | None = None,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._http = http_client
        self._owns_client = http_client is None
        self._discovery: tuple[float, dict[str, Any]] | None = None
        self._jwks: tuple[float, PyJWKSet] | None = None

    async def _get_http(self) -> httpx.AsyncClient:
        if self._http is None:
            self._http = httpx.AsyncClient(timeout=_REQUEST_TIMEOUT_SECONDS)
        return self._http

    async def close(self) -> None:
        if self._owns_client and self._http is not None:
            await self._http.aclose()
            self._http = None

    async def _discover(self) -> dict[str, Any]:
        issuer = self._settings.oidc_issuer
        if not issuer:
            raise OidcConfigurationError("OIDC issuer is not configured")
        now = time.monotonic()
        if self._discovery is not None and now - self._discovery[0] < _DISCOVERY_TTL_SECONDS:
            return self._discovery[1]
        http = await self._get_http()
        url = f"{issuer.rstrip('/')}{DISCOVERY_PATH}"
        try:
            response = await http.get(url)
            response.raise_for_status()
            document = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise OidcConfigurationError("OIDC discovery failed") from exc
        if document.get("issuer") != issuer:
            raise OidcConfigurationError("OIDC discovery issuer mismatch")
        self._discovery = (now, document)
        return document

    async def _jwks_keys(self) -> PyJWKSet:
        document = await self._discover()
        jwks_uri = document.get("jwks_uri")
        if not isinstance(jwks_uri, str) or not jwks_uri:
            raise OidcConfigurationError("OIDC discovery missing jwks_uri")
        now = time.monotonic()
        if self._jwks is not None and now - self._jwks[0] < _JWKS_TTL_SECONDS:
            return self._jwks[1]
        http = await self._get_http()
        try:
            response = await http.get(jwks_uri)
            response.raise_for_status()
            jwks = PyJWKSet.from_dict(response.json())
        except (httpx.HTTPError, ValueError, jwt.PyJWTError) as exc:
            raise OidcConfigurationError("OIDC JWKS fetch failed") from exc
        self._jwks = (now, jwks)
        return jwks

    def authorization_endpoint(self, document: dict[str, Any]) -> str:
        endpoint = document.get("authorization_endpoint")
        if not isinstance(endpoint, str) or not endpoint:
            raise OidcConfigurationError("OIDC discovery missing authorization_endpoint")
        return endpoint

    def token_endpoint(self, document: dict[str, Any]) -> str:
        endpoint = document.get("token_endpoint")
        if not isinstance(endpoint, str) or not endpoint:
            raise OidcConfigurationError("OIDC discovery missing token_endpoint")
        return endpoint

    def device_authorization_endpoint(self, document: dict[str, Any]) -> str:
        endpoint = document.get("device_authorization_endpoint")
        if not isinstance(endpoint, str) or not endpoint:
            raise OidcConfigurationError(
                "OIDC discovery missing device_authorization_endpoint"
            )
        return endpoint

    async def has_device_authorization_endpoint(self) -> bool:
        """Return True when the provider advertises a device authorization endpoint."""
        document = await self._discover()
        return isinstance(document.get("device_authorization_endpoint"), str)

    async def start_device_authorization(self) -> dict[str, Any]:
        """Call the provider device-authorization endpoint (RFC 8628).

        Uses the public device client (no client secret). Returns the raw
        provider response (device_code/user_code/verification URIs/expiry/
        interval). Raises :class:`OidcConfigurationError` when the endpoint is
        absent or the provider rejects the request.
        """
        document = await self._discover()
        endpoint = self.device_authorization_endpoint(document)
        client_id = self._settings.oidc_device_client_id
        if not client_id:
            raise OidcConfigurationError("device client id is not configured")
        data = {
            "client_id": client_id,
            "scope": " ".join(self._settings.oidc_device_scope_list),
        }
        http = await self._get_http()
        try:
            response = await http.post(endpoint, data=data)
            response.raise_for_status()
            return response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise OidcConfigurationError("device authorization failed") from exc

    async def poll_device_token(self, device_code: str) -> tuple[str, dict[str, Any]]:
        """Poll the provider token endpoint using the device grant.

        Returns ``(status, payload)`` where ``status`` is one of ``success``,
        ``authorization_pending``, ``slow_down``, ``expired_token``, or
        ``access_denied``. ``payload`` is the token response on success, empty
        otherwise. Never logs the device_code.
        """
        document = await self._discover()
        endpoint = self.token_endpoint(document)
        client_id = self._settings.oidc_device_client_id
        data = {
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
            "device_code": device_code,
            "client_id": client_id,
        }
        http = await self._get_http()
        try:
            response = await http.post(endpoint, data=data)
        except httpx.HTTPError:
            raise OidcAuthenticationFailed() from None

        if 200 <= response.status_code < 300:
            return "success", response.json()

        try:
            body = response.json()
            error = body.get("error")
        except ValueError:
            raise OidcAuthenticationFailed() from None

        if error in (
            "authorization_pending",
            "slow_down",
            "expired_token",
            "access_denied",
        ):
            return error, {}
        raise OidcAuthenticationFailed()

    async def validate_device_id_token(self, id_token: str) -> dict[str, Any]:
        """Validate a device-flow ID token (no nonce; audience == device client).

        The device grant has no ``nonce`` binding, so nonce validation is
        skipped; ``iss``, ``aud`` (device client id), ``exp``/``nbf``, signature,
        algorithm, and ``sub`` are still strictly validated.
        """
        return await self._decode_id_token(
            id_token,
            audience=self._settings.oidc_device_client_id,
            expected_nonce=None,
        )

    async def begin_login(
        self,
        *,
        state: str,
        nonce: str,
        code_challenge: str,
    ) -> str:
        """Resolve discovery and return the provider authorization URL."""
        document = await self._discover()
        from urllib.parse import urlencode

        params = {
            "response_type": "code",
            "client_id": self._settings.oidc_client_id,
            "redirect_uri": self._settings.oidc_redirect_uri,
            "scope": " ".join(self._settings.oidc_scope_list),
            "state": state,
            "nonce": nonce,
            "code_challenge": code_challenge,
            "code_challenge_method": PKCE_METHOD,
        }
        endpoint = self.authorization_endpoint(document)
        separator = "&" if "?" in endpoint else "?"
        return f"{endpoint}{separator}{urlencode(params)}"

    async def exchange_code(self, *, code: str, code_verifier: str) -> dict[str, Any]:
        """Exchange an authorization code for tokens at the provider token endpoint."""
        document = await self._discover()
        endpoint = self.token_endpoint(document)
        data = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": self._settings.oidc_redirect_uri,
            "client_id": self._settings.oidc_client_id,
            "code_verifier": code_verifier,
        }
        client_secret = self._settings.oidc_client_secret
        if client_secret is not None:
            data["client_secret"] = client_secret.get_secret_value()
        http = await self._get_http()
        try:
            response = await http.post(endpoint, data=data)
            response.raise_for_status()
            return response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise OidcAuthenticationFailed() from exc

    async def validate_id_token(
        self,
        id_token: str,
        *,
        expected_nonce: str,
    ) -> dict[str, Any]:
        """Validate and decode an ID token, returning its verified claims.

        Raises :class:`OidcAuthenticationFailed` on any failure with a fixed,
        indistinguishable message. ``expected_nonce`` is compared in constant
        time. The ``sub`` claim must be present and non-empty.
        """
        return await self._decode_id_token(
            id_token,
            audience=self._settings.oidc_client_id,
            expected_nonce=expected_nonce,
        )

    async def _decode_id_token(
        self,
        id_token: str,
        *,
        audience: str | None,
        expected_nonce: str | None,
    ) -> dict[str, Any]:
        try:
            header = jwt.get_unverified_header(id_token)
        except jwt.PyJWTError as exc:
            raise OidcAuthenticationFailed() from exc

        alg = header.get("alg")
        if alg not in ALLOWED_ALGORITHMS:
            raise OidcAuthenticationFailed()

        try:
            jwks = await self._jwks_keys()
            signing_key = self._find_signing_key(jwks, header.get("kid"), alg)
        except OidcConfigurationError as exc:
            raise OidcAuthenticationFailed() from exc

        try:
            claims = jwt.decode(
                id_token,
                key=signing_key,
                algorithms=[alg],
                audience=audience,
                issuer=self._settings.oidc_issuer,
                options={"require": ["exp", "iss", "aud", "sub"]},
            )
        except jwt.PyJWTError as exc:
            raise OidcAuthenticationFailed() from exc

        subject = claims.get("sub")
        if not isinstance(subject, str) or not subject:
            raise OidcAuthenticationFailed()
        if expected_nonce is not None and not secrets.compare_digest(
            claims.get("nonce", ""), expected_nonce
        ):
            raise OidcAuthenticationFailed()

        return claims

    @staticmethod
    def _find_signing_key(jwks: PyJWKSet, kid: str | None, alg: str) -> Any:
        if kid is not None:
            for key in jwks.keys:
                if key.key_id == kid:
                    return key.key
        if len(jwks.keys) == 1:
            return jwks.keys[0].key
        raise OidcAuthenticationFailed()


_provider: OidcProvider | None = None


def get_oidc_provider() -> OidcProvider:
    """Return the process-wide OIDC provider client singleton."""
    global _provider
    if _provider is None:
        _provider = OidcProvider()
    return _provider


def reset_oidc_provider() -> None:
    """Reset the singleton (test isolation)."""
    global _provider
    _provider = None


__all__ = [
    "ALLOWED_ALGORITHMS",
    "OidcProvider",
    "generate_nonce",
    "generate_pkce_pair",
    "generate_state",
    "get_oidc_provider",
    "reset_oidc_provider",
]
