import { useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { Plus, RefreshCw, Pencil } from "lucide-react";
import {
  api,
  apiErrorMessage,
  type Page,
  type ProjectBudgetPolicyRead,
  type ProjectBudgetPolicyCreate,
  type ProjectBudgetPolicyUpdate,
  type BudgetStatusRead,
  type ProjectRead,
} from "../../lib/client";
import { useAuth } from "../../context/auth-context";
import { isSystemAdmin, canManageBudgets } from "../../lib/roles";
import { usePolling } from "../../lib/usePolling";
import { isPositiveDecimalString, formatMoney } from "../../lib/decimal";
import Pagination from "../../components/ui/Pagination";
import StatusBadge from "../../components/ui/StatusBadge";
import ErrorBanner from "../../components/ui/ErrorBanner";
import EmptyState from "../../components/ui/EmptyState";
import Modal from "../../components/ui/Modal";
import { Field, Select, TextInput, NumberInput, Toggle } from "../../components/ui/Form";

const PAGE_SIZE = 20;

interface BudgetForm {
  id: string | null;
  projectId: string;
  name: string;
  currency: string;
  limitAmount: string;
  windowSeconds: string;
  enabled: boolean;
}

const emptyForm: BudgetForm = {
  id: null,
  projectId: "",
  name: "",
  currency: "USD",
  limitAmount: "",
  windowSeconds: "3600",
  enabled: true,
};

function formatWindow(iso: string): string {
  return iso.replace("T", " ").replace(/\.\d+Z?$/, "").replace("Z", " UTC");
}

export default function BudgetsPage() {
  const { roles, session } = useAuth();
  const systemAdmin = isSystemAdmin(roles);
  const canEdit = canManageBudgets(roles);
  const [searchParams] = useSearchParams();

  const [page, setPage] = useState(0);
  const [projectId, setProjectId] = useState(searchParams.get("project") ?? "");
  const [enabledFilter, setEnabledFilter] = useState<"" | "true" | "false">("");
  const [currencyFilter, setCurrencyFilter] = useState("");

  const [modalOpen, setModalOpen] = useState(false);
  const [form, setForm] = useState<BudgetForm>(emptyForm);
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);

  const projects = usePolling<ProjectRead[] | null>(
    async () => (systemAdmin ? (await api.listProjects({ limit: 200 })).items : null),
    60_000,
    [systemAdmin],
  );

  const effectiveProjectId = systemAdmin ? projectId : (session?.project_id ?? "");

  const list = usePolling<Page<ProjectBudgetPolicyRead> | null>(
    async () =>
      effectiveProjectId
        ? await api.listProjectBudgetPolicies({
            limit: PAGE_SIZE,
            offset: page * PAGE_SIZE,
            project_id: effectiveProjectId,
            ...(enabledFilter ? { enabled: enabledFilter === "true" } : {}),
            ...(currencyFilter ? { currency: currencyFilter } : {}),
          })
        : null,
    60_000,
    [effectiveProjectId, page, enabledFilter, currencyFilter],
  );

  const status = usePolling<BudgetStatusRead[] | null>(
    async () => (effectiveProjectId ? await api.budgetStatus(effectiveProjectId) : null),
    30_000,
    [effectiveProjectId],
  );

  const openCreate = () => {
    setForm({ ...emptyForm, projectId: effectiveProjectId });
    setActionError(null);
    setModalOpen(true);
  };

  const openEdit = (p: ProjectBudgetPolicyRead) => {
    setForm({
      id: p.id,
      projectId: p.project_id,
      name: p.name,
      currency: p.currency,
      limitAmount: p.limit_amount,
      windowSeconds: String(p.window_seconds),
      enabled: p.enabled,
    });
    setActionError(null);
    setModalOpen(true);
  };

  const formValid = () => {
    if (!form.name.trim()) return false;
    if (!form.currency.trim()) return false;
    if (form.id === null && !form.projectId) return false;
    if (!isPositiveDecimalString(form.limitAmount)) return false;
    if (!/^[0-9]+$/.test(form.windowSeconds.trim())) return false;
    if (parseInt(form.windowSeconds, 10) < 1) return false;
    return true;
  };

  const onCreate = async () => {
    const body: ProjectBudgetPolicyCreate = {
      project_id: form.projectId,
      name: form.name.trim(),
      currency: form.currency.trim(),
      limit_amount: form.limitAmount.trim(),
      window_seconds: parseInt(form.windowSeconds, 10),
      enabled: form.enabled,
    };
    setBusy(true);
    setActionError(null);
    try {
      await api.createProjectBudgetPolicy(body);
      setModalOpen(false);
      list.refresh();
      status.refresh();
    } catch (err) {
      setActionError(apiErrorMessage(err));
    } finally {
      setBusy(false);
    }
  };

  const onUpdate = async () => {
    if (!form.id) return;
    const original = (list.data?.items ?? []).find((p) => p.id === form.id);
    if (!original) return;
    const patch: ProjectBudgetPolicyUpdate = {};
    const name = form.name.trim();
    if (name !== original.name) patch.name = name;
    const limitAmount = form.limitAmount.trim();
    if (limitAmount !== original.limit_amount) patch.limit_amount = limitAmount;
    if (form.enabled !== original.enabled) patch.enabled = form.enabled;
    if (Object.keys(patch).length === 0) {
      setModalOpen(false);
      return;
    }
    setBusy(true);
    setActionError(null);
    try {
      await api.updateProjectBudgetPolicy(form.id, patch);
      setModalOpen(false);
      list.refresh();
      status.refresh();
    } catch (err) {
      setActionError(apiErrorMessage(err));
    } finally {
      setBusy(false);
    }
  };

  const total = list.data?.total ?? 0;

  return (
    <div className="space-y-8 max-w-6xl">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-xl font-bold">Budgets</h2>
          <p className="text-sm text-[var(--ag-text-muted)] mt-1">
            Optional project spending-cap policies. Budgets gate spending; they are not prepaid
            balances.
          </p>
        </div>
        <div className="flex items-center gap-2">
          {canEdit && effectiveProjectId ? (
            <button
              type="button"
              onClick={openCreate}
              className="inline-flex items-center gap-2 text-sm px-3 py-2 rounded-lg bg-[var(--ag-accent)] hover:bg-[var(--ag-accent-hover)] text-white transition-colors"
            >
              <Plus size={16} /> New budget
            </button>
          ) : null}
          <button
            type="button"
            onClick={() => {
              list.refresh();
              status.refresh();
            }}
            className="inline-flex items-center gap-2 text-sm px-3 py-2 rounded-lg border border-[var(--ag-border)] text-[var(--ag-text-muted)] hover:text-[var(--ag-text)] hover:bg-[var(--ag-surface-2)] transition-colors"
          >
            <RefreshCw size={14} /> Refresh
          </button>
        </div>
      </div>

      <div className="flex flex-wrap items-end gap-4">
        {systemAdmin ? (
          <div className="max-w-xs">
            <Field label="Project" htmlFor="budget-project">
              <Select
                id="budget-project"
                value={projectId}
                onChange={(e) => {
                  setProjectId(e.target.value);
                  setPage(0);
                }}
              >
                <option value="">Select a project…</option>
                {(projects.data ?? []).map((p) => (
                  <option key={p.id} value={p.id}>{p.name}</option>
                ))}
              </Select>
            </Field>
          </div>
        ) : null}
        <div className="max-w-xs">
          <Field label="Enabled" htmlFor="budget-enabled-filter">
            <Select id="budget-enabled-filter" value={enabledFilter} onChange={(e) => { setEnabledFilter(e.target.value as "" | "true" | "false"); setPage(0); }}>
              <option value="">All</option>
              <option value="true">Enabled</option>
              <option value="false">Disabled</option>
            </Select>
          </Field>
        </div>
        <div className="max-w-xs">
          <Field label="Currency" htmlFor="budget-currency-filter">
            <TextInput id="budget-currency-filter" value={currencyFilter} onChange={(e) => { setCurrencyFilter(e.target.value); setPage(0); }} placeholder="USD" />
          </Field>
        </div>
      </div>

      <ErrorBanner message={actionError} />

      {/* current status / headroom */}
      <section className="space-y-3">
        <h3 className="text-lg font-semibold">Current status</h3>
        {!effectiveProjectId ? (
          <EmptyState message="Select a project to view its budget status." />
        ) : status.error ? (
          <p className="text-sm text-[var(--ag-danger)]">Error: {status.error}</p>
        ) : (status.data ?? []).length === 0 ? (
          <EmptyState message="No budget policies for this project." />
        ) : (
          <div className="grid gap-3 sm:grid-cols-2">
            {(status.data ?? []).map((s) => (
              <div key={s.budget_policy_id} className="bg-[var(--ag-surface)] border border-[var(--ag-border)] rounded-xl p-4 space-y-2">
                <div className="flex items-center justify-between">
                  <span className="text-sm font-mono text-xs text-[var(--ag-text-muted)]">{s.budget_policy_id}</span>
                  <StatusBadge label={s.enabled ? "Enabled" : "Disabled"} tone={s.enabled ? "active" : "inactive"} />
                </div>
                <div className="grid grid-cols-2 gap-2 text-sm">
                  <div>
                    <span className="text-xs text-[var(--ag-text-muted)] uppercase">Limit</span>
                    <div className="font-mono">{formatMoney(s.limit_amount, s.currency)}</div>
                  </div>
                  <div>
                    <span className="text-xs text-[var(--ag-text-muted)] uppercase">Headroom</span>
                    <div className="font-mono">{formatMoney(s.headroom, s.currency)}</div>
                  </div>
                  <div>
                    <span className="text-xs text-[var(--ag-text-muted)] uppercase">Committed</span>
                    <div className="font-mono">{formatMoney(s.committed_amount, s.currency)}</div>
                  </div>
                  <div>
                    <span className="text-xs text-[var(--ag-text-muted)] uppercase">Reserved</span>
                    <div className="font-mono">{formatMoney(s.reserved_amount, s.currency)}</div>
                  </div>
                </div>
                <p className="text-xs text-[var(--ag-text-muted)]">
                  Window {formatWindow(s.window_start)} → {formatWindow(s.window_end)}
                </p>
              </div>
            ))}
          </div>
        )}
      </section>

      {/* policies */}
      <section className="space-y-3">
        <h3 className="text-lg font-semibold">Policies</h3>
        {list.error ? <p className="text-sm text-[var(--ag-danger)]">Error: {list.error}</p> : null}
        <div className="bg-[var(--ag-surface)] border border-[var(--ag-border)] rounded-xl overflow-hidden">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-[var(--ag-border)] text-left text-xs text-[var(--ag-text-muted)] uppercase tracking-wider">
                <th className="px-4 py-3">Name</th>
                <th className="px-4 py-3">Limit</th>
                <th className="px-4 py-3">Window (s)</th>
                <th className="px-4 py-3">Status</th>
                <th className="px-4 py-3"></th>
              </tr>
            </thead>
            <tbody>
              {!effectiveProjectId ? (
                <tr><td colSpan={5}><EmptyState message="Select a project to view its budget policies." /></td></tr>
              ) : list.loading && !list.data ? (
                <tr><td colSpan={5} className="px-4 py-6 text-[var(--ag-text-muted)]">Loading…</td></tr>
              ) : (list.data?.items ?? []).length === 0 ? (
                <tr><td colSpan={5}><EmptyState message="No budget policies." /></td></tr>
              ) : (
                (list.data?.items ?? []).map((p) => (
                  <tr key={p.id} className="border-b border-[var(--ag-border)] last:border-0 hover:bg-[var(--ag-surface-2)]/40">
                    <td className="px-4 py-3">{p.name}</td>
                    <td className="px-4 py-3 font-mono text-xs">{formatMoney(p.limit_amount, p.currency)}</td>
                    <td className="px-4 py-3 text-xs">{p.window_seconds}</td>
                    <td className="px-4 py-3">
                      <StatusBadge label={p.enabled ? "Enabled" : "Disabled"} tone={p.enabled ? "active" : "inactive"} />
                    </td>
                    <td className="px-4 py-3 text-right whitespace-nowrap">
                      <div className="inline-flex items-center gap-2">
                        <Link
                          to={`/accounting/reservations?policy=${p.id}`}
                          className="inline-flex items-center gap-1 text-xs px-2 py-1 rounded-md border border-[var(--ag-border)] text-[var(--ag-text-muted)] hover:text-[var(--ag-text)] transition-colors"
                        >
                          Reservations
                        </Link>
                        {canEdit ? (
                          <button type="button" onClick={() => openEdit(p)} className="inline-flex items-center gap-1 text-xs px-2 py-1 rounded-md border border-[var(--ag-border)] text-[var(--ag-text-muted)] hover:text-[var(--ag-text)] transition-colors">
                            <Pencil size={13} /> Edit
                          </button>
                        ) : null}
                      </div>
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
        <Pagination page={page} pageSize={PAGE_SIZE} total={total} onPageChange={setPage} />
      </section>

      {modalOpen ? (
        <Modal title={form.id === null ? "New budget" : "Edit budget"} onClose={() => setModalOpen(false)}>
          <div className="space-y-4">
            {systemAdmin ? (
              <Field label="Project" htmlFor="budget-form-project">
                <Select id="budget-form-project" value={form.projectId} onChange={(e) => setForm({ ...form, projectId: e.target.value })} disabled={form.id !== null}>
                  <option value="">Select a project…</option>
                  {(projects.data ?? []).map((p) => (
                    <option key={p.id} value={p.id}>{p.name}</option>
                  ))}
                </Select>
              </Field>
            ) : null}
            <Field label="Name" htmlFor="budget-form-name">
              <TextInput id="budget-form-name" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} placeholder="Budget name" autoFocus={form.id === null} />
            </Field>
            <Field label="Currency" htmlFor="budget-form-currency" hint={form.id !== null ? "Currency is immutable after creation." : undefined}>
              <TextInput id="budget-form-currency" value={form.currency} onChange={(e) => setForm({ ...form, currency: e.target.value })} disabled={form.id !== null} placeholder="USD" />
            </Field>
            <Field label="Limit amount" htmlFor="budget-form-limit">
              <TextInput id="budget-form-limit" value={form.limitAmount} onChange={(e) => setForm({ ...form, limitAmount: e.target.value })} placeholder="100.00" inputMode="decimal" />
            </Field>
            <Field label="Window seconds" htmlFor="budget-form-window" hint={form.id !== null ? "Window is immutable after creation. Create a replacement policy to change it." : undefined}>
              <NumberInput id="budget-form-window" min={1} step={1} value={form.windowSeconds} onChange={(e) => setForm({ ...form, windowSeconds: e.target.value })} disabled={form.id !== null} />
            </Field>
            <Toggle id="budget-form-enabled" label="Enabled" checked={form.enabled} onChange={(v) => setForm({ ...form, enabled: v })} />
            <ErrorBanner message={actionError} />
            <div className="flex justify-end gap-3">
              <button type="button" onClick={() => setModalOpen(false)} className="text-sm px-3 py-2 rounded-lg border border-[var(--ag-border)] text-[var(--ag-text-muted)] hover:text-[var(--ag-text)] transition-colors">
                Cancel
              </button>
              <button
                type="button"
                onClick={() => void (form.id === null ? onCreate() : onUpdate())}
                disabled={busy || !formValid()}
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
