import { useState, type ReactNode } from "react";
import {
  Clock,
  ListOrdered,
  Activity,
  TriangleAlert,
  Server,
  ShieldAlert,
  Wallet,
  RefreshCw,
  Gauge,
  Zap,
  Repeat,
} from "lucide-react";
import {
  api,
  ApiError,
  type QueueSummaryRead,
  type EndpointRuntimeRead,
  type QuotaStatusRead,
  type BudgetStatusRead,
  type ObservabilitySummaryRead,
  type UpstreamHealthRead,
} from "../lib/client";
import { useAuth } from "../context/auth-context";
import { isSystemAdmin, hasRole } from "../lib/roles";
import { usePolling } from "../lib/usePolling";
import {
  formatDuration,
  formatMillis,
  formatRate,
  operationalStateLabel,
} from "../lib/format";
import { compareDecimalToZero } from "../lib/decimal";

const WINDOWS: { label: string; seconds: number }[] = [
  { label: "15m", seconds: 900 },
  { label: "1h", seconds: 3600 },
  { label: "24h", seconds: 86400 },
];

function StatCard({
  label,
  value,
  icon,
  hint,
}: {
  label: string;
  value: string | number;
  icon: ReactNode;
  hint?: string;
}) {
  return (
    <div className="bg-[var(--ag-surface)] border border-[var(--ag-border)] rounded-xl p-5">
      <div className="flex items-center justify-between mb-3">
        <span className="text-xs font-medium uppercase tracking-wider text-[var(--ag-text-muted)]">
          {label}
        </span>
        <span className="text-[var(--ag-accent)]">{icon}</span>
      </div>
      <p className="text-2xl font-bold">{value}</p>
      {hint ? <p className="text-xs text-[var(--ag-text-muted)] mt-1">{hint}</p> : null}
    </div>
  );
}

function PercentileGrid({
  title,
  subtitle,
  metrics,
  icon,
}: {
  title: string;
  subtitle: string;
  metrics: ObservabilitySummaryRead["queue_wait"];
  icon: ReactNode;
}) {
  return (
    <div className="bg-[var(--ag-surface)] border border-[var(--ag-border)] rounded-xl p-5">
      <div className="flex items-center justify-between mb-1">
        <span className="text-xs font-medium uppercase tracking-wider text-[var(--ag-text-muted)]">
          {title}
        </span>
        <span className="text-[var(--ag-accent)]">{icon}</span>
      </div>
      <p className="text-[11px] text-[var(--ag-text-muted)] mb-3">{subtitle}</p>
      {metrics.sample_count === 0 ? (
        <p className="text-sm text-[var(--ag-text-muted)]">No samples</p>
      ) : (
        <div className="grid grid-cols-3 gap-2">
          {(["p50_ms", "p95_ms", "p99_ms"] as const).map((key) => (
            <div key={key}>
              <div className="text-[10px] uppercase text-[var(--ag-text-muted)]">
                {key.replace("_ms", "")}
              </div>
              <div className="text-sm font-semibold">{formatMillis(metrics[key])}</div>
            </div>
          ))}
        </div>
      )}
      <p className="text-[11px] text-[var(--ag-text-muted)] mt-2">
        {metrics.sample_count} sample{metrics.sample_count === 1 ? "" : "s"}
      </p>
    </div>
  );
}

function RetryCard({ retry }: { retry: ObservabilitySummaryRead["retry"] }) {
  return (
    <div className="bg-[var(--ag-surface)] border border-[var(--ag-border)] rounded-xl p-5">
      <div className="flex items-center justify-between mb-1">
        <span className="text-xs font-medium uppercase tracking-wider text-[var(--ag-text-muted)]">
          Retry rate
        </span>
        <span className="text-[var(--ag-accent)]">
          <Repeat size={18} />
        </span>
      </div>
      <p className="text-[11px] text-[var(--ag-text-muted)] mb-3">
        Additional execution attempts for the same gateway request
      </p>
      {retry.attempted_requests === 0 ? (
        <p className="text-sm text-[var(--ag-text-muted)]">No samples</p>
      ) : (
        <>
          <p className="text-2xl font-bold">{formatRate(retry.request_retry_rate)}</p>
          <p className="text-[11px] text-[var(--ag-text-muted)] mt-1">
            {retry.retried_requests} retried / {retry.attempted_requests} attempted ·{" "}
            {retry.retry_attempts} retry attempt
            {retry.retry_attempts === 1 ? "" : "s"}
          </p>
        </>
      )}
    </div>
  );
}

