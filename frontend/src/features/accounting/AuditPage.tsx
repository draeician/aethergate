import { useState } from "react";
import { RefreshCw } from "lucide-react";
import {
  api,
  type Page,
  type AuditEventRead,
  type ProjectRead,
} from "../../lib/client";
import { useAuth } from "../../context/auth-context";
import { isSystemAdmin } from "../../lib/roles";
import { usePolling } from "../../lib/usePolling";
import Pagination from "../../components/ui/Pagination";
import EmptyState from "../../components/ui/EmptyState";
import { Field, Select, TextInput } from "../../components/ui/Form";

const PAGE_SIZE = 20;

function formatOccurredAt(iso: string): string {
  return iso.replace("T", " ").replace(/\.\d+Z?$/, "").replace("Z", " UTC");
}

function formatMetadata(metadata: Record<string, unknown> | undefined): string {
  if (!metadata || Object.keys(metadata).length === 0) return "—";
  try {
    return JSON.stringify(metadata);
  } catch {
    return "—";
  }
}

export default function AuditPage() {
  const { roles, session } = useAuth();
  const systemAdmin = isSystemAdmin(roles);

  const [page, setPage] = useState(0);
  const [projectId, setProjectId] = useState("");
  const [actorFilter, setActorFilter] = useState("");
  const [actionFilter, setActionFilter] = useState("");
  const [resourceTypeFilter, setResourceTypeFilter] = useState("");
  const [resourceIdFilter, setResourceIdFilter] = useState("");
  const [fromFilter, setFromFilter] = useState("");
  const [toFilter, setToFilter] = useState("");

  const projects = usePolling<ProjectRead[] | null>(
    async () => (systemAdmin ? (await api.listProjects({ limit: 200 })).items : null),
    60_000,
    [systemAdmin],
  );

  const effectiveProjectId = systemAdmin ? projectId : (session?.project_id ?? "");

  const list = usePolling<Page<AuditEventRead> | null>(
    async () =>
      await api.listAuditEvents({
        limit: PAGE_SIZE,
        offset: page * PAGE_SIZE,
        ...(effectiveProjectId ? { project_id: effectiveProjectId } : {}),
        ...(actorFilter ? { actor_principal_id: actorFilter } : {}),
        ...(actionFilter ? { action: actionFilter } : {}),
        ...(resourceTypeFilter ? { resource_type: resourceTypeFilter } : {}),
        ...(resourceIdFilter ? { resource_id: resourceIdFilter } : {}),
        ...(fromFilter ? { occurred_from: fromFilter } : {}),
        ...(toFilter ? { occurred_to: toFilter } : {}),
      }),
    60_000,
    [effectiveProjectId, page, actorFilter, actionFilter, resourceTypeFilter, resourceIdFilter, fromFilter, toFilter],
  );

  const total = list.data?.total ?? 0;

  return (
    <div className="space-y-6 max-w-6xl">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-xl font-bold">Audit</h2>
          <p className="text-sm text-[var(--ag-text-muted)] mt-1">
            Safe administrative audit events. Project roles see only events scoped to their own
            project; deployment-scoped events are visible to system administrators only.
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
            <Field label="Project" htmlFor="audit-project">
              <Select id="audit-project" value={projectId} onChange={(e) => { setProjectId(e.target.value); setPage(0); }}>
                <option value="">All projects</option>
                {(projects.data ?? []).map((p) => (
                  <option key={p.id} value={p.id}>{p.name}</option>
                ))}
              </Select>
            </Field>
          </div>
        ) : null}
        <div className="max-w-xs">
          <Field label="Actor principal" htmlFor="audit-actor">
            <TextInput id="audit-actor" value={actorFilter} onChange={(e) => { setActorFilter(e.target.value); setPage(0); }} placeholder="Filter by principal ID" />
          </Field>
        </div>
        <div className="max-w-xs">
          <Field label="Action" htmlFor="audit-action">
            <TextInput id="audit-action" value={actionFilter} onChange={(e) => { setActionFilter(e.target.value); setPage(0); }} placeholder="e.g. project.created" />
          </Field>
        </div>
        <div className="max-w-xs">
          <Field label="Resource type" htmlFor="audit-resource-type">
            <TextInput id="audit-resource-type" value={resourceTypeFilter} onChange={(e) => { setResourceTypeFilter(e.target.value); setPage(0); }} placeholder="e.g. project" />
          </Field>
        </div>
        <div className="max-w-xs">
          <Field label="Resource ID" htmlFor="audit-resource-id">
            <TextInput id="audit-resource-id" value={resourceIdFilter} onChange={(e) => { setResourceIdFilter(e.target.value); setPage(0); }} placeholder="Filter by resource ID" />
          </Field>
        </div>
        <div className="max-w-xs">
          <Field label="Occurred from" htmlFor="audit-from">
            <TextInput id="audit-from" value={fromFilter} onChange={(e) => { setFromFilter(e.target.value); setPage(0); }} placeholder="YYYY-MM-DDTHH:MM:SSZ" />
          </Field>
        </div>
        <div className="max-w-xs">
          <Field label="Occurred to" htmlFor="audit-to">
            <TextInput id="audit-to" value={toFilter} onChange={(e) => { setToFilter(e.target.value); setPage(0); }} placeholder="YYYY-MM-DDTHH:MM:SSZ" />
          </Field>
        </div>
      </div>

      {list.error ? <p className="text-sm text-[var(--ag-danger)]">Error: {list.error}</p> : null}

      <div className="bg-[var(--ag-surface)] border border-[var(--ag-border)] rounded-xl overflow-hidden">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-[var(--ag-border)] text-left text-xs text-[var(--ag-text-muted)] uppercase tracking-wider">
              <th className="px-4 py-3">Occurred at</th>
              <th className="px-4 py-3">Action</th>
              <th className="px-4 py-3">Resource</th>
              <th className="px-4 py-3">Actor</th>
              <th className="px-4 py-3">Project</th>
              <th className="px-4 py-3">Metadata</th>
            </tr>
          </thead>
          <tbody>
            {list.loading && !list.data ? (
              <tr><td colSpan={6} className="px-4 py-6 text-[var(--ag-text-muted)]">Loading…</td></tr>
            ) : (list.data?.items ?? []).length === 0 ? (
              <tr><td colSpan={6}><EmptyState message="No audit events." /></td></tr>
            ) : (
              (list.data?.items ?? []).map((e) => (
                <tr key={e.id} className="border-b border-[var(--ag-border)] last:border-0 hover:bg-[var(--ag-surface-2)]/40">
                  <td className="px-4 py-3 text-xs font-mono">{formatOccurredAt(e.occurred_at)}</td>
                  <td className="px-4 py-3 text-xs font-mono">{e.action}</td>
                  <td className="px-4 py-3 text-xs">
                    <span className="font-mono">{e.resource_type}</span>
                    <span className="ml-2 font-mono text-[var(--ag-text-muted)]">{e.resource_id}</span>
                  </td>
                  <td className="px-4 py-3 font-mono text-xs text-[var(--ag-text-muted)]">{e.actor_principal_id ?? "—"}</td>
                  <td className="px-4 py-3 text-xs">
                    {e.project_id ? (
                      <span className="font-mono">{e.project_id}</span>
                    ) : (
                      <span className="text-[var(--ag-text-muted)]">deployment</span>
                    )}
                  </td>
                  <td className="px-4 py-3 text-xs text-[var(--ag-text-muted)] max-w-xs truncate" title={formatMetadata(e.metadata)}>
                    {formatMetadata(e.metadata)}
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
