import { useState } from "react";
import { useParams, Link } from "react-router-dom";
import { ArrowLeft, XCircle, RotateCcw } from "lucide-react";
import { api, ApiError, type QueueRequestRead } from "../lib/client";
import { useAuth } from "../context/auth-context";
import { isSystemAdmin, canCancel } from "../lib/roles";
import { usePolling } from "../lib/usePolling";
import { stateLabel, waitReasonLabel, formatTimestamp } from "../lib/format";

const CANCELLABLE = new Set(["validated", "queued", "reserved", "dispatched", "streaming"]);

function Field({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex flex-col gap-0.5">
      <span className="text-xs text-[var(--ag-text-muted)] uppercase tracking-wider">{label}</span>
      <span className="text-sm font-mono break-all">{value || "—"}</span>
    </div>
  );
}

export default function QueueDetailPage() {
  const { requestId } = useParams<{ requestId: string }>();
  const { roles } = useAuth();
  const systemAdmin = isSystemAdmin(roles);

  const [pending, setPending] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  const detail = usePolling<QueueRequestRead | null>(
    async () => (requestId ? await api.queueRequest(requestId) : null),
    4000,
    [requestId],
  );

  const request = detail.data;

  const onCancel = async () => {
    if (!request || !window.confirm(`Cancel request ${request.request_id}?`)) return;
    setPending("cancel");
    setActionError(null);
    try {
      await api.cancelRequest(request.request_id);
      detail.refresh();
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setPending(null);
    }
  };

  const onReconcile = async (disposition: "failed" | "cancelled") => {
    if (!request) return;
    if (!window.confirm(`Reconcile request ${request.request_id} as ${disposition}?`)) return;
    setPending(`reconcile:${disposition}`);
    setActionError(null);
    try {
      await api.reconcileRequest(request.request_id, disposition);
      detail.refresh();
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setPending(null);
    }
  };

  if (detail.loading && !request) {
    return <p className="text-sm text-[var(--ag-text-muted)]">Loading…</p>;
  }
  if (detail.error) {
    return (
      <div className="space-y-4">
        <Link to="/queue" className="inline-flex items-center gap-1 text-sm text-[var(--ag-accent)]">
          <ArrowLeft size={16} /> Back to queue
        </Link>
        <p className="text-sm text-[var(--ag-danger)]">Error: {detail.error}</p>
      </div>
    );
  }
  if (!request) return null;

  return (
    <div className="space-y-6 max-w-4xl">
      <div className="flex items-center justify-between">
        <div>
          <Link to="/queue" className="inline-flex items-center gap-1 text-sm text-[var(--ag-accent)]">
            <ArrowLeft size={16} /> Back to queue
          </Link>
          <h2 className="text-xl font-bold mt-2">{request.request_id}</h2>
        </div>
        <span className="text-xs font-medium text-[var(--ag-text-muted)] uppercase">
          {stateLabel(request.state)}
        </span>
      </div>

      {actionError ? <p className="text-sm text-[var(--ag-danger)]">{actionError}</p> : null}

      <div className="flex flex-wrap gap-3">
        {canCancel(roles) && CANCELLABLE.has(request.state) ? (
          <button
            type="button"
            disabled={pending !== null}
            onClick={() => void onCancel()}
            className="inline-flex items-center gap-2 text-sm px-3 py-2 rounded-lg border border-[var(--ag-border)] text-[var(--ag-danger)] hover:bg-[var(--ag-danger)]/10 disabled:opacity-50 transition-colors"
          >
            <XCircle size={16} />
            {pending === "cancel" ? "Cancelling…" : "Cancel request"}
          </button>
        ) : null}

        {systemAdmin && request.state === "outcome_unknown" ? (
          <>
            <button
              type="button"
              disabled={pending !== null}
              onClick={() => void onReconcile("failed")}
              className="inline-flex items-center gap-2 text-sm px-3 py-2 rounded-lg border border-[var(--ag-border)] text-[var(--ag-warning)] hover:bg-[var(--ag-warning)]/10 disabled:opacity-50 transition-colors"
            >
              <RotateCcw size={16} />
              {pending === "reconcile:failed" ? "Reconciling…" : "Reconcile as failed"}
            </button>
            <button
              type="button"
              disabled={pending !== null}
              onClick={() => void onReconcile("cancelled")}
              className="inline-flex items-center gap-2 text-sm px-3 py-2 rounded-lg border border-[var(--ag-border)] text-[var(--ag-warning)] hover:bg-[var(--ag-warning)]/10 disabled:opacity-50 transition-colors"
            >
              <RotateCcw size={16} />
              {pending === "reconcile:cancelled" ? "Reconciling…" : "Reconcile as cancelled"}
            </button>
          </>
        ) : null}
      </div>

      <div className="bg-[var(--ag-surface)] border border-[var(--ag-border)] rounded-xl p-6 grid grid-cols-2 sm:grid-cols-3 gap-6">
        <Field label="State" value={stateLabel(request.state)} />
        <Field label="Model alias" value={request.model_alias_id} />
        <Field label="Endpoint" value={request.endpoint_id ?? "—"} />
        <Field label="Project" value={request.project_id ?? "—"} />
        <Field label="Principal" value={request.principal_id ?? "—"} />
        <Field label="API credential" value={request.api_credential_id ?? "—"} />
        <Field label="Stream" value={request.stream ? "true" : "false"} />
        <Field label="Wait reason" value={waitReasonLabel(request.effective_wait_reason)} />
        <Field label="Error code" value={request.error_code ?? "—"} />
        <Field label="Cancellation requested" value={request.cancellation_requested ? "true" : "false"} />
        <Field label="Queued at" value={formatTimestamp(request.queued_at)} />
        <Field label="Started at" value={formatTimestamp(request.started_at)} />
        <Field label="Finished at" value={formatTimestamp(request.finished_at)} />
        <Field label="Expires at" value={formatTimestamp(request.expires_at)} />
        <Field label="Next eligible at" value={formatTimestamp(request.next_eligible_at)} />
        <Field label="Worker" value={request.worker_id ?? "—"} />
        <Field label="Reconciled state" value={request.reconciled_state ?? "—"} />
      </div>
    </div>
  );
}
