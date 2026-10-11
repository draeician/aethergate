import { useState } from "react";
import { Plus, RefreshCw, Pencil } from "lucide-react";
import {
  api,
  apiErrorMessage,
  type Page,
  type ProviderRead,
  type ProviderCreate,
  type ProviderUpdate,
  type Capability,
} from "../../lib/client";
import { usePolling } from "../../lib/usePolling";
import Pagination from "../../components/ui/Pagination";
import StatusBadge from "../../components/ui/StatusBadge";
import ErrorBanner from "../../components/ui/ErrorBanner";
import EmptyState from "../../components/ui/EmptyState";
import Modal from "../../components/ui/Modal";
import { Field, TextInput, Toggle } from "../../components/ui/Form";

const PAGE_SIZE = 20;

const CAPABILITIES: Capability[] = ["text", "image", "audio_stt", "audio_tts", "embedding"];

export default function ProvidersPage() {
  const [page, setPage] = useState(0);
  const [editing, setEditing] = useState<ProviderRead | null>(null);
  const [creating, setCreating] = useState(false);
  const [form, setForm] = useState({ kind: "", name: "", isActive: true, capabilities: [] as Capability[] });
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);

  const list = usePolling<Page<ProviderRead>>(
    () => api.listProviders({ limit: PAGE_SIZE, offset: page * PAGE_SIZE }),
    60_000,
    [page],
  );

  const resetForm = () => {
    setForm({ kind: "", name: "", isActive: true, capabilities: [] });
    setActionError(null);
  };

  const openCreate = () => {
    resetForm();
    setCreating(true);
  };

  const openEdit = (provider: ProviderRead) => {
    setForm({
      kind: provider.kind,
      name: provider.name,
      isActive: provider.is_active,
      capabilities: provider.capabilities,
    });
    setActionError(null);
    setEditing(provider);
  };

  const toggleCapability = (cap: Capability) => {
    setForm((f) => ({
      ...f,
      capabilities: f.capabilities.includes(cap)
        ? f.capabilities.filter((c) => c !== cap)
        : [...f.capabilities, cap],
    }));
  };

  const onCreate = async () => {
    if (!form.name.trim() || !form.kind.trim()) return;
    const body: ProviderCreate = {
      kind: form.kind.trim(),
      name: form.name.trim(),
      capabilities: form.capabilities,
      is_active: form.isActive,
    };
    setBusy(true);
    setActionError(null);
    try {
      await api.createProvider(body);
      setCreating(false);
      list.refresh();
    } catch (err) {
      setActionError(apiErrorMessage(err));
    } finally {
      setBusy(false);
    }
  };

  const onUpdate = async () => {
    if (!editing) return;
    const patch: ProviderUpdate = {};
    const kind = form.kind.trim();
    const name = form.name.trim();
    if (kind && kind !== editing.kind) patch.kind = kind;
    if (name && name !== editing.name) patch.name = name;
    if (form.isActive !== editing.is_active) patch.is_active = form.isActive;
    if (JSON.stringify(form.capabilities) !== JSON.stringify(editing.capabilities)) {
      patch.capabilities = form.capabilities;
    }
    if (Object.keys(patch).length === 0) {
      setEditing(null);
      return;
    }
    setBusy(true);
    setActionError(null);
    try {
      await api.updateProvider(editing.id, patch);
      setEditing(null);
      list.refresh();
    } catch (err) {
      setActionError(apiErrorMessage(err));
    } finally {
      setBusy(false);
    }
  };

  const total = list.data?.total ?? 0;

  return (
    <div className="space-y-6 max-w-6xl">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-xl font-bold">Providers</h2>
          <p className="text-sm text-[var(--ag-text-muted)] mt-1">
            {total.toLocaleString()} provider{total === 1 ? "" : "s"}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={openCreate}
            className="inline-flex items-center gap-2 text-sm px-3 py-2 rounded-lg bg-[var(--ag-accent)] hover:bg-[var(--ag-accent-hover)] text-white transition-colors"
          >
            <Plus size={16} />
            New provider
          </button>
          <button
            type="button"
            onClick={list.refresh}
            className="inline-flex items-center gap-2 text-sm px-3 py-2 rounded-lg border border-[var(--ag-border)] text-[var(--ag-text-muted)] hover:text-[var(--ag-text)] hover:bg-[var(--ag-surface-2)] transition-colors"
          >
            <RefreshCw size={14} />
            Refresh
          </button>
        </div>
      </div>

      <ErrorBanner message={actionError} />
      {list.error ? <p className="text-sm text-[var(--ag-danger)]">Error: {list.error}</p> : null}

      <div className="bg-[var(--ag-surface)] border border-[var(--ag-border)] rounded-xl overflow-hidden">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-[var(--ag-border)] text-left text-xs text-[var(--ag-text-muted)] uppercase tracking-wider">
              <th className="px-4 py-3">Name</th>
              <th className="px-4 py-3">Kind</th>
              <th className="px-4 py-3">Capabilities</th>
              <th className="px-4 py-3">Status</th>
              <th className="px-4 py-3"></th>
            </tr>
          </thead>
          <tbody>
            {list.loading && !list.data ? (
              <tr>
                <td colSpan={5} className="px-4 py-6 text-[var(--ag-text-muted)]">Loading…</td>
              </tr>
            ) : (list.data?.items ?? []).length === 0 ? (
              <tr>
                <td colSpan={5}>
                  <EmptyState message="No providers." />
                </td>
              </tr>
            ) : (
              (list.data?.items ?? []).map((p) => (
                <tr key={p.id} className="border-b border-[var(--ag-border)] last:border-0 hover:bg-[var(--ag-surface-2)]/40">
                  <td className="px-4 py-3">{p.name}</td>
                  <td className="px-4 py-3 text-xs">{p.kind}</td>
                  <td className="px-4 py-3 text-xs">{(p.capabilities ?? []).join(", ") || "—"}</td>
                  <td className="px-4 py-3">
                    <StatusBadge label={p.is_active ? "Active" : "Inactive"} tone={p.is_active ? "active" : "inactive"} />
                  </td>
                  <td className="px-4 py-3 text-right">
                    <button
                      type="button"
                      onClick={() => openEdit(p)}
                      className="inline-flex items-center gap-1 text-xs px-2 py-1 rounded-md border border-[var(--ag-border)] text-[var(--ag-text-muted)] hover:text-[var(--ag-text)] transition-colors"
                    >
                      <Pencil size={13} />
                      Edit
                    </button>
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>

      <Pagination page={page} pageSize={PAGE_SIZE} total={total} onPageChange={setPage} />

      {creating || editing ? (
        <Modal title={creating ? "New provider" : "Edit provider"} onClose={() => (creating ? setCreating(false) : setEditing(null))}>
          <div className="space-y-4">
            <Field label="Kind" htmlFor="provider-kind">
              <TextInput id="provider-kind" value={form.kind} onChange={(e) => setForm({ ...form, kind: e.target.value })} placeholder="e.g. openai, ollama" autoFocus={creating} />
            </Field>
            <Field label="Name" htmlFor="provider-name">
              <TextInput id="provider-name" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} placeholder="Provider name" />
            </Field>
            <fieldset className="space-y-2">
              <legend className="text-xs text-[var(--ag-text-muted)] uppercase tracking-wider">Capabilities</legend>
              {CAPABILITIES.map((cap) => (
                <label key={cap} className="flex items-center gap-2 text-sm text-[var(--ag-text)] cursor-pointer select-none">
                  <input
                    type="checkbox"
                    checked={form.capabilities.includes(cap)}
                    onChange={() => toggleCapability(cap)}
                    className="h-4 w-4 accent-[var(--ag-accent)]"
                  />
                  {cap}
                </label>
              ))}
            </fieldset>
            <Toggle id="provider-active" label="Active" checked={form.isActive} onChange={(v) => setForm({ ...form, isActive: v })} />
            <ErrorBanner message={actionError} />
            <div className="flex justify-end gap-3">
              <button
                type="button"
                onClick={() => (creating ? setCreating(false) : setEditing(null))}
                className="text-sm px-3 py-2 rounded-lg border border-[var(--ag-border)] text-[var(--ag-text-muted)] hover:text-[var(--ag-text)] transition-colors"
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={() => void (creating ? onCreate() : onUpdate())}
                disabled={busy || !form.name.trim() || !form.kind.trim()}
                className="text-sm px-3 py-2 rounded-lg bg-[var(--ag-accent)] hover:bg-[var(--ag-accent-hover)] text-white disabled:opacity-50 transition-colors"
              >
                {busy ? "Saving…" : creating ? "Create" : "Save changes"}
              </button>
            </div>
          </div>
        </Modal>
      ) : null}
    </div>
  );
}
