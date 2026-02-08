import { useEffect, useState, useCallback } from "react";
import { KeyRound, RefreshCw, Copy, Check } from "lucide-react";
import { useAuth } from "../context/AuthContext";
import { listKeys, createKey, listUsers } from "../lib/api";
import type { APIKeyInfo, GeneratedKey, User } from "../lib/types";

export default function KeysPage() {
  const { adminKey } = useAuth();
  const [keys, setKeys] = useState<APIKeyInfo[]>([]);
  const [users, setUsers] = useState<User[]>([]);
  const [loading, setLoading] = useState(true);
  const [showForm, setShowForm] = useState(false);
  const [username, setUsername] = useState("");
  const [keyName, setKeyName] = useState("default");
  const [rateLimit, setRateLimit] = useState("");
  const [formError, setFormError] = useState("");
  const [newKey, setNewKey] = useState<GeneratedKey | null>(null);
  const [copied, setCopied] = useState(false);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    if (!adminKey) return;
    setLoading(true);
    try {
      const [k, u] = await Promise.all([listKeys(adminKey), listUsers(adminKey)]);
      setKeys(k);
      setUsers(u);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, [adminKey]);

  useEffect(() => { load(); }, [load]);

  async function handleCreate(e: React.FormEvent) {
    e.preventDefault();
    if (!adminKey) return;
    setFormError("");
    try {
      const result = await createKey(adminKey, {
        username: username.trim(),
        name: keyName.trim() || "default",
        rate_limit: rateLimit.trim() || undefined,
      });
      setNewKey(result);
      setUsername("");
      setKeyName("default");
      setRateLimit("");
      load();
    } catch (err: any) {
      setFormError(err.message);
    }
  }

  function copyKey() {
    if (!newKey) return;
    navigator.clipboard.writeText(newKey.key);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  }

  return (
    <div className="space-y-6 max-w-5xl">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-xl font-bold">API Keys</h2>
          <p className="text-sm text-[var(--ag-text-muted)] mt-1">Generate and manage access keys</p>
        </div>
        <div className="flex gap-2">
          <button onClick={load} className="p-2 rounded-lg border border-[var(--ag-border)] text-[var(--ag-text-muted)] hover:text-[var(--ag-text)] hover:border-[var(--ag-border-hover)] transition-colors">
            <RefreshCw size={16} />
          </button>
          <button
            onClick={() => { setShowForm(!showForm); setNewKey(null); }}
            className="flex items-center gap-2 px-4 py-2 rounded-lg bg-[var(--ag-accent)] text-white text-sm font-medium hover:bg-[var(--ag-accent-hover)] transition-colors"
          >
            <KeyRound size={16} />
            Generate Key
          </button>
        </div>
      </div>

      {/* Generated Key Banner */}
      {newKey && (
        <div className="bg-[var(--ag-success)]/10 border border-[var(--ag-success)]/30 rounded-xl p-5 space-y-2">
          <p className="text-sm font-medium text-[var(--ag-success)]">Key generated for {newKey.user}. Save it now — it won't be shown again.</p>
          <div className="flex items-center gap-2">
            <code className="flex-1 px-3 py-2 rounded-lg bg-[var(--ag-bg)] text-sm font-mono text-[var(--ag-text)] select-all break-all">
              {newKey.key}
            </code>
            <button onClick={copyKey} className="p-2 rounded-lg border border-[var(--ag-border)] hover:border-[var(--ag-border-hover)] transition-colors">
              {copied ? <Check size={16} className="text-[var(--ag-success)]" /> : <Copy size={16} className="text-[var(--ag-text-muted)]" />}
            </button>
          </div>
        </div>
      )}

      {/* Create Form */}
      {showForm && (
        <form onSubmit={handleCreate} className="bg-[var(--ag-surface)] border border-[var(--ag-border)] rounded-xl p-5 space-y-4">
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
            <div>
              <label className="block text-xs font-medium text-[var(--ag-text-muted)] mb-1.5">User</label>
              <select
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                required
                className="w-full px-3 py-2 rounded-lg bg-[var(--ag-bg)] border border-[var(--ag-border)] text-sm focus:outline-none focus:border-[var(--ag-accent)] focus:ring-1 focus:ring-[var(--ag-accent)] transition-colors"
              >
                <option value="">Select user...</option>
                {users.map((u) => (
                  <option key={u.id} value={u.username}>{u.username}</option>
                ))}
              </select>
            </div>
            <div>
              <label className="block text-xs font-medium text-[var(--ag-text-muted)] mb-1.5">Key Name</label>
              <input
                value={keyName}
                onChange={(e) => setKeyName(e.target.value)}
                placeholder="default"
                className="w-full px-3 py-2 rounded-lg bg-[var(--ag-bg)] border border-[var(--ag-border)] text-sm focus:outline-none focus:border-[var(--ag-accent)] focus:ring-1 focus:ring-[var(--ag-accent)] transition-colors"
              />
            </div>
            <div>
              <label className="block text-xs font-medium text-[var(--ag-text-muted)] mb-1.5">Rate Limit</label>
              <input
                value={rateLimit}
                onChange={(e) => setRateLimit(e.target.value)}
                placeholder="60/m (optional)"
                className="w-full px-3 py-2 rounded-lg bg-[var(--ag-bg)] border border-[var(--ag-border)] text-sm focus:outline-none focus:border-[var(--ag-accent)] focus:ring-1 focus:ring-[var(--ag-accent)] transition-colors"
              />
            </div>
          </div>
          {formError && <p className="text-sm text-[var(--ag-danger)]">{formError}</p>}
          <button type="submit" className="px-4 py-2 rounded-lg bg-[var(--ag-accent)] text-white text-sm font-medium hover:bg-[var(--ag-accent-hover)] transition-colors">
            Generate
          </button>
        </form>
      )}

      {error && <p className="text-[var(--ag-danger)] text-sm">{error}</p>}

      {/* Table */}
      {loading ? (
        <p className="text-[var(--ag-text-muted)] text-sm">Loading...</p>
      ) : (
        <div className="bg-[var(--ag-surface)] border border-[var(--ag-border)] rounded-xl overflow-hidden">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-[var(--ag-border)] text-[var(--ag-text-muted)] text-xs uppercase tracking-wider">
                <th className="text-left px-5 py-3 font-medium">Prefix</th>
                <th className="text-left px-5 py-3 font-medium">Name</th>
                <th className="text-left px-5 py-3 font-medium">User</th>
                <th className="text-center px-5 py-3 font-medium">Status</th>
                <th className="text-left px-5 py-3 font-medium">Rate Limit</th>
              </tr>
            </thead>
            <tbody>
              {keys.map((k) => (
                <tr key={k.id} className="border-b border-[var(--ag-border)] last:border-b-0 hover:bg-[var(--ag-surface-2)] transition-colors">
                  <td className="px-5 py-3 font-mono text-[var(--ag-accent)]">{k.key_prefix}...</td>
                  <td className="px-5 py-3">{k.name}</td>
                  <td className="px-5 py-3">{k.username}</td>
                  <td className="px-5 py-3 text-center">
                    <span className={`inline-block px-2 py-0.5 rounded-full text-xs font-medium ${k.is_active ? "bg-[var(--ag-success)]/15 text-[var(--ag-success)]" : "bg-[var(--ag-danger)]/15 text-[var(--ag-danger)]"}`}>
                      {k.is_active ? "Active" : "Revoked"}
                    </span>
                  </td>
                  <td className="px-5 py-3 text-[var(--ag-text-muted)] font-mono">{k.rate_limit ?? "—"}</td>
                </tr>
              ))}
              {keys.length === 0 && (
                <tr>
                  <td colSpan={5} className="px-5 py-8 text-center text-[var(--ag-text-muted)]">
                    No keys generated yet.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
