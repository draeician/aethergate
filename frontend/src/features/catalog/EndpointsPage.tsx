import { useState } from "react";
import { Plus, RefreshCw, Pencil } from "lucide-react";
import {
  api,
  apiErrorMessage,
  type Page,
  type EndpointRead,
  type EndpointCreate,
  type EndpointUpdate,
  type EndpointRuntimeRead,
  type ProviderAccountRead,
} from "../../lib/client";
import { usePolling } from "../../lib/usePolling";
import Pagination from "../../components/ui/Pagination";
import StatusBadge from "../../components/ui/StatusBadge";
import ErrorBanner from "../../components/ui/ErrorBanner";
import EmptyState from "../../components/ui/EmptyState";
import Modal from "../../components/ui/Modal";
import { Field, Select, TextInput, NumberInput, Toggle } from "../../components/ui/Form";

const PAGE_SIZE = 20;

function runtimeTone(state: string | undefined): "active" | "warning" | "inactive" {
  if (state === "active") return "active";
  if (state === "paused" || state === "draining") return "warning";
  return "inactive";
}

export default function EndpointsPage() {
  const [page, setPage] = useState(0);
  const [creating, setCreating] = useState(false);
  const [editing, setEditing] = useState<EndpointRead | null>(null);
  const [form, setForm] = useState({
    providerAccountId: "",
    name: "",
    baseDestination: "",
    maxConcurrency: "1",
    isActive: true,
  });
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);

  const list = usePolling<Page<EndpointRead>>(
    () => api.listEndpoints({ limit: PAGE_SIZE, offset: page * PAGE_SIZE }),
    60_000,
    [page],
  );
  const accounts = usePolling<ProviderAccountRead[] | null>(
    async () => (await api.listProviderAccounts({ limit: 200 })).items,
    60_000,
    [],
  );
  const runtime = usePolling<Page<EndpointRuntimeRead>>(
    () => api.queueEndpoints(),
    15_000,
    [],
  );

  const runtimeById = new Map((runtime.data?.items ?? []).map((e) => [e.endpoint_id, e]));

  const openCreate = () => {
    setForm({ providerAccountId: "", name: "", baseDestination: "", maxConcurrency: "1", isActive: true });
    setActionError(null);
    setCreating(true);
  };

  const openEdit = (endpoint: EndpointRead) => {
    setForm({
      providerAccountId: endpoint.provider_account_id,
      name: endpoint.name,
      baseDestination: endpoint.base_destination,
      maxConcurrency: String(endpoint.max_concurrency),
      isActive: endpoint.is_active,
    });
    setActionError(null);
    setEditing(endpoint);
  };

  const close = () => {
    setCreating(false);
    setEditing(null);
  };

  const onCreate = async () => {
    if (!form.name.trim() || !form.providerAccountId || !form.baseDestination.trim()) return;
    const body: EndpointCreate = {
      provider_account_id: form.providerAccountId,
      name: form.name.trim(),
      base_destination: form.baseDestination.trim(),
      max_concurrency: parseInt(form.maxConcurrency, 10) || 1,
    };
    setBusy(true);
    setActionError(null);
    try {
      await api.createEndpoint(body);
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
    const patch: EndpointUpdate = {};
    const name = form.name.trim();
    const base = form.baseDestination.trim();
    const concurrency = parseInt(form.maxConcurrency, 10);
    if (name && name !== editing.name) patch.name = name;
    if (base && base !== editing.base_destination) patch.base_destination = base;
    if (Number.isFinite(concurrency) && concurrency > 0 && concurrency !== editing.max_concurrency) {
      patch.max_concurrency = concurrency;
    }
    if (form.isActive !== editing.is_active) patch.is_active = form.isActive;
    if (Object.keys(patch).length === 0) {
      setEditing(null);
      return;
    }
    setBusy(true);
    setActionError(null);
    try {
      await api.updateEndpoint(editing.id, patch);
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
          <h2 className="text-xl font-bold">Endpoints</h2>
          <p className="text-sm text-[var(--ag-text-muted)] mt-1">
            {total.toLocaleString()} endpoint{total === 1 ? "" : "s"}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={openCreate}
            className="inline-flex items-center gap-2 text-sm px-3 py-2 rounded-lg bg-[var(--ag-accent)] hover:bg-[var(--ag-accent-hover)] text-white transition-colors"
          >
            <Plus size={16} />
            New endpoint
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

      <p className="text-xs text-[var(--ag-text-muted)]">
        <span className="font-semibold text-[var(--ag-text)]">Catalog</span> status is durable configuration;
        the <span className="font-semibold text-[var(--ag-text)]">runtime</span> column shows temporary
        scheduler state (paused/draining), which is managed from the operator dashboard, not by editing
        the catalog record.
      </p>

      <ErrorBanner message={actionError} />
      {list.error ? <p className="text-sm text-[var(--ag-danger)]">Error: {list.error}</p> : null}

      <div className="bg-[var(--ag-surface)] border border-[var(--ag-border)] rounded-xl overflow-hidden">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-[var(--ag-border)] text-left text-xs text-[var(--ag-text-muted)] uppercase tracking-wider">
              <th className="px-4 py-3">Name</th>
              <th className="px-4 py-3">Base destination</th>
              <th className="px-4 py-3">Max concurrency</th>
              <th className="px-4 py-3">Catalog</th>
              <th className="px-4 py-3">Runtime</th>
              <th className="px-4 py-3"></th>
            </tr>
          </thead>
          <tbody>
            {list.loading && !list.data ? (
              <tr>
                <td colSpan={6} className="px-4 py-6 text-[var(--ag-text-muted)]">Loading…</td>
              </tr>
            ) : (list.data?.items ?? []).length === 0 ? (
              <tr>
                <td colSpan={6}>
                  <EmptyState message="No endpoints." />
                </td>
              </tr>
            ) : (
              (list.data?.items ?? []).map((e) => {
                const rt = runtimeById.get(e.id);
                return (
                  <tr key={e.id} className="border-b border-[var(--ag-border)] last:border-0 hover:bg-[var(--ag-surface-2)]/40">
                    <td className="px-4 py-3">{e.name}</td>
                    <td className="px-4 py-3 font-mono text-xs text-[var(--ag-text-muted)] break-all">{e.base_destination}</td>
                    <td className="px-4 py-3 text-xs">{e.max_concurrency}</td>
                    <td className="px-4 py-3">
                      <StatusBadge label={e.is_active ? "Active" : "Inactive"} tone={e.is_active ? "active" : "inactive"} />
                    </td>
                    <td className="px-4 py-3">
                      {rt ? (
                        <span className="inline-flex items-center gap-2">
                          <StatusBadge label={rt.operational_state} tone={runtimeTone(rt.operational_state)} />
                          <span className="text-xs text-[var(--ag-text-muted)]">
                            {rt.occupied_slots} / {rt.max_concurrency} slots
                          </span>
                        </span>
                      ) : (
                        <span className="text-xs text-[var(--ag-text-muted)]">—</span>
                      )}
                    </td>
                    <td className="px-4 py-3 text-right">
                      <button
                        type="button"
                        onClick={() => openEdit(e)}
                        className="inline-flex items-center gap-1 text-xs px-2 py-1 rounded-md border border-[var(--ag-border)] text-[var(--ag-text-muted)] hover:text-[var(--ag-text)] transition-colors"
                      >
                        <Pencil size={13} />
                        Edit
                      </button>
                    </td>
                  </tr>
                );
              })
            )}
          </tbody>
        </table>
      </div>

      <Pagination page={page} pageSize={PAGE_SIZE} total={total} onPageChange={setPage} />

      {creating || editing ? (
        <Modal title={creating ? "New endpoint" : "Edit endpoint"} onClose={close}>
          <div className="space-y-4">
            <Field label="Provider account" htmlFor="endpoint-account">
              <Select id="endpoint-account" value={form.providerAccountId} onChange={(e) => setForm({ ...form, providerAccountId: e.target.value })}>
                <option value="">Select an account…</option>
                {(accounts.data ?? []).map((a) => (
                  <option key={a.id} value={a.id}>
                    {a.name}
                  </option>
                ))}
              </Select>
            </Field>
            <Field label="Name" htmlFor="endpoint-name">
              <TextInput id="endpoint-name" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} placeholder="Endpoint name" autoFocus={creating} />
            </Field>
            <Field label="Base destination" htmlFor="endpoint-base" hint="Validated against the backend egress destination policy.">
              <TextInput id="endpoint-base" value={form.baseDestination} onChange={(e) => setForm({ ...form, baseDestination: e.target.value })} placeholder="https://…" />
            </Field>
            <Field label="Max concurrency" htmlFor="endpoint-concurrency">
              <NumberInput id="endpoint-concurrency" min={1} step={1} value={form.maxConcurrency} onChange={(e) => setForm({ ...form, maxConcurrency: e.target.value })} />
            </Field>
            {editing ? (
              <Toggle id="endpoint-active" label="Catalog active" checked={form.isActive} onChange={(v) => setForm({ ...form, isActive: v })} />
            ) : null}
            <ErrorBanner message={actionError} />
            <div className="flex justify-end gap-3">
              <button
                type="button"
                onClick={close}
                className="text-sm px-3 py-2 rounded-lg border border-[var(--ag-border)] text-[var(--ag-text-muted)] hover:text-[var(--ag-text)] transition-colors"
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={() => void (creating ? onCreate() : onUpdate())}
                disabled={busy || !form.name.trim() || !form.providerAccountId || !form.baseDestination.trim()}
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
