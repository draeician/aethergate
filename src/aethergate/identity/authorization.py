"""Centralized admin authorization: policy, escalation, and resource resolution.

This module is the single place that answers the two authorization questions for
the control plane:

1. ``authorize_admin`` — is a resolved ``AdminRequestContext`` permitted to
   exercise a permission against a declared resource scope? Raises a fixed,
   indistinguishable ``AdminAuthorizationError``.

2. ``resolve_admin_resource`` — resolve a resource by opaque ID for read/mutate
   operations, returning it only when it exists **and** is within the caller's
   authority. A nonexistent resource and a cross-project resource are
   deliberately indistinguishable (both raise ``AdminResourceNotFound``), so a
   project-scoped caller cannot enumerate resources outside its project.

Resource-scope resolution is centralized here so callers never compare unrelated
opaque resource IDs directly to project IDs.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from aethergate.domain import entities as domain
from aethergate.domain.enums import (
    AdminAuthenticationKind,
    CredentialScope,
    ResourceScopeType,
    Role,
)
from aethergate.domain.ids import (
    ApiCredentialId,
    PrincipalId,
    ProjectId,
    RoleAssignmentId,
)
from aethergate.errors import (
    AdminAuthorizationError,
    AdminResourceNotFound,
    AdminValidationError,
)
from aethergate.identity import rbac
from aethergate.persistence import repository

# Resource types understood by the resolver.
RESOURCE_PROJECT = "project"
RESOURCE_PRINCIPAL = "principal"
RESOURCE_CREDENTIAL = "credential"
RESOURCE_ROLE_ASSIGNMENT = "role_assignment"
RESOURCE_DEPLOYMENT = "deployment"


def authorize_admin(
    context: domain.AdminRequestContext,
    permission: CredentialScope,
    resource_type: str | None = None,
    resource_id: ProjectId | None = None,
) -> None:
    """Authorize a protected admin action against a resolved context.

    A service credential must carry ``permission`` in its scopes; a human browser
    session has no credential scopes and is authorized by role assignment alone.
    In both cases at least one active role assignment must grant the permission
    for the requested scope. ``system_admin`` grants deployment-wide; a
    project-scoped role grants only for its assigned project. Failures are a
    fixed ``AdminAuthorizationError``.
    """
    if (
        context.authentication_kind is AdminAuthenticationKind.SERVICE_CREDENTIAL
        and permission not in context.scopes
    ):
        raise AdminAuthorizationError()
    for assignment in context.assignments:
        if not rbac.role_grants_permission(assignment.role, permission):
            continue
        if assignment.resource_scope_type is ResourceScopeType.DEPLOYMENT:
            return
        if resource_id is not None and assignment.resource_id == resource_id:
            return
    raise AdminAuthorizationError()


def _validate_role_scope_coherence(
    role: Role, resource_scope_type: ResourceScopeType, resource_id: ProjectId | None
) -> None:
    """Reject role/scope combinations that violate the RBAC shape."""
    if role is Role.SYSTEM_ADMIN:
        if resource_scope_type is not ResourceScopeType.DEPLOYMENT:
            raise AdminValidationError("system_admin must be deployment-scoped")
    elif role in (Role.PROJECT_ADMIN, Role.PROJECT_VIEWER):
        if resource_scope_type is not ResourceScopeType.PROJECT or resource_id is None:
            raise AdminValidationError(
                "project-scoped roles require a project resource scope"
            )


def authorize_role_grant(
    context: domain.AdminRequestContext,
    role: Role,
    resource_scope_type: ResourceScopeType,
    resource_id: ProjectId | None,
) -> None:
    """Authorize a role assignment create (privilege-escalation defense).

    ``system_admin`` may grant any role/scope. A project-scoped role may grant
    only project_admin/project_viewer within its own project, and never a
    role/scope broader than its own authority (e.g. never ``system_admin`` or a
    deployment scope). ``project_viewer`` can never mutate roles (no write
    permission). Coherence violations are validation errors, not authorization
    errors.
    """
    _validate_role_scope_coherence(role, resource_scope_type, resource_id)

    scope_type = (
        RESOURCE_DEPLOYMENT
        if resource_scope_type is ResourceScopeType.DEPLOYMENT
        else RESOURCE_PROJECT
    )
    # The escalation defense falls out of the scope check: a deployment-scoped
    # grant requires deployment authority (system_admin), and a project-scoped
    # grant requires project write authority over exactly that project.
    authorize_admin(
        context,
        CredentialScope.ADMIN_PRINCIPALS_WRITE,
        scope_type,
        resource_id,
    )


def _in_scope(
    context: domain.AdminRequestContext, scope_type: str, scope_id: ProjectId | None
) -> bool:
    """Return True if the resource's scope is within the caller's authority.

    This is about *visibility*, independent of the specific read/write
    permission: a deployment-scoped assignment covers every scope; a
    project-scoped assignment covers only its own project. A resource outside
    the caller's scope is indistinguishable from a nonexistent one.
    """
    for assignment in context.assignments:
        if assignment.resource_scope_type is ResourceScopeType.DEPLOYMENT:
            return True
        if scope_type == RESOURCE_PROJECT and assignment.resource_id == scope_id:
            return True
    return False


def _scope_of(resource_type: str, entity: object) -> tuple[str, ProjectId | None]:
    """Map a resolved resource to its authorization scope (project or deployment)."""
    if resource_type == RESOURCE_ROLE_ASSIGNMENT:
        assignment = entity  # domain.RoleAssignment
        if assignment.resource_scope_type is ResourceScopeType.DEPLOYMENT:
            return RESOURCE_DEPLOYMENT, None
        return RESOURCE_PROJECT, assignment.resource_id
    if resource_type == RESOURCE_PROJECT:
        return RESOURCE_PROJECT, entity.id
    # principal and credential resolve to their owning project.
    return RESOURCE_PROJECT, entity.project_id


async def _load_resource(
    session: AsyncSession, resource_type: str, resource_id: str
) -> domain.Entity | None:
    if resource_type == RESOURCE_PROJECT:
        return await repository.get_project(session, ProjectId(resource_id))
    if resource_type == RESOURCE_PRINCIPAL:
        return await repository.get_principal(session, PrincipalId(resource_id))
    if resource_type == RESOURCE_CREDENTIAL:
        return await repository.get_api_credential(session, ApiCredentialId(resource_id))
    if resource_type == RESOURCE_ROLE_ASSIGNMENT:
        return await repository.get_role_assignment(
            session, RoleAssignmentId(resource_id)
        )
    raise ValueError(f"unknown resource type {resource_type!r}")


async def resolve_admin_resource(
    session: AsyncSession,
    context: domain.AdminRequestContext,
    permission: CredentialScope,
    resource_type: str,
    resource_id: str,
) -> domain.Entity:
    """Resolve a resource for read/mutate, enforcing scope non-enumeration.

    Returns the resource only if it exists and the caller is authorized.
    A nonexistent resource and a cross-project (out-of-scope) resource are
    deliberately indistinguishable (both raise ``AdminResourceNotFound``) so a
    project-scoped caller cannot enumerate resources outside its project.

    A resource that *is* within the caller's scope but for which the caller
    lacks the specific permission (e.g. a project_viewer attempting a write)
    raises ``AdminAuthorizationError`` (403), because the resource is already
    visible to the caller and a not-found response would be misleading.
    """
    entity = await _load_resource(session, resource_type, resource_id)
    if entity is None:
        raise AdminResourceNotFound()
    scope_type, scope_id = _scope_of(resource_type, entity)
    if not _in_scope(context, scope_type, scope_id):
        raise AdminResourceNotFound()
    authorize_admin(context, permission, scope_type, scope_id)
    return entity


def authorized_project_ids(
    context: domain.AdminRequestContext,
) -> set[ProjectId] | None:
    """Return the set of project IDs the context may act on, or ``None`` = all.

    A deployment-scoped assignment (``system_admin``) authorizes every project,
    represented as ``None``. Otherwise the union of all project-scoped
    assignments' resource IDs is returned (possibly empty).
    """
    if any(
        a.resource_scope_type is ResourceScopeType.DEPLOYMENT
        for a in context.assignments
    ):
        return None
    return {
        a.resource_id
        for a in context.assignments
        if a.resource_scope_type is ResourceScopeType.PROJECT and a.resource_id is not None
    }


__all__ = [
    "authorize_admin",
    "authorize_role_grant",
    "authorized_project_ids",
    "resolve_admin_resource",
    "RESOURCE_PROJECT",
    "RESOURCE_PRINCIPAL",
    "RESOURCE_CREDENTIAL",
    "RESOURCE_ROLE_ASSIGNMENT",
    "RESOURCE_DEPLOYMENT",
]
