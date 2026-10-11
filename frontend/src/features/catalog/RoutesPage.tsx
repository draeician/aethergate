import { useState } from "react";
import { Plus, RefreshCw, Pencil } from "lucide-react";
import {
  api,
  apiErrorMessage,
  type Page,
  type RouteBindingRead,
  type RouteBindingCreate,
  type RouteBindingUpdate,
  type ModelAliasRead,
  type ProviderAccountRead,
  type EndpointRead,
  type QuotaGroupRead,
} from "../../lib/client";
import { usePolling } from "../../lib/usePolling";
import Pagination from "../../components/ui/Pagination";
import StatusBadge from "../../components/ui/StatusBadge";
import ErrorBanner from "../../components/ui/ErrorBanner";
import EmptyState from "../../components/ui/EmptyState";
import Modal from "../../components/ui/Modal";
import { Field, Select, TextInput, NumberInput, Toggle } from "../../components/ui/Form";

const PAGE_SIZE = 20;

interface RouteForm {
  id: string | null;
  modelAliasId: string;
  providerAccountId: string;
  endpointId: string;
  upstreamModel: string;
  quotaGroupId: string;
  defaultOutputTokens: string;
  isActive: boolean;
}

const emptyForm: RouteForm = {
  id: null,
  modelAliasId: "",
  providerAccountId: "",
  endpointId: "",
  upstreamModel: "",
  quotaGroupId: "",
  defaultOutputTokens: "",
  isActive: true,
};

