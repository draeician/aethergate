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
    upstream URLs, or secret material. ``retry_after_seconds`` carries safe
    structured rate-limit feedback when the provider reliably supplies it.
    """

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        retry_after_seconds: float | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.retry_after_seconds = retry_after_seconds


class AuthenticationRequired(DomainError):
    """Inference authentication is not configured/allowed for this request."""


class AdminAuthenticationRequired(DomainError):
    """Admin authentication is not configured/allowed for this request.

    Distinct from :class:`AuthenticationRequired` so the control plane can map it
    to its own structured error envelope without changing the inference surface.
    """


class AdminAuthorizationError(DomainError):
    """An authenticated admin is not authorized for the requested action.

    Raised by the centralized authorization service. The message is deliberately
    fixed and indistinguishable: it never reveals whether a target resource
    exists or which specific role/permission was missing.
    """


class AdminResourceNotFound(DomainError):
    """A target resource is not visible to the caller.

    Raised by the authorization-aware resource resolver when a resource does not
    exist **or** exists outside the caller's authorized project scope. The two
    cases are deliberately indistinguishable so a project-scoped caller cannot
    enumerate resources in other projects by opaque ID.
    """


class AdminValidationError(DomainError):
    """An admin mutation is semantically invalid (not an authorization failure).

    Used for role/scope coherence and other request-shape violations that are
    not about the caller's authority, so they surface as a clear 4xx rather than
    a misleading authorization failure.
    """


class BootstrapTokenRejected(DomainError):
    """The bootstrap secret was missing or did not match the configured value.

    Raised before any database work so a wrong/missing token cannot be used to
    probe bootstrap state.
    """


class BootstrapAlreadyCompleted(DomainError):
    """Bootstrap has already been completed and cannot be run again."""


class OidcConfigurationError(DomainError):
    """The OIDC provider is missing, unreachable, or misconfigured.

    Raised when discovery/JWKS cannot be fetched or validated. The message never
    includes secrets (client secret, tokens, verifier material).
    """


class OidcAuthenticationFailed(DomainError):
    """An OIDC login/callback failed validation (fixed, indistinguishable).

    Covers discovery issuer mismatch, ID-token issuer/audience/expiry/nonce
    validation, unsafe algorithms, and token exchange failures. The message is
    deliberately fixed so a client cannot probe which validation failed.
    """


class OidcLoginStateInvalid(DomainError):
    """An OIDC login transaction is missing, expired, consumed, or mismatched.

    Covers state/nonce/PKCE verifier mismatches and one-time consumption
    violations. The message is fixed and never echoes the supplied material.
    """


class SessionInvalid(DomainError):
    """A browser session cookie is missing, invalid, expired, or revoked.

    Raised by the admin context resolver; mapped to a fixed 401. The message is
    deliberately indistinguishable from a nonexistent session.
    """


class CsrfValidationError(DomainError):
    """A CSRF token is missing or invalid for a cookie-authenticated mutation.

    Raised only for browser-session authentication on mutating methods. Mapped to
    a fixed 403; the message never reveals the expected token.
    """


class CredentialLifecycleError(DomainError):
    """A credential create/rotate request violates a lifecycle invariant.

    Raised by the identity service before any raw key is generated/persisted for
    an invalid transition: missing/inactive/mismatched project or principal,
    rotating a revoked/expired/inactive credential, or an incompatible
    audience/scope combination. It is a clear domain validation error, distinct
    from ``AuthenticationRequired`` (which is about a presented key).
    """


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


class AccountingInvariantError(Exception):
    """An accounting invariant was violated during settlement (internal).

    Raised when an idempotent settlement replay supplies data that conflicts with
    the already-persisted canonical record. It is an internal correctness signal,
    never surfaced to clients. A conflicting replay must not mutate state.
    """


class PricePolicyConflictError(DomainError):
    """A second enabled price policy would violate the one-per-route invariant.

    Surfaces a clear domain/admin validation error instead of an unexpected
    scheduler ``MultipleResultsFound``. The database partial unique index is the
    authoritative backstop; this error is raised by the service/repository layer
    when the conflict can be detected before commit.
    """

    def __init__(self, route_binding_id: str) -> None:
        super().__init__(
            f"route binding {route_binding_id!r} already has an enabled price policy"
        )
        self.route_binding_id = route_binding_id
