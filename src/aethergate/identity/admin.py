"""Admin control-plane identity: authentication, authorization, bootstrap, RBAC.

The control plane has its own identity boundary, separate from the inference
data plane. Authentication (a Bearer ``agk_...`` admin-audience credential) and
RBAC authorization (durable role assignments) are distinct steps, and both must
succeed before any protected admin action. The bootstrap endpoint is the single
one-use path to establish the first ``system_admin`` service account.

No raw key, hash, bootstrap token, or Authorization header is ever logged or
written to the audit log.
"""

from __future__ import annotations

import secrets
import uuid
from datetime import UTC, datetime

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from aethergate.config import get_settings
from aethergate.domain import entities as domain
from aethergate.domain.enums import (
    CredentialAudience,
    CredentialScope,
    PrincipalKind,
    ResourceScopeType,
    Role,
)
from aethergate.domain.ids import (
    ApiCredentialId,
    AuditEventId,
    PrincipalId,
    ProjectId,
    RoleAssignmentId,
)
from aethergate.errors import (
    AdminAuthenticationRequired,
    AdminAuthorizationError,
    BootstrapAlreadyCompleted,
    BootstrapTokenRejected,
)
from aethergate.identity import rbac
from aethergate.identity import service as identity_service
from aethergate.identity.keys import hash_raw_key
from aethergate.persistence import repository

# Audit action names (stable strings; safe metadata only).
AUDIT_BOOTSTRAP_COMPLETED = "bootstrap.completed"
AUDIT_ROLE_ASSIGNMENT_CREATED = "role_assignment.created"
AUDIT_ROLE_ASSIGNMENT_REVOKED = "role_assignment.revoked"
AUDIT_ADMIN_CREDENTIAL_CREATED = "admin_credential.created"
AUDIT_ADMIN_CREDENTIAL_ROTATED = "admin_credential.rotated"
AUDIT_ADMIN_CREDENTIAL_REVOKED = "admin_credential.revoked"

INITIAL_PROJECT_NAME = "default"
INITIAL_PRINCIPAL_NAME = "bootstrap-admin"


def utcnow() -> datetime:
    return datetime.now(UTC)


def _new_id() -> str:
    return uuid.uuid4().hex


def _ensure_admin_access(
    credential: domain.ApiCredential | None,
    project: domain.Project | None,
    principal: domain.Principal | None,
    now: datetime,
) -> None:
    """Raise ``AdminAuthenticationRequired`` unless every admin precondition holds.

    Failures are intentionally indistinguishable: no branch reveals whether a
    project, principal, or credential exists or which condition failed.
    """
    if credential is None or project is None or principal is None:
        raise AdminAuthenticationRequired()
    if not project.is_active or not principal.is_active or not credential.is_active:
        raise AdminAuthenticationRequired()
    if principal.project_id != project.id:
        raise AdminAuthenticationRequired()
    if credential.project_id != project.id:
        raise AdminAuthenticationRequired()
    if credential.principal_id != principal.id:
        raise AdminAuthenticationRequired()
    if credential.revoked_at is not None:
        raise AdminAuthenticationRequired()
    if credential.expires_at is not None and credential.expires_at <= now:
        raise AdminAuthenticationRequired()
    if credential.audience != CredentialAudience.ADMIN:
        raise AdminAuthenticationRequired()


async def authenticate_admin(
    session: AsyncSession, raw_key: str
) -> domain.AdminRequestContext:
    """Resolve a raw admin API key to a durable ``AdminRequestContext``.

    Loads the credential's active role assignments so authorization can answer
    without re-resolving identity. Any failure raises an indistinguishable
    ``AdminAuthenticationRequired``.
    """
    credential = await repository.get_api_credential_by_hash(
        session, hash_raw_key(raw_key)
    )
    if credential is None:
        raise AdminAuthenticationRequired()
    project = await repository.get_project(session, credential.project_id)
    principal = (
        await repository.get_principal(session, credential.principal_id)
        if credential.principal_id is not None
        else None
    )
    _ensure_admin_access(credential, project, principal, utcnow())
    assert project is not None and principal is not None
    assignments = await repository.list_active_role_assignments(session, principal.id)
    return domain.AdminRequestContext(
        project_id=project.id,
        principal_id=principal.id,
        api_credential_id=credential.id,
        audience=credential.audience,
        scopes=credential.scopes,
        roles=tuple(a.role for a in assignments),
        assignments=tuple(assignments),
    )


