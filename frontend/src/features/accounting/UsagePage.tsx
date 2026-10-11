import { useState } from "react";
import { Link } from "react-router-dom";
import { RefreshCw, Lock } from "lucide-react";
import {
  api,
  type Page,
  type UsageRecordRead,
  type BillingUnit,
  type ProjectRead,
} from "../../lib/client";
import { useAuth } from "../../context/auth-context";
import { isSystemAdmin } from "../../lib/roles";
import { usePolling } from "../../lib/usePolling";
import { formatMoney } from "../../lib/decimal";
import Pagination from "../../components/ui/Pagination";
import EmptyState from "../../components/ui/EmptyState";
import { Field, Select, TextInput } from "../../components/ui/Form";

const PAGE_SIZE = 20;

function formatRecordedAt(iso: string): string {
  return iso.replace("T", " ").replace(/\.\d+Z?$/, "").replace("Z", " UTC");
}

export default function UsagePage() {
  const { roles, session } = useAuth();
  const systemAdmin = isSystemAdmin(roles);

  const [page, setPage] = useState(0);
  const [projectId, setProjectId] = useState("");
  const [requestFilter, setRequestFilter] = useState("");
  const [billingUnitFilter, setBillingUnitFilter] = useState<BillingUnit | "">("");
  const [currencyFilter, setCurrencyFilter] = useState("");
  const [fromFilter, setFromFilter] = useState("");
  const [toFilter, setToFilter] = useState("");

  const projects = usePolling<ProjectRead[] | null>(
    async () => (systemAdmin ? (await api.listProjects({ limit: 200 })).items : null),
    60_000,
    [systemAdmin],
  );

  const effectiveProjectId = systemAdmin ? projectId : (session?.project_id ?? "");

  const list = usePolling<Page<UsageRecordRead> | null>(
    async () =>
      await api.listUsageRecords({
        limit: PAGE_SIZE,
        offset: page * PAGE_SIZE,
        ...(effectiveProjectId ? { project_id: effectiveProjectId } : {}),
        ...(requestFilter ? { request_id: requestFilter } : {}),
        ...(billingUnitFilter ? { billing_unit: billingUnitFilter } : {}),
        ...(currencyFilter ? { currency: currencyFilter } : {}),
        ...(fromFilter ? { recorded_from: fromFilter } : {}),
        ...(toFilter ? { recorded_to: toFilter } : {}),
      }),
    60_000,
    [effectiveProjectId, page, requestFilter, billingUnitFilter, currencyFilter, fromFilter, toFilter],
  );

  const total = list.data?.total ?? 0;

  return (
    <div className="space-y-6 max-w-6xl">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-xl font-bold flex items-center gap-2">
            Usage <Lock size={16} className="text-[var(--ag-text-muted)]" />
          </h2>
          <p className="text-sm text-[var(--ag-text-muted)] mt-1">
            Immutable measured-usage records. Accounting attribution only; no prompt or completion
            content is ever shown.
          </p>
        </div>
        <button
          type="button"
          onClick={list.refresh}
          className="inline-flex items-center gap-2 text-sm px-3 py-2 rounded-lg border border-[var(--ag-border)] text-[var(--ag-text-muted)] hover:text-[var(--ag-text)] hover:bg-[var(--ag-surface-2)] transition-colors"
        >
          <RefreshCw size={14} /> Refresh
        </button>
      </div>

      <div className="flex flex-wrap items-end gap-4">
        {systemAdmin ? (
          <div className="max-w-xs">
            <Field label="Project" htmlFor="usage-project">
              <Select id="usage-project" value={projectId} onChange={(e) => { setProjectId(e.target.value); setPage(0); }}>
                <option value="">All projects</option>
                {(projects.data ?? []).map((p) => (
                  <option key={p.id} value={p.id}>{p.name}</option>
                ))}
              </Select>
            </Field>
          </div>
        ) : null}
        <div className="max-w-xs">
          <Field label="Request" htmlFor="usage-request">
            <TextInput id="usage-request" value={requestFilter} onChange={(e) => { setRequestFilter(e.target.value); setPage(0); }} placeholder="Filter by request ID" />
          </Field>
        </div>
        <div className="max-w-xs">
          <Field label="Billing unit" htmlFor="usage-unit">
            <Select id="usage-unit" value={billingUnitFilter} onChange={(e) => { setBillingUnitFilter(e.target.value as BillingUnit | ""); setPage(0); }}>
              <option value="">All units</option>
              <option value="request">Request</option>
              <option value="token">Token</option>
              <option value="image">Image</option>
              <option value="minute">Minute</option>
            </Select>
          </Field>
        </div>
        <div className="max-w-xs">
          <Field label="Currency" htmlFor="usage-currency">
            <TextInput id="usage-currency" value={currencyFilter} onChange={(e) => { setCurrencyFilter(e.target.value); setPage(0); }} placeholder="USD" />
          </Field>
        </div>
        <div className="max-w-xs">
          <Field label="Recorded from" htmlFor="usage-from">
            <TextInput id="usage-from" value={fromFilter} onChange={(e) => { setFromFilter(e.target.value); setPage(0); }} placeholder="YYYY-MM-DDTHH:MM:SSZ" />
          </Field>
        </div>
        <div className="max-w-xs">
          <Field label="Recorded to" htmlFor="usage-to">
            <TextInput id="usage-to" value={toFilter} onChange={(e) => { setToFilter(e.target.value); setPage(0); }} placeholder="YYYY-MM-DDTHH:MM:SSZ" />
          </Field>
        </div>
      </div>

      {list.error ? <p className="text-sm text-[var(--ag-danger)]">Error: {list.error}</p> : null}

      <div className="bg-[var(--ag-surface)] border border-[var(--ag-border)] rounded-xl overflow-hidden">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-[var(--ag-border)] text-left text-xs text-[var(--ag-text-muted)] uppercase tracking-wider">
              <th className="px-4 py-3">Recorded at</th>
              <th className="px-4 py-3">Request</th>
              <th className="px-4 py-3">Billing</th>
              <th className="px-4 py-3">Units</th>
              <th className="px-4 py-3">Amount</th>
              <th className="px-4 py-3">Snapshot</th>
            </tr>
          </thead>
          <tbody>
            {list.loading && !list.data ? (
              <tr><td colSpan={6} className="px-4 py-6 text-[var(--ag-text-muted)]">Loading…</td></tr>
            ) : (list.data?.items ?? []).length === 0 ? (
              <tr><td colSpan={6}><EmptyState message="No usage records." /></td></tr>
            ) : (
              (list.data?.items ?? []).map((u) => (
                <tr key={u.id} className="border-b border-[var(--ag-border)] last:border-0 hover:bg-[var(--ag-surface-2)]/40">
                  <td className="px-4 py-3 text-xs font-mono">{formatRecordedAt(u.recorded_at)}</td>
                  <td className="px-4 py-3">
                    <Link to={`/queue/${u.request_id}`} className="font-mono text-xs text-[var(--ag-accent)] hover:underline">{u.request_id}</Link>
                  </td>
                  <td className="px-4 py-3 text-xs">{u.billing_unit}</td>
                  <td className="px-4 py-3 text-xs font-mono">
                    {u.billing_unit === "token"
                      ? `${u.input_units} in / ${u.output_units} out`
                      : `req ${u.request_units ?? 1}`}
                  </td>
                  <td className="px-4 py-3 font-mono text-xs">{formatMoney(u.amount, u.currency)}</td>
                  <td className="px-4 py-3 font-mono text-xs text-[var(--ag-text-muted)]">{u.price_snapshot_id}</td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>

      <Pagination page={page} pageSize={PAGE_SIZE} total={total} onPageChange={setPage} />
    </div>
  );
}
