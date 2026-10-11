import { useEffect, useState } from "react";
import { useParams, Link } from "react-router-dom";
import { ArrowLeft, Save } from "lucide-react";
import {
  api,
  apiErrorMessage,
  type PrincipalRead,
  type PrincipalUpdate,
} from "../../lib/client";
import { useAuth } from "../../context/auth-context";
import { canManageIdentity } from "../../lib/roles";
import { usePolling } from "../../lib/usePolling";
import StatusBadge from "../../components/ui/StatusBadge";
import ErrorBanner from "../../components/ui/ErrorBanner";
import ConfirmDialog from "../../components/ui/ConfirmDialog";
import { Field, TextInput, Toggle } from "../../components/ui/Form";

export default function PrincipalDetailPage() {
  const { principalId } = useParams<{ principalId: string }>();
  const { roles } = useAuth();
  const canEdit = canManageIdentity(roles);

  const [name, setName] = useState("");
  const [isActive, setIsActive] = useState(true);
  const [hydrated, setHydrated] = useState(false);
  const [busy, setBusy] = useState(false);
  const [confirmDeactivate, setConfirmDeactivate] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);

  const detail = usePolling<PrincipalRead | null>(
    async () => (principalId ? await api.getPrincipal(principalId) : null),
    60_000,
    [principalId],
  );

  const principal = detail.data;

  useEffect(() => {
    if (principal && !hydrated) {
      setName(principal.name);
      setIsActive(principal.is_active);
      setHydrated(true);
    }
  }, [principal, hydrated]);

  const onToggleActive = (checked: boolean) => {
    if (!checked && isActive) {
      setConfirmDeactivate(true);
      return;
    }
    setIsActive(checked);
  };

  const confirmDeactivation = () => {
    setIsActive(false);
    setConfirmDeactivate(false);
  };

  const onSave = async () => {
    if (!principal) return;
    const patch: PrincipalUpdate = {};
    const trimmed = name.trim();
    if (trimmed && trimmed !== principal.name) patch.name = trimmed;
    if (isActive !== principal.is_active) patch.is_active = isActive;
    if (Object.keys(patch).length === 0) return;

    setBusy(true);
    setActionError(null);
    try {
      await api.updatePrincipal(principal.id, patch);
      detail.refresh();
      setHydrated(false);
    } catch (err) {
      setActionError(apiErrorMessage(err));
    } finally {
      setBusy(false);
    }
  };

  if (detail.loading && !principal) {
    return <p className="text-sm text-[var(--ag-text-muted)]">Loading…</p>;
  }
  if (detail.error) {
    return (
      <div className="space-y-4">
        <Link to="/principals" className="inline-flex items-center gap-1 text-sm text-[var(--ag-accent)]">
          <ArrowLeft size={16} /> Back to principals
        </Link>
        <p className="text-sm text-[var(--ag-danger)]">Error: {detail.error}</p>
      </div>
    );
  }
  if (!principal) return null;

  return (
    <div className="space-y-6 max-w-3xl">
      <div className="flex items-center justify-between">
        <div>
          <Link to="/principals" className="inline-flex items-center gap-1 text-sm text-[var(--ag-accent)]">
            <ArrowLeft size={16} /> Back to principals
          </Link>
          <h2 className="text-xl font-bold mt-2">{principal.name}</h2>
        </div>
        <StatusBadge label={principal.is_active ? "Active" : "Inactive"} tone={principal.is_active ? "active" : "inactive"} />
      </div>

      <div className="bg-[var(--ag-surface)] border border-[var(--ag-border)] rounded-xl p-6 space-y-5">
        <div className="flex flex-col gap-1">
          <span className="text-xs text-[var(--ag-text-muted)] uppercase tracking-wider">Principal ID</span>
          <span className="text-sm font-mono break-all">{principal.id}</span>
        </div>
        <div className="flex flex-col gap-1">
          <span className="text-xs text-[var(--ag-text-muted)] uppercase tracking-wider">Kind</span>
          <span className="text-sm">{principal.kind === "service_account" ? "Service account" : "User"}</span>
        </div>
        <div className="flex flex-col gap-1">
          <span className="text-xs text-[var(--ag-text-muted)] uppercase tracking-wider">Project</span>
          <span className="text-sm font-mono break-all">{principal.project_id}</span>
        </div>

        <Field label="Name" htmlFor="principal-name">
          <TextInput
            id="principal-name"
            value={name}
            onChange={(e) => setName(e.target.value)}
            disabled={!canEdit}
          />
        </Field>

        <Toggle id="principal-active" label="Active" checked={isActive} onChange={onToggleActive} />
        {!isActive ? (
          <p className="text-xs text-[var(--ag-warning)]">
            Deactivated principals become ineffective through backend authorization: existing sessions and
            credentials are rejected on their next protected request.
          </p>
        ) : null}

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

      <ConfirmDialog
        open={confirmDeactivate}
        title="Deactivate principal?"
        message="Deactivating this principal will make its active sessions and credentials ineffective through backend authorization. This can be reversed by reactivating it."
        confirmLabel="Deactivate"
        tone="danger"
        onConfirm={confirmDeactivation}
        onCancel={() => setConfirmDeactivate(false)}
      />
    </div>
  );
}
