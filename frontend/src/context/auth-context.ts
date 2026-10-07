import { createContext, useContext } from "react";
import type { Role, SessionRead } from "../lib/client";

export type AuthStatus = "loading" | "authenticated" | "unauthenticated";

export interface AuthState {
  status: AuthStatus;
  session: SessionRead | null;
  roles: Role[];
  /** Re-resolve the session from the server (used by the callback page). */
  refreshSession: () => Promise<void>;
  /** Revoke the server session and transition to unauthenticated. */
  logout: () => Promise<void>;
}

export const AuthContext = createContext<AuthState | null>(null);

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}
