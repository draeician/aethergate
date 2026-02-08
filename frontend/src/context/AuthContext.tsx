import { createContext, useContext, useState, useCallback, type ReactNode } from "react";

interface AuthState {
  adminKey: string | null;
  setAdminKey: (key: string) => void;
  logout: () => void;
}

const AuthContext = createContext<AuthState | null>(null);

const STORAGE_KEY = "aethergate_admin_key";

export function AuthProvider({ children }: { children: ReactNode }) {
  const [adminKey, setAdminKeyState] = useState<string | null>(
    () => sessionStorage.getItem(STORAGE_KEY)
  );

  const setAdminKey = useCallback((key: string) => {
    sessionStorage.setItem(STORAGE_KEY, key);
    setAdminKeyState(key);
  }, []);

  const logout = useCallback(() => {
    sessionStorage.removeItem(STORAGE_KEY);
    setAdminKeyState(null);
  }, []);

  return (
    <AuthContext.Provider value={{ adminKey, setAdminKey, logout }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}
