import type { Role } from "./client";

/** Pure role helpers (kept separate from AuthContext so Fast Refresh works). */

export function hasRole(roles: Role[], role: Role): boolean {
  return roles.includes(role);
}

export function isSystemAdmin(roles: Role[]): boolean {
  return roles.includes("system_admin");
}

export function canCancel(roles: Role[]): boolean {
  return roles.includes("system_admin") || roles.includes("project_admin");
}
