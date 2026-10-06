"""Human CLI identity: OAuth device flow + durable CLI sessions.

This module owns the server-side state for human CLI authentication. It works
closely with :mod:`aethergate.identity.oidc` (provider HTTP/crypto) but contains
no provider HTTP itself — the API router orchestrates the two, exactly like the
browser-session module.

Security invariants:

- Only one-way SHA-256 verifiers are persisted for the raw device ``device_code``
  and the raw ``ags_...`` CLI session token; the raw values are returned exactly
  once (device ``start`` / device-flow success) and are never stored or logged.
- A device transaction is short-lived, consumed exactly once, and enforces the
  provider-recommended polling interval; terminal/denied states consume the code
  so it cannot be replayed.
- A CLI session shares the Principal/RoleAssignment RBAC engine with browser
  sessions; it carries no API credential and never authenticates the inference
  surface.
"""

from __future__ import annotations

import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from aethergate.config import get_settings
from aethergate.domain import entities as domain
from aethergate.domain.enums import Role
from aethergate.domain.ids import CliSessionId, DeviceAuthorizationId, PrincipalId
from aethergate.errors import (
    CliSessionInvalid,
    DeviceAccessDenied,
    DeviceCodeInvalid,
    DeviceExpired,
    DeviceFlowUnavailable,
    OidcAuthenticationFailed,
)
from aethergate.identity import oidc as oidc_module
from aethergate.identity import session as session_service
from aethergate.persistence import repository

CLI_SESSION_PREFIX = "ags_"

# RFC 8628: on ``slow_down`` the client increases its polling interval by 5s.
_SLOW_DOWN_BUMP_SECONDS = 5
_SLOW_DOWN_MAX_INTERVAL_SECONDS = 60

AUDIT_CLI_DEVICE_STARTED = "cli.device_authorization_started"
AUDIT_CLI_LOGIN_SUCCESS = "cli.login_success"
AUDIT_CLI_LOGIN_FAILED = "cli.login_failed"
AUDIT_CLI_LOGOUT = "cli.logout"


def utcnow() -> datetime:
    return datetime.now(UTC)


def _new_id() -> str:
    return uuid.uuid4().hex


def generate_cli_session_token() -> str:
    """Return a raw ``ags_<display>_<secret>`` token (>=256-bit random secret)."""
    return f"{CLI_SESSION_PREFIX}{secrets.token_hex(4)}_{secrets.token_urlsafe(32)}"


@dataclass(frozen=True)
class DevicePollResult:
    """Outcome of one device-flow poll.

    ``status`` is one of ``pending``, ``slow_down``, ``expired``, ``denied``, or
    ``success``. ``success`` carries the freshly established CLI session and the
    one-time raw token (returned exactly once). Terminal non-success states carry
    no secrets.
    """

    status: str
    principal: domain.Principal | None = None
    roles: tuple[Role, ...] = ()
    cli_session_id: CliSessionId | None = None
    raw_token: str | None = None


