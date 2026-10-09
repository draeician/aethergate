import type { Role } from "./client";

/** Pure role helpers (kept separate from AuthContext so Fast Refresh works). */

export function hasRole(roles: Role[], role: Role): boolean {
  return roles.includes(role);
}

export function isSystemAdmin(roles: Role[]): boolean {
  return roles.includes("system_admin");
}

export function isProjectAdmin(roles: Role[]): boolean {
  return roles.includes("project_admin");
}

/** Identity CRUD (projects/principals/credentials/roles) is open to admins. */
export function canManageIdentity(roles: Role[]): boolean {
  return roles.includes("system_admin") || roles.includes("project_admin");
}

/** Deployment-scoped catalog controls are system_admin only. */
export function canManageCatalog(roles: Role[]): boolean {
  return roles.includes("system_admin");
}

export function canCancel(roles: Role[]): boolean {
  return roles.includes("system_admin") || roles.includes("project_admin");
}
