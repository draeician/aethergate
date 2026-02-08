import { useState } from "react";
import { KeyRound, AlertCircle, Loader2 } from "lucide-react";
import { useAuth } from "../context/AuthContext";
import { getStats } from "../lib/api";

export default function LoginPage() {
  const { setAdminKey } = useAuth();
  const [key, setKey] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    setLoading(true);
    try {
      // Validate by hitting the stats endpoint
      await getStats(key.trim());
      setAdminKey(key.trim());
    } catch {
      setError("Invalid admin key. Check your MASTER_API_KEY.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="min-h-screen flex items-center justify-center px-4">
      <div className="w-full max-w-sm">
        {/* Brand */}
        <div className="text-center mb-8">
          <div className="inline-flex items-center justify-center w-14 h-14 rounded-2xl bg-[var(--ag-accent)]/15 mb-4">
            <KeyRound className="text-[var(--ag-accent)]" size={28} />
          </div>
          <h1 className="text-2xl font-bold tracking-tight">
            <span className="text-[var(--ag-accent)]">Aether</span>Gate
          </h1>
          <p className="text-sm text-[var(--ag-text-muted)] mt-1">
            Enter your master admin key to continue
          </p>
        </div>

        {/* Form */}
        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <input
              type="password"
              value={key}
              onChange={(e) => setKey(e.target.value)}
              placeholder="sk-admin-master-key"
              autoFocus
              className="w-full px-4 py-3 rounded-lg bg-[var(--ag-surface)] border border-[var(--ag-border)] text-[var(--ag-text)] placeholder:text-[var(--ag-text-muted)]/50 focus:outline-none focus:border-[var(--ag-accent)] focus:ring-1 focus:ring-[var(--ag-accent)] transition-colors text-sm"
            />
          </div>

          {error && (
            <div className="flex items-center gap-2 text-[var(--ag-danger)] text-sm">
              <AlertCircle size={16} />
              {error}
            </div>
          )}

          <button
            type="submit"
            disabled={!key.trim() || loading}
            className="w-full py-3 rounded-lg bg-[var(--ag-accent)] text-white font-medium text-sm hover:bg-[var(--ag-accent-hover)] disabled:opacity-50 disabled:cursor-not-allowed transition-colors flex items-center justify-center gap-2"
          >
            {loading ? <Loader2 size={18} className="animate-spin" /> : null}
            {loading ? "Verifying..." : "Authenticate"}
          </button>
        </form>
      </div>
    </div>
  );
}
