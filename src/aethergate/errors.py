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
