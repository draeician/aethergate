import { useEffect, useState, useCallback } from "react";
import { UserPlus, RefreshCw, Pencil, Trash2, X, Check } from "lucide-react";
import { useAuth } from "../context/AuthContext";
import { listUsers, createUser, updateUser, deleteUser } from "../lib/api";
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

  // Edit state
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editBalance, setEditBalance] = useState("");
  const [editOrg, setEditOrg] = useState("");
  const [editError, setEditError] = useState("");
  const [saving, setSaving] = useState(false);

  // Delete confirm state
  const [confirmDeleteId, setConfirmDeleteId] = useState<string | null>(null);
  const [deleting, setDeleting] = useState(false);

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

  function startEdit(u: User) {
    setEditingId(u.id);
    setEditBalance(u.balance.toString());
    setEditOrg(u.organization ?? "");
    setEditError("");
  }

  function cancelEdit() {
    setEditingId(null);
    setEditError("");
  }

  async function handleToggleActive(u: User) {
    if (!adminKey) return;
    setSaving(true);
    try {
      await updateUser(adminKey, u.id, { is_active: !u.is_active });
      await load();
    } catch (err: any) {
      setError(err.message);
    } finally {
      setSaving(false);
    }
  }

  async function handleSaveEdit(u: User) {
    if (!adminKey) return;
    setSaving(true);
    setEditError("");
    try {
      await updateUser(adminKey, u.id, {
        balance: parseFloat(editBalance),
        organization: editOrg.trim() || null,
      });
      setEditingId(null);
      await load();
    } catch (err: any) {
      setEditError(err.message);
    } finally {
      setSaving(false);
    }
  }

  async function handleDelete(userId: string) {
    if (!adminKey) return;
    setDeleting(true);
    try {
      await deleteUser(adminKey, userId);
      setConfirmDeleteId(null);
      await load();
    } catch (err: any) {
      setError(err.message);
      setConfirmDeleteId(null);
    } finally {
      setDeleting(false);
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

      {/* Delete Confirmation */}
      {confirmDeleteId && (
        <div className="bg-[var(--ag-danger)]/10 border border-[var(--ag-danger)]/30 rounded-xl p-5 flex items-center justify-between">
          <div>
            <p className="text-sm font-medium text-[var(--ag-danger)]">
              Delete user "{users.find((u) => u.id === confirmDeleteId)?.username}"?
            </p>
            <p className="text-xs text-[var(--ag-text-muted)] mt-1">This will also remove all their API keys. Request logs are preserved.</p>
          </div>
          <div className="flex gap-2">
            <button
              onClick={() => setConfirmDeleteId(null)}
              disabled={deleting}
              className="px-3 py-1.5 rounded-lg border border-[var(--ag-border)] text-sm hover:border-[var(--ag-border-hover)] transition-colors"
            >
              Cancel
            </button>
            <button
              onClick={() => handleDelete(confirmDeleteId)}
              disabled={deleting}
              className="px-3 py-1.5 rounded-lg bg-[var(--ag-danger)] text-white text-sm font-medium hover:opacity-90 transition-opacity disabled:opacity-50"
            >
              {deleting ? "Deleting..." : "Confirm Delete"}
            </button>
          </div>
        </div>
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
                <th className="text-right px-5 py-3 font-medium">Actions</th>
              </tr>
            </thead>
            <tbody>
              {users.map((u) => (
                editingId === u.id ? (
                  /* Inline edit row */
                  <tr key={u.id} className="border-b border-[var(--ag-border)] bg-[var(--ag-accent)]/5">
                    <td className="px-5 py-3 font-medium">{u.username}</td>
                    <td className="px-5 py-2 text-right">
                      <input
                        type="number"
                        step="0.01"
                        value={editBalance}
                        onChange={(e) => setEditBalance(e.target.value)}
                        className="w-28 px-2 py-1 rounded bg-[var(--ag-bg)] border border-[var(--ag-border)] text-sm text-right font-mono focus:outline-none focus:border-[var(--ag-accent)]"
                      />
                    </td>
                    <td className="px-5 py-3 text-center">
                      <button
                        onClick={() => handleToggleActive(u)}
                        disabled={saving}
                        className={`inline-block px-2 py-0.5 rounded-full text-xs font-medium cursor-pointer transition-colors ${u.is_active ? "bg-[var(--ag-success)]/15 text-[var(--ag-success)] hover:bg-[var(--ag-danger)]/15 hover:text-[var(--ag-danger)]" : "bg-[var(--ag-danger)]/15 text-[var(--ag-danger)] hover:bg-[var(--ag-success)]/15 hover:text-[var(--ag-success)]"}`}
                      >
                        {u.is_active ? "Active" : "Inactive"}
                      </button>
                    </td>
                    <td className="px-5 py-2">
                      <input
                        value={editOrg}
                        onChange={(e) => setEditOrg(e.target.value)}
                        placeholder="—"
                        className="w-full px-2 py-1 rounded bg-[var(--ag-bg)] border border-[var(--ag-border)] text-sm focus:outline-none focus:border-[var(--ag-accent)]"
                      />
                    </td>
                    <td className="px-5 py-3 text-[var(--ag-text-muted)]">
                      {new Date(u.created_at).toLocaleDateString()}
                    </td>
                    <td className="px-5 py-3 text-right">
                      <div className="flex items-center justify-end gap-1">
                        {editError && <span className="text-xs text-[var(--ag-danger)] mr-2">{editError}</span>}
                        <button
                          onClick={() => handleSaveEdit(u)}
                          disabled={saving}
                          className="p-1.5 rounded-lg text-[var(--ag-success)] hover:bg-[var(--ag-success)]/10 transition-colors disabled:opacity-50"
                          title="Save"
                        >
                          <Check size={15} />
                        </button>
                        <button
                          onClick={cancelEdit}
                          className="p-1.5 rounded-lg text-[var(--ag-text-muted)] hover:bg-[var(--ag-surface-2)] transition-colors"
                          title="Cancel"
                        >
                          <X size={15} />
                        </button>
                      </div>
                    </td>
                  </tr>
                ) : (
                  /* Normal row */
                  <tr key={u.id} className="border-b border-[var(--ag-border)] last:border-b-0 hover:bg-[var(--ag-surface-2)] transition-colors">
                    <td className="px-5 py-3 font-medium">{u.username}</td>
                    <td className={`px-5 py-3 text-right font-mono ${u.balance > 0 ? "text-[var(--ag-success)]" : "text-[var(--ag-danger)]"}`}>
                      ${u.balance.toFixed(4)}
                    </td>
                    <td className="px-5 py-3 text-center">
                      <button
                        onClick={() => handleToggleActive(u)}
                        disabled={saving}
                        className={`inline-block px-2 py-0.5 rounded-full text-xs font-medium cursor-pointer transition-colors ${u.is_active ? "bg-[var(--ag-success)]/15 text-[var(--ag-success)] hover:bg-[var(--ag-danger)]/15 hover:text-[var(--ag-danger)]" : "bg-[var(--ag-danger)]/15 text-[var(--ag-danger)] hover:bg-[var(--ag-success)]/15 hover:text-[var(--ag-success)]"}`}
                        title={u.is_active ? "Click to deactivate" : "Click to activate"}
                      >
                        {u.is_active ? "Active" : "Inactive"}
                      </button>
                    </td>
                    <td className="px-5 py-3 text-[var(--ag-text-muted)]">{u.organization ?? "—"}</td>
                    <td className="px-5 py-3 text-[var(--ag-text-muted)]">
                      {new Date(u.created_at).toLocaleDateString()}
                    </td>
                    <td className="px-5 py-3 text-right">
                      <div className="flex items-center justify-end gap-1">
                        <button
                          onClick={() => startEdit(u)}
                          className="p-1.5 rounded-lg text-[var(--ag-text-muted)] hover:text-[var(--ag-text)] hover:bg-[var(--ag-surface-2)] transition-colors"
                          title="Edit"
                        >
                          <Pencil size={14} />
                        </button>
                        <button
                          onClick={() => setConfirmDeleteId(u.id)}
                          className="p-1.5 rounded-lg text-[var(--ag-text-muted)] hover:text-[var(--ag-danger)] hover:bg-[var(--ag-danger)]/10 transition-colors"
                          title="Delete"
                        >
                          <Trash2 size={14} />
                        </button>
                      </div>
                    </td>
                  </tr>
                )
              ))}
              {users.length === 0 && (
                <tr>
                  <td colSpan={6} className="px-5 py-8 text-center text-[var(--ag-text-muted)]">
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
