"""Identity service: credential lifecycle, Bearer authentication, authorization.

This is the single source of truth for credential policy, used by both the API
router (enqueue-time authentication) and the scheduler worker (pre-dispatch
authorization revalidation), so the two never diverge.

The raw API key is never logged, never returned by read operations, and never
persisted; only its SHA-256 verifier and non-secret display prefix are stored.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from aethergate.domain import entities as domain
from aethergate.domain.entities import RequestContext
from aethergate.domain.enums import CredentialAudience, CredentialScope
from aethergate.domain.ids import ApiCredentialId, PrincipalId, ProjectId
from aethergate.errors import AuthenticationRequired, CredentialLifecycleError
from aethergate.identity.keys import generate_api_key, hash_raw_key
from aethergate.persistence import repository


def utcnow() -> datetime:
    return datetime.now(UTC)


def _new_id() -> str:
    return uuid.uuid4().hex


def _scope_defaults(audience: CredentialAudience) -> tuple[CredentialScope, ...]:
    """Default scopes derived from the audience for supported audience types.

    ``inference`` defaults to ``inference:invoke``. ``admin`` has no default
    scopes until admin resource scopes are explicitly introduced, so an admin
    credential never silently inherits the inference permission.
    """
    if audience is CredentialAudience.INFERENCE:
        return (CredentialScope.INFERENCE_INVOKE,)
    return ()


def _validate_scope_coherence(
    audience: CredentialAudience, scopes: tuple[CredentialScope, ...]
) -> None:
    """Reject audience/scope combinations that are semantically incoherent.

    For phase 1 the only incompatible combination is an ``admin`` credential
    carrying the inference ``inference:invoke`` scope; accepting it would make a
    future admin credential valid for inference merely because it exists.
    """
    if audience is CredentialAudience.ADMIN and CredentialScope.INFERENCE_INVOKE in scopes:
        raise CredentialLifecycleError(
            f"audience {audience.value!r} may not carry scope "
            f"{CredentialScope.INFERENCE_INVOKE.value!r}"
        )


async def _require_valid_identity(
    session: AsyncSession, project_id: ProjectId, principal_id: PrincipalId
) -> None:
    """Validate that a credential's project and principal are valid and matched.

    A credential may only be created against an existing, active project and an
    existing, active principal that belongs to that project. Failing here means
    no raw key is generated and no credential row is written.
    """
    project = await repository.get_project(session, project_id)
    if project is None:
        raise CredentialLifecycleError(f"project {project_id!s} does not exist")
    if not project.is_active:
        raise CredentialLifecycleError(f"project {project_id!s} is inactive")
    principal = await repository.get_principal(session, principal_id)
    if principal is None:
        raise CredentialLifecycleError(f"principal {principal_id!s} does not exist")
    if not principal.is_active:
        raise CredentialLifecycleError(f"principal {principal_id!s} is inactive")
    if principal.project_id != project_id:
        raise CredentialLifecycleError(
            f"principal {principal_id!s} does not belong to project {project_id!s}"
        )


def parse_bearer_token(auth_values: list[str]) -> str:
    """Parse a strict, standards-aware ``Authorization: Bearer`` header.

    ``auth_values`` is the list of ``Authorization`` header values for the
    request (a well-behaved request has exactly one). Missing, ambiguous,
    wrong-scheme, malformed, and junk-suffixed values all raise the same
    indistinguishable authentication error; no raw token is echoed.
    """
    if not auth_values:
        raise AuthenticationRequired()
    if len(auth_values) > 1:
        raise AuthenticationRequired()
    header = auth_values[0]
    if not header:
        raise AuthenticationRequired()
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer":
        raise AuthenticationRequired()
    if not token or token != token.strip() or any(ch.isspace() for ch in token):
        raise AuthenticationRequired()
    return token


def _ensure_inference_access(
    credential: domain.ApiCredential | None,
    project: domain.Project | None,
    principal: domain.Principal | None,
    now: datetime,
) -> None:
    """Raise ``AuthenticationRequired`` unless every identity precondition holds.

    All failures are intentionally indistinguishable: no branch reveals whether
    a project, principal, or credential exists or which specific condition
    failed.
    """
    if credential is None or project is None or principal is None:
        raise AuthenticationRequired()
    if not project.is_active or not principal.is_active or not credential.is_active:
        raise AuthenticationRequired()
    if principal.project_id != project.id:
        raise AuthenticationRequired()
    if credential.project_id != project.id:
        raise AuthenticationRequired()
    if credential.principal_id != principal.id:
        raise AuthenticationRequired()
    if credential.revoked_at is not None:
        raise AuthenticationRequired()
    if credential.expires_at is not None and credential.expires_at <= now:
        raise AuthenticationRequired()
    if credential.audience != CredentialAudience.INFERENCE:
        raise AuthenticationRequired()
    if CredentialScope.INFERENCE_INVOKE not in credential.scopes:
        raise AuthenticationRequired()


async def authenticate(session: AsyncSession, raw_key: str) -> RequestContext:
    """Resolve a raw API key to a durable, typed ``RequestContext``.

    Lookup is by full-key SHA-256 hash (an indexed, fixed-cost verifier); the
    safe display prefix is never used as the authentication selector. Any
    failure raises an indistinguishable ``AuthenticationRequired``.
    """
    credential = await repository.get_api_credential_by_hash(
        session, hash_raw_key(raw_key)
    )
    if credential is None:
        raise AuthenticationRequired()
    project = await repository.get_project(session, credential.project_id)
    principal = (
        await repository.get_principal(session, credential.principal_id)
        if credential.principal_id is not None
        else None
    )
    _ensure_inference_access(credential, project, principal, utcnow())
    assert project is not None and principal is not None
    return RequestContext(
        project_id=project.id,
        principal_id=principal.id,
        api_credential_id=credential.id,
        audience=credential.audience,
        scopes=credential.scopes,
    )


async def authorize_for_dispatch(
    session: AsyncSession,
    project_id: ProjectId | None,
    principal_id: PrincipalId | None,
    api_credential_id: ApiCredentialId | None,
) -> None:
    """Revalidate authorization immediately before dispatch.

    Revocation/expiry/inactivation applied while a request is queued must block
    it from ever contacting upstream. Raises ``AuthenticationRequired`` on any
    failure; callers must terminate the request without reserving capacity.
    """
    project = await repository.get_project(session, project_id) if project_id else None
    principal = (
        await repository.get_principal(session, principal_id) if principal_id else None
    )
    credential = (
        await repository.get_api_credential(session, api_credential_id)
        if api_credential_id
        else None
    )
    _ensure_inference_access(credential, project, principal, utcnow())


async def create_credential(
    session: AsyncSession,
    *,
    project_id: ProjectId,
    principal_id: PrincipalId,
    name: str,
    audience: CredentialAudience = CredentialAudience.INFERENCE,
    scopes: tuple[CredentialScope, ...] | None = None,
    expires_at: datetime | None = None,
) -> tuple[domain.ApiCredential, str]:
    """Create a credential and return ``(metadata, raw_key)`` once.

    Validation (project/principal validity and audience/scope coherence) happens
    before any key material is generated, so a rejected creation never generates
    or returns a raw key. Only the verifier/hash and safe prefix are persisted;
    the raw key is never stored.
    """
    resolved_scopes = _scope_defaults(audience) if scopes is None else scopes
    _validate_scope_coherence(audience, resolved_scopes)
    await _require_valid_identity(session, project_id, principal_id)
    raw, prefix = generate_api_key()
    entity = domain.ApiCredential(
        id=ApiCredentialId(_new_id()),
        project_id=project_id,
        principal_id=principal_id,
        name=name,
        key_prefix=prefix,
        key_hash=hash_raw_key(raw),
        audience=audience,
        scopes=resolved_scopes,
        expires_at=expires_at,
    )
    created = await repository.create_api_credential(session, entity)
    return created, raw


async def list_credentials(
    session: AsyncSession, project_id: ProjectId
) -> list[domain.ApiCredential]:
    """Metadata-only listing; never includes raw keys or hashes to callers."""
    return await repository.list_api_credentials(session, project_id)


async def revoke_credential(
    session: AsyncSession, credential_id: ApiCredentialId
) -> domain.ApiCredential | None:
    """Durably revoke a credential; idempotent, immediately blocks new work."""
    return await repository.revoke_api_credential(session, credential_id, utcnow())


async def rotate_credential(
    session: AsyncSession,
    credential_id: ApiCredentialId,
    *,
    name: str | None = None,
    expires_at: datetime | None = None,
) -> tuple[domain.ApiCredential, str]:
    """Rotate an active credential: create a new key and atomically revoke the old one.

    Rotation is only valid for an existing, active, non-revoked, non-expired
    credential whose project and principal are still valid/active/matching.
    Failing any precondition raises ``CredentialLifecycleError`` before a
    replacement is created. The old credential's metadata is retained (revoked)
    for auditability.
    """
    old = await repository.get_api_credential(session, credential_id)
    if old is None:
        raise CredentialLifecycleError(f"api credential {credential_id!s} does not exist")
    if old.revoked_at is not None:
        raise CredentialLifecycleError(f"api credential {credential_id!s} is revoked")
    if not old.is_active:
        raise CredentialLifecycleError(f"api credential {credential_id!s} is inactive")
    if old.expires_at is not None and old.expires_at <= utcnow():
        raise CredentialLifecycleError(f"api credential {credential_id!s} is expired")
    if old.principal_id is None:
        raise CredentialLifecycleError(
            f"api credential {credential_id!s} has no principal to rotate against"
        )
    await _require_valid_identity(session, old.project_id, old.principal_id)
    _validate_scope_coherence(old.audience, old.scopes)
    raw, prefix = generate_api_key()
    entity = domain.ApiCredential(
        id=ApiCredentialId(_new_id()),
        project_id=old.project_id,
        principal_id=old.principal_id,
        name=name if name is not None else old.name,
        key_prefix=prefix,
        key_hash=hash_raw_key(raw),
        audience=old.audience,
        scopes=old.scopes,
        expires_at=expires_at if expires_at is not None else old.expires_at,
    )
    created = await repository.create_api_credential(session, entity)
    await repository.revoke_api_credential(session, credential_id, utcnow())
    return created, raw


__all__ = [
    "authenticate",
    "authorize_for_dispatch",
    "create_credential",
    "list_credentials",
    "parse_bearer_token",
    "revoke_credential",
    "rotate_credential",
    "utcnow",
]
