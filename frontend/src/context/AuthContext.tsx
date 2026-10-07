import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
import { api, AUTH_EXPIRED_EVENT, type SessionRead } from "../lib/client";
import { AuthContext, type AuthState, type AuthStatus } from "./auth-context";

export function AuthProvider({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<AuthStatus>("loading");
  const [session, setSession] = useState<SessionRead | null>(null);

  const refreshSession = useCallback(async () => {
    try {
      const current = await api.getSession();
      setSession(current);
      setStatus("authenticated");
    } catch {
      setSession(null);
      setStatus("unauthenticated");
    }
  }, []);

  const logout = useCallback(async () => {
    try {
      await api.logout();
    } catch {
      // Even if the server call fails, the local state must clear.
    } finally {
      setSession(null);
      setStatus("unauthenticated");
    }
  }, []);

  useEffect(() => {
    void refreshSession();
  }, [refreshSession]);

  useEffect(() => {
    const onExpired = () => {
      setSession(null);
      setStatus("unauthenticated");
    };
    window.addEventListener(AUTH_EXPIRED_EVENT, onExpired);
    return () => window.removeEventListener(AUTH_EXPIRED_EVENT, onExpired);
  }, []);

  const value = useMemo<AuthState>(
    () => ({
      status,
      session,
      roles: session?.roles ?? [],
      refreshSession,
      logout,
    }),
    [status, session, refreshSession, logout],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}
