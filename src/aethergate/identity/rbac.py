"""Centralized RBAC policy: built-in roles and their admin permission grants.

This is the single place that maps a :class:`~aethergate.domain.enums.Role` to
the administrative permissions it may exercise, so authorization never relies on
ad-hoc router role checks. Custom-role CRUD is deferred, but a future custom-role
model plugs in by supplying an equivalent permission set for an arbitrary role
name without changing the authorization service's shape.
"""

from __future__ import annotations

from aethergate.domain.enums import CredentialScope, Role

# Typed admin permissions (doubling as admin ``CredentialScope`` values).
ADMIN_PERMISSIONS: tuple[CredentialScope, ...] = (
    CredentialScope.ADMIN_CREDENTIALS_READ,
    CredentialScope.ADMIN_CREDENTIALS_WRITE,
    CredentialScope.ADMIN_PROJECTS_READ,
    CredentialScope.ADMIN_PROJECTS_WRITE,
    CredentialScope.ADMIN_PRINCIPALS_READ,
    CredentialScope.ADMIN_PRINCIPALS_WRITE,
    CredentialScope.ADMIN_CATALOG_READ,
    CredentialScope.ADMIN_CATALOG_WRITE,
    CredentialScope.ADMIN_ACCOUNTING_READ,
    CredentialScope.ADMIN_ACCOUNTING_WRITE,
    CredentialScope.ADMIN_QUEUE_READ,
    CredentialScope.ADMIN_QUEUE_WRITE,
    CredentialScope.ADMIN_AUDIT_READ,
)

READ_PERMISSIONS: frozenset[CredentialScope] = frozenset(
    p for p in ADMIN_PERMISSIONS if p.value.endswith(":read")
)
WRITE_PERMISSIONS: frozenset[CredentialScope] = frozenset(
    p for p in ADMIN_PERMISSIONS if p.value.endswith(":write")
)


def is_admin_permission(scope: CredentialScope) -> bool:
    """Return True for any typed ``admin:*`` permission scope."""
    return scope.value.startswith("admin:")


def permission_is_write(permission: CredentialScope) -> bool:
    """Return True for a mutating (write) admin permission."""
    return permission.value.endswith(":write")


def role_grants_permission(role: Role, permission: CredentialScope) -> bool:
    """Return True if ``role`` is permitted to exercise ``permission``.

    ``system_admin`` and ``project_admin`` grant read and write permissions;
    ``project_viewer`` grants only read permissions (write operations are always
    denied). This is resource-scope-agnostic: project-scoping is enforced by the
    authorization service using the assignment's ``resource_id``.
    """
    if not is_admin_permission(permission):
        return False
    if role is Role.SYSTEM_ADMIN or role is Role.PROJECT_ADMIN:
        return True
    if role is Role.PROJECT_VIEWER:
        return not permission_is_write(permission)
    return False


def role_is_project_scoped(role: Role) -> bool:
    """Return True if the role's authority is limited to a single project."""
    return role in (Role.PROJECT_ADMIN, Role.PROJECT_VIEWER)


__all__ = [
    "ADMIN_PERMISSIONS",
    "READ_PERMISSIONS",
    "WRITE_PERMISSIONS",
    "is_admin_permission",
    "permission_is_write",
    "role_grants_permission",
    "role_is_project_scoped",
]
