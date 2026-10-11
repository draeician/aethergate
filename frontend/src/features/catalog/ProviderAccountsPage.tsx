import { useState } from "react";
import { Plus, RefreshCw, Pencil } from "lucide-react";
import {
  api,
  apiErrorMessage,
  type Page,
  type ProviderAccountRead,
  type ProviderAccountCreate,
  type ProviderAccountUpdate,
  type ProviderRead,
  type SecretRefRead,
} from "../../lib/client";
import { usePolling } from "../../lib/usePolling";
import Pagination from "../../components/ui/Pagination";
import StatusBadge from "../../components/ui/StatusBadge";
import ErrorBanner from "../../components/ui/ErrorBanner";
import EmptyState from "../../components/ui/EmptyState";
import Modal from "../../components/ui/Modal";
import { Field, Select, TextInput, Toggle } from "../../components/ui/Form";

const PAGE_SIZE = 20;

export default function ProviderAccountsPage() {
  const [page, setPage] = useState(0);
  const [creating, setCreating] = useState(false);
  const [editing, setEditing] = useState<ProviderAccountRead | null>(null);
  const [form, setForm] = useState({
    providerId: "",
    name: "",
    externalAccountId: "",
    secretRefId: "",
    isActive: true,
  });
  const [newRefName, setNewRefName] = useState("");
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);

  const list = usePolling<Page<ProviderAccountRead>>(
    () => api.listProviderAccounts({ limit: PAGE_SIZE, offset: page * PAGE_SIZE }),
    60_000,
    [page],
  );
  const providers = usePolling<ProviderRead[] | null>(
    async () => (await api.listProviders({ limit: 200 })).items,
    60_000,
    [],
  );
  const secretRefs = usePolling<SecretRefRead[] | null>(
    async () => (await api.listSecretRefs({ limit: 200 })).items,
    60_000,
    [],
  );

  const openCreate = () => {
    setForm({ providerId: "", name: "", externalAccountId: "", secretRefId: "", isActive: true });
    setNewRefName("");
    setActionError(null);
    setCreating(true);
  };

  const openEdit = (account: ProviderAccountRead) => {
    setForm({
      providerId: account.provider_id,
      name: account.name,
      externalAccountId: account.external_account_id ?? "",
      secretRefId: account.secret_ref_id ?? "",
      isActive: account.is_active,
    });
    setNewRefName("");
    setActionError(null);
    setEditing(account);
  };

  const close = () => {
    setCreating(false);
    setEditing(null);
  };

  const onCreateSecretRef = async () => {
    const name = newRefName.trim();
    if (!name) return;
    setActionError(null);
    try {
      const ref = await api.createSecretRef({ name });
      setNewRefName("");
      setForm((f) => ({ ...f, secretRefId: ref.id }));
      secretRefs.refresh();
    } catch (err) {
      setActionError(apiErrorMessage(err));
    }
  };

  const onCreate = async () => {
    if (!form.name.trim() || !form.providerId) return;
    const body: ProviderAccountCreate = {
      provider_id: form.providerId,
      name: form.name.trim(),
    };
    if (form.externalAccountId.trim()) body.external_account_id = form.externalAccountId.trim();
    if (form.secretRefId) body.secret_ref_id = form.secretRefId;
    setBusy(true);
    setActionError(null);
    try {
      await api.createProviderAccount(body);
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
    const patch: ProviderAccountUpdate = {};
    const name = form.name.trim();
    if (name && name !== editing.name) patch.name = name;
    if (form.isActive !== editing.is_active) patch.is_active = form.isActive;
    const external = form.externalAccountId.trim();
    if (external !== (editing.external_account_id ?? "")) {
      patch.external_account_id = external === "" ? null : external;
    }
    if (form.secretRefId !== (editing.secret_ref_id ?? "")) {
      patch.secret_ref_id = form.secretRefId === "" ? null : form.secretRefId;
    }
    if (Object.keys(patch).length === 0) {
      setEditing(null);
      return;
    }
    setBusy(true);
    setActionError(null);
    try {
      await api.updateProviderAccount(editing.id, patch);
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
          <h2 className="text-xl font-bold">Provider Accounts</h2>
          <p className="text-sm text-[var(--ag-text-muted)] mt-1">
            {total.toLocaleString()} account{total === 1 ? "" : "s"}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={openCreate}
            className="inline-flex items-center gap-2 text-sm px-3 py-2 rounded-lg bg-[var(--ag-accent)] hover:bg-[var(--ag-accent-hover)] text-white transition-colors"
          >
            <Plus size={16} />
            New account
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
              <th className="px-4 py-3">Provider</th>
              <th className="px-4 py-3">External ID</th>
              <th className="px-4 py-3">Secret ref</th>
              <th className="px-4 py-3">Status</th>
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
                  <EmptyState message="No provider accounts." />
                </td>
              </tr>
            ) : (
              (list.data?.items ?? []).map((a) => (
                <tr key={a.id} className="border-b border-[var(--ag-border)] last:border-0 hover:bg-[var(--ag-surface-2)]/40">
                  <td className="px-4 py-3">{a.name}</td>
                  <td className="px-4 py-3 font-mono text-xs text-[var(--ag-text-muted)]">{a.provider_id}</td>
                  <td className="px-4 py-3 text-xs">{a.external_account_id ?? "—"}</td>
                  <td className="px-4 py-3 font-mono text-xs text-[var(--ag-text-muted)]">{a.secret_ref_id ?? "—"}</td>
                  <td className="px-4 py-3">
                    <StatusBadge label={a.is_active ? "Active" : "Inactive"} tone={a.is_active ? "active" : "inactive"} />
                  </td>
                  <td className="px-4 py-3 text-right">
                    <button
                      type="button"
                      onClick={() => openEdit(a)}
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
        <Modal title={creating ? "New provider account" : "Edit provider account"} onClose={close}>
          <div className="space-y-4">
            <Field label="Provider" htmlFor="account-provider">
              <Select id="account-provider" value={form.providerId} onChange={(e) => setForm({ ...form, providerId: e.target.value })} disabled={editing !== null}>
                <option value="">Select a provider…</option>
                {(providers.data ?? []).map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.name} ({p.kind})
                  </option>
                ))}
              </Select>
            </Field>
            <Field label="Name" htmlFor="account-name">
              <TextInput id="account-name" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} placeholder="Account name" autoFocus={creating} />
            </Field>
            <Field label="External account ID" htmlFor="account-external" hint="Non-secret upstream account identifier.">
              <TextInput id="account-external" value={form.externalAccountId} onChange={(e) => setForm({ ...form, externalAccountId: e.target.value })} placeholder="Optional" />
            </Field>
            <Field label="Secret reference" htmlFor="account-secret-ref" hint="Metadata reference only — raw provider secrets are never stored or shown.">
              <Select id="account-secret-ref" value={form.secretRefId} onChange={(e) => setForm({ ...form, secretRefId: e.target.value })}>
                <option value="">None</option>
                {(secretRefs.data ?? []).map((r) => (
                  <option key={r.id} value={r.id}>
                    {r.name}
                  </option>
                ))}
              </Select>
            </Field>
            <div className="flex items-end gap-2">
              <div className="flex-1">
                <Field label="New secret ref name" htmlFor="account-new-ref">
                  <TextInput id="account-new-ref" value={newRefName} onChange={(e) => setNewRefName(e.target.value)} placeholder="Create a secret-reference entry" />
                </Field>
              </div>
              <button
                type="button"
                onClick={() => void onCreateSecretRef()}
                disabled={!newRefName.trim()}
                className="text-sm px-3 py-2 rounded-lg border border-[var(--ag-border)] text-[var(--ag-text-muted)] hover:text-[var(--ag-text)] disabled:opacity-50 transition-colors"
              >
                Add ref
              </button>
            </div>
            {editing ? (
              <Toggle id="account-active" label="Active" checked={form.isActive} onChange={(v) => setForm({ ...form, isActive: v })} />
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
                disabled={busy || !form.name.trim() || !form.providerId}
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
