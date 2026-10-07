import { useState } from "react";
import { Link } from "react-router-dom";
import { RotateCcw, RefreshCw, ChevronLeft, ChevronRight } from "lucide-react";
import { api, ApiError, type Page, type QueueRequestRead } from "../lib/client";
import { useAuth } from "../context/auth-context";
import { isSystemAdmin } from "../lib/roles";
import { usePolling } from "../lib/usePolling";
import { stateLabel, formatTimestamp } from "../lib/format";

const PAGE_SIZE = 20;

export default function OutcomeUnknownPage() {
  const { roles } = useAuth();
  const systemAdmin = isSystemAdmin(roles);

  const [page, setPage] = useState(0);
  const [pending, setPending] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  const list = usePolling<Page<QueueRequestRead>>(
    () => api.outcomeUnknown({ limit: PAGE_SIZE, offset: page * PAGE_SIZE }),
    4000,
    [page],
  );

  const total = list.data?.total ?? 0;
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  if (!systemAdmin) {
    return <p className="text-sm text-[var(--ag-text-muted)]">Not authorized.</p>;
  }

  const onReconcile = async (request: QueueRequestRead, disposition: "failed" | "cancelled") => {
    if (!window.confirm(`Reconcile request ${request.request_id} as ${disposition}?`)) return;
    setPending(`${request.request_id}:${disposition}`);
    setActionError(null);
    try {
      await api.reconcileRequest(request.request_id, disposition);
      list.refresh();
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setPending(null);
    }
  };

  return (
    <div className="space-y-6 max-w-6xl">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-xl font-bold">Outcome Unknown</h2>
          <p className="text-sm text-[var(--ag-text-muted)] mt-1">
            {total.toLocaleString()} request{total === 1 ? "" : "s"} with an unknown outcome
          </p>
        </div>
        <button
          type="button"
          onClick={list.refresh}
          className="flex items-center gap-2 text-xs px-3 py-2 rounded-lg border border-[var(--ag-border)] text-[var(--ag-text-muted)] hover:text-[var(--ag-text)] hover:bg-[var(--ag-surface-2)] transition-colors"
        >
          <RefreshCw size={14} />
          Refresh
        </button>
      </div>

      {actionError ? <p className="text-sm text-[var(--ag-danger)]">{actionError}</p> : null}
      {list.error ? <p className="text-sm text-[var(--ag-danger)]">Error: {list.error}</p> : null}

      <div className="bg-[var(--ag-surface)] border border-[var(--ag-border)] rounded-xl overflow-hidden">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-[var(--ag-border)] text-left text-xs text-[var(--ag-text-muted)] uppercase tracking-wider">
              <th className="px-4 py-3">State</th>
              <th className="px-4 py-3">Request</th>
              <th className="px-4 py-3">Model</th>
              <th className="px-4 py-3">Endpoint</th>
              <th className="px-4 py-3">Queued</th>
              <th className="px-4 py-3">Actions</th>
            </tr>
          </thead>
          <tbody>
            {list.loading && !list.data ? (
              <tr>
                <td colSpan={6} className="px-4 py-6 text-[var(--ag-text-muted)]">Loading…</td>
              </tr>
            ) : (list.data?.items ?? []).length === 0 ? (
              <tr>
                <td colSpan={6} className="px-4 py-6 text-[var(--ag-text-muted)]">No requests.</td>
              </tr>
            ) : (
              (list.data?.items ?? []).map((r) => (
                <tr key={r.request_id} className="border-b border-[var(--ag-border)] last:border-0 hover:bg-[var(--ag-surface-2)]/40">
                  <td className="px-4 py-3 text-xs font-medium text-[var(--ag-warning)]">
                    {stateLabel(r.state)}
                  </td>
                  <td className="px-4 py-3">
                    <Link
                      to={`/queue/${r.request_id}`}
                      className="font-mono text-xs text-[var(--ag-accent)] hover:underline"
                    >
                      {r.request_id.slice(0, 8)}…
                    </Link>
                  </td>
                  <td className="px-4 py-3 text-xs">{r.model_alias_id}</td>
                  <td className="px-4 py-3 text-xs">{r.endpoint_id ?? "—"}</td>
                  <td className="px-4 py-3 text-xs">{formatTimestamp(r.queued_at)}</td>
                  <td className="px-4 py-3">
                    <div className="flex items-center gap-2">
                      <button
                        type="button"
                        disabled={pending !== null}
                        onClick={() => void onReconcile(r, "failed")}
                        className="inline-flex items-center gap-1 text-xs px-2 py-1 rounded-md border border-[var(--ag-border)] text-[var(--ag-warning)] hover:bg-[var(--ag-warning)]/10 disabled:opacity-50 transition-colors"
                      >
                        <RotateCcw size={13} />
                        {pending === `${r.request_id}:failed` ? "…" : "failed"}
                      </button>
                      <button
                        type="button"
                        disabled={pending !== null}
                        onClick={() => void onReconcile(r, "cancelled")}
                        className="inline-flex items-center gap-1 text-xs px-2 py-1 rounded-md border border-[var(--ag-border)] text-[var(--ag-warning)] hover:bg-[var(--ag-warning)]/10 disabled:opacity-50 transition-colors"
                      >
                        <RotateCcw size={13} />
                        {pending === `${r.request_id}:cancelled` ? "…" : "cancelled"}
                      </button>
                    </div>
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>

      <div className="flex items-center justify-between">
        <button
          type="button"
          disabled={page === 0}
          onClick={() => setPage((p) => Math.max(0, p - 1))}
          className="flex items-center gap-1 text-xs px-3 py-2 rounded-lg border border-[var(--ag-border)] text-[var(--ag-text-muted)] hover:text-[var(--ag-text)] disabled:opacity-40 transition-colors"
        >
          <ChevronLeft size={14} />
          Prev
        </button>
        <span className="text-xs text-[var(--ag-text-muted)]">
          Page {page + 1} of {totalPages}
        </span>
        <button
          type="button"
          disabled={page + 1 >= totalPages}
          onClick={() => setPage((p) => p + 1)}
          className="flex items-center gap-1 text-xs px-3 py-2 rounded-lg border border-[var(--ag-border)] text-[var(--ag-text-muted)] hover:text-[var(--ag-text)] disabled:opacity-40 transition-colors"
        >
          Next
          <ChevronRight size={14} />
        </button>
      </div>
    </div>
  );
}
