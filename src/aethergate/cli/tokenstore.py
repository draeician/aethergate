"""Protected token storage for the CLI.

Human CLI session tokens and service-account credentials are persisted through a
protected OS credential store (``keyring``) — never in plaintext profile TOML. A
``TokenStore`` abstracts this so tests use an in-memory fake. If a usable
protected store is unavailable, we fail safely rather than silently falling back
to plaintext disk.
"""

from __future__ import annotations

from typing import Protocol

from aethergate.cli.errors import TokenStoreUnavailable

SERVICE_NAME = "aethergate-cli"


class TokenStore(Protocol):
    def get(self, profile_name: str) -> str | None: ...

    def set(self, profile_name: str, token: str) -> None: ...

    def delete(self, profile_name: str) -> None: ...


class KeyringTokenStore:
    """Persists tokens via the OS keyring (Secret Service / KWallet / macOS)."""

    def __init__(self, service_name: str = SERVICE_NAME) -> None:
        self._service = service_name

    def get(self, profile_name: str) -> str | None:
        import keyring

        return keyring.get_password(self._service, profile_name)

    def set(self, profile_name: str, token: str) -> None:
        import keyring

        keyring.set_password(self._service, profile_name, token)
        # Fail safe: verify the secret actually round-tripped. A keyring without a
        # usable backend may silently ignore writes; do not fall back to plaintext.
        if keyring.get_password(self._service, profile_name) != token:
            raise TokenStoreUnavailable("the credential store did not persist the token")

    def delete(self, profile_name: str) -> None:
        import keyring

        try:
            keyring.delete_password(self._service, profile_name)
        except Exception:
            # Best-effort removal; absence is the postcondition.
            return


class MemoryTokenStore:
    """In-memory fake for tests and ``--no-store``-style ephemeral use."""

    def __init__(self) -> None:
        self._tokens: dict[str, str] = {}

    def get(self, profile_name: str) -> str | None:
        return self._tokens.get(profile_name)

    def set(self, profile_name: str, token: str) -> None:
        self._tokens[profile_name] = token

    def delete(self, profile_name: str) -> None:
        self._tokens.pop(profile_name, None)


def default_token_store() -> TokenStore:
    return KeyringTokenStore()


__all__ = [
    "SERVICE_NAME",
    "KeyringTokenStore",
    "MemoryTokenStore",
    "TokenStore",
    "default_token_store",
]
