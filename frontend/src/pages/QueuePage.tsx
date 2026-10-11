import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { XCircle, RefreshCw, ChevronLeft, ChevronRight } from "lucide-react";
import {
  api,
  ApiError,
  type Page,
  type QueueRequestRead,
  type EndpointRuntimeRead,
  type ProjectRead,
} from "../lib/client";
import { useAuth } from "../context/auth-context";
import { isSystemAdmin, canCancel } from "../lib/roles";
import { usePolling } from "../lib/usePolling";
import { stateLabel, waitReasonLabel, formatTimestamp } from "../lib/format";

const PAGE_SIZE = 20;

const STATES = [
  "validated",
  "queued",
  "reserved",
  "dispatched",
  "streaming",
  "succeeded",
  "failed",
  "cancelled",
  "expired",
  "outcome_unknown",
] as const;

const CANCELLABLE = new Set(["validated", "queued", "reserved", "dispatched", "streaming"]);

function stateColor(state: string): string {
  switch (state) {
    case "succeeded":
      return "text-[var(--ag-success)]";
    case "failed":
    case "expired":
      return "text-[var(--ag-danger)]";
    case "outcome_unknown":
      return "text-[var(--ag-warning)]";
    case "queued":
    case "reserved":
    case "dispatched":
    case "streaming":
      return "text-[var(--ag-accent)]";
    default:
      return "text-[var(--ag-text-muted)]";
  }
}

export default function QueuePage() {
  const { roles } = useAuth();
  const systemAdmin = isSystemAdmin(roles);

  const [state, setState] = useState<string>("");
  const [endpointId, setEndpointId] = useState<string>("");
  const [projectId, setProjectId] = useState<string>("");
  const [page, setPage] = useState(0);
  const [pending, setPending] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  const endpoints = usePolling<EndpointRuntimeRead[] | null>(
    async () => (systemAdmin ? (await api.queueEndpoints()).items : null),
    15000,
    [systemAdmin],
  );
  const projects = usePolling<ProjectRead[] | null>(
    async () => (systemAdmin ? (await api.listProjects()).items : null),
    15000,
    [systemAdmin],
  );

  const query = useMemo(
    () => ({
      limit: PAGE_SIZE,
      offset: page * PAGE_SIZE,
      ...(state ? { state } : {}),
      ...(endpointId ? { endpoint_id: endpointId } : {}),
      ...(projectId ? { project_id: projectId } : {}),
    }),
    [state, endpointId, projectId, page],
  );

  const list = usePolling<Page<QueueRequestRead>>(() => api.queueRequests(query), 4000, [
    state,
    endpointId,
    projectId,
    page,
  ]);

  const total = list.data?.total ?? 0;
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  const onCancel = async (request: QueueRequestRead) => {
    if (!window.confirm(`Cancel request ${request.request_id}?`)) return;
    setPending(request.request_id);
    setActionError(null);
    try {
      await api.cancelRequest(request.request_id);
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
          <h2 className="text-xl font-bold">Queue</h2>
          <p className="text-sm text-[var(--ag-text-muted)] mt-1">
            {total.toLocaleString()} request{total === 1 ? "" : "s"}
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

      <div className="flex flex-wrap items-center gap-3">
        <select
          value={state}
          onChange={(e) => {
            setState(e.target.value);
            setPage(0);
          }}
          className="text-sm bg-[var(--ag-surface)] border border-[var(--ag-border)] rounded-lg px-3 py-2 text-[var(--ag-text)]"
        >
          <option value="">All states</option>
          {STATES.map((s) => (
            <option key={s} value={s}>
              {stateLabel(s)}
            </option>
          ))}
        </select>

        {systemAdmin ? (
          <select
            value={endpointId}
            onChange={(e) => {
              setEndpointId(e.target.value);
              setPage(0);
            }}
            className="text-sm bg-[var(--ag-surface)] border border-[var(--ag-border)] rounded-lg px-3 py-2 text-[var(--ag-text)]"
          >
            <option value="">All endpoints</option>
            {(endpoints.data ?? []).map((e) => (
              <option key={e.endpoint_id} value={e.endpoint_id}>
                {e.name}
              </option>
            ))}
          </select>
        ) : null}

        {systemAdmin ? (
          <select
            value={projectId}
            onChange={(e) => {
              setProjectId(e.target.value);
              setPage(0);
            }}
            className="text-sm bg-[var(--ag-surface)] border border-[var(--ag-border)] rounded-lg px-3 py-2 text-[var(--ag-text)]"
          >
            <option value="">All projects</option>
            {(projects.data ?? []).map((p) => (
              <option key={p.id} value={p.id}>
                {p.name}
              </option>
            ))}
          </select>
        ) : null}
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
              {systemAdmin ? <th className="px-4 py-3">Project</th> : null}
              <th className="px-4 py-3">Wait reason</th>
              <th className="px-4 py-3">Queued</th>
              <th className="px-4 py-3"></th>
            </tr>
          </thead>
          <tbody>
            {list.loading && !list.data ? (
              <tr>
                <td colSpan={systemAdmin ? 8 : 7} className="px-4 py-6 text-[var(--ag-text-muted)]">
                  Loading…
                </td>
              </tr>
            ) : (list.data?.items ?? []).length === 0 ? (
              <tr>
                <td colSpan={systemAdmin ? 8 : 7} className="px-4 py-6 text-[var(--ag-text-muted)]">
                  No requests.
                </td>
              </tr>
            ) : (
              (list.data?.items ?? []).map((r) => (
                <tr key={r.request_id} className="border-b border-[var(--ag-border)] last:border-0 hover:bg-[var(--ag-surface-2)]/40">
                  <td className="px-4 py-3">
                    <span className={`text-xs font-medium ${stateColor(r.state)}`}>
                      {stateLabel(r.state)}
                    </span>
                    {r.cancellation_requested ? (
                      <span className="ml-1 text-[10px] text-[var(--ag-warning)]">(cancel req)</span>
                    ) : null}
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
                  {systemAdmin ? (
                    <td className="px-4 py-3 text-xs">{r.project_id ?? "—"}</td>
                  ) : null}
                  <td className="px-4 py-3 text-xs">{waitReasonLabel(r.effective_wait_reason)}</td>
                  <td className="px-4 py-3 text-xs">{formatTimestamp(r.queued_at)}</td>
                  <td className="px-4 py-3 text-right">
                    {canCancel(roles) && CANCELLABLE.has(r.state) ? (
                      <button
                        type="button"
                        disabled={pending === r.request_id}
                        onClick={() => void onCancel(r)}
                        className="inline-flex items-center gap-1 text-xs px-2 py-1 rounded-md border border-[var(--ag-border)] text-[var(--ag-danger)] hover:bg-[var(--ag-danger)]/10 disabled:opacity-50 transition-colors"
                      >
                        <XCircle size={13} />
                        {pending === r.request_id ? "Cancelling…" : "Cancel"}
                      </button>
                    ) : null}
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
