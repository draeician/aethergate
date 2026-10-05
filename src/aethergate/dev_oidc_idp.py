"""Deterministic local OIDC test provider (development / verification only).

This is a small, self-contained OpenID Connect provider used to test and
live-verify the human login flow without any external Internet dependency. It
implements:

- OIDC discovery (``/.well-known/openid-configuration``);
- a JWKS endpoint (``/jwks``) exposing the RS256 public key;
- an authorization endpoint (``/authorize``) that implicitly approves and issues
  a one-time authorization code bound to the PKCE challenge and nonce;
- a token endpoint (``/token``) that validates S256 PKCE and returns a signed ID
  token.

It is **not** production identity infrastructure: it generates its own RSA key in
memory, auto-approves every authorization request, and keeps one-time codes in
memory. It exists solely to make the real flow deterministic and offline.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
import time
from typing import Any

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse


def _b64url_int(value: int) -> str:
    length = (value.bit_length() + 7) // 8
    return base64.urlsafe_b64encode(value.to_bytes(length, "big")).rstrip(b"=").decode()


def _sha256_b64url(value: str) -> str:
    digest = hashlib.sha256(value.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


class DevOidcIdp:
    """A local, deterministic OIDC provider served as an ASGI app."""

    def __init__(
        self,
        *,
        issuer: str,
        client_id: str,
        client_secret: str | None = None,
        subject: str = "dev-user",
        email: str | None = None,
        display_name: str | None = None,
    ) -> None:
        self.issuer = issuer.rstrip("/")
        self.client_id = client_id
        self.client_secret = client_secret
        self.subject = subject
        self.email = email
        self.display_name = display_name
        self.kid = "dev-idp-rsa"
        self._private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self._codes: dict[str, dict[str, Any]] = {}
        self.app = FastAPI(title="dev-oidc-idp")
        self._register()

    def _register(self) -> None:
        self.app.get("/.well-known/openid-configuration")(self._discovery)
        self.app.get("/jwks")(self._jwks)
        self.app.get("/authorize")(self._authorize)
        self.app.post("/token")(self._token)

    async def _discovery(self) -> JSONResponse:
        return JSONResponse(
            {
                "issuer": self.issuer,
                "authorization_endpoint": f"{self.issuer}/authorize",
                "token_endpoint": f"{self.issuer}/token",
                "jwks_uri": f"{self.issuer}/jwks",
                "response_types_supported": ["code"],
                "subject_types_supported": ["public"],
                "id_token_signing_alg_values_supported": ["RS256"],
                "code_challenge_methods_supported": ["S256"],
            }
        )

    async def _jwks(self) -> JSONResponse:
        numbers = self._private_key.public_key().public_numbers()
        return JSONResponse(
            {
                "keys": [
                    {
                        "kty": "RSA",
                        "kid": self.kid,
                        "use": "sig",
                        "alg": "RS256",
                        "n": _b64url_int(numbers.n),
                        "e": _b64url_int(numbers.e),
                    }
                ]
            }
        )

    async def _authorize(self, request: Request) -> RedirectResponse:
        params = request.query_params
        state = params.get("state", "")
        nonce = params.get("nonce", "")
        redirect_uri = params.get("redirect_uri", "")
        code_challenge = params.get("code_challenge", "")
        if params.get("client_id") != self.client_id:
            return RedirectResponse(
                f"{redirect_uri}?error=invalid_request&state={state}", status_code=302
            )
        if not redirect_uri or not state:
            return JSONResponse(status_code=400, content={"error": "invalid_request"})

        code = secrets.token_urlsafe(32)
        self._codes[code] = {
            "code_challenge": code_challenge,
            "nonce": nonce,
            "redirect_uri": redirect_uri,
            "consumed": False,
        }
        separator = "&" if "?" in redirect_uri else "?"
        return RedirectResponse(
            f"{redirect_uri}{separator}code={code}&state={state}", status_code=302
        )

    async def _token(self, request: Request) -> JSONResponse:
        from urllib.parse import parse_qs

        body = await request.body()
        form = {k: v[0] for k, v in parse_qs(body.decode("utf-8")).items()}
        code = form.get("code")
        code_verifier = form.get("code_verifier") or ""
        record = self._codes.get(code) if isinstance(code, str) else None
        if record is None or record["consumed"]:
            return JSONResponse(status_code=400, content={"error": "invalid_grant"})
        if _sha256_b64url(code_verifier) != record["code_challenge"]:
            return JSONResponse(status_code=400, content={"error": "invalid_grant"})
        if form.get("client_id") != self.client_id:
            return JSONResponse(status_code=400, content={"error": "invalid_client"})
        if self.client_secret is not None and form.get("client_secret") != self.client_secret:
            return JSONResponse(status_code=400, content={"error": "invalid_client"})

        record["consumed"] = True
        now = int(time.time())
        claims: dict[str, Any] = {
            "iss": self.issuer,
            "sub": self.subject,
            "aud": self.client_id,
            "iat": now,
            "exp": now + 300,
            "nonce": record["nonce"],
        }
        if self.email is not None:
            claims["email"] = self.email
        if self.display_name is not None:
            claims["name"] = self.display_name

        id_token = jwt.encode(
            claims, self._private_key, algorithm="RS256", headers={"kid": self.kid}
        )
        return JSONResponse(
            {
                "access_token": secrets.token_urlsafe(32),
                "token_type": "Bearer",
                "expires_in": 300,
                "id_token": id_token,
            }
        )

    def mint_id_token(
        self, claims: dict[str, Any], *, headers: dict[str, Any] | None = None
    ) -> str:
        """Sign an arbitrary ID token (test/verification helper).

        Lets tests exercise issuer/audience/expiry/subject/nonce validation
        directly against the provider's real RS256 key.
        """
        return jwt.encode(
            claims,
            self._private_key,
            algorithm="RS256",
            headers={"kid": self.kid, **(headers or {})},
        )


__all__ = ["DevOidcIdp"]
