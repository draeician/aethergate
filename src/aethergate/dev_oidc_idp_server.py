"""Deterministic local OIDC IdP server entrypoint (development / verification only).

Runs the in-repo :class:`~aethergate.dev_oidc_idp.DevOidcIdp` as a standalone
uvicorn process so the web-console / device-flow live verification can use a
real, offline OpenID Connect provider without any external Internet dependency.

Configuration (environment):

- ``ISSUER`` — the public issuer URL (must be reachable by both the gateway and
  the browser, e.g. ``http://<host-lan-ip>:8090``).
- ``CLIENT_ID`` — the web-console confidential client id (default
  ``aethergate-dev-client``).
- ``SUBJECT`` / ``EMAIL`` / ``DISPLAY_NAME`` — the deterministic subject and
  optional claims (default subject ``dev-user``).
- ``DEVICE_CLIENT_ID`` — optional device-flow client id (defaults to the web
  client id).
- ``PORT`` — listen port (default ``8090``).

This is NOT production identity infrastructure: it auto-approves every
authorization request and keeps one-time codes in memory.
"""

from __future__ import annotations

import os

import uvicorn

from aethergate.dev_oidc_idp import DevOidcIdp


def main() -> None:
    issuer = os.environ.get("ISSUER", "http://127.0.0.1:8090").rstrip("/")
    client_id = os.environ.get("CLIENT_ID", "aethergate-dev-client")
    subject = os.environ.get("SUBJECT", "dev-user")
    email = os.environ.get("EMAIL") or None
    display_name = os.environ.get("DISPLAY_NAME") or None
    device_client_id = os.environ.get("DEVICE_CLIENT_ID") or None
    port = int(os.environ.get("PORT", "8090"))

    idp = DevOidcIdp(
        issuer=issuer,
        client_id=client_id,
        subject=subject,
        email=email,
        display_name=display_name,
        device_client_id=device_client_id,
    )
    uvicorn.run(idp.app, host="0.0.0.0", port=port)


if __name__ == "__main__":
    main()
