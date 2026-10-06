"""CLI exit-code contract and error types.

Every command terminates with one of these stable, documented exit codes. The
mapping is tested and described in ``docs/cli.md``. No Python traceback is
printed by default; a human-readable message goes to stderr.
"""

from __future__ import annotations

EXIT_OK = 0
EXIT_GENERIC = 1
EXIT_USAGE = 2
EXIT_AUTH = 3
EXIT_FORBIDDEN = 4
EXIT_NOT_FOUND = 5
EXIT_CONFLICT = 6
EXIT_NETWORK = 7
EXIT_SERVER = 8


class CliError(Exception):
    """A CLI-visible error with a stable exit code and safe message."""

    def __init__(self, message: str, exit_code: int = EXIT_GENERIC) -> None:
        super().__init__(message)
        self.message = message
        self.exit_code = exit_code


class UsageError(CliError):
    def __init__(self, message: str) -> None:
        super().__init__(message, EXIT_USAGE)


class AuthError(CliError):
    def __init__(self, message: str = "Authentication is required or has expired.") -> None:
        super().__init__(message, EXIT_AUTH)


class ForbiddenError(CliError):
    def __init__(self, message: str = "Not authorized for this action.") -> None:
        super().__init__(message, EXIT_FORBIDDEN)


class NotFoundError(CliError):
    def __init__(self, message: str = "Resource not found.") -> None:
        super().__init__(message, EXIT_NOT_FOUND)


class ConflictError(CliError):
    def __init__(self, message: str) -> None:
        super().__init__(message, EXIT_CONFLICT)


class NetworkError(CliError):
    def __init__(self, message: str) -> None:
        super().__init__(message, EXIT_NETWORK)


class ServerError(CliError):
    def __init__(self, message: str = "The gateway returned a server error.") -> None:
        super().__init__(message, EXIT_SERVER)


class TokenStoreUnavailable(CliError):
    """No protected OS credential store is available to persist the token."""

    def __init__(self, detail: str) -> None:
        super().__init__(
            "No protected credential store is available to save the token "
            f"({detail}). Use --no-store for a one-off login, or configure a "
            "system keyring (Secret Service / KWallet).",
            EXIT_GENERIC,
        )


__all__ = [
    "EXIT_OK",
    "EXIT_GENERIC",
    "EXIT_USAGE",
    "EXIT_AUTH",
    "EXIT_FORBIDDEN",
    "EXIT_NOT_FOUND",
    "EXIT_CONFLICT",
    "EXIT_NETWORK",
    "EXIT_SERVER",
    "CliError",
    "UsageError",
    "AuthError",
    "ForbiddenError",
    "NotFoundError",
    "ConflictError",
    "NetworkError",
    "ServerError",
    "TokenStoreUnavailable",
]