def _parse_int(value: object, field: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise DeviceFlowUnavailable()
    return value


def _parse_str(value: object, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise DeviceFlowUnavailable()
    return value


async def start_device_transaction(
    session: AsyncSession, provider: oidc_module.OidcProvider
) -> tuple[domain.DeviceAuthorization, dict]:
    """Start an RFC 8628 device authorization and persist its one-way verifier.

    Returns ``(entity, raw_payload)``. The raw payload (with the one-time
    ``device_code`` and non-secret ``user_code``/verification URIs) is returned to
    the router and never persisted beyond the one-way ``device_code_hash``.
    """
    raw = await provider.start_device_authorization()
    device_code = _parse_str(raw.get("device_code"), "device_code")
    user_code = _parse_str(raw.get("user_code"), "user_code")
    verification_uri = _parse_str(raw.get("verification_uri"), "verification_uri")
    verification_uri_complete = raw.get("verification_uri_complete")
    if verification_uri_complete is not None and not isinstance(
        verification_uri_complete, str
    ):
        raise DeviceFlowUnavailable()
    expires_in = _parse_int(raw.get("expires_in"), "expires_in")
    interval = _parse_int(raw.get("interval"), "interval")

    now = utcnow()
    entity = domain.DeviceAuthorization(
        id=DeviceAuthorizationId(_new_id()),
        device_code_hash=session_service.hash_verifier(device_code),
        user_code=user_code,
        verification_uri=verification_uri,
        verification_uri_complete=verification_uri_complete,
        created_at=now,
        expires_at=now + timedelta(seconds=expires_in),
        poll_interval_seconds=min(max(interval, 1), _SLOW_DOWN_MAX_INTERVAL_SECONDS),
        last_poll_at=None,
        consumed_at=None,
    )
    await repository.delete_expired_device_authorizations(session, now)
    await repository.create_device_authorization(session, entity)
    return entity, raw


async def poll_device_transaction(
    session: AsyncSession,
    provider: oidc_module.OidcProvider,
    device_code: str,
    now: datetime,
) -> DevicePollResult:
    """Poll a device authorization, atomically consuming/authorizing exactly once.

    Rejects unknown/consumed codes indistinguishably (``DeviceCodeInvalid``);
    raises ``DeviceExpired``/``DeviceAccessDenied`` for terminal provider states;
    returns ``pending``/``slow_down`` for keep-polling states; and on success
    provisions/resolves the principal, creates a durable CLI session, consumes the
    transaction, and returns the one-time raw token.
    """
    code_hash = session_service.hash_verifier(device_code)
    entity = await repository.get_device_authorization_by_code_hash_for_update(
        session, code_hash
    )
    if entity is None:
        raise DeviceCodeInvalid()
    if entity.consumed_at is not None:
        raise DeviceCodeInvalid()
    if entity.expires_at <= now:
        await repository.consume_device_authorization(session, entity.id, now)
        raise DeviceExpired()

    # Gateway-enforced polling interval: a poll faster than the provider's
    # interval is answered with ``slow_down`` without calling the provider.
    if entity.last_poll_at is not None:
        elapsed = (now - entity.last_poll_at).total_seconds()
        if elapsed < entity.poll_interval_seconds:
            return DevicePollResult(status="slow_down")

    await repository.touch_device_authorization_poll(session, entity.id, now)

    status, payload = await provider.poll_device_token(device_code)

    if status == "authorization_pending":
        return DevicePollResult(status="pending")
    if status == "slow_down":
        new_interval = min(
            entity.poll_interval_seconds + _SLOW_DOWN_BUMP_SECONDS,
            _SLOW_DOWN_MAX_INTERVAL_SECONDS,
        )
        await repository.update_device_authorization_poll_interval(
            session, entity.id, new_interval
        )
        return DevicePollResult(status="slow_down")
    if status == "expired_token":
        await repository.consume_device_authorization(session, entity.id, now)
        raise DeviceExpired()
    if status == "access_denied":
        await repository.consume_device_authorization(session, entity.id, now)
        raise DeviceAccessDenied()

    # success
    id_token = payload.get("id_token")
    if not isinstance(id_token, str) or not id_token:
        raise OidcAuthenticationFailed()

    try:
        claims = await provider.validate_device_id_token(id_token)
    except OidcAuthenticationFailed:
        raise

    subject = claims.get("sub")
    issuer = get_settings().oidc_issuer or ""
    principal = await session_service.resolve_or_provision_principal(
        session,
        issuer=issuer,
        subject=subject,
        email=claims.get("email"),
        display_name=claims.get("name"),
        now=now,
    )

    cli_session, raw_token = await create_cli_session(session, principal.id, now)
    assignments = await repository.list_active_role_assignments(session, principal.id)
    await repository.consume_device_authorization(session, entity.id, now)

    await session_service.record_audit(
        session,
        actor_principal_id=principal.id,
        action=AUDIT_CLI_LOGIN_SUCCESS,
        resource_type="cli_session",
        resource_id=str(cli_session.id),
        project_id=principal.project_id,
        metadata={"issuer": issuer, "subject": subject},
    )

    return DevicePollResult(
        status="success",
        principal=principal,
        roles=tuple(a.role for a in assignments),
        cli_session_id=cli_session.id,
        raw_token=raw_token,
    )


async def create_cli_session(
    session: AsyncSession, principal_id: PrincipalId, now: datetime
) -> tuple[domain.CliSession, str]:
    """Create a durable CLI session; returns ``(entity, raw_token)``.

    Only the one-way SHA-256 verifier is persisted; the raw token is returned
    exactly once here and never stored or logged.
    """
    settings = get_settings()
    raw_token = generate_cli_session_token()
    entity = domain.CliSession(
        id=CliSessionId(_new_id()),
        principal_id=principal_id,
        token_hash=session_service.hash_verifier(raw_token),
        created_at=now,
        expires_at=now + timedelta(seconds=settings.cli_session_ttl_seconds),
        last_seen_at=now,
        revoked_at=None,
        is_active=True,
    )
    created = await repository.create_cli_session(session, entity)
    return created, raw_token


async def resolve_cli_session(
    session: AsyncSession, raw_token: str, now: datetime
) -> tuple[domain.CliSession, domain.Principal]:
    """Resolve a raw ``ags_...`` token to an active CLI session + principal.

    Enforces revocation and absolute expiry; raises
    :class:`~aethergate.errors.CliSessionInvalid` (indistinguishable) on any
    failure. ``last_seen_at`` is slid on a throttled basis to avoid a hot-row
    write per request.
    """
    entity = await repository.get_cli_session_by_hash(
        session, session_service.hash_verifier(raw_token)
    )
    if entity is None:
        raise CliSessionInvalid()
    if not entity.is_active or entity.revoked_at is not None:
        raise CliSessionInvalid()
    if entity.expires_at <= now:
        raise CliSessionInvalid()

    principal = await repository.get_principal(session, entity.principal_id)
    if principal is None or not principal.is_active:
        raise CliSessionInvalid()
    project = await repository.get_project(session, principal.project_id)
    if project is None or not project.is_active:
        raise CliSessionInvalid()

    if (
        entity.last_seen_at is None
        or (now - entity.last_seen_at).total_seconds() >= _IDLE_SLIDE_THROTTLE_SECONDS
    ):
        await repository.touch_cli_session(session, entity.id, now)
    return entity, principal


async def revoke_cli_session(
    session: AsyncSession, session_id: CliSessionId, now: datetime
) -> domain.CliSession | None:
    """Durably revoke a CLI session; idempotent."""
    return await repository.revoke_cli_session(session, session_id, now)


_IDLE_SLIDE_THROTTLE_SECONDS = 60.0


__all__ = [
    "CLI_SESSION_PREFIX",
    "AUDIT_CLI_DEVICE_STARTED",
    "AUDIT_CLI_LOGIN_SUCCESS",
    "AUDIT_CLI_LOGIN_FAILED",
    "AUDIT_CLI_LOGOUT",
    "DevicePollResult",
    "create_cli_session",
    "generate_cli_session_token",
    "poll_device_transaction",
    "resolve_cli_session",
    "revoke_cli_session",
    "start_device_transaction",
    "utcnow",
]
