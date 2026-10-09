import { useState } from "react";
import { useSearchParams } from "react-router-dom";
import { RefreshCw, Lock } from "lucide-react";
import {
  api,
  type Page,
  type PriceSnapshotRead,
  type RouteBindingRead,
} from "../../lib/client";
import { usePolling } from "../../lib/usePolling";
import { formatMoney } from "../../lib/decimal";
import Pagination from "../../components/ui/Pagination";
import EmptyState from "../../components/ui/EmptyState";
import { Field, Select, TextInput } from "../../components/ui/Form";

const PAGE_SIZE = 20;

function formatCapturedAt(iso: string): string {
  return iso.replace("T", " ").replace(/\.\d+Z?$/, "").replace("Z", " UTC");
}

export default function PriceSnapshotsPage() {
  const [searchParams] = useSearchParams();
  const [page, setPage] = useState(0);
  const [routeFilter, setRouteFilter] = useState(searchParams.get("route") ?? "");
  const [sourcePolicyFilter, setSourcePolicyFilter] = useState("");
  const [modelAliasFilter, setModelAliasFilter] = useState("");
  const [accountFilter, setAccountFilter] = useState("");

  const list = usePolling<Page<PriceSnapshotRead>>(
    () =>
      api.listPriceSnapshots({
        limit: PAGE_SIZE,
        offset: page * PAGE_SIZE,
        ...(routeFilter ? { route_binding_id: routeFilter } : {}),
        ...(sourcePolicyFilter ? { source_price_policy_id: sourcePolicyFilter } : {}),
        ...(modelAliasFilter ? { model_alias_id: modelAliasFilter } : {}),
        ...(accountFilter ? { provider_account_id: accountFilter } : {}),
      }),
    60_000,
    [page, routeFilter, sourcePolicyFilter, modelAliasFilter, accountFilter],
  );
  const routeBindings = usePolling<RouteBindingRead[] | null>(
    async () => (await api.listRouteBindings({ limit: 200 })).items,
    60_000,
    [],
  );

  const total = list.data?.total ?? 0;

  return (
    <div className="space-y-6 max-w-6xl">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-xl font-bold flex items-center gap-2">
            Price Snapshots <Lock size={16} className="text-[var(--ag-text-muted)]" />
          </h2>
          <p className="text-sm text-[var(--ag-text-muted)] mt-1">
            Immutable pricing captured at dispatch time. Historical requests keep their snapshot even
            when the price policy changes later.
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
        <div className="max-w-xs">
          <Field label="Route binding" htmlFor="snapshot-route">
            <Select id="snapshot-route" value={routeFilter} onChange={(e) => { setRouteFilter(e.target.value); setPage(0); }}>
              <option value="">All route bindings</option>
              {(routeBindings.data ?? []).map((b) => (
                <option key={b.id} value={b.id}>{b.upstream_model ?? b.model_alias_id}</option>
              ))}
            </Select>
          </Field>
        </div>
        <div className="max-w-xs">
          <Field label="Source policy" htmlFor="snapshot-policy">
            <TextInput id="snapshot-policy" value={sourcePolicyFilter} onChange={(e) => { setSourcePolicyFilter(e.target.value); setPage(0); }} placeholder="Filter by policy ID" />
          </Field>
        </div>
        <div className="max-w-xs">
          <Field label="Model alias" htmlFor="snapshot-alias">
            <TextInput id="snapshot-alias" value={modelAliasFilter} onChange={(e) => { setModelAliasFilter(e.target.value); setPage(0); }} placeholder="Filter by alias ID" />
          </Field>
        </div>
        <div className="max-w-xs">
          <Field label="Provider account" htmlFor="snapshot-account">
            <TextInput id="snapshot-account" value={accountFilter} onChange={(e) => { setAccountFilter(e.target.value); setPage(0); }} placeholder="Filter by account ID" />
          </Field>
        </div>
      </div>

      {list.error ? <p className="text-sm text-[var(--ag-danger)]">Error: {list.error}</p> : null}

      <div className="bg-[var(--ag-surface)] border border-[var(--ag-border)] rounded-xl overflow-hidden">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-[var(--ag-border)] text-left text-xs text-[var(--ag-text-muted)] uppercase tracking-wider">
              <th className="px-4 py-3">Captured at</th>
              <th className="px-4 py-3">Source policy</th>
              <th className="px-4 py-3">Route</th>
              <th className="px-4 py-3">Billing</th>
              <th className="px-4 py-3">Price</th>
            </tr>
          </thead>
          <tbody>
            {list.loading && !list.data ? (
              <tr><td colSpan={5} className="px-4 py-6 text-[var(--ag-text-muted)]">Loading…</td></tr>
            ) : (list.data?.items ?? []).length === 0 ? (
              <tr><td colSpan={5}><EmptyState message="No price snapshots." /></td></tr>
            ) : (
              (list.data?.items ?? []).map((s) => (
                <tr key={s.id} className="border-b border-[var(--ag-border)] last:border-0 hover:bg-[var(--ag-surface-2)]/40">
                  <td className="px-4 py-3 text-xs font-mono">{formatCapturedAt(s.captured_at)}</td>
                  <td className="px-4 py-3 font-mono text-xs text-[var(--ag-text-muted)]">{s.source_price_policy_id}</td>
                  <td className="px-4 py-3 font-mono text-xs text-[var(--ag-text-muted)]">{s.route_binding_id}</td>
                  <td className="px-4 py-3 text-xs">{s.billing_unit}</td>
                  <td className="px-4 py-3 text-xs">
                    {s.billing_unit === "token"
                      ? `${formatMoney(s.input_price, s.currency)} in / ${formatMoney(s.output_price, s.currency)} out (per ${s.unit_scale})`
                      : formatMoney(s.request_price, s.currency)}
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
