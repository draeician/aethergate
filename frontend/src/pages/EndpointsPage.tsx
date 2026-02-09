import { useEffect, useState, useCallback } from "react";
import { Globe, RefreshCw, Plus, Pencil, Trash2, X, Check } from "lucide-react";
import { useAuth } from "../context/AuthContext";
import { listEndpoints, upsertEndpoint, updateEndpoint, deleteEndpoint } from "../lib/api";
import type { LLMEndpoint } from "../lib/types";

export default function EndpointsPage() {
  const { adminKey } = useAuth();
  const [endpoints, setEndpoints] = useState<LLMEndpoint[]>([]);
  const [loading, setLoading] = useState(true);
  const [showForm, setShowForm] = useState(false);
  const [name, setName] = useState("");
  const [baseUrl, setBaseUrl] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [rpm, setRpm] = useState("");
  const [day, setDay] = useState("");
  const [formError, setFormError] = useState("");
  const [error, setError] = useState("");

  // Edit state
  const [editingId, setEditingId] = useState<number | null>(null);
  const [editName, setEditName] = useState("");
  const [editBaseUrl, setEditBaseUrl] = useState("");
  const [editApiKey, setEditApiKey] = useState("");
  const [editRpm, setEditRpm] = useState("");
  const [editDay, setEditDay] = useState("");
  const [editError, setEditError] = useState("");
  const [saving, setSaving] = useState(false);

  // Delete confirm state
  const [confirmDeleteId, setConfirmDeleteId] = useState<number | null>(null);
  const [deleting, setDeleting] = useState(false);

  const load = useCallback(async () => {
    if (!adminKey) return;
    setLoading(true);
    try {
      setEndpoints(await listEndpoints(adminKey));
    } catch (e: any) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, [adminKey]);

  useEffect(() => { load(); }, [load]);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!adminKey) return;
    setFormError("");
    try {
      await upsertEndpoint(adminKey, {
        name: name.trim(),
        base_url: baseUrl.trim(),
        api_key: apiKey.trim() || null,
        rpm_limit: rpm ? parseInt(rpm) : null,
        day_limit: day ? parseInt(day) : null,
      });
      setName(""); setBaseUrl(""); setApiKey(""); setRpm(""); setDay("");
      setShowForm(false);
      load();
    } catch (err: any) {
      setFormError(err.message);
    }
  }

  function startEdit(ep: LLMEndpoint) {
    setEditingId(ep.id);
    setEditName(ep.name);
    setEditBaseUrl(ep.base_url);
    setEditApiKey("");
    setEditRpm(ep.rpm_limit?.toString() ?? "");
    setEditDay(ep.day_limit?.toString() ?? "");
    setEditError("");
  }

  function cancelEdit() {
    setEditingId(null);
    setEditError("");
  }

  async function handleToggleActive(ep: LLMEndpoint) {
    if (!adminKey) return;
    setSaving(true);
    try {
      await updateEndpoint(adminKey, ep.id, { is_active: !ep.is_active });
      await load();
    } catch (err: any) {
      setError(err.message);
    } finally {
      setSaving(false);
    }
  }

  async function handleSaveEdit(ep: LLMEndpoint) {
    if (!adminKey) return;
    setSaving(true);
    setEditError("");
    try {
      const payload: Parameters<typeof updateEndpoint>[2] = {
        name: editName.trim(),
        base_url: editBaseUrl.trim(),
        rpm_limit: editRpm ? parseInt(editRpm) : 0,
        day_limit: editDay ? parseInt(editDay) : 0,
      };
      // Only send api_key if user typed something (avoids clearing it accidentally)
      if (editApiKey.trim()) {
        payload.api_key = editApiKey.trim();
      }
      await updateEndpoint(adminKey, ep.id, payload);
      setEditingId(null);
      await load();
    } catch (err: any) {
      setEditError(err.message);
    } finally {
      setSaving(false);
    }
  }

  async function handleDelete(id: number) {
    if (!adminKey) return;
    setDeleting(true);
    try {
      await deleteEndpoint(adminKey, id);
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
          <h2 className="text-xl font-bold">Endpoints</h2>
          <p className="text-sm text-[var(--ag-text-muted)] mt-1">Manage provider connections and global rate limits</p>
        </div>
        <div className="flex gap-2">
          <button onClick={load} className="p-2 rounded-lg border border-[var(--ag-border)] text-[var(--ag-text-muted)] hover:text-[var(--ag-text)] hover:border-[var(--ag-border-hover)] transition-colors">
            <RefreshCw size={16} />
          </button>
          <button onClick={() => setShowForm(!showForm)} className="flex items-center gap-2 px-4 py-2 rounded-lg bg-[var(--ag-accent)] text-white text-sm font-medium hover:bg-[var(--ag-accent-hover)] transition-colors">
            <Plus size={16} /> Add Endpoint
          </button>
        </div>
      </div>

      {showForm && (
        <form onSubmit={handleSubmit} className="bg-[var(--ag-surface)] border border-[var(--ag-border)] rounded-xl p-5 space-y-4">
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            <div>
              <label className="block text-xs font-medium text-[var(--ag-text-muted)] mb-1.5">Name</label>
              <input value={name} onChange={(e) => setName(e.target.value)} placeholder="OpenAI" required className="w-full px-3 py-2 rounded-lg bg-[var(--ag-bg)] border border-[var(--ag-border)] text-sm focus:outline-none focus:border-[var(--ag-accent)] focus:ring-1 focus:ring-[var(--ag-accent)] transition-colors" />
            </div>
            <div>
              <label className="block text-xs font-medium text-[var(--ag-text-muted)] mb-1.5">Base URL</label>
              <input value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)} placeholder="https://api.openai.com/v1" required className="w-full px-3 py-2 rounded-lg bg-[var(--ag-bg)] border border-[var(--ag-border)] text-sm focus:outline-none focus:border-[var(--ag-accent)] focus:ring-1 focus:ring-[var(--ag-accent)] transition-colors" />
            </div>
          </div>
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
            <div>
              <label className="block text-xs font-medium text-[var(--ag-text-muted)] mb-1.5">API Key</label>
              <input type="password" value={apiKey} onChange={(e) => setApiKey(e.target.value)} placeholder="sk-..." className="w-full px-3 py-2 rounded-lg bg-[var(--ag-bg)] border border-[var(--ag-border)] text-sm focus:outline-none focus:border-[var(--ag-accent)] focus:ring-1 focus:ring-[var(--ag-accent)] transition-colors" />
            </div>
            <div>
              <label className="block text-xs font-medium text-[var(--ag-text-muted)] mb-1.5">RPM Limit</label>
              <input type="number" min="0" value={rpm} onChange={(e) => setRpm(e.target.value)} placeholder="0 = unlimited" className="w-full px-3 py-2 rounded-lg bg-[var(--ag-bg)] border border-[var(--ag-border)] text-sm font-mono focus:outline-none focus:border-[var(--ag-accent)] focus:ring-1 focus:ring-[var(--ag-accent)] transition-colors" />
            </div>
            <div>
              <label className="block text-xs font-medium text-[var(--ag-text-muted)] mb-1.5">Daily Limit</label>
              <input type="number" min="0" value={day} onChange={(e) => setDay(e.target.value)} placeholder="0 = unlimited" className="w-full px-3 py-2 rounded-lg bg-[var(--ag-bg)] border border-[var(--ag-border)] text-sm font-mono focus:outline-none focus:border-[var(--ag-accent)] focus:ring-1 focus:ring-[var(--ag-accent)] transition-colors" />
            </div>
          </div>
          {formError && <p className="text-sm text-[var(--ag-danger)]">{formError}</p>}
          <button type="submit" className="px-4 py-2 rounded-lg bg-[var(--ag-accent)] text-white text-sm font-medium hover:bg-[var(--ag-accent-hover)] transition-colors">Save Endpoint</button>
        </form>
      )}

      {/* Delete Confirmation */}
      {confirmDeleteId !== null && (
        <div className="bg-[var(--ag-danger)]/10 border border-[var(--ag-danger)]/30 rounded-xl p-5 flex items-center justify-between">
          <div>
            <p className="text-sm font-medium text-[var(--ag-danger)]">
              Delete endpoint "{endpoints.find((ep) => ep.id === confirmDeleteId)?.name}"?
            </p>
            <p className="text-xs text-[var(--ag-text-muted)] mt-1">Models using this endpoint will be unlinked. This cannot be undone.</p>
          </div>
          <div className="flex gap-2">
            <button onClick={() => setConfirmDeleteId(null)} disabled={deleting} className="px-3 py-1.5 rounded-lg border border-[var(--ag-border)] text-sm hover:border-[var(--ag-border-hover)] transition-colors">Cancel</button>
            <button onClick={() => handleDelete(confirmDeleteId)} disabled={deleting} className="px-3 py-1.5 rounded-lg bg-[var(--ag-danger)] text-white text-sm font-medium hover:opacity-90 transition-opacity disabled:opacity-50">
              {deleting ? "Deleting..." : "Confirm Delete"}
            </button>
          </div>
        </div>
      )}

      {error && <p className="text-[var(--ag-danger)] text-sm">{error}</p>}

      {loading ? (
        <p className="text-[var(--ag-text-muted)] text-sm">Loading...</p>
      ) : endpoints.length === 0 ? (
        <div className="bg-[var(--ag-surface)] border border-[var(--ag-border)] rounded-xl p-8 text-center text-[var(--ag-text-muted)]">No endpoints configured yet.</div>
      ) : (
        <div className="bg-[var(--ag-surface)] border border-[var(--ag-border)] rounded-xl overflow-hidden">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-[var(--ag-border)] text-[var(--ag-text-muted)] text-xs uppercase tracking-wider">
                <th className="text-left px-5 py-3 font-medium">Name</th>
                <th className="text-left px-5 py-3 font-medium">Base URL</th>
                <th className="text-center px-5 py-3 font-medium">Key</th>
                <th className="text-right px-5 py-3 font-medium">RPM</th>
                <th className="text-right px-5 py-3 font-medium">Daily</th>
                <th className="text-center px-5 py-3 font-medium">Status</th>
                <th className="text-right px-5 py-3 font-medium">Actions</th>
              </tr>
            </thead>
            <tbody>
              {endpoints.map((ep) => (
                editingId === ep.id ? (
                  <tr key={ep.id} className="border-b border-[var(--ag-border)] bg-[var(--ag-accent)]/5">
                    <td className="px-5 py-2">
                      <input value={editName} onChange={(e) => setEditName(e.target.value)} className="w-full px-2 py-1 rounded bg-[var(--ag-bg)] border border-[var(--ag-border)] text-sm focus:outline-none focus:border-[var(--ag-accent)]" />
                    </td>
                    <td className="px-5 py-2">
                      <input value={editBaseUrl} onChange={(e) => setEditBaseUrl(e.target.value)} className="w-full px-2 py-1 rounded bg-[var(--ag-bg)] border border-[var(--ag-border)] text-sm font-mono focus:outline-none focus:border-[var(--ag-accent)]" />
                    </td>
                    <td className="px-5 py-2">
                      <input type="password" value={editApiKey} onChange={(e) => setEditApiKey(e.target.value)} placeholder="(unchanged)" className="w-24 px-2 py-1 rounded bg-[var(--ag-bg)] border border-[var(--ag-border)] text-sm focus:outline-none focus:border-[var(--ag-accent)]" />
                    </td>
                    <td className="px-5 py-2 text-right">
                      <input type="number" min="0" value={editRpm} onChange={(e) => setEditRpm(e.target.value)} placeholder="0" className="w-20 px-2 py-1 rounded bg-[var(--ag-bg)] border border-[var(--ag-border)] text-sm text-right font-mono focus:outline-none focus:border-[var(--ag-accent)]" />
                    </td>
                    <td className="px-5 py-2 text-right">
                      <input type="number" min="0" value={editDay} onChange={(e) => setEditDay(e.target.value)} placeholder="0" className="w-20 px-2 py-1 rounded bg-[var(--ag-bg)] border border-[var(--ag-border)] text-sm text-right font-mono focus:outline-none focus:border-[var(--ag-accent)]" />
                    </td>
                    <td className="px-5 py-3 text-center">
                      <button
                        onClick={() => handleToggleActive(ep)}
                        disabled={saving}
                        className={`inline-block px-2 py-0.5 rounded-full text-xs font-medium cursor-pointer transition-colors ${ep.is_active ? "bg-[var(--ag-success)]/15 text-[var(--ag-success)] hover:bg-[var(--ag-danger)]/15 hover:text-[var(--ag-danger)]" : "bg-[var(--ag-danger)]/15 text-[var(--ag-danger)] hover:bg-[var(--ag-success)]/15 hover:text-[var(--ag-success)]"}`}
                      >
                        {ep.is_active ? "Active" : "Inactive"}
                      </button>
                    </td>
                    <td className="px-5 py-3 text-right">
                      <div className="flex items-center justify-end gap-1">
                        {editError && <span className="text-xs text-[var(--ag-danger)] mr-2">{editError}</span>}
                        <button onClick={() => handleSaveEdit(ep)} disabled={saving} className="p-1.5 rounded-lg text-[var(--ag-success)] hover:bg-[var(--ag-success)]/10 transition-colors disabled:opacity-50" title="Save">
                          <Check size={15} />
                        </button>
                        <button onClick={cancelEdit} className="p-1.5 rounded-lg text-[var(--ag-text-muted)] hover:bg-[var(--ag-surface-2)] transition-colors" title="Cancel">
                          <X size={15} />
                        </button>
                      </div>
                    </td>
                  </tr>
                ) : (
                  <tr key={ep.id} className="border-b border-[var(--ag-border)] last:border-b-0 hover:bg-[var(--ag-surface-2)] transition-colors">
                    <td className="px-5 py-3 font-medium flex items-center gap-2"><Globe size={14} className="text-[var(--ag-accent)]" />{ep.name}</td>
                    <td className="px-5 py-3 font-mono text-xs text-[var(--ag-text-muted)]">{ep.base_url}</td>
                    <td className="px-5 py-3 text-center">{ep.has_api_key ? <span className="text-[var(--ag-success)]">Set</span> : <span className="text-[var(--ag-text-muted)]">—</span>}</td>
                    <td className="px-5 py-3 text-right font-mono">{ep.rpm_limit ?? "—"}</td>
                    <td className="px-5 py-3 text-right font-mono">{ep.day_limit ?? "—"}</td>
                    <td className="px-5 py-3 text-center">
                      <button
                        onClick={() => handleToggleActive(ep)}
                        disabled={saving}
                        className={`inline-block px-2 py-0.5 rounded-full text-xs font-medium cursor-pointer transition-colors ${ep.is_active ? "bg-[var(--ag-success)]/15 text-[var(--ag-success)] hover:bg-[var(--ag-danger)]/15 hover:text-[var(--ag-danger)]" : "bg-[var(--ag-danger)]/15 text-[var(--ag-danger)] hover:bg-[var(--ag-success)]/15 hover:text-[var(--ag-success)]"}`}
                        title={ep.is_active ? "Click to deactivate" : "Click to activate"}
                      >
                        {ep.is_active ? "Active" : "Inactive"}
                      </button>
                    </td>
                    <td className="px-5 py-3 text-right">
                      <div className="flex items-center justify-end gap-1">
                        <button onClick={() => startEdit(ep)} className="p-1.5 rounded-lg text-[var(--ag-text-muted)] hover:text-[var(--ag-text)] hover:bg-[var(--ag-surface-2)] transition-colors" title="Edit">
                          <Pencil size={14} />
                        </button>
                        <button onClick={() => setConfirmDeleteId(ep.id)} className="p-1.5 rounded-lg text-[var(--ag-text-muted)] hover:text-[var(--ag-danger)] hover:bg-[var(--ag-danger)]/10 transition-colors" title="Delete">
                          <Trash2 size={14} />
                        </button>
                      </div>
                    </td>
                  </tr>
                )
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
