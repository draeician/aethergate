"""Domain-level error types shared across v2 modules."""

from __future__ import annotations


class DomainError(Exception):
    """Base class for expected, domain-meaningful failures."""


class ModelAliasNotFound(DomainError):
    """The requested public model alias does not exist."""

    def __init__(self, alias: str) -> None:
        super().__init__(f"model alias {alias!r} not found")
        self.alias = alias


class ResourceInactive(DomainError):
    """A resource exists but is disabled, or is missing a required dependency."""

    def __init__(self, resource: str, identifier: str) -> None:
        super().__init__(f"{resource} {identifier!r} is inactive or unavailable")
        self.resource = resource
        self.identifier = identifier


class AmbiguousRoute(DomainError):
    """Multiple active routes resolve a model alias and no policy selects one."""

    def __init__(self, alias: str, route_ids: list[str]) -> None:
        super().__init__(
            f"model alias {alias!r} resolves to {len(route_ids)} active routes "
            f"({', '.join(route_ids)}); no selection policy is defined"
        )
        self.alias = alias
        self.route_ids = route_ids


class SecretResolutionError(DomainError):
    """Secret material could not be resolved inside the trusted runtime."""


class RouteUnresolved(DomainError):
    """An active route has no provider-facing model configured yet."""

    def __init__(self, route_id: str) -> None:
        super().__init__(
            f"route {route_id!r} has no upstream model configured"
        )
        self.route_id = route_id


class UnsupportedProvider(DomainError):
    """No adapter is registered for the requested provider kind."""

    def __init__(self, provider_kind: str) -> None:
        super().__init__(f"no adapter for provider kind {provider_kind!r}")
        self.provider_kind = provider_kind


class DestinationDenied(DomainError):
    """An upstream destination violates the egress/destination policy."""

    def __init__(self, reason: str) -> None:
        super().__init__(f"upstream destination denied: {reason}")
        self.reason = reason


class ProviderError(DomainError):
    """An upstream provider failure, safe for client-visible mapping.

    ``message`` is a sanitized description; it must never contain credentials,
    upstream URLs, or secret material.
    """

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


class AuthenticationRequired(DomainError):
    """Inference authentication is not configured/allowed for this request."""


class QueueKeyError(DomainError):
    """The scheduler queue encryption key is absent or invalid."""


class QueueFull(DomainError):
    """The scheduler queue has reached its configured capacity."""


class QueueTimeout(DomainError):
    """A queued request expired before it could be dispatched or completed."""


class SchedulerInvariantError(Exception):
    """A coupled scheduler state transition failed atomically (internal invariant).

    Raised when a request + execution-attempt transition that must move together
    cannot move together (missing/terminal row, stale fence, or ownership
    mismatch). It is an internal correctness signal, never surfaced to clients.
    """
