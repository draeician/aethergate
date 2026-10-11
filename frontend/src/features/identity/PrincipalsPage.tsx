import { useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { Plus, RefreshCw } from "lucide-react";
import {
  api,
  apiErrorMessage,
  type Page,
  type PrincipalRead,
  type PrincipalKind,
  type ProjectRead,
} from "../../lib/client";
import { useAuth } from "../../context/auth-context";
import { isSystemAdmin, canManageIdentity } from "../../lib/roles";
import { usePolling } from "../../lib/usePolling";
import Pagination from "../../components/ui/Pagination";
import StatusBadge from "../../components/ui/StatusBadge";
import ErrorBanner from "../../components/ui/ErrorBanner";
import EmptyState from "../../components/ui/EmptyState";
import Modal from "../../components/ui/Modal";
import { Field, Select, TextInput } from "../../components/ui/Form";

const PAGE_SIZE = 20;

export default function PrincipalsPage() {
  const { roles, session } = useAuth();
  const systemAdmin = isSystemAdmin(roles);
  const canEdit = canManageIdentity(roles);
  const [searchParams] = useSearchParams();

  const [page, setPage] = useState(0);
  const [projectId, setProjectId] = useState<string>(searchParams.get("project") ?? "");
  const [creating, setCreating] = useState(false);
  const [name, setName] = useState("");
  const [kind, setKind] = useState<PrincipalKind>("user");
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);

  const projects = usePolling<ProjectRead[] | null>(
    async () => (systemAdmin ? (await api.listProjects({ limit: 200 })).items : null),
    60_000,
    [systemAdmin],
  );

  // Project roles are bound to their session project; system_admin picks one.
  const effectiveProjectId = systemAdmin ? projectId : (session?.project_id ?? "");

  const list = usePolling<Page<PrincipalRead> | null>(
    async () => (effectiveProjectId ? await api.listPrincipals(effectiveProjectId, { limit: PAGE_SIZE, offset: page * PAGE_SIZE }) : null),
    60_000,
    [effectiveProjectId, page],
  );

  const onCreate = async () => {
    if (!name.trim() || !effectiveProjectId) return;
    setBusy(true);
    setActionError(null);
    try {
      await api.createPrincipal(effectiveProjectId, { kind, name: name.trim(), is_active: true });
      setCreating(false);
      setName("");
      setKind("user");
      list.refresh();
    } catch (err) {
      setActionError(apiErrorMessage(err));
    } finally {
      setBusy(false);
    }
  };

  const total = list.data?.total ?? 0;

  return (
    <div className="space-y-6 max-w-6xl">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-xl font-bold">Principals</h2>
          <p className="text-sm text-[var(--ag-text-muted)] mt-1">
            {total.toLocaleString()} principal{total === 1 ? "" : "s"}
          </p>
        </div>
        <div className="flex items-center gap-2">
          {canEdit && effectiveProjectId ? (
            <button
              type="button"
              onClick={() => {
                setActionError(null);
                setCreating(true);
              }}
              className="inline-flex items-center gap-2 text-sm px-3 py-2 rounded-lg bg-[var(--ag-accent)] hover:bg-[var(--ag-accent-hover)] text-white transition-colors"
            >
              <Plus size={16} />
              New principal
            </button>
          ) : null}
          <button
            type="button"
            onClick={list.refresh}
            className="inline-flex items-center gap-2 text-sm px-3 py-2 rounded-lg border border-[var(--ag-border)] text-[var(--ag-text-muted)] hover:text-[var(--ag-text)] hover:bg-[var(--ag-surface-2)] transition-colors"
          >
            <RefreshCw size={14} />
            Refresh
          </button>
        </div>
      </div>

      {systemAdmin ? (
        <div className="max-w-xs">
          <Field label="Project" htmlFor="principal-project">
            <Select
              id="principal-project"
              value={projectId}
              onChange={(e) => {
                setProjectId(e.target.value);
                setPage(0);
              }}
            >
              <option value="">Select a project…</option>
              {(projects.data ?? []).map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name}
                </option>
              ))}
            </Select>
          </Field>
        </div>
      ) : null}

      <ErrorBanner message={actionError} />
      {list.error ? <p className="text-sm text-[var(--ag-danger)]">Error: {list.error}</p> : null}

      <div className="bg-[var(--ag-surface)] border border-[var(--ag-border)] rounded-xl overflow-hidden">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-[var(--ag-border)] text-left text-xs text-[var(--ag-text-muted)] uppercase tracking-wider">
              <th className="px-4 py-3">Name</th>
              <th className="px-4 py-3">Kind</th>
              <th className="px-4 py-3">ID</th>
              <th className="px-4 py-3">Status</th>
            </tr>
          </thead>
          <tbody>
            {!effectiveProjectId ? (
              <tr>
                <td colSpan={4}>
                  <EmptyState message="Select a project to view its principals." />
                </td>
              </tr>
            ) : list.loading && !list.data ? (
              <tr>
                <td colSpan={4} className="px-4 py-6 text-[var(--ag-text-muted)]">Loading…</td>
              </tr>
            ) : (list.data?.items ?? []).length === 0 ? (
              <tr>
                <td colSpan={4}>
                  <EmptyState message="No principals." />
                </td>
              </tr>
            ) : (
              (list.data?.items ?? []).map((p) => (
                <tr key={p.id} className="border-b border-[var(--ag-border)] last:border-0 hover:bg-[var(--ag-surface-2)]/40">
                  <td className="px-4 py-3">
                    <Link to={`/principals/${p.id}`} className="text-[var(--ag-accent)] hover:underline">
                      {p.name}
                    </Link>
                  </td>
                  <td className="px-4 py-3 text-xs">{p.kind === "service_account" ? "Service account" : "User"}</td>
                  <td className="px-4 py-3 font-mono text-xs text-[var(--ag-text-muted)]">{p.id}</td>
                  <td className="px-4 py-3">
                    <StatusBadge label={p.is_active ? "Active" : "Inactive"} tone={p.is_active ? "active" : "inactive"} />
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>

      <Pagination page={page} pageSize={PAGE_SIZE} total={total} onPageChange={setPage} />

      {creating ? (
        <Modal title="New principal" onClose={() => setCreating(false)}>
          <div className="space-y-4">
            <Field label="Name" htmlFor="principal-name">
              <TextInput
                id="principal-name"
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="Principal name"
                autoFocus
              />
            </Field>
            <Field label="Kind" htmlFor="principal-kind">
              <Select id="principal-kind" value={kind} onChange={(e) => setKind(e.target.value as PrincipalKind)}>
                <option value="user">User</option>
                <option value="service_account">Service account</option>
              </Select>
            </Field>
            <ErrorBanner message={actionError} />
            <div className="flex justify-end gap-3">
              <button
                type="button"
                onClick={() => setCreating(false)}
                className="text-sm px-3 py-2 rounded-lg border border-[var(--ag-border)] text-[var(--ag-text-muted)] hover:text-[var(--ag-text)] transition-colors"
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={() => void onCreate()}
                disabled={busy || !name.trim()}
                className="text-sm px-3 py-2 rounded-lg bg-[var(--ag-accent)] hover:bg-[var(--ag-accent-hover)] text-white disabled:opacity-50 transition-colors"
              >
                {busy ? "Creating…" : "Create"}
              </button>
            </div>
          </div>
        </Modal>
      ) : null}
    </div>
  );
}
