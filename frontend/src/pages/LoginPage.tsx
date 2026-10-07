import { ShieldCheck, LogIn } from "lucide-react";

export default function LoginPage() {
  return (
    <div className="min-h-screen flex items-center justify-center px-4">
      <div className="w-full max-w-sm text-center">
        <div className="inline-flex items-center justify-center w-14 h-14 rounded-2xl bg-[var(--ag-accent)]/15 mb-4">
          <ShieldCheck className="text-[var(--ag-accent)]" size={28} />
        </div>
        <h1 className="text-2xl font-bold tracking-tight">
          <span className="text-[var(--ag-accent)]">Aether</span>Gate
        </h1>
        <p className="text-sm text-[var(--ag-text-muted)] mt-1">
          Operator console
        </p>
        <p className="text-sm text-[var(--ag-text-muted)] mt-6 text-left">
          Sign in with your company identity provider to continue. Your session is
          managed by the server and no credentials are stored in this browser.
        </p>
        <a
          href="/admin/v1/auth/oidc/login"
          className="mt-6 w-full py-3 rounded-lg bg-[var(--ag-accent)] text-white font-medium text-sm hover:bg-[var(--ag-accent-hover)] transition-colors flex items-center justify-center gap-2"
        >
          <LogIn size={18} />
          Sign in with OIDC
        </a>
      </div>
    </div>
  );
}
