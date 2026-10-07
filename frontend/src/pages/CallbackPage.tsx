import { useEffect } from "react";
import { useNavigate } from "react-router-dom";
import { Loader2, AlertTriangle } from "lucide-react";
import { useAuth } from "../context/auth-context";

/**
 * OIDC callback completion page.
 *
 * The backend completes the Authorization Code + PKCE flow, sets the HttpOnly
 * session cookie plus a JS-readable CSRF cookie, and 302-redirects here. This
 * page re-resolves the session; on success it lands on the console, on failure
 * it renders a fixed failure state. No CSRF/session/OIDC secret is ever read
 * into React state or rendered.
 */
export default function CallbackPage() {
  const { status, refreshSession } = useAuth();
  const navigate = useNavigate();

  useEffect(() => {
    void refreshSession();
  }, [refreshSession]);

  useEffect(() => {
    if (status === "authenticated") {
      navigate("/", { replace: true });
    }
  }, [status, navigate]);

  if (status === "authenticated" || status === "loading") {
    return (
      <div className="min-h-screen flex items-center justify-center px-4">
        <div className="text-center">
          <Loader2 size={28} className="animate-spin text-[var(--ag-accent)] mx-auto" />
          <p className="text-sm text-[var(--ag-text-muted)] mt-3">Completing sign-in…</p>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen flex items-center justify-center px-4">
      <div className="w-full max-w-sm text-center">
        <AlertTriangle size={28} className="text-[var(--ag-warning)] mx-auto" />
        <h1 className="text-xl font-bold mt-4">Sign-in failed</h1>
        <p className="text-sm text-[var(--ag-text-muted)] mt-2">
          We could not complete your sign-in. Please try again.
        </p>
        <a
          href="/login"
          className="mt-6 inline-block w-full py-3 rounded-lg bg-[var(--ag-accent)] text-white font-medium text-sm hover:bg-[var(--ag-accent-hover)] transition-colors"
        >
          Back to sign-in
        </a>
      </div>
    </div>
  );
}
