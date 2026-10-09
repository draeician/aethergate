import { useEffect, useState } from "react";
import { useParams, Link } from "react-router-dom";
import { ArrowLeft, Save, UserRound, KeyRound, Wallet } from "lucide-react";
import {
  api,
  apiErrorMessage,
  type ProjectRead,
  type ProjectUpdate,
} from "../../lib/client";
import { useAuth } from "../../context/auth-context";
import { canManageIdentity } from "../../lib/roles";
import { usePolling } from "../../lib/usePolling";
import StatusBadge from "../../components/ui/StatusBadge";
import ErrorBanner from "../../components/ui/ErrorBanner";
import { Field, TextInput, Toggle } from "../../components/ui/Form";

export default function ProjectDetailPage() {
  const { projectId } = useParams<{ projectId: string }>();
  const { roles } = useAuth();
  const canEdit = canManageIdentity(roles);

  const [name, setName] = useState("");
  const [isActive, setIsActive] = useState(true);
  const [hydrated, setHydrated] = useState(false);
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);

  const detail = usePolling<ProjectRead | null>(
    async () => (projectId ? await api.getProject(projectId) : null),
    60_000,
    [projectId],
  );

  const project = detail.data;

  useEffect(() => {
    if (project && !hydrated) {
      setName(project.name);
      setIsActive(project.is_active);
      setHydrated(true);
    }
  }, [project, hydrated]);

  const onSave = async () => {
    if (!project) return;
    const patch: ProjectUpdate = {};
    const trimmed = name.trim();
    if (trimmed && trimmed !== project.name) patch.name = trimmed;
    if (isActive !== project.is_active) patch.is_active = isActive;
    if (Object.keys(patch).length === 0) return;

    setBusy(true);
    setActionError(null);
    try {
      await api.updateProject(project.id, patch);
      detail.refresh();
      setHydrated(false);
    } catch (err) {
      setActionError(apiErrorMessage(err));
    } finally {
      setBusy(false);
    }
  };

  if (detail.loading && !project) {
    return <p className="text-sm text-[var(--ag-text-muted)]">Loading…</p>;
  }
  if (detail.error) {
    return (
      <div className="space-y-4">
        <Link to="/projects" className="inline-flex items-center gap-1 text-sm text-[var(--ag-accent)]">
          <ArrowLeft size={16} /> Back to projects
        </Link>
        <p className="text-sm text-[var(--ag-danger)]">Error: {detail.error}</p>
      </div>
    );
  }
  if (!project) return null;

  return (
    <div className="space-y-6 max-w-3xl">
      <div className="flex items-center justify-between">
        <div>
          <Link to="/projects" className="inline-flex items-center gap-1 text-sm text-[var(--ag-accent)]">
            <ArrowLeft size={16} /> Back to projects
          </Link>
          <h2 className="text-xl font-bold mt-2">{project.name}</h2>
        </div>
        <StatusBadge label={project.is_active ? "Active" : "Inactive"} tone={project.is_active ? "active" : "inactive"} />
      </div>

      <div className="flex flex-wrap gap-3">
        <Link
          to={`/principals?project=${project.id}`}
          className="inline-flex items-center gap-2 text-sm px-3 py-2 rounded-lg border border-[var(--ag-border)] text-[var(--ag-text-muted)] hover:text-[var(--ag-text)] hover:bg-[var(--ag-surface-2)] transition-colors"
        >
          <UserRound size={16} /> Principals
        </Link>
        <Link
          to={`/credentials?project=${project.id}`}
          className="inline-flex items-center gap-2 text-sm px-3 py-2 rounded-lg border border-[var(--ag-border)] text-[var(--ag-text-muted)] hover:text-[var(--ag-text)] hover:bg-[var(--ag-surface-2)] transition-colors"
        >
          <KeyRound size={16} /> Credentials
        </Link>
        <Link
          to={`/accounting/budgets?project=${project.id}`}
          className="inline-flex items-center gap-2 text-sm px-3 py-2 rounded-lg border border-[var(--ag-border)] text-[var(--ag-text-muted)] hover:text-[var(--ag-text)] hover:bg-[var(--ag-surface-2)] transition-colors"
        >
          <Wallet size={16} /> Budgets
        </Link>
      </div>

      <div className="bg-[var(--ag-surface)] border border-[var(--ag-border)] rounded-xl p-6 space-y-5">
        <div className="flex flex-col gap-1">
          <span className="text-xs text-[var(--ag-text-muted)] uppercase tracking-wider">Project ID</span>
          <span className="text-sm font-mono break-all">{project.id}</span>
        </div>

        <Field label="Name" htmlFor="project-name">
          <TextInput
            id="project-name"
            value={name}
            onChange={(e) => setName(e.target.value)}
            disabled={!canEdit}
          />
        </Field>

        <Toggle id="project-active" label="Active" checked={isActive} onChange={setIsActive} />

        <ErrorBanner message={actionError} />

        {canEdit ? (
          <button
            type="button"
            onClick={() => void onSave()}
            disabled={busy}
            className="inline-flex items-center gap-2 text-sm px-3 py-2 rounded-lg bg-[var(--ag-accent)] hover:bg-[var(--ag-accent-hover)] text-white disabled:opacity-50 transition-colors"
          >
            <Save size={16} />
            {busy ? "Saving…" : "Save changes"}
          </button>
        ) : null}
      </div>
    </div>
  );
}