function UpstreamHealthCard({ row }: { row: UpstreamHealthRead }) {
  const hasDefinitive = row.succeeded_attempts + row.upstream_failed_attempts > 0;
  return (
    <div className="bg-[var(--ag-surface)] border border-[var(--ag-border)] rounded-xl p-4">
      <div className="flex items-center justify-between mb-1">
        <span className="text-sm font-medium truncate">{row.endpoint_name}</span>
        <span className="text-xs text-[var(--ag-text-muted)]">
          {hasDefinitive ? formatRate(row.upstream_success_rate) : "No samples"}
        </span>
      </div>
      <div className="text-[11px] text-[var(--ag-text-muted)]">
        {row.succeeded_attempts} success · {row.upstream_failed_attempts} upstream fail ·{" "}
        {row.ambiguous_attempts} ambiguous · {row.rate_limited_attempts} 429
      </div>
      <div className="text-[11px] text-[var(--ag-text-muted)] mt-1">
        last success: {row.last_success_at ?? "—"} · last failure:{" "}
        {row.last_failure_at ?? "—"}
      </div>
      {row.cooldown_until ? (
        <div className="text-[11px] text-[var(--ag-warning)] mt-1">
          Cooldown until {new Date(row.cooldown_until).toLocaleTimeString()}
        </div>
      ) : null}
    </div>
  );
}

function SlotRow({
  endpoint,
  pending,
  onAction,
}: {
  endpoint: EndpointRuntimeRead;
  pending: string | null;
  onAction: (endpointId: string, action: "pause" | "drain" | "resume") => void;
}) {
  const pct =
    endpoint.max_concurrency > 0
      ? Math.round((endpoint.occupied_slots / endpoint.max_concurrency) * 100)
      : 0;
  const state = endpoint.operational_state;

  const actionButton = (action: "pause" | "drain" | "resume", label: string) => (
    <button
      key={action}
      type="button"
      disabled={pending !== null}
      onClick={() => onAction(endpoint.endpoint_id, action)}
      className="inline-flex items-center gap-1 text-[11px] px-2 py-1 rounded-md border border-[var(--ag-border)] text-[var(--ag-text-muted)] hover:text-[var(--ag-text)] hover:bg-[var(--ag-surface-2)] disabled:opacity-50 transition-colors"
    >
      {pending === `${endpoint.endpoint_id}:${action}` ? "…" : label}
    </button>
  );

  return (
    <div className="bg-[var(--ag-surface)] border border-[var(--ag-border)] rounded-xl p-5">
      <div className="flex items-center justify-between mb-2">
        <span className="text-sm font-medium truncate">{endpoint.name}</span>
        <span className="text-xs text-[var(--ag-text-muted)]">{endpoint.endpoint_id}</span>
      </div>
      <div className="text-xs text-[var(--ag-text-muted)] mb-1">
        {endpoint.occupied_slots} / {endpoint.max_concurrency} slots
      </div>
      <div className="h-2 rounded-full bg-[var(--ag-surface-2)] overflow-hidden">
        <div
          className="h-full bg-[var(--ag-accent)]"
          style={{ width: `${pct}%` }}
        />
      </div>
      <div className="text-xs mt-2">
        <span
          className={
            endpoint.is_active ? "text-[var(--ag-success)]" : "text-[var(--ag-warning)]"
          }
        >
          {endpoint.is_active ? "active" : "inactive"}
        </span>
        <span className="text-[var(--ag-text-muted)]"> · </span>
        <span className="text-[var(--ag-text-muted)]">
          {operationalStateLabel(state)}
        </span>
        {endpoint.draining_complete ? (
          <span className="ml-2 text-[var(--ag-warning)]">(draining complete)</span>
        ) : null}
      </div>
      <div className="flex items-center gap-2 mt-3">
        {state === "active" ? (
          <>
            {actionButton("pause", "Pause")}
            {actionButton("drain", "Drain")}
          </>
        ) : (
          actionButton("resume", "Resume")
        )}
      </div>
    </div>
  );
}

function QuotaRow({ quota }: { quota: QuotaStatusRead }) {
  const blocked = quota.enabled && quota.remaining_units <= 0;
  return (
    <div className="bg-[var(--ag-surface)] border border-[var(--ag-border)] rounded-xl p-4">
      <div className="flex items-center justify-between mb-1">
        <span className="text-sm font-medium truncate">{quota.quota_group_name}</span>
        <span
          className={`text-xs px-2 py-0.5 rounded-md ${
            blocked ? "bg-[var(--ag-danger)]/15 text-[var(--ag-danger)]" : "bg-[var(--ag-success)]/15 text-[var(--ag-success)]"
          }`}
        >
          {blocked ? "blocked" : "ok"}
        </span>
      </div>
      <div className="text-xs text-[var(--ag-text-muted)]">
        {quota.metric}: {quota.remaining_units.toLocaleString()} / {quota.limit_units.toLocaleString()} remaining
      </div>
      {quota.cooldown_until ? (
        <div className="text-xs text-[var(--ag-warning)] mt-1">
          Cooldown until {new Date(quota.cooldown_until).toLocaleTimeString()}
        </div>
      ) : null}
    </div>
  );
}

