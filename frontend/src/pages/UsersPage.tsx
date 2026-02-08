import { useEffect, useState, useCallback } from "react";
import { UserPlus, RefreshCw } from "lucide-react";
import { useAuth } from "../context/AuthContext";
import { listUsers, createUser } from "../lib/api";
import type { User } from "../lib/types";

export default function UsersPage() {
  const { adminKey } = useAuth();
  const [users, setUsers] = useState<User[]>([]);
  const [loading, setLoading] = useState(true);
  const [showForm, setShowForm] = useState(false);
  const [username, setUsername] = useState("");
  const [balance, setBalance] = useState("10.00");
  const [error, setError] = useState("");
  const [formError, setFormError] = useState("");

  const load = useCallback(async () => {
    if (!adminKey) return;
    setLoading(true);
    try {
      setUsers(await listUsers(adminKey));
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
      await createUser(adminKey, { username: username.trim(), balance: parseFloat(balance) });
      setUsername("");
      setBalance("10.00");
      setShowForm(false);
      load();
    } catch (err: any) {
      setFormError(err.message);
    }
  }

  return (
    <div className="space-y-6 max-w-5xl">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-xl font-bold">Users</h2>
          <p className="text-sm text-[var(--ag-text-muted)] mt-1">Manage registered users</p>
        </div>
        <div className="flex gap-2">
          <button
            onClick={load}
            className="p-2 rounded-lg border border-[var(--ag-border)] text-[var(--ag-text-muted)] hover:text-[var(--ag-text)] hover:border-[var(--ag-border-hover)] transition-colors"
          >
            <RefreshCw size={16} />
          </button>
          <button
            onClick={() => setShowForm(!showForm)}
            className="flex items-center gap-2 px-4 py-2 rounded-lg bg-[var(--ag-accent)] text-white text-sm font-medium hover:bg-[var(--ag-accent-hover)] transition-colors"
          >
            <UserPlus size={16} />
            New User
          </button>
        </div>
      </div>

      {/* Create Form */}
      {showForm && (
        <form onSubmit={handleCreate} className="bg-[var(--ag-surface)] border border-[var(--ag-border)] rounded-xl p-5 space-y-4">
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            <div>
              <label className="block text-xs font-medium text-[var(--ag-text-muted)] mb-1.5">Username</label>
              <input
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                placeholder="client_name"
                required
                className="w-full px-3 py-2 rounded-lg bg-[var(--ag-bg)] border border-[var(--ag-border)] text-sm focus:outline-none focus:border-[var(--ag-accent)] focus:ring-1 focus:ring-[var(--ag-accent)] transition-colors"
              />
            </div>
            <div>
              <label className="block text-xs font-medium text-[var(--ag-text-muted)] mb-1.5">Initial Balance ($)</label>
              <input
                type="number"
                step="0.01"
                min="0"
                value={balance}
                onChange={(e) => setBalance(e.target.value)}
                className="w-full px-3 py-2 rounded-lg bg-[var(--ag-bg)] border border-[var(--ag-border)] text-sm focus:outline-none focus:border-[var(--ag-accent)] focus:ring-1 focus:ring-[var(--ag-accent)] transition-colors"
              />
            </div>
          </div>
          {formError && <p className="text-sm text-[var(--ag-danger)]">{formError}</p>}
          <button
            type="submit"
            className="px-4 py-2 rounded-lg bg-[var(--ag-accent)] text-white text-sm font-medium hover:bg-[var(--ag-accent-hover)] transition-colors"
          >
            Create User
          </button>
        </form>
      )}

      {/* Error */}
      {error && <p className="text-[var(--ag-danger)] text-sm">{error}</p>}

      {/* Table */}
      {loading ? (
        <p className="text-[var(--ag-text-muted)] text-sm">Loading...</p>
      ) : (
        <div className="bg-[var(--ag-surface)] border border-[var(--ag-border)] rounded-xl overflow-hidden">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-[var(--ag-border)] text-[var(--ag-text-muted)] text-xs uppercase tracking-wider">
                <th className="text-left px-5 py-3 font-medium">Username</th>
                <th className="text-right px-5 py-3 font-medium">Balance</th>
                <th className="text-center px-5 py-3 font-medium">Status</th>
                <th className="text-left px-5 py-3 font-medium">Organization</th>
                <th className="text-left px-5 py-3 font-medium">Created</th>
              </tr>
            </thead>
            <tbody>
              {users.map((u) => (
                <tr key={u.id} className="border-b border-[var(--ag-border)] last:border-b-0 hover:bg-[var(--ag-surface-2)] transition-colors">
                  <td className="px-5 py-3 font-medium">{u.username}</td>
                  <td className={`px-5 py-3 text-right font-mono ${u.balance > 0 ? "text-[var(--ag-success)]" : "text-[var(--ag-danger)]"}`}>
                    ${u.balance.toFixed(4)}
                  </td>
                  <td className="px-5 py-3 text-center">
                    <span className={`inline-block px-2 py-0.5 rounded-full text-xs font-medium ${u.is_active ? "bg-[var(--ag-success)]/15 text-[var(--ag-success)]" : "bg-[var(--ag-danger)]/15 text-[var(--ag-danger)]"}`}>
                      {u.is_active ? "Active" : "Inactive"}
                    </span>
                  </td>
                  <td className="px-5 py-3 text-[var(--ag-text-muted)]">{u.organization ?? "—"}</td>
                  <td className="px-5 py-3 text-[var(--ag-text-muted)]">
                    {new Date(u.created_at).toLocaleDateString()}
                  </td>
                </tr>
              ))}
              {users.length === 0 && (
                <tr>
                  <td colSpan={5} className="px-5 py-8 text-center text-[var(--ag-text-muted)]">
                    No users yet. Create one above.
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
