import { useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { RefreshCw } from "lucide-react";
import {
  api,
  type Page,
  type BudgetReservationRead,
  type BudgetReservationState,
  type ProjectRead,
} from "../../lib/client";
import { useAuth } from "../../context/auth-context";
import { isSystemAdmin } from "../../lib/roles";
import { usePolling } from "../../lib/usePolling";
import { formatMoney } from "../../lib/decimal";
import Pagination from "../../components/ui/Pagination";
import StatusBadge from "../../components/ui/StatusBadge";
import EmptyState from "../../components/ui/EmptyState";
import { Field, Select, TextInput } from "../../components/ui/Form";

const PAGE_SIZE = 20;

function stateTone(state: string): "active" | "inactive" | "warning" | "neutral" {
  if (state === "committed") return "active";
  if (state === "reserved") return "warning";
  if (state === "released") return "inactive";
  return "neutral";
}

export default function BudgetReservationsPage() {
  const { roles, session } = useAuth();
  const systemAdmin = isSystemAdmin(roles);
  const [searchParams] = useSearchParams();

  const [page, setPage] = useState(0);
  const [projectId, setProjectId] = useState("");
  const [requestFilter, setRequestFilter] = useState("");
  const [policyFilter, setPolicyFilter] = useState(searchParams.get("policy") ?? "");
  const [stateFilter, setStateFilter] = useState<BudgetReservationState | "">("");

  const projects = usePolling<ProjectRead[] | null>(
    async () => (systemAdmin ? (await api.listProjects({ limit: 200 })).items : null),
    60_000,
    [systemAdmin],
  );

  const effectiveProjectId = systemAdmin ? projectId : (session?.project_id ?? "");

  const list = usePolling<Page<BudgetReservationRead> | null>(
    async () =>
      await api.listBudgetReservations({
        limit: PAGE_SIZE,
        offset: page * PAGE_SIZE,
        ...(effectiveProjectId ? { project_id: effectiveProjectId } : {}),
        ...(requestFilter ? { request_id: requestFilter } : {}),
        ...(policyFilter ? { budget_policy_id: policyFilter } : {}),
        ...(stateFilter ? { state: stateFilter } : {}),
      }),
    60_000,
    [effectiveProjectId, page, requestFilter, policyFilter, stateFilter],
  );

  const total = list.data?.total ?? 0;

  return (
    <div className="space-y-6 max-w-6xl">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-xl font-bold">Budget Reservations</h2>
          <p className="text-sm text-[var(--ag-text-muted)] mt-1">
            Per-request monetary reservations against budget policy windows. Read-only history.
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
            <Field label="Project" htmlFor="reservation-project">
              <Select id="reservation-project" value={projectId} onChange={(e) => { setProjectId(e.target.value); setPage(0); }}>
                <option value="">All projects</option>
                {(projects.data ?? []).map((p) => (
                  <option key={p.id} value={p.id}>{p.name}</option>
                ))}
              </Select>
            </Field>
          </div>
        ) : null}
        <div className="max-w-xs">
          <Field label="Request" htmlFor="reservation-request">
            <TextInput id="reservation-request" value={requestFilter} onChange={(e) => { setRequestFilter(e.target.value); setPage(0); }} placeholder="Filter by request ID" />
          </Field>
        </div>
        <div className="max-w-xs">
          <Field label="Budget policy" htmlFor="reservation-policy">
            <TextInput id="reservation-policy" value={policyFilter} onChange={(e) => { setPolicyFilter(e.target.value); setPage(0); }} placeholder="Filter by policy ID" />
          </Field>
        </div>
        <div className="max-w-xs">
          <Field label="State" htmlFor="reservation-state">
            <Select id="reservation-state" value={stateFilter} onChange={(e) => { setStateFilter(e.target.value as BudgetReservationState | ""); setPage(0); }}>
              <option value="">All states</option>
              <option value="reserved">Reserved</option>
              <option value="committed">Committed</option>
              <option value="released">Released</option>
            </Select>
          </Field>
        </div>
      </div>

      {list.error ? <p className="text-sm text-[var(--ag-danger)]">Error: {list.error}</p> : null}

      <div className="bg-[var(--ag-surface)] border border-[var(--ag-border)] rounded-xl overflow-hidden">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-[var(--ag-border)] text-left text-xs text-[var(--ag-text-muted)] uppercase tracking-wider">
              <th className="px-4 py-3">Request</th>
              <th className="px-4 py-3">Policy</th>
              <th className="px-4 py-3">Snapshot</th>
              <th className="px-4 py-3">Reserved</th>
              <th className="px-4 py-3">Committed</th>
              <th className="px-4 py-3">State</th>
            </tr>
          </thead>
          <tbody>
            {list.loading && !list.data ? (
              <tr><td colSpan={6} className="px-4 py-6 text-[var(--ag-text-muted)]">Loading…</td></tr>
            ) : (list.data?.items ?? []).length === 0 ? (
              <tr><td colSpan={6}><EmptyState message="No budget reservations." /></td></tr>
            ) : (
              (list.data?.items ?? []).map((r) => (
                <tr key={r.id} className="border-b border-[var(--ag-border)] last:border-0 hover:bg-[var(--ag-surface-2)]/40">
                  <td className="px-4 py-3">
                    <Link to={`/queue/${r.request_id}`} className="font-mono text-xs text-[var(--ag-accent)] hover:underline">{r.request_id}</Link>
                  </td>
                  <td className="px-4 py-3 font-mono text-xs text-[var(--ag-text-muted)]">{r.budget_policy_id}</td>
                  <td className="px-4 py-3 text-xs">
                    {r.price_snapshot_id ? (
                      <span className="font-mono">{r.price_snapshot_id}</span>
                    ) : (
                      <span className="text-[var(--ag-text-muted)]">No snapshot / released before dispatch</span>
                    )}
                  </td>
                  <td className="px-4 py-3 font-mono text-xs">{formatMoney(r.reserved_amount)}</td>
                  <td className="px-4 py-3 font-mono text-xs">{formatMoney(r.committed_amount)}</td>
                  <td className="px-4 py-3">
                    <StatusBadge label={r.state} tone={stateTone(r.state)} />
                    {r.settlement_reason ? <span className="ml-2 text-xs text-[var(--ag-text-muted)]">{r.settlement_reason}</span> : null}
                  </td>
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