def authorize_admin(
    context: domain.AdminRequestContext,
    permission: CredentialScope,
    resource_type: str | None = None,
    resource_id: ProjectId | None = None,
) -> None:
    """Authorize a protected admin action against the resolved context.

    Both layers must hold: the credential must carry the ``permission`` scope,
    and at least one active role assignment must grant the permission for the
    requested resource scope. ``system_admin`` grants deployment-wide; a
    project-scoped role grants only for its assigned project. Failures are a
    fixed, indistinguishable ``AdminAuthorizationError``.
    """
    if permission not in context.scopes:
        raise AdminAuthorizationError()
    for assignment in context.assignments:
        if not rbac.role_grants_permission(assignment.role, permission):
            continue
        if assignment.resource_scope_type is ResourceScopeType.DEPLOYMENT:
            return
        if resource_id is not None and assignment.resource_id == resource_id:
            return
    raise AdminAuthorizationError()


async def _write_audit(
    session: AsyncSession,
    *,
    actor_principal_id: PrincipalId | None,
    action: str,
    resource_type: str,
    resource_id: str,
    project_id: ProjectId | None = None,
    metadata: dict | None = None,
) -> None:
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


async def bootstrap(
    session: AsyncSession, raw_token: str | None
) -> tuple[domain.ApiCredential, str]:
    """One-use bootstrap: establish the first ``system_admin`` service account.

    The configured secret is compared with ``secrets.compare_digest`` before any
    database work. Exactly one concurrent/sequential bootstrap may succeed; the
    completion state is durable (survives restart) and the bootstrap token is
    never an ongoing master key.
    """
    expected = get_settings().bootstrap_token
    if expected is None or raw_token is None:
        raise BootstrapTokenRejected()
    if not secrets.compare_digest(raw_token, expected.get_secret_value()):
        raise BootstrapTokenRejected()

    async with session.begin():
        state = await repository.get_bootstrap_state_for_update(session)
        if state is None:
            try:
                state = await repository.create_bootstrap_state(session)
            except IntegrityError:
                raise BootstrapAlreadyCompleted() from None
        if state.completed:
            raise BootstrapAlreadyCompleted()

        project = await repository.get_project_by_name(session, INITIAL_PROJECT_NAME)
        if project is None:
            project = await repository.create_project(
                session,
                domain.Project(id=ProjectId(_new_id()), name=INITIAL_PROJECT_NAME),
            )

        principal = await repository.get_principal_by_name(
            session, project.id, INITIAL_PRINCIPAL_NAME
        )
        if principal is None:
            principal = await repository.create_principal(
                session,
                domain.Principal(
                    id=PrincipalId(_new_id()),
                    project_id=project.id,
                    kind=PrincipalKind.SERVICE_ACCOUNT,
                    name=INITIAL_PRINCIPAL_NAME,
                ),
            )

        assignment = await repository.create_role_assignment(
            session,
            domain.RoleAssignment(
                id=RoleAssignmentId(_new_id()),
                principal_id=principal.id,
                role=Role.SYSTEM_ADMIN,
                resource_scope_type=ResourceScopeType.DEPLOYMENT,
            ),
        )

        credential, raw_key = await identity_service.create_credential(
            session,
            project_id=project.id,
            principal_id=principal.id,
            name=INITIAL_PRINCIPAL_NAME,
            audience=CredentialAudience.ADMIN,
            scopes=rbac.ADMIN_PERMISSIONS,
        )

        await repository.mark_bootstrap_completed(
            session,
            project_id=project.id,
            principal_id=principal.id,
            credential_id=credential.id,
            completed_at=utcnow(),
        )

        # No authenticated actor yet; represent explicitly with a NULL actor.
        await _write_audit(
            session,
            actor_principal_id=None,
            action=AUDIT_BOOTSTRAP_COMPLETED,
            resource_type="bootstrap",
            resource_id="bootstrap",
            project_id=project.id,
            metadata={"role_assignment_id": str(assignment.id)},
        )

    return credential, raw_key


async def get_bootstrap_status(session: AsyncSession) -> domain.BootstrapState | None:
    return await repository.get_bootstrap_state(session)


