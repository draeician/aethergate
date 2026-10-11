"""One reusable CLI HTTP client over the admin API.

The client owns base-URL resolution, Authorization injection, structured error
parsing, timeouts, and a safe single retry for idempotent GETs only (never for
state-changing methods). No sensitive header is ever logged.
"""

from __future__ import annotations

import json
from typing import Any

import httpx

from aethergate import __version__
from aethergate.cli.errors import (
    AuthError,
    ConflictError,
    ForbiddenError,
    NetworkError,
    NotFoundError,
    ServerError,
    UsageError,
)

_USER_AGENT = f"aethergate-cli/{__version__}"
_TIMEOUT_SECONDS = 30.0

# Gateway error codes that indicate an expired/revoked human session rather than
# a service-credential problem; callers may clear a stored human token on these.
_AUTH_CLEAR_CODES = {"not_authenticated"}

# Device-flow terminal error codes surface as authentication failures, not usage
# errors, so `auth login` can treat them as a failed login with a stable exit.
_DEVICE_TERMINAL_CODES = {"device_expired", "device_access_denied", "device_code_invalid"}


class Client:
    def __init__(
        self,
        *,
        base_url: str,
        token: str | None,
        verify: bool | str = True,
        timeout: float = _TIMEOUT_SECONDS,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._token = token
        self._client = httpx.Client(
            base_url=self._base_url,
            timeout=timeout,
            verify=verify,
            headers={"User-Agent": _USER_AGENT},
            transport=transport,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> Client:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _headers(self) -> dict[str, str]:
        headers: dict[str, str] = {"Accept": "application/json"}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        return headers

    def _parse_error(self, response: httpx.Response) -> str:
        try:
            body = response.json()
        except (ValueError, json.JSONDecodeError):
            return response.text.strip() or "request failed"
        error = body.get("error", {})
        if isinstance(error, dict):
            message = error.get("message")
            if isinstance(message, str) and message:
                return message
        return "request failed"

    def _error_code(self, response: httpx.Response) -> str:
        try:
            body = response.json()
        except (ValueError, json.JSONDecodeError):
            return ""
        error = body.get("error", {})
        if isinstance(error, dict) and isinstance(error.get("code"), str):
            return error["code"]
        return ""

    def _raise_for_status(self, response: httpx.Response) -> None:
        status = response.status_code
        if 200 <= status < 300:
            return
        message = self._parse_error(response)
        code = self._error_code(response)
        if status == 401 or (status == 400 and code in _DEVICE_TERMINAL_CODES):
            raise AuthError(message or "Authentication is required or has expired.")
        if status == 403:
            raise ForbiddenError(message or "Not authorized for this action.")
        if status == 404:
            raise NotFoundError(message or "Resource not found.")
        if status == 409:
            raise ConflictError(message or "Resource conflict.")
        if 400 <= status < 500:
            raise UsageError(message or "Invalid request.")
        raise ServerError(message or "The gateway returned a server error.")

    def _request(
        self, method: str, path: str, *, json_body: Any = None, params: dict | None = None
    ) -> dict:
        kwargs: dict[str, Any] = {"headers": self._headers(), "params": params}
        if json_body is not None:
            kwargs["json"] = json_body
        try:
            response = self._client.request(method, path, **kwargs)
        except httpx.HTTPError as exc:
            raise NetworkError(f"could not reach the gateway: {exc}") from exc
        self._raise_for_status(response)
        try:
            return response.json()
        except (ValueError, json.JSONDecodeError):
            return {}

    def get(self, path: str, params: dict | None = None) -> dict:
        try:
            return self._request("GET", path, params=params)
        except NetworkError:
            # One safe retry for idempotent GETs only.
            return self._request("GET", path, params=params)

    def post(self, path: str, json_body: Any = None) -> dict:
        return self._request("POST", path, json_body=json_body)

    def patch(self, path: str, json_body: Any = None) -> dict:
        return self._request("PATCH", path, json_body=json_body)


__all__ = ["Client", "_AUTH_CLEAR_CODES"]
