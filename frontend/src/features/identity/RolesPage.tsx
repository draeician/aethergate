import { useState } from "react";
import { Plus, RefreshCw, Ban } from "lucide-react";
import {
  api,
  apiErrorMessage,
  type Page,
  type RoleAssignmentRead,
  type RoleAssignmentCreate,
  type Role,
  type PrincipalRead,
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
import ConfirmDialog from "../../components/ui/ConfirmDialog";
import { Field, Select } from "../../components/ui/Form";

const PAGE_SIZE = 20;

function roleLabel(role: string): string {
  switch (role) {
    case "system_admin":
      return "System Admin";
    case "project_admin":
      return "Project Admin";
    case "project_viewer":
      return "Project Viewer";
    default:
      return role;
  }
}

export default function RolesPage() {
  const { roles, session } = useAuth();
  const systemAdmin = isSystemAdmin(roles);
  const canEdit = canManageIdentity(roles);

  const [page, setPage] = useState(0);
  const [granting, setGranting] = useState(false);
  const [projectId, setProjectId] = useState("");
  const [principalId, setPrincipalId] = useState("");
  const [role, setRole] = useState<Role>("project_viewer");
  const [confirmRevoke, setConfirmRevoke] = useState<RoleAssignmentRead | null>(null);
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);

  const effectiveProjectId = systemAdmin ? projectId : (session?.project_id ?? "");

  const list = usePolling<Page<RoleAssignmentRead>>(
    () => api.listRoleAssignments({ limit: PAGE_SIZE, offset: page * PAGE_SIZE }),
    60_000,
    [page],
  );

  const projects = usePolling<ProjectRead[] | null>(
    async () => (systemAdmin ? (await api.listProjects({ limit: 200 })).items : null),
    60_000,
    [systemAdmin],
  );

  const principals = usePolling<PrincipalRead[] | null>(
    async () => (granting && effectiveProjectId ? (await api.listPrincipals(effectiveProjectId, { limit: 200 })).items : null),
    60_000,
    [granting, effectiveProjectId],
  );

  const openGrant = () => {
    setActionError(null);
    setProjectId(systemAdmin ? "" : effectiveProjectId);
    setPrincipalId("");
    setRole("project_viewer");
    setGranting(true);
  };

  const onGrant = async () => {
    if (!principalId || !effectiveProjectId) return;
    const body: RoleAssignmentCreate = {
      principal_id: principalId,
      role,
      resource_scope_type: role === "system_admin" ? "deployment" : "project",
      resource_id: role === "system_admin" ? null : effectiveProjectId,
    };
    setBusy(true);
    setActionError(null);
    try {
      await api.createRoleAssignment(body);
      setGranting(false);
      list.refresh();
    } catch (err) {
      setActionError(apiErrorMessage(err));
    } finally {
      setBusy(false);
    }
  };

  const onRevoke = async (assignment: RoleAssignmentRead) => {
    setBusy(true);
    setActionError(null);
    try {
      await api.revokeRoleAssignment(assignment.id);
      setConfirmRevoke(null);
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
          <h2 className="text-xl font-bold">Role Assignments</h2>
          <p className="text-sm text-[var(--ag-text-muted)] mt-1">
            {total.toLocaleString()} assignment{total === 1 ? "" : "s"}
          </p>
        </div>
        <div className="flex items-center gap-2">
          {canEdit ? (
            <button
              type="button"
              onClick={openGrant}
              className="inline-flex items-center gap-2 text-sm px-3 py-2 rounded-lg bg-[var(--ag-accent)] hover:bg-[var(--ag-accent-hover)] text-white transition-colors"
            >
              <Plus size={16} />
              Grant role
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

      <ErrorBanner message={actionError} />
      {list.error ? <p className="text-sm text-[var(--ag-danger)]">Error: {list.error}</p> : null}

      <div className="bg-[var(--ag-surface)] border border-[var(--ag-border)] rounded-xl overflow-hidden">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-[var(--ag-border)] text-left text-xs text-[var(--ag-text-muted)] uppercase tracking-wider">
              <th className="px-4 py-3">Role</th>
              <th className="px-4 py-3">Principal</th>
              <th className="px-4 py-3">Scope</th>
              <th className="px-4 py-3">Resource</th>
              <th className="px-4 py-3">Status</th>
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
                <td colSpan={6}>
                  <EmptyState message="No role assignments." />
                </td>
              </tr>
            ) : (
              (list.data?.items ?? []).map((a) => (
                <tr key={a.id} className="border-b border-[var(--ag-border)] last:border-0 hover:bg-[var(--ag-surface-2)]/40">
                  <td className="px-4 py-3 text-xs">{roleLabel(a.role)}</td>
                  <td className="px-4 py-3 font-mono text-xs text-[var(--ag-text-muted)]">{a.principal_id}</td>
                  <td className="px-4 py-3 text-xs">{a.resource_scope_type}</td>
                  <td className="px-4 py-3 font-mono text-xs text-[var(--ag-text-muted)]">{a.resource_id ?? "—"}</td>
                  <td className="px-4 py-3">
                    <StatusBadge label={a.is_active ? "Active" : "Revoked"} tone={a.is_active ? "active" : "inactive"} />
                  </td>
                  <td className="px-4 py-3">
                    {canEdit && a.is_active ? (
                      <button
                        type="button"
                        onClick={() => setConfirmRevoke(a)}
                        className="inline-flex items-center gap-1 text-xs px-2 py-1 rounded-md border border-[var(--ag-border)] text-[var(--ag-danger)] hover:bg-[var(--ag-danger)]/10 transition-colors"
                      >
                        <Ban size={13} />
                        Revoke
                      </button>
                    ) : null}
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>

      <Pagination page={page} pageSize={PAGE_SIZE} total={total} onPageChange={setPage} />

      {granting ? (
        <Modal title="Grant role" onClose={() => setGranting(false)}>
          <div className="space-y-4">
            {systemAdmin ? (
              <Field label="Project" htmlFor="grant-project">
                <Select id="grant-project" value={projectId} onChange={(e) => setProjectId(e.target.value)}>
                  <option value="">Select a project…</option>
                  {(projects.data ?? []).map((p) => (
                    <option key={p.id} value={p.id}>
                      {p.name}
                    </option>
                  ))}
                </Select>
              </Field>
            ) : null}
            <Field label="Principal" htmlFor="grant-principal">
              <Select id="grant-principal" value={principalId} onChange={(e) => setPrincipalId(e.target.value)}>
                <option value="">Select a principal…</option>
                {(principals.data ?? []).map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.name}
                  </option>
                ))}
              </Select>
            </Field>
            <Field label="Role" htmlFor="grant-role">
              <Select id="grant-role" value={role} onChange={(e) => setRole(e.target.value as Role)}>
                <option value="project_viewer">Project Viewer</option>
                <option value="project_admin">Project Admin</option>
                {systemAdmin ? <option value="system_admin">System Admin</option> : null}
              </Select>
            </Field>
            <ErrorBanner message={actionError} />
            <div className="flex justify-end gap-3">
              <button
                type="button"
                onClick={() => setGranting(false)}
                className="text-sm px-3 py-2 rounded-lg border border-[var(--ag-border)] text-[var(--ag-text-muted)] hover:text-[var(--ag-text)] transition-colors"
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={() => void onGrant()}
                disabled={busy || !principalId || !effectiveProjectId}
                className="text-sm px-3 py-2 rounded-lg bg-[var(--ag-accent)] hover:bg-[var(--ag-accent-hover)] text-white disabled:opacity-50 transition-colors"
              >
                {busy ? "Granting…" : "Grant"}
              </button>
            </div>
          </div>
        </Modal>
      ) : null}

      <ConfirmDialog
        open={confirmRevoke !== null}
        title="Revoke role?"
        message="Revoking this role assignment takes effect on the next protected request."
        confirmLabel="Revoke"
        tone="danger"
        busy={busy}
        onConfirm={() => confirmRevoke && void onRevoke(confirmRevoke)}
        onCancel={() => setConfirmRevoke(null)}
      />
    </div>
  );
}
