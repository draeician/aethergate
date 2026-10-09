import type { ReactNode } from "react";
import { MemoryRouter } from "react-router-dom";
import { AuthContext, type AuthState } from "../context/auth-context";
import type { SessionRead } from "../lib/client";

export function systemAdminSession(): SessionRead {
  return {
    principal_id: "p-admin",
    project_id: "proj1",
    authentication_kind: "browser_session",
    browser_session_id: "bs1",
    roles: ["system_admin"],
    issuer: null,
    subject: null,
  };
}

export function projectSession(projectId = "proj1"): SessionRead {
  return {
    principal_id: "p1",
    project_id: projectId,
    authentication_kind: "browser_session",
    browser_session_id: "bs1",
    roles: ["project_admin"],
    issuer: null,
    subject: null,
  };
}

/** Wrap a component with the AuthContext and a MemoryRouter for testing. */
export function wrap(auth: Partial<AuthState>) {
  const value: AuthState = {
    status: "authenticated",
    session: null,
    roles: [],
    refreshSession: async () => {},
    logout: async () => {},
    ...auth,
  };
  return function Wrapper({ children }: { children: ReactNode }) {
    return (
      <AuthContext.Provider value={value}>
        <MemoryRouter>{children}</MemoryRouter>
      </AuthContext.Provider>
    );
  };
}