async def create_role_assignment(
    session: AsyncSession,
    *,
    actor_principal_id: PrincipalId,
    principal_id: PrincipalId,
    role: Role,
    resource_scope_type: ResourceScopeType,
    resource_id: ProjectId | None,
) -> domain.RoleAssignment:
    """Create a role assignment, preventing duplicate active equivalents."""
    if role is Role.SYSTEM_ADMIN and resource_scope_type is not ResourceScopeType.DEPLOYMENT:
        raise ValueError("system_admin must be deployment-scoped")
    if role in (Role.PROJECT_ADMIN, Role.PROJECT_VIEWER) and (
        resource_scope_type is not ResourceScopeType.PROJECT or resource_id is None
    ):
        raise ValueError("project-scoped roles require a project resource scope")
    existing = await repository.find_active_equivalent_assignment(
        session, principal_id, role, resource_scope_type, resource_id
    )
    if existing is not None:
        return existing

    principal = await repository.get_principal(session, principal_id)
    if principal is None or not principal.is_active:
        raise ValueError("principal does not exist or is inactive")

    assignment = await repository.create_role_assignment(
        session,
        domain.RoleAssignment(
            id=RoleAssignmentId(_new_id()),
            principal_id=principal_id,
            role=role,
            resource_scope_type=resource_scope_type,
            resource_id=resource_id,
            created_by=actor_principal_id,
        ),
    )
    await _write_audit(
        session,
        actor_principal_id=actor_principal_id,
        action=AUDIT_ROLE_ASSIGNMENT_CREATED,
        resource_type="role_assignment",
        resource_id=str(assignment.id),
        project_id=resource_id if resource_scope_type is ResourceScopeType.PROJECT else None,
        metadata={
            "role": role.value,
            "resource_scope_type": resource_scope_type.value,
        },
    )
    return assignment


async def revoke_role_assignment(
    session: AsyncSession,
    *,
    actor_principal_id: PrincipalId,
    assignment_id: RoleAssignmentId,
) -> domain.RoleAssignment | None:
    """Revoke a role assignment (idempotent), recording an audit event."""
    assignment = await repository.revoke_role_assignment(
        session, assignment_id, utcnow()
    )
    if assignment is not None:
        await _write_audit(
            session,
            actor_principal_id=actor_principal_id,
            action=AUDIT_ROLE_ASSIGNMENT_REVOKED,
            resource_type="role_assignment",
            resource_id=str(assignment.id),
            project_id=assignment.resource_id
            if assignment.resource_scope_type is ResourceScopeType.PROJECT
            else None,
            metadata={"role": assignment.role.value},
        )
    return assignment


async def create_admin_credential(
    session: AsyncSession,
    *,
    actor_principal_id: PrincipalId,
    project_id: ProjectId,
    principal_id: PrincipalId,
    name: str,
    audience: CredentialAudience,
    scopes: tuple[CredentialScope, ...] | None,
    expires_at: datetime | None,
) -> tuple[domain.ApiCredential, str]:
    credential, raw_key = await identity_service.create_credential(
        session,
        project_id=project_id,
        principal_id=principal_id,
        name=name,
        audience=audience,
        scopes=scopes,
        expires_at=expires_at,
    )
    await _write_audit(
        session,
        actor_principal_id=actor_principal_id,
        action=AUDIT_ADMIN_CREDENTIAL_CREATED,
        resource_type="api_credential",
        resource_id=str(credential.id),
        project_id=project_id,
        metadata={"audience": audience.value},
    )
    return credential, raw_key


async def rotate_admin_credential(
    session: AsyncSession,
    *,
    actor_principal_id: PrincipalId,
    credential_id: ApiCredentialId,
) -> tuple[domain.ApiCredential, str]:
    credential, raw_key = await identity_service.rotate_credential(
        session, credential_id
    )
    await _write_audit(
        session,
        actor_principal_id=actor_principal_id,
        action=AUDIT_ADMIN_CREDENTIAL_ROTATED,
        resource_type="api_credential",
        resource_id=str(credential.id),
        project_id=credential.project_id,
    )
    return credential, raw_key


async def revoke_admin_credential(
    session: AsyncSession,
    *,
    actor_principal_id: PrincipalId,
    credential_id: ApiCredentialId,
) -> domain.ApiCredential | None:
    credential = await identity_service.revoke_credential(session, credential_id)
    if credential is not None:
        await _write_audit(
            session,
            actor_principal_id=actor_principal_id,
            action=AUDIT_ADMIN_CREDENTIAL_REVOKED,
            resource_type="api_credential",
            resource_id=str(credential.id),
            project_id=credential.project_id,
        )
    return credential


__all__ = [
    "authenticate_admin",
    "authorize_admin",
    "bootstrap",
    "get_bootstrap_status",
    "create_role_assignment",
    "revoke_role_assignment",
    "create_admin_credential",
    "rotate_admin_credential",
    "revoke_admin_credential",
    "utcnow",
]