function BudgetRow({ budget }: { budget: BudgetStatusRead }) {
  const blocked = budget.enabled && compareDecimalToZero(budget.headroom) <= 0;
  return (
    <div className="bg-[var(--ag-surface)] border border-[var(--ag-border)] rounded-xl p-4">
      <div className="flex items-center justify-between mb-1">
        <span className="text-sm font-medium">Budget</span>
        <span
          className={`text-xs px-2 py-0.5 rounded-md ${
            blocked ? "bg-[var(--ag-danger)]/15 text-[var(--ag-danger)]" : "bg-[var(--ag-success)]/15 text-[var(--ag-success)]"
          }`}
        >
          {blocked ? "exhausted" : "ok"}
        </span>
      </div>
      <div className="text-xs text-[var(--ag-text-muted)]">
        {budget.headroom} {budget.currency} headroom of {budget.limit_amount} {budget.currency}
      </div>
    </div>
  );
}

export default function DashboardPage() {
  const { session, roles } = useAuth();
  const projectScoped = hasRole(roles, "project_admin") || hasRole(roles, "project_viewer");
  const systemAdmin = isSystemAdmin(roles);

  const [endpointPending, setEndpointPending] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [windowSeconds, setWindowSeconds] = useState(900);

  const summary = usePolling<QueueSummaryRead>(() => api.queueSummary(), 4000);
  const endpoints = usePolling<EndpointRuntimeRead[] | null>(
    async () => (systemAdmin ? (await api.queueEndpoints()).items : null),
    4000,
    [systemAdmin],
  );
  const quotas = usePolling<QuotaStatusRead[] | null>(
    async () => (systemAdmin ? (await api.queueQuotaStatus()).items : null),
    4000,
    [systemAdmin],
  );
  const budgets = usePolling<BudgetStatusRead[] | null>(
    async () =>
      projectScoped && session?.project_id
        ? await api.budgetStatus(session.project_id)
        : null,
    4000,
    [projectScoped, session?.project_id],
  );
  const observability = usePolling<ObservabilitySummaryRead>(
    () => api.observabilitySummary({ window_seconds: windowSeconds }),
    4500,
    [windowSeconds],
  );
  const upstreams = usePolling<UpstreamHealthRead[] | null>(
    async () =>
      systemAdmin
        ? (await api.observabilityUpstreams({ window_seconds: windowSeconds })).items
        : null,
    4500,
    [systemAdmin, windowSeconds],
  );

  const handleEndpointAction = async (
    endpointId: string,
    action: "pause" | "drain" | "resume",
  ) => {
    if (!window.confirm(`${action} endpoint ${endpointId}?`)) return;
    setEndpointPending(`${endpointId}:${action}`);
    setActionError(null);
    try {
      if (action === "pause") await api.pauseEndpoint(endpointId);
      else if (action === "drain") await api.drainEndpoint(endpointId);
      else await api.resumeEndpoint(endpointId);
      endpoints.refresh();
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setEndpointPending(null);
    }
  };

  const error = summary.error;

  return (
    <div className="space-y-8 max-w-6xl">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-xl font-bold">Dashboard</h2>
          <p className="text-sm text-[var(--ag-text-muted)] mt-1">Live operator overview</p>
        </div>
        <div className="flex items-center gap-3">
          <div
            role="group"
            aria-label="Observation window"
            className="inline-flex rounded-lg border border-[var(--ag-border)] overflow-hidden"
          >
            {WINDOWS.map((w) => (
              <button
                key={w.seconds}
                type="button"
                aria-pressed={windowSeconds === w.seconds}
                onClick={() => setWindowSeconds(w.seconds)}
                className={`text-xs px-3 py-2 transition-colors ${
                  windowSeconds === w.seconds
                    ? "bg-[var(--ag-surface-2)] text-[var(--ag-text)]"
                    : "text-[var(--ag-text-muted)] hover:text-[var(--ag-text)]"
                }`}
              >
                {w.label}
              </button>
            ))}
          </div>
          <button
            type="button"
            onClick={() => {
              summary.refresh();
              endpoints.refresh();
              quotas.refresh();
              budgets.refresh();
              observability.refresh();
              upstreams.refresh();
            }}
            className="flex items-center gap-2 text-xs px-3 py-2 rounded-lg border border-[var(--ag-border)] text-[var(--ag-text-muted)] hover:text-[var(--ag-text)] hover:bg-[var(--ag-surface-2)] transition-colors"
          >
            <RefreshCw size={14} />
            Refresh
          </button>
        </div>
      </div>

      {error ? (
        <p className="text-sm text-[var(--ag-danger)]">Error: {error}</p>
      ) : null}
      {actionError ? (
        <p className="text-sm text-[var(--ag-danger)]">Error: {actionError}</p>
      ) : null}

      {summary.loading && !summary.data ? (
        <p className="text-sm text-[var(--ag-text-muted)]">Loading…</p>
      ) : summary.data ? (
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
          <StatCard
            label="Queued"
            value={summary.data.queued_total}
            icon={<ListOrdered size={18} />}
          />
          <StatCard
            label="In Flight"
            value={summary.data.in_flight_total}
            icon={<Activity size={18} />}
          />
          <StatCard
            label="Outcome Unknown"
            value={summary.data.outcome_unknown_total}
            icon={<TriangleAlert size={18} />}
          />
          <StatCard
            label="Oldest Wait"
            value={formatDuration(summary.data.oldest_wait_seconds)}
            icon={<Clock size={18} />}
            hint={
              summary.data.oldest_queued_at
                ? `queued ${new Date(summary.data.oldest_queued_at).toLocaleTimeString()}`
                : undefined
            }
          />
        </div>
      ) : null}

      {/* Observability: queue wait, streaming TTFT, retry rate */}
      <section>
        <h3 className="text-sm font-semibold mb-3 flex items-center gap-2">
          <Gauge size={16} className="text-[var(--ag-accent)]" />
          Performance
        </h3>
        {observability.error ? (
          <p className="text-sm text-[var(--ag-danger)]">Error: {observability.error}</p>
        ) : observability.data ? (
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
            <PercentileGrid
              title="Queue wait"
              subtitle="Admission delay: earliest attempt start − queued"
              metrics={observability.data.queue_wait}
              icon={<Clock size={18} />}
            />
            <PercentileGrid
              title="Streaming TTFT"
              subtitle="Dispatch-to-first-token (streaming only): first token − attempt start"
              metrics={observability.data.ttft}
              icon={<Zap size={18} />}
            />
            <RetryCard retry={observability.data.retry} />
          </div>
        ) : (
          <p className="text-sm text-[var(--ag-text-muted)]">Loading…</p>
        )}
      </section>

      {/* Deployment-only: upstream health */}
      {systemAdmin ? (
        <section>
          <h3 className="text-sm font-semibold mb-3 flex items-center gap-2">
            <Activity size={16} className="text-[var(--ag-accent)]" />
            Upstream health
          </h3>
          {upstreams.data && upstreams.data.length > 0 ? (
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
              {upstreams.data.map((row) => (
                <UpstreamHealthCard key={row.endpoint_id} row={row} />
              ))}
            </div>
          ) : (
            <p className="text-sm text-[var(--ag-text-muted)]">No samples</p>
          )}
        </section>
      ) : null}

      {/* Deployment-only: endpoint slots + quota status */}
      {systemAdmin ? (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-8">
          <section>
            <h3 className="text-sm font-semibold mb-3 flex items-center gap-2">
              <Server size={16} className="text-[var(--ag-accent)]" />
              Endpoints
            </h3>
            {endpoints.loading && !endpoints.data ? (
              <p className="text-sm text-[var(--ag-text-muted)]">Loading…</p>
            ) : (
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                {(endpoints.data ?? []).map((e) => (
                  <SlotRow
                    key={e.endpoint_id}
                    endpoint={e}
                    pending={endpointPending}
                    onAction={(id, action) => void handleEndpointAction(id, action)}
                  />
                ))}
              </div>
            )}
          </section>
          <section>
            <h3 className="text-sm font-semibold mb-3 flex items-center gap-2">
              <ShieldAlert size={16} className="text-[var(--ag-accent)]" />
              Quota / Cooldown
            </h3>
            {quotas.loading && !quotas.data ? (
              <p className="text-sm text-[var(--ag-text-muted)]">Loading…</p>
            ) : (
              <div className="grid grid-cols-1 gap-4">
                {(quotas.data ?? []).map((q) => (
                  <QuotaRow key={q.quota_limit_id} quota={q} />
                ))}
              </div>
            )}
          </section>
        </div>
      ) : null}

      {/* Project-scoped: budget headroom */}
      {projectScoped ? (
        <section>
          <h3 className="text-sm font-semibold mb-3 flex items-center gap-2">
            <Wallet size={16} className="text-[var(--ag-accent)]" />
            Budget Headroom
          </h3>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            {(budgets.data ?? []).map((b) => (
              <BudgetRow key={b.budget_policy_id} budget={b} />
            ))}
          </div>
        </section>
      ) : null}
    </div>
  );
}
