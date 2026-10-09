import { useState } from "react";
import { Link } from "react-router-dom";
import { Plus, RefreshCw, Pencil } from "lucide-react";
import {
  api,
  apiErrorMessage,
  type Page,
  type QuotaGroupRead,
  type QuotaGroupCreate,
  type QuotaGroupUpdate,
  type QuotaLimitRead,
  type QuotaLimitCreate,
  type QuotaLimitUpdate,
  type QuotaMetric,
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

interface GroupForm {
  id: string | null;
  providerAccountId: string;
  name: string;
  description: string;
}

interface LimitForm {
  id: string | null;
  quotaGroupId: string;
  metric: QuotaMetric;
  limitUnits: string;
  windowSeconds: string;
  enabled: boolean;
  name: string;
}

const emptyGroupForm: GroupForm = { id: null, providerAccountId: "", name: "", description: "" };
const emptyLimitForm: LimitForm = {
  id: null,
  quotaGroupId: "",
  metric: "requests",
  limitUnits: "",
  windowSeconds: "3600",
  enabled: true,
  name: "",
};

export default function QuotasPage() {
  const [groupPage, setGroupPage] = useState(0);
  const [limitPage, setLimitPage] = useState(0);
  const [limitGroupFilter, setLimitGroupFilter] = useState("");

  const [groupModalOpen, setGroupModalOpen] = useState(false);
  const [limitModalOpen, setLimitModalOpen] = useState(false);
  const [groupForm, setGroupForm] = useState<GroupForm>(emptyGroupForm);
  const [limitForm, setLimitForm] = useState<LimitForm>(emptyLimitForm);

  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);

  const groups = usePolling<Page<QuotaGroupRead>>(
    () => api.listQuotaGroups({ limit: PAGE_SIZE, offset: groupPage * PAGE_SIZE }),
    60_000,
    [groupPage],
  );
  const limits = usePolling<Page<QuotaLimitRead>>(
    () =>
      api.listQuotaLimits({
        limit: PAGE_SIZE,
        offset: limitPage * PAGE_SIZE,
        ...(limitGroupFilter ? { quota_group_id: limitGroupFilter } : {}),
      }),
    60_000,
    [limitPage, limitGroupFilter],
  );
  const accounts = usePolling<ProviderAccountRead[] | null>(
    async () => (await api.listProviderAccounts({ limit: 200 })).items,
    60_000,
    [],
  );

  // -- quota groups ------------------------------------------------------------

  const openGroupCreate = () => {
    setGroupForm(emptyGroupForm);
    setActionError(null);
    setGroupModalOpen(true);
  };
  const openGroupEdit = (g: QuotaGroupRead) => {
    setGroupForm({ id: g.id, providerAccountId: g.provider_account_id, name: g.name, description: g.description ?? "" });
    setActionError(null);
    setGroupModalOpen(true);
  };

  const saveGroup = async () => {
    if (!groupForm.name.trim() || !groupForm.providerAccountId) return;
    setBusy(true);
    setActionError(null);
    try {
      if (groupForm.id === null) {
        const body: QuotaGroupCreate = {
          provider_account_id: groupForm.providerAccountId,
          name: groupForm.name.trim(),
        };
        if (groupForm.description.trim()) body.description = groupForm.description.trim();
        await api.createQuotaGroup(body);
      } else {
        const original = (groups.data?.items ?? []).find((g) => g.id === groupForm.id);
        const patch: QuotaGroupUpdate = {};
        const name = groupForm.name.trim();
        if (original && name !== original.name) patch.name = name;
        const desc = groupForm.description.trim();
        if (original && desc !== (original.description ?? "")) {
          patch.description = desc === "" ? null : desc;
        }
        if (Object.keys(patch).length > 0) {
          await api.updateQuotaGroup(groupForm.id, patch);
        }
      }
      setGroupModalOpen(false);
      groups.refresh();
    } catch (err) {
      setActionError(apiErrorMessage(err));
    } finally {
      setBusy(false);
    }
  };

  // -- quota limits ------------------------------------------------------------

  const openLimitCreate = () => {
    setLimitForm({ ...emptyLimitForm, quotaGroupId: limitGroupFilter });
    setActionError(null);
    setLimitModalOpen(true);
  };
  const openLimitEdit = (l: QuotaLimitRead) => {
    setLimitForm({
      id: l.id,
      quotaGroupId: l.quota_group_id,
      metric: l.metric,
      limitUnits: String(l.limit_units),
      windowSeconds: String(l.window_seconds),
      enabled: l.enabled,
      name: l.name ?? "",
    });
    setActionError(null);
    setLimitModalOpen(true);
  };

  const saveLimit = async () => {
    if (!limitForm.quotaGroupId || !limitForm.limitUnits.trim() || !limitForm.windowSeconds.trim()) return;
    const limitUnits = parseInt(limitForm.limitUnits, 10);
    const windowSeconds = parseInt(limitForm.windowSeconds, 10);
    if (!Number.isFinite(limitUnits) || limitUnits < 0) return;
    if (!Number.isFinite(windowSeconds) || windowSeconds <= 0) return;

    setBusy(true);
    setActionError(null);
    try {
      if (limitForm.id === null) {
        const body: QuotaLimitCreate = {
          quota_group_id: limitForm.quotaGroupId,
          metric: limitForm.metric,
          limit_units: limitUnits,
          window_seconds: windowSeconds,
          enabled: limitForm.enabled,
        };
        if (limitForm.name.trim()) body.name = limitForm.name.trim();
        await api.createQuotaLimit(body);
      } else {
        const original = (limits.data?.items ?? []).find((l) => l.id === limitForm.id);
        const patch: QuotaLimitUpdate = {};
        if (original) {
          if (limitUnits !== original.limit_units) patch.limit_units = limitUnits;
          if (windowSeconds !== original.window_seconds) patch.window_seconds = windowSeconds;
          if (limitForm.enabled !== original.enabled) patch.enabled = limitForm.enabled;
          const name = limitForm.name.trim();
          if (name !== (original.name ?? "")) patch.name = name === "" ? null : name;
        }
        if (Object.keys(patch).length > 0) {
          await api.updateQuotaLimit(limitForm.id, patch);
        }
      }
      setLimitModalOpen(false);
      limits.refresh();
    } catch (err) {
      setActionError(apiErrorMessage(err));
    } finally {
      setBusy(false);
    }
  };

  const groupTotal = groups.data?.total ?? 0;
  const limitTotal = limits.data?.total ?? 0;

  return (
    <div className="space-y-8 max-w-6xl">
      <div>
        <h2 className="text-xl font-bold">Quotas</h2>
        <p className="text-sm text-[var(--ag-text-muted)] mt-1">
          Quota groups and their limits. Runtime cooldown/committed values are shown on the{" "}
          <Link to="/" className="text-[var(--ag-accent)] hover:underline">Dashboard</Link>.
        </p>
      </div>

      <ErrorBanner message={actionError} />

      {/* quota groups */}
      <section className="space-y-4">
        <div className="flex items-center justify-between">
          <h3 className="text-lg font-semibold">Quota groups</h3>
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={openGroupCreate}
              className="inline-flex items-center gap-2 text-sm px-3 py-2 rounded-lg bg-[var(--ag-accent)] hover:bg-[var(--ag-accent-hover)] text-white transition-colors"
            >
              <Plus size={16} /> New group
            </button>
            <button
              type="button"
              onClick={groups.refresh}
              className="inline-flex items-center gap-2 text-sm px-3 py-2 rounded-lg border border-[var(--ag-border)] text-[var(--ag-text-muted)] hover:text-[var(--ag-text)] hover:bg-[var(--ag-surface-2)] transition-colors"
            >
              <RefreshCw size={14} /> Refresh
            </button>
          </div>
        </div>

        {groups.error ? <p className="text-sm text-[var(--ag-danger)]">Error: {groups.error}</p> : null}
        <div className="bg-[var(--ag-surface)] border border-[var(--ag-border)] rounded-xl overflow-hidden">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-[var(--ag-border)] text-left text-xs text-[var(--ag-text-muted)] uppercase tracking-wider">
                <th className="px-4 py-3">Name</th>
                <th className="px-4 py-3">Provider account</th>
                <th className="px-4 py-3">Description</th>
                <th className="px-4 py-3"></th>
              </tr>
            </thead>
            <tbody>
              {groups.loading && !groups.data ? (
                <tr><td colSpan={4} className="px-4 py-6 text-[var(--ag-text-muted)]">Loading…</td></tr>
              ) : (groups.data?.items ?? []).length === 0 ? (
                <tr><td colSpan={4}><EmptyState message="No quota groups." /></td></tr>
              ) : (
                (groups.data?.items ?? []).map((g) => (
                  <tr key={g.id} className="border-b border-[var(--ag-border)] last:border-0 hover:bg-[var(--ag-surface-2)]/40">
                    <td className="px-4 py-3">{g.name}</td>
                    <td className="px-4 py-3 font-mono text-xs text-[var(--ag-text-muted)]">{g.provider_account_id}</td>
                    <td className="px-4 py-3 text-xs">{g.description ?? "—"}</td>
                    <td className="px-4 py-3 text-right">
                      <button type="button" onClick={() => openGroupEdit(g)} className="inline-flex items-center gap-1 text-xs px-2 py-1 rounded-md border border-[var(--ag-border)] text-[var(--ag-text-muted)] hover:text-[var(--ag-text)] transition-colors">
                        <Pencil size={13} /> Edit
                      </button>
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
        <Pagination page={groupPage} pageSize={PAGE_SIZE} total={groupTotal} onPageChange={setGroupPage} />
      </section>

      {/* quota limits */}
      <section className="space-y-4">
        <div className="flex items-center justify-between">
          <h3 className="text-lg font-semibold">Quota limits</h3>
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={openLimitCreate}
              className="inline-flex items-center gap-2 text-sm px-3 py-2 rounded-lg bg-[var(--ag-accent)] hover:bg-[var(--ag-accent-hover)] text-white transition-colors"
            >
              <Plus size={16} /> New limit
            </button>
            <button
              type="button"
              onClick={limits.refresh}
              className="inline-flex items-center gap-2 text-sm px-3 py-2 rounded-lg border border-[var(--ag-border)] text-[var(--ag-text-muted)] hover:text-[var(--ag-text)] hover:bg-[var(--ag-surface-2)] transition-colors"
            >
              <RefreshCw size={14} /> Refresh
            </button>
          </div>
        </div>

        <div className="max-w-xs">
          <Field label="Filter by quota group" htmlFor="limit-group-filter">
            <Select id="limit-group-filter" value={limitGroupFilter} onChange={(e) => { setLimitGroupFilter(e.target.value); setLimitPage(0); }}>
              <option value="">All groups</option>
              {(groups.data?.items ?? []).map((g) => (
                <option key={g.id} value={g.id}>{g.name}</option>
              ))}
            </Select>
          </Field>
        </div>

        {limits.error ? <p className="text-sm text-[var(--ag-danger)]">Error: {limits.error}</p> : null}
        <div className="bg-[var(--ag-surface)] border border-[var(--ag-border)] rounded-xl overflow-hidden">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-[var(--ag-border)] text-left text-xs text-[var(--ag-text-muted)] uppercase tracking-wider">
                <th className="px-4 py-3">Name</th>
                <th className="px-4 py-3">Metric</th>
                <th className="px-4 py-3">Limit</th>
                <th className="px-4 py-3">Window (s)</th>
                <th className="px-4 py-3">Status</th>
                <th className="px-4 py-3"></th>
              </tr>
            </thead>
            <tbody>
              {limits.loading && !limits.data ? (
                <tr><td colSpan={6} className="px-4 py-6 text-[var(--ag-text-muted)]">Loading…</td></tr>
              ) : (limits.data?.items ?? []).length === 0 ? (
                <tr><td colSpan={6}><EmptyState message="No quota limits." /></td></tr>
              ) : (
                (limits.data?.items ?? []).map((l) => (
                  <tr key={l.id} className="border-b border-[var(--ag-border)] last:border-0 hover:bg-[var(--ag-surface-2)]/40">
                    <td className="px-4 py-3">{l.name ?? "—"}</td>
                    <td className="px-4 py-3 text-xs">{l.metric}</td>
                    <td className="px-4 py-3 text-xs">{l.limit_units}</td>
                    <td className="px-4 py-3 text-xs">{l.window_seconds}</td>
                    <td className="px-4 py-3">
                      <StatusBadge label={l.enabled ? "Enabled" : "Disabled"} tone={l.enabled ? "active" : "inactive"} />
                    </td>
                    <td className="px-4 py-3 text-right">
                      <button type="button" onClick={() => openLimitEdit(l)} className="inline-flex items-center gap-1 text-xs px-2 py-1 rounded-md border border-[var(--ag-border)] text-[var(--ag-text-muted)] hover:text-[var(--ag-text)] transition-colors">
                        <Pencil size={13} /> Edit
                      </button>
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
        <Pagination page={limitPage} pageSize={PAGE_SIZE} total={limitTotal} onPageChange={setLimitPage} />
      </section>

      {/* group modal */}
      {groupModalOpen ? (
        <Modal title={groupForm.id === null ? "New quota group" : "Edit quota group"} onClose={() => setGroupModalOpen(false)}>
          <div className="space-y-4">
            <Field label="Provider account" htmlFor="group-account">
              <Select id="group-account" value={groupForm.providerAccountId} onChange={(e) => setGroupForm({ ...groupForm, providerAccountId: e.target.value })}>
                <option value="">Select an account…</option>
                {(accounts.data ?? []).map((a) => (
                  <option key={a.id} value={a.id}>{a.name}</option>
                ))}
              </Select>
            </Field>
            <Field label="Name" htmlFor="group-name">
              <TextInput id="group-name" value={groupForm.name} onChange={(e) => setGroupForm({ ...groupForm, name: e.target.value })} placeholder="Group name" autoFocus={groupForm.id === null} />
            </Field>
            <Field label="Description" htmlFor="group-description">
              <TextInput id="group-description" value={groupForm.description} onChange={(e) => setGroupForm({ ...groupForm, description: e.target.value })} placeholder="Optional" />
            </Field>
            <ErrorBanner message={actionError} />
            <div className="flex justify-end gap-3">
              <button type="button" onClick={() => setGroupModalOpen(false)} className="text-sm px-3 py-2 rounded-lg border border-[var(--ag-border)] text-[var(--ag-text-muted)] hover:text-[var(--ag-text)] transition-colors">
                Cancel
              </button>
              <button type="button" onClick={() => void saveGroup()} disabled={busy || !groupForm.name.trim() || !groupForm.providerAccountId} className="text-sm px-3 py-2 rounded-lg bg-[var(--ag-accent)] hover:bg-[var(--ag-accent-hover)] text-white disabled:opacity-50 transition-colors">
                {busy ? "Saving…" : groupForm.id === null ? "Create" : "Save changes"}
              </button>
            </div>
          </div>
        </Modal>
      ) : null}

      {/* limit modal */}
      {limitModalOpen ? (
        <Modal title={limitForm.id === null ? "New quota limit" : "Edit quota limit"} onClose={() => setLimitModalOpen(false)}>
          <div className="space-y-4">
            <Field label="Quota group" htmlFor="limit-group">
              <Select id="limit-group" value={limitForm.quotaGroupId} onChange={(e) => setLimitForm({ ...limitForm, quotaGroupId: e.target.value })}>
                <option value="">Select a group…</option>
                {(groups.data?.items ?? []).map((g) => (
                  <option key={g.id} value={g.id}>{g.name}</option>
                ))}
              </Select>
            </Field>
            <Field label="Metric" htmlFor="limit-metric">
              <Select id="limit-metric" value={limitForm.metric} onChange={(e) => setLimitForm({ ...limitForm, metric: e.target.value as QuotaMetric })}>
                <option value="requests">Requests</option>
                <option value="tokens">Tokens</option>
              </Select>
            </Field>
            <Field label="Limit units" htmlFor="limit-units">
              <NumberInput id="limit-units" min={0} step={1} value={limitForm.limitUnits} onChange={(e) => setLimitForm({ ...limitForm, limitUnits: e.target.value })} />
            </Field>
            <Field label="Window seconds" htmlFor="limit-window">
              <NumberInput id="limit-window" min={1} step={1} value={limitForm.windowSeconds} onChange={(e) => setLimitForm({ ...limitForm, windowSeconds: e.target.value })} />
            </Field>
            <Field label="Name (optional)" htmlFor="limit-name">
              <TextInput id="limit-name" value={limitForm.name} onChange={(e) => setLimitForm({ ...limitForm, name: e.target.value })} />
            </Field>
            <Toggle id="limit-enabled" label="Enabled" checked={limitForm.enabled} onChange={(v) => setLimitForm({ ...limitForm, enabled: v })} />
            <ErrorBanner message={actionError} />
            <div className="flex justify-end gap-3">
              <button type="button" onClick={() => setLimitModalOpen(false)} className="text-sm px-3 py-2 rounded-lg border border-[var(--ag-border)] text-[var(--ag-text-muted)] hover:text-[var(--ag-text)] transition-colors">
                Cancel
              </button>
              <button type="button" onClick={() => void saveLimit()} disabled={busy || !limitForm.quotaGroupId || !limitForm.limitUnits.trim() || !limitForm.windowSeconds.trim()} className="text-sm px-3 py-2 rounded-lg bg-[var(--ag-accent)] hover:bg-[var(--ag-accent-hover)] text-white disabled:opacity-50 transition-colors">
                {busy ? "Saving…" : limitForm.id === null ? "Create" : "Save changes"}
              </button>
            </div>
          </div>
        </Modal>
      ) : null}
    </div>
  );
}