export default function RoutesPage() {
  const [page, setPage] = useState(0);
  const [modalOpen, setModalOpen] = useState(false);
  const [form, setForm] = useState<RouteForm>(emptyForm);
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);

  const list = usePolling<Page<RouteBindingRead>>(
    () => api.listRouteBindings({ limit: PAGE_SIZE, offset: page * PAGE_SIZE }),
    60_000,
    [page],
  );
  const aliases = usePolling<ModelAliasRead[] | null>(
    async () => (await api.listModelAliases({ limit: 200 })).items,
    60_000,
    [],
  );
  const accounts = usePolling<ProviderAccountRead[] | null>(
    async () => (await api.listProviderAccounts({ limit: 200 })).items,
    60_000,
    [],
  );
  const endpoints = usePolling<EndpointRead[] | null>(
    async () => (await api.listEndpoints({ limit: 200 })).items,
    60_000,
    [],
  );
  const quotaGroups = usePolling<QuotaGroupRead[] | null>(
    async () => (await api.listQuotaGroups({ limit: 200 })).items,
    60_000,
    [],
  );

  // Only endpoints / quota groups belonging to the selected account are offered,
  // matching the backend's account-consistency invariants.
  const eligibleEndpoints = (endpoints.data ?? []).filter((e) => e.provider_account_id === form.providerAccountId);
  const eligibleQuotaGroups = (quotaGroups.data ?? []).filter((g) => g.provider_account_id === form.providerAccountId);

  const openCreate = () => {
    setForm(emptyForm);
    setActionError(null);
    setModalOpen(true);
  };

  const openEdit = (binding: RouteBindingRead) => {
    setForm({
      id: binding.id,
      modelAliasId: binding.model_alias_id,
      providerAccountId: binding.provider_account_id,
      endpointId: binding.endpoint_id,
      upstreamModel: binding.upstream_model ?? "",
      quotaGroupId: binding.quota_group_id ?? "",
      defaultOutputTokens: binding.default_output_tokens != null ? String(binding.default_output_tokens) : "",
      isActive: binding.is_active,
    });
    setActionError(null);
    setModalOpen(true);
  };

  const onCreate = async () => {
    if (!form.modelAliasId || !form.providerAccountId || !form.endpointId) return;
    const body: RouteBindingCreate = {
      model_alias_id: form.modelAliasId,
      provider_account_id: form.providerAccountId,
      endpoint_id: form.endpointId,
      is_active: form.isActive,
    };
    if (form.upstreamModel.trim()) body.upstream_model = form.upstreamModel.trim();
    if (form.quotaGroupId) body.quota_group_id = form.quotaGroupId;
    if (form.defaultOutputTokens.trim()) {
      const v = parseInt(form.defaultOutputTokens, 10);
      if (Number.isFinite(v) && v > 0) body.default_output_tokens = v;
    }
    setBusy(true);
    setActionError(null);
    try {
      await api.createRouteBinding(body);
      setModalOpen(false);
      list.refresh();
    } catch (err) {
      setActionError(apiErrorMessage(err));
    } finally {
      setBusy(false);
    }
  };

  const onUpdate = async () => {
    if (!form.id) return;
    const original = (list.data?.items ?? []).find((b) => b.id === form.id);
    if (!original) return;
    const patch: RouteBindingUpdate = {};
    if (form.isActive !== original.is_active) patch.is_active = form.isActive;
    const upstream = form.upstreamModel.trim();
    if (upstream !== (original.upstream_model ?? "")) {
      patch.upstream_model = upstream === "" ? null : upstream;
    }
    if (form.quotaGroupId !== (original.quota_group_id ?? "")) {
      patch.quota_group_id = form.quotaGroupId === "" ? null : form.quotaGroupId;
    }
    if (form.defaultOutputTokens.trim()) {
      const v = parseInt(form.defaultOutputTokens, 10);
      if (Number.isFinite(v) && v !== original.default_output_tokens) patch.default_output_tokens = v;
    } else if (original.default_output_tokens != null) {
      patch.default_output_tokens = null;
    }
    if (Object.keys(patch).length === 0) {
      setModalOpen(false);
      return;
    }
    setBusy(true);
    setActionError(null);
    try {
      await api.updateRouteBinding(form.id, patch);
      setModalOpen(false);
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
          <h2 className="text-xl font-bold">Route Bindings</h2>
          <p className="text-sm text-[var(--ag-text-muted)] mt-1">
            One active route per model alias; account consistency is enforced by the backend.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={openCreate}
            className="inline-flex items-center gap-2 text-sm px-3 py-2 rounded-lg bg-[var(--ag-accent)] hover:bg-[var(--ag-accent-hover)] text-white transition-colors"
          >
            <Plus size={16} />
            New route
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
              <th className="px-4 py-3">Alias</th>
              <th className="px-4 py-3">Endpoint</th>
              <th className="px-4 py-3">Account</th>
              <th className="px-4 py-3">Upstream model</th>
              <th className="px-4 py-3">Quota group</th>
              <th className="px-4 py-3">Status</th>
              <th className="px-4 py-3"></th>
            </tr>
          </thead>
          <tbody>
            {list.loading && !list.data ? (
              <tr><td colSpan={7} className="px-4 py-6 text-[var(--ag-text-muted)]">Loading…</td></tr>
            ) : (list.data?.items ?? []).length === 0 ? (
              <tr><td colSpan={7}><EmptyState message="No route bindings." /></td></tr>
            ) : (
              (list.data?.items ?? []).map((b) => (
                <tr key={b.id} className="border-b border-[var(--ag-border)] last:border-0 hover:bg-[var(--ag-surface-2)]/40">
                  <td className="px-4 py-3 font-mono text-xs">{b.model_alias_id}</td>
                  <td className="px-4 py-3 font-mono text-xs text-[var(--ag-text-muted)]">{b.endpoint_id}</td>
                  <td className="px-4 py-3 font-mono text-xs text-[var(--ag-text-muted)]">{b.provider_account_id}</td>
                  <td className="px-4 py-3 text-xs">{b.upstream_model ?? "—"}</td>
                  <td className="px-4 py-3 font-mono text-xs text-[var(--ag-text-muted)]">{b.quota_group_id ?? "—"}</td>
                  <td className="px-4 py-3">
                    <StatusBadge label={b.is_active ? "Active" : "Inactive"} tone={b.is_active ? "active" : "inactive"} />
                  </td>
                  <td className="px-4 py-3 text-right">
                    <button type="button" onClick={() => openEdit(b)} className="inline-flex items-center gap-1 text-xs px-2 py-1 rounded-md border border-[var(--ag-border)] text-[var(--ag-text-muted)] hover:text-[var(--ag-text)] transition-colors">
                      <Pencil size={13} /> Edit
                    </button>
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>

      <Pagination page={page} pageSize={PAGE_SIZE} total={total} onPageChange={setPage} />

      {modalOpen ? (
        <Modal title={form.id === null ? "New route binding" : "Edit route binding"} onClose={() => setModalOpen(false)}>
          <div className="space-y-4">
            <Field label="Model alias" htmlFor="route-alias">
              <Select id="route-alias" value={form.modelAliasId} onChange={(e) => setForm({ ...form, modelAliasId: e.target.value })} disabled={form.id !== null}>
                <option value="">Select an alias…</option>
                {(aliases.data ?? []).map((a) => (
                  <option key={a.id} value={a.id}>{a.name}</option>
                ))}
              </Select>
            </Field>
            <Field label="Provider account" htmlFor="route-account">
              <Select id="route-account" value={form.providerAccountId} onChange={(e) => setForm({ ...form, providerAccountId: e.target.value, endpointId: "", quotaGroupId: "" })} disabled={form.id !== null}>
                <option value="">Select an account…</option>
                {(accounts.data ?? []).map((a) => (
                  <option key={a.id} value={a.id}>{a.name}</option>
                ))}
              </Select>
            </Field>
            <Field label="Endpoint" htmlFor="route-endpoint" hint="Endpoints shown belong to the selected account.">
              <Select id="route-endpoint" value={form.endpointId} onChange={(e) => setForm({ ...form, endpointId: e.target.value })} disabled={form.id !== null}>
                <option value="">Select an endpoint…</option>
                {eligibleEndpoints.map((e) => (
                  <option key={e.id} value={e.id}>{e.name}</option>
                ))}
              </Select>
            </Field>
            <Field label="Upstream model" htmlFor="route-upstream">
              <TextInput id="route-upstream" value={form.upstreamModel} onChange={(e) => setForm({ ...form, upstreamModel: e.target.value })} placeholder="Optional" />
            </Field>
            <Field label="Quota group (optional)" htmlFor="route-quota">
              <Select id="route-quota" value={form.quotaGroupId} onChange={(e) => setForm({ ...form, quotaGroupId: e.target.value })}>
                <option value="">None</option>
                {eligibleQuotaGroups.map((g) => (
                  <option key={g.id} value={g.id}>{g.name}</option>
                ))}
              </Select>
            </Field>
            <Field label="Default output tokens" htmlFor="route-default-tokens">
              <NumberInput id="route-default-tokens" min={1} step={1} value={form.defaultOutputTokens} onChange={(e) => setForm({ ...form, defaultOutputTokens: e.target.value })} placeholder="Optional" />
            </Field>
            <Toggle id="route-active" label="Active" checked={form.isActive} onChange={(v) => setForm({ ...form, isActive: v })} />
            <ErrorBanner message={actionError} />
            <div className="flex justify-end gap-3">
              <button type="button" onClick={() => setModalOpen(false)} className="text-sm px-3 py-2 rounded-lg border border-[var(--ag-border)] text-[var(--ag-text-muted)] hover:text-[var(--ag-text)] transition-colors">
                Cancel
              </button>
              <button
                type="button"
                onClick={() => void (form.id === null ? onCreate() : onUpdate())}
                disabled={busy || (form.id === null && (!form.modelAliasId || !form.providerAccountId || !form.endpointId))}
                className="text-sm px-3 py-2 rounded-lg bg-[var(--ag-accent)] hover:bg-[var(--ag-accent-hover)] text-white disabled:opacity-50 transition-colors"
              >
                {busy ? "Saving…" : form.id === null ? "Create" : "Save changes"}
              </button>
            </div>
          </div>
        </Modal>
      ) : null}
    </div>
  );
}
