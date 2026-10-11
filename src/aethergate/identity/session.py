"""Human browser-session identity: login transactions, sessions, CSRF, linking.

This module owns the server-side state for human admin authentication. It works
closely with :mod:`aethergate.identity.oidc` (provider HTTP/crypto) but contains
no provider HTTP itself — the API router orchestrates the two.

Security invariants:

- Only one-way SHA-256 verifiers for the raw session cookie and raw CSRF token
  are persisted; the raw values are returned only in ``Set-Cookie`` / the session
  response and are never logged or written to audit.
- A login transaction binds ``state``/``nonce``/PKCE to the initiating browser
  via a dedicated short-lived transaction cookie (only its one-way
  ``txn_cookie_hash`` is persisted), expires quickly, and is consumed exactly
  once.
- External identity linking is by ``(issuer, subject)``; email is never the
  identity key. JIT provisioning (when enabled) creates an unprivileged USER
  principal with zero role assignments.
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from aethergate.config import get_settings
from aethergate.domain import entities as domain
from aethergate.domain.enums import PrincipalKind
from aethergate.domain.ids import (
    AuditEventId,
    BrowserSessionId,
    ExternalIdentityId,
    OidcLoginStateId,
    PrincipalId,
    ProjectId,
)
from aethergate.errors import (
    AdminValidationError,
    CsrfValidationError,
    OidcAuthenticationFailed,
    OidcLoginStateInvalid,
    SessionInvalid,
)
from aethergate.identity import oidc as oidc_module
from aethergate.persistence import repository

SESSION_COOKIE_NAME = "ag_session"
CSRF_HEADER_NAME = "X-CSRF-Token"
LOGIN_TXN_COOKIE_NAME = "ag_oidc_txn"
# JS-readable CSRF cookie for the web console. It carries the raw per-session CSRF
# token so browser JavaScript can echo it back in ``X-CSRF-Token``; it is NOT an
# authentication credential and is validated against the session's one-way
# verifier exactly like the header token. Never placed in local/sessionStorage.
CSRF_COOKIE_NAME = "ag_csrf"

# Throttle for sliding the idle window / last_seen write. Avoids a hot-row write
# on every browser request while still enforcing idle expiry on each request.
_IDLE_SLIDE_THROTTLE_SECONDS = 60.0


def utcnow() -> datetime:
    return datetime.now(UTC)


def _new_id() -> str:
    return uuid.uuid4().hex


def hash_verifier(value: str) -> str:
    """Return the SHA-256 hex digest of a high-entropy value (the persisted verifier)."""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def generate_session_token() -> str:
    """Return a >=256-bit random raw session cookie value."""
    return secrets.token_urlsafe(32)


def generate_csrf_token() -> str:
    """Return a >=256-bit random raw CSRF token value."""
    return secrets.token_urlsafe(32)


# ---------------------------------------------------------------------------
# Login transactions
# ---------------------------------------------------------------------------


async def create_login_transaction(
    session: AsyncSession,
) -> tuple[domain.OidcLoginState, str]:
    """Create a one-time login transaction (state/nonce/PKCE) and persist it.

    Returns ``(entity, raw_txn_cookie)``. The raw transaction cookie is a
    cryptographically random, short-lived value that binds the transaction to the
    initiating browser; only its one-way ``txn_cookie_hash`` is persisted. The
    raw ``state``/``nonce``/``code_verifier`` are returned only on the entity
    (to the API router), never logged.
    """
    settings = get_settings()
    state = oidc_module.generate_state()
    nonce = oidc_module.generate_nonce()
    code_verifier, code_challenge = oidc_module.generate_pkce_pair()
    raw_txn_cookie = secrets.token_urlsafe(32)
    now = utcnow()
    entity = domain.OidcLoginState(
        id=OidcLoginStateId(_new_id()),
        state=state,
        nonce=nonce,
        code_verifier=code_verifier,
        code_challenge=code_challenge,
        txn_cookie_hash=hash_verifier(raw_txn_cookie),
        created_at=now,
        expires_at=now + timedelta(seconds=settings.oidc_login_ttl_seconds),
        consumed_at=None,
    )
    await repository.delete_expired_oidc_login_states(session, now)
    await repository.create_oidc_login_state(session, entity)
    return entity, raw_txn_cookie


async def consume_login_transaction(
    session: AsyncSession, state: str, txn_cookie: str | None, now: datetime
) -> domain.OidcLoginState:
    """Atomically consume a login transaction by ``state`` and browser binding.

    The transaction is consumed only when the initiating browser's transaction
    cookie matches the stored one-way verifier (constant-time). Raises
    :class:`OidcLoginStateInvalid` for a missing, expired, already-consumed, or
    mis-bound transaction. The row is locked ``FOR UPDATE`` then deleted, so a
    concurrent replay serializes and the second one observes the missing row.
    """
    entity = await repository.get_oidc_login_state_for_update(session, state)
    if entity is None:
        raise OidcLoginStateInvalid()
    if entity.expires_at <= now:
        raise OidcLoginStateInvalid()
    if txn_cookie is None or not txn_cookie:
        raise OidcLoginStateInvalid()
    if not secrets.compare_digest(entity.txn_cookie_hash, hash_verifier(txn_cookie)):
        raise OidcLoginStateInvalid()
    await repository.delete_oidc_login_state(session, entity.id)
    return entity


# ---------------------------------------------------------------------------
# External identity resolution / provisioning
# ---------------------------------------------------------------------------


async def resolve_or_provision_principal(
    session: AsyncSession,
    *,
    issuer: str,
    subject: str,
    email: str | None,
    display_name: str | None,
    now: datetime,
) -> domain.Principal:
    """Resolve an OIDC identity to an active Principal, or provision one via JIT.

    Raises :class:`OidcAuthenticationFailed` (indistinguishable) when the
    identity is unknown with JIT disabled, when the linked principal or its
    project is inactive, or when the external link is disabled.
    """
    settings = get_settings()
    identity = await repository.get_external_identity(session, issuer, subject)
    if identity is not None:
        if not identity.is_active:
            raise OidcAuthenticationFailed()
        principal = await repository.get_principal(session, identity.principal_id)
        if principal is None or not principal.is_active:
            raise OidcAuthenticationFailed()
        project = await repository.get_project(session, principal.project_id)
        if project is None or not project.is_active:
            raise OidcAuthenticationFailed()
        await repository.touch_external_identity_login(session, identity.id, now)
        return principal

    if not settings.oidc_jit_provisioning:
        raise OidcAuthenticationFailed()

    project = await repository.get_project_by_name(
        session, settings.oidc_jit_project_name
    )
    if project is None or not project.is_active:
        raise OidcAuthenticationFailed()

    principal = domain.Principal(
        id=PrincipalId(_new_id()),
        project_id=project.id,
        kind=PrincipalKind.USER,
        name=subject,
        is_active=True,
    )
    identity_entity = domain.ExternalIdentity(
        id=ExternalIdentityId(_new_id()),
        principal_id=principal.id,
        issuer=issuer,
        subject=subject,
        email=email,
        display_name=display_name,
        last_login_at=now,
        is_active=True,
    )
    try:
        async with session.begin_nested():
            await repository.create_principal(session, principal)
            await repository.create_external_identity(session, identity_entity)
    except IntegrityError as exc:
        # A concurrent first-login created the same (issuer, subject); adopt the
        # canonical winner (and discard our unprivileged, unlinked principal).
        identity = await repository.get_external_identity(session, issuer, subject)
        if identity is None:
            raise OidcAuthenticationFailed() from exc
        existing = await repository.get_principal(session, identity.principal_id)
        if existing is None or not existing.is_active:
            raise OidcAuthenticationFailed() from exc
        return existing
    return principal


# ---------------------------------------------------------------------------
# Browser sessions
# ---------------------------------------------------------------------------


async def create_browser_session(
    session: AsyncSession,
    principal_id: PrincipalId,
    now: datetime,
) -> tuple[domain.BrowserSession, str, str]:
    """Create a fresh server-managed browser session.

    Returns ``(entity, raw_cookie, raw_csrf)``. Only the SHA-256 verifiers are
    persisted; the raw cookie value is returned solely for the ``Set-Cookie``
    header and the raw CSRF token solely for the session response.
    """
    settings = get_settings()
    raw_cookie = generate_session_token()
    raw_csrf = generate_csrf_token()
    idle_seconds = settings.oidc_session_idle_seconds
    absolute_seconds = settings.oidc_session_absolute_seconds
    entity = domain.BrowserSession(
        id=BrowserSessionId(_new_id()),
        principal_id=principal_id,
        session_hash=hash_verifier(raw_cookie),
        csrf_token_hash=hash_verifier(raw_csrf),
        created_at=now,
        last_seen_at=now,
        idle_expires_at=now + timedelta(seconds=idle_seconds),
        absolute_expires_at=now + timedelta(seconds=absolute_seconds),
        revoked_at=None,
        is_active=True,
    )
    created = await repository.create_browser_session(session, entity)
    return created, raw_cookie, raw_csrf


async def resolve_browser_session(
    session: AsyncSession, raw_cookie: str, now: datetime
) -> tuple[domain.BrowserSession, domain.Principal]:
    """Resolve a raw session cookie to an active session and its active principal.

    Enforces revocation, absolute expiry, and idle expiry. Raises
    :class:`SessionInvalid` (indistinguishable) on any failure. The idle window
    is slid on a throttled basis so a hot-row write is not performed per request.
    """
    entity = await repository.get_browser_session_by_hash(
        session, hash_verifier(raw_cookie)
    )
    if entity is None:
        raise SessionInvalid()
    if not entity.is_active or entity.revoked_at is not None:
        raise SessionInvalid()
    if entity.absolute_expires_at <= now:
        raise SessionInvalid()
    if entity.idle_expires_at <= now:
        raise SessionInvalid()

    principal = await repository.get_principal(session, entity.principal_id)
    if principal is None or not principal.is_active:
        raise SessionInvalid()
    project = await repository.get_project(session, principal.project_id)
    if project is None or not project.is_active:
        raise SessionInvalid()

    if (
        entity.last_seen_at is None
        or (now - entity.last_seen_at).total_seconds() >= _IDLE_SLIDE_THROTTLE_SECONDS
    ):
        idle_seconds = get_settings().oidc_session_idle_seconds
        new_idle = now + timedelta(seconds=idle_seconds)
        await repository.touch_browser_session(
            session, entity.id, now, new_idle
        )
    return entity, principal


def validate_csrf(session_entity: domain.BrowserSession, raw_csrf: str | None) -> None:
    """Validate a CSRF token against a browser session's stored verifier.

    Raises :class:`CsrfValidationError` for a missing or mismatched token. The
    comparison is constant-time and never reveals the expected token.
    """
    if raw_csrf is None or not raw_csrf:
        raise CsrfValidationError()
    if not secrets.compare_digest(
        session_entity.csrf_token_hash, hash_verifier(raw_csrf)
    ):
        raise CsrfValidationError()


async def revoke_browser_session(
    session: AsyncSession, session_id: BrowserSessionId, now: datetime
) -> domain.BrowserSession | None:
    """Durably revoke a browser session; idempotent."""
    return await repository.revoke_browser_session(session, session_id, now)


async def record_audit(
    session: AsyncSession,
    *,
    actor_principal_id: PrincipalId | None,
    action: str,
    resource_type: str,
    resource_id: str,
    project_id: ProjectId | None = None,
    metadata: dict | None = None,
) -> None:
    """Write an immutable audit event with safe metadata only.

    Never accepts or stores authorization codes, ID/access/refresh tokens, raw
    state/nonce, PKCE verifiers, session cookies, CSRF tokens, API keys, or
    client secrets.
    """
    await repository.create_audit_event(
        session,
        domain.AuditEvent(
            id=AuditEventId(_new_id()),
            actor_principal_id=actor_principal_id,
            project_id=project_id,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            occurred_at=utcnow(),
            metadata=metadata or {},
        ),
    )


# ---------------------------------------------------------------------------
# External identity linking (admin action)
# ---------------------------------------------------------------------------


async def link_external_identity(
    session: AsyncSession,
    *,
    principal_id: PrincipalId,
    issuer: str,
    subject: str,
    now: datetime,
) -> domain.ExternalIdentity:
    """Durably link an OIDC ``(issuer, subject)`` identity to an existing principal.

    The principal must exist, be active, and be a USER principal. Raises
    :class:`AdminValidationError` for an invalid target; :class:`IntegrityError`
    (unique issuer+subject) is surfaced to the caller as a validation error.
    """
    principal = await repository.get_principal(session, principal_id)
    if principal is None or not principal.is_active:
        raise AdminValidationError(f"principal {principal_id!s} is not active")
    if principal.kind is not PrincipalKind.USER:
        raise AdminValidationError("only a USER principal can be linked to an OIDC identity")
    if not issuer or not subject:
        raise AdminValidationError("issuer and subject are required")
    entity = domain.ExternalIdentity(
        id=ExternalIdentityId(_new_id()),
        principal_id=principal_id,
        issuer=issuer,
        subject=subject,
        email=None,
        display_name=None,
        last_login_at=None,
        is_active=True,
    )
    try:
        return await repository.create_external_identity(session, entity)
    except IntegrityError as exc:
        raise AdminValidationError(
            "an external identity with that issuer and subject already exists"
        ) from exc


__all__ = [
    "CSRF_COOKIE_NAME",
    "CSRF_HEADER_NAME",
    "LOGIN_TXN_COOKIE_NAME",
    "SESSION_COOKIE_NAME",
    "consume_login_transaction",
    "create_browser_session",
    "create_login_transaction",
    "generate_csrf_token",
    "generate_session_token",
    "hash_verifier",
    "link_external_identity",
    "record_audit",
    "resolve_browser_session",
    "resolve_or_provision_principal",
    "revoke_browser_session",
    "utcnow",
    "validate_csrf",
]
