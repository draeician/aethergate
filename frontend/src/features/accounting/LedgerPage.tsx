import { useState } from "react";
import { RefreshCw, Lock } from "lucide-react";
import {
  api,
  type Page,
  type LedgerEntryRead,
  type LedgerEntryType,
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

function formatCreatedAt(iso: string): string {
  return iso.replace("T", " ").replace(/\.\d+Z?$/, "").replace("Z", " UTC");
}

export default function LedgerPage() {
  const { roles, session } = useAuth();
  const systemAdmin = isSystemAdmin(roles);

  const [page, setPage] = useState(0);
  const [projectId, setProjectId] = useState("");
  const [usageRecordFilter, setUsageRecordFilter] = useState("");
  const [entryTypeFilter, setEntryTypeFilter] = useState<LedgerEntryType | "">("");
  const [currencyFilter, setCurrencyFilter] = useState("");
  const [fromFilter, setFromFilter] = useState("");
  const [toFilter, setToFilter] = useState("");

  const projects = usePolling<ProjectRead[] | null>(
    async () => (systemAdmin ? (await api.listProjects({ limit: 200 })).items : null),
    60_000,
    [systemAdmin],
  );

  const effectiveProjectId = systemAdmin ? projectId : (session?.project_id ?? "");

  const list = usePolling<Page<LedgerEntryRead> | null>(
    async () =>
      await api.listLedgerEntries({
        limit: PAGE_SIZE,
        offset: page * PAGE_SIZE,
        ...(effectiveProjectId ? { project_id: effectiveProjectId } : {}),
        ...(usageRecordFilter ? { usage_record_id: usageRecordFilter } : {}),
        ...(entryTypeFilter ? { entry_type: entryTypeFilter } : {}),
        ...(currencyFilter ? { currency: currencyFilter } : {}),
        ...(fromFilter ? { created_from: fromFilter } : {}),
        ...(toFilter ? { created_to: toFilter } : {}),
      }),
    60_000,
    [effectiveProjectId, page, usageRecordFilter, entryTypeFilter, currencyFilter, fromFilter, toFilter],
  );

  const total = list.data?.total ?? 0;

  return (
    <div className="space-y-6 max-w-6xl">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-xl font-bold flex items-center gap-2">
            Ledger <Lock size={16} className="text-[var(--ag-text-muted)]" />
          </h2>
          <p className="text-sm text-[var(--ag-text-muted)] mt-1">
            Immutable append-only ledger entries. Historical accounting activity, not an
            authorization balance.
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
            <Field label="Project" htmlFor="ledger-project">
              <Select id="ledger-project" value={projectId} onChange={(e) => { setProjectId(e.target.value); setPage(0); }}>
                <option value="">All projects</option>
                {(projects.data ?? []).map((p) => (
                  <option key={p.id} value={p.id}>{p.name}</option>
                ))}
              </Select>
            </Field>
          </div>
        ) : null}
        <div className="max-w-xs">
          <Field label="Usage record" htmlFor="ledger-usage">
            <TextInput id="ledger-usage" value={usageRecordFilter} onChange={(e) => { setUsageRecordFilter(e.target.value); setPage(0); }} placeholder="Filter by usage record ID" />
          </Field>
        </div>
        <div className="max-w-xs">
          <Field label="Entry type" htmlFor="ledger-type">
            <Select id="ledger-type" value={entryTypeFilter} onChange={(e) => { setEntryTypeFilter(e.target.value as LedgerEntryType | ""); setPage(0); }}>
              <option value="">All types</option>
              <option value="usage_debit">Usage debit</option>
              <option value="adjustment_credit">Adjustment credit</option>
              <option value="adjustment_debit">Adjustment debit</option>
            </Select>
          </Field>
        </div>
        <div className="max-w-xs">
          <Field label="Currency" htmlFor="ledger-currency">
            <TextInput id="ledger-currency" value={currencyFilter} onChange={(e) => { setCurrencyFilter(e.target.value); setPage(0); }} placeholder="USD" />
          </Field>
        </div>
        <div className="max-w-xs">
          <Field label="Created from" htmlFor="ledger-from">
            <TextInput id="ledger-from" value={fromFilter} onChange={(e) => { setFromFilter(e.target.value); setPage(0); }} placeholder="YYYY-MM-DDTHH:MM:SSZ" />
          </Field>
        </div>
        <div className="max-w-xs">
          <Field label="Created to" htmlFor="ledger-to">
            <TextInput id="ledger-to" value={toFilter} onChange={(e) => { setToFilter(e.target.value); setPage(0); }} placeholder="YYYY-MM-DDTHH:MM:SSZ" />
          </Field>
        </div>
      </div>

      {list.error ? <p className="text-sm text-[var(--ag-danger)]">Error: {list.error}</p> : null}

      <div className="bg-[var(--ag-surface)] border border-[var(--ag-border)] rounded-xl overflow-hidden">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-[var(--ag-border)] text-left text-xs text-[var(--ag-text-muted)] uppercase tracking-wider">
              <th className="px-4 py-3">Created at</th>
              <th className="px-4 py-3">Type</th>
              <th className="px-4 py-3">Amount</th>
              <th className="px-4 py-3">Usage record</th>
              <th className="px-4 py-3">Reason</th>
            </tr>
          </thead>
          <tbody>
            {list.loading && !list.data ? (
              <tr><td colSpan={5} className="px-4 py-6 text-[var(--ag-text-muted)]">Loading…</td></tr>
            ) : (list.data?.items ?? []).length === 0 ? (
              <tr><td colSpan={5}><EmptyState message="No ledger entries." /></td></tr>
            ) : (
              (list.data?.items ?? []).map((e) => (
                <tr key={e.id} className="border-b border-[var(--ag-border)] last:border-0 hover:bg-[var(--ag-surface-2)]/40">
                  <td className="px-4 py-3 text-xs font-mono">{formatCreatedAt(e.created_at)}</td>
                  <td className="px-4 py-3 text-xs">{e.entry_type}</td>
                  <td className="px-4 py-3 font-mono text-xs">{formatMoney(e.amount, e.currency)}</td>
                  <td className="px-4 py-3 text-xs">
                    {e.usage_record_id ? (
                      <span className="font-mono">{e.usage_record_id}</span>
                    ) : (
                      <span className="text-[var(--ag-text-muted)]">—</span>
                    )}
                  </td>
                  <td className="px-4 py-3 text-xs text-[var(--ag-text-muted)]">{e.reason ?? (e.idempotency_key ? `idempotency: ${e.idempotency_key}` : "—")}</td>
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
