import { useState } from "react";
import { useSearchParams } from "react-router-dom";
import { Plus, RefreshCw, RotateCcw, Ban } from "lucide-react";
import {
  api,
  apiErrorMessage,
  type Page,
  type ApiCredentialRead,
  type ApiCredentialCreate,
  type CredentialAudience,
  type CredentialScope,
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
import RevealSecret from "../../components/ui/RevealSecret";
import { Field, Select, TextInput } from "../../components/ui/Form";

const PAGE_SIZE = 20;

const ADMIN_SCOPES: CredentialScope[] = [
  "admin:credentials:read",
  "admin:credentials:write",
  "admin:projects:read",
  "admin:projects:write",
  "admin:principals:read",
  "admin:principals:write",
  "admin:catalog:read",
  "admin:catalog:write",
  "admin:accounting:read",
  "admin:accounting:write",
  "admin:queue:read",
  "admin:queue:write",
  "admin:audit:read",
];

function statusOf(cred: ApiCredentialRead): { label: string; tone: "active" | "inactive" | "warning" } {
  if (!cred.is_active) return { label: "Revoked", tone: "inactive" };
  if (cred.expires_at && new Date(cred.expires_at).getTime() < Date.now()) {
    return { label: "Expired", tone: "warning" };
  }
  return { label: "Active", tone: "active" };
}

export default function CredentialsPage() {
  const { roles, session } = useAuth();
  const systemAdmin = isSystemAdmin(roles);
  const canEdit = canManageIdentity(roles);
  const [searchParams] = useSearchParams();

  const [page, setPage] = useState(0);
  const [projectId, setProjectId] = useState<string>(searchParams.get("project") ?? "");
  const [audienceFilter, setAudienceFilter] = useState<CredentialAudience | "">("");

  const [creating, setCreating] = useState(false);
  const [name, setName] = useState("");
  const [principalId, setPrincipalId] = useState("");
  const [audience, setAudience] = useState<CredentialAudience>("inference");
  const [scopes, setScopes] = useState<CredentialScope[]>([]);

  const [reveal, setReveal] = useState<{ title: string; rawKey: string } | null>(null);
  const [confirmRevoke, setConfirmRevoke] = useState<ApiCredentialRead | null>(null);
  const [confirmRotate, setConfirmRotate] = useState<ApiCredentialRead | null>(null);

  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);

  const projects = usePolling<ProjectRead[] | null>(
    async () => (systemAdmin ? (await api.listProjects({ limit: 200 })).items : null),
    60_000,
    [systemAdmin],
  );

  const effectiveProjectId = systemAdmin ? projectId : (session?.project_id ?? "");

  const list = usePolling<Page<ApiCredentialRead> | null>(
    async () =>
      effectiveProjectId
        ? await api.listCredentials(effectiveProjectId, { limit: PAGE_SIZE, offset: page * PAGE_SIZE })
        : null,
    60_000,
    [effectiveProjectId, page],
  );

  const principals = usePolling<PrincipalRead[] | null>(
    async () => (creating && effectiveProjectId ? (await api.listPrincipals(effectiveProjectId, { limit: 200 })).items : null),
    60_000,
    [creating, effectiveProjectId],
  );

  const items = (list.data?.items ?? []).filter(
    (c) => audienceFilter === "" || c.audience === audienceFilter,
  );
  const total = list.data?.total ?? 0;

  const openCreate = () => {
    setActionError(null);
    setName("");
    setPrincipalId("");
    setAudience("inference");
    setScopes([]);
    setCreating(true);
  };

  const toggleScope = (scope: CredentialScope) => {
    setScopes((prev) => (prev.includes(scope) ? prev.filter((s) => s !== scope) : [...prev, scope]));
  };

  const onCreate = async () => {
    if (!name.trim() || !principalId || !effectiveProjectId) return;
    if (audience === "admin" && scopes.length === 0) {
      setActionError("Select at least one admin scope.");
      return;
    }
    const body: ApiCredentialCreate = {
      project_id: effectiveProjectId,
      principal_id: principalId,
      name: name.trim(),
      audience,
    };
    if (audience === "admin") body.scopes = scopes;

    setBusy(true);
    setActionError(null);
    try {
      const result = await api.createCredential(body);
      setCreating(false);
      setReveal({ title: "New credential key", rawKey: result.raw_key });
      list.refresh();
    } catch (err) {
      setActionError(apiErrorMessage(err));
    } finally {
      setBusy(false);
    }
  };

  const onRotate = async (cred: ApiCredentialRead) => {
    setBusy(true);
    setActionError(null);
    try {
      const result = await api.rotateCredential(cred.id);
      setConfirmRotate(null);
      setReveal({ title: "Rotated credential key", rawKey: result.raw_key });
      list.refresh();
    } catch (err) {
      setActionError(apiErrorMessage(err));
    } finally {
      setBusy(false);
    }
  };

  const onRevoke = async (cred: ApiCredentialRead) => {
    setBusy(true);
    setActionError(null);
    try {
      await api.revokeCredential(cred.id);
      setConfirmRevoke(null);
      list.refresh();
    } catch (err) {
      setActionError(apiErrorMessage(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-6 max-w-6xl">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-xl font-bold">Credentials</h2>
          <p className="text-sm text-[var(--ag-text-muted)] mt-1">
            {total.toLocaleString()} credential{total === 1 ? "" : "s"}
          </p>
        </div>
        <div className="flex items-center gap-2">
          {canEdit && effectiveProjectId ? (
            <button
              type="button"
              onClick={openCreate}
              className="inline-flex items-center gap-2 text-sm px-3 py-2 rounded-lg bg-[var(--ag-accent)] hover:bg-[var(--ag-accent-hover)] text-white transition-colors"
            >
              <Plus size={16} />
              New credential
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

      <div className="flex flex-wrap items-end gap-4">
        {systemAdmin ? (
          <div className="max-w-xs">
            <Field label="Project" htmlFor="credential-project">
              <Select
                id="credential-project"
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
        <div className="max-w-xs">
          <Field label="Audience" htmlFor="credential-audience-filter">
            <Select
              id="credential-audience-filter"
              value={audienceFilter}
              onChange={(e) => setAudienceFilter(e.target.value as CredentialAudience | "")}
            >
              <option value="">All audiences</option>
              <option value="inference">Inference</option>
              <option value="admin">Admin</option>
            </Select>
          </Field>
        </div>
      </div>

      <ErrorBanner message={actionError} />
      {list.error ? <p className="text-sm text-[var(--ag-danger)]">Error: {list.error}</p> : null}

      <div className="bg-[var(--ag-surface)] border border-[var(--ag-border)] rounded-xl overflow-hidden">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-[var(--ag-border)] text-left text-xs text-[var(--ag-text-muted)] uppercase tracking-wider">
              <th className="px-4 py-3">Name</th>
              <th className="px-4 py-3">Audience</th>
              <th className="px-4 py-3">Key prefix</th>
              <th className="px-4 py-3">Status</th>
              <th className="px-4 py-3">Actions</th>
            </tr>
          </thead>
          <tbody>
            {!effectiveProjectId ? (
              <tr>
                <td colSpan={5}>
                  <EmptyState message="Select a project to view its credentials." />
                </td>
              </tr>
            ) : list.loading && !list.data ? (
              <tr>
                <td colSpan={5} className="px-4 py-6 text-[var(--ag-text-muted)]">Loading…</td>
              </tr>
            ) : items.length === 0 ? (
              <tr>
                <td colSpan={5}>
                  <EmptyState message="No credentials." />
                </td>
              </tr>
            ) : (
              items.map((c) => {
                const status = statusOf(c);
                const isActive = c.is_active;
                return (
                  <tr key={c.id} className="border-b border-[var(--ag-border)] last:border-0 hover:bg-[var(--ag-surface-2)]/40">
                    <td className="px-4 py-3">{c.name}</td>
                    <td className="px-4 py-3 text-xs">{c.audience}</td>
                    <td className="px-4 py-3 font-mono text-xs text-[var(--ag-text-muted)]">{c.key_prefix ?? "—"}</td>
                    <td className="px-4 py-3">
                      <StatusBadge label={status.label} tone={status.tone} />
                    </td>
                    <td className="px-4 py-3">
                      {canEdit && isActive ? (
                        <div className="flex items-center gap-2">
                          <button
                            type="button"
                            onClick={() => setConfirmRotate(c)}
                            className="inline-flex items-center gap-1 text-xs px-2 py-1 rounded-md border border-[var(--ag-border)] text-[var(--ag-text-muted)] hover:text-[var(--ag-text)] transition-colors"
                          >
                            <RotateCcw size={13} />
                            Rotate
                          </button>
                          <button
                            type="button"
                            onClick={() => setConfirmRevoke(c)}
                            className="inline-flex items-center gap-1 text-xs px-2 py-1 rounded-md border border-[var(--ag-border)] text-[var(--ag-danger)] hover:bg-[var(--ag-danger)]/10 transition-colors"
                          >
                            <Ban size={13} />
                            Revoke
                          </button>
                        </div>
                      ) : null}
                    </td>
                  </tr>
                );
              })
            )}
          </tbody>
        </table>
      </div>

      <Pagination page={page} pageSize={PAGE_SIZE} total={total} onPageChange={setPage} />

      {creating ? (
        <Modal title="New credential" onClose={() => setCreating(false)}>
          <div className="space-y-4">
            <Field label="Name" htmlFor="credential-name">
              <TextInput id="credential-name" value={name} onChange={(e) => setName(e.target.value)} placeholder="Credential name" autoFocus />
            </Field>
            <Field label="Principal" htmlFor="credential-principal">
              <Select id="credential-principal" value={principalId} onChange={(e) => setPrincipalId(e.target.value)}>
                <option value="">Select a principal…</option>
                {(principals.data ?? []).map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.name}
                  </option>
                ))}
              </Select>
            </Field>
            <Field label="Audience" htmlFor="credential-audience">
              <Select id="credential-audience" value={audience} onChange={(e) => setAudience(e.target.value as CredentialAudience)}>
                <option value="inference">Inference</option>
                <option value="admin">Admin</option>
              </Select>
            </Field>
            {audience === "admin" ? (
              <fieldset className="space-y-2">
                <legend className="text-xs text-[var(--ag-text-muted)] uppercase tracking-wider">Admin scopes</legend>
                {ADMIN_SCOPES.map((scope) => (
                  <label key={scope} className="flex items-center gap-2 text-sm text-[var(--ag-text)] cursor-pointer select-none">
                    <input
                      type="checkbox"
                      checked={scopes.includes(scope)}
                      onChange={() => toggleScope(scope)}
                      className="h-4 w-4 accent-[var(--ag-accent)]"
                    />
                    {scope}
                  </label>
                ))}
              </fieldset>
            ) : null}
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
                disabled={busy || !name.trim() || !principalId}
                className="text-sm px-3 py-2 rounded-lg bg-[var(--ag-accent)] hover:bg-[var(--ag-accent-hover)] text-white disabled:opacity-50 transition-colors"
              >
                {busy ? "Creating…" : "Create"}
              </button>
            </div>
          </div>
        </Modal>
      ) : null}

      <RevealSecret
        open={reveal !== null}
        value={reveal?.rawKey ?? ""}
        title={reveal?.title ?? ""}
        onDismiss={() => setReveal(null)}
      />

      <ConfirmDialog
        open={confirmRotate !== null}
        title="Rotate credential?"
        message="Rotation issues a replacement key (shown once) and invalidates the old key immediately."
        confirmLabel="Rotate"
        busy={busy}
        onConfirm={() => confirmRotate && void onRotate(confirmRotate)}
        onCancel={() => setConfirmRotate(null)}
      />

      <ConfirmDialog
        open={confirmRevoke !== null}
        title="Revoke credential?"
        message="Revoking a credential makes it immediately invalid. This cannot be undone."
        confirmLabel="Revoke"
        tone="danger"
        busy={busy}
        onConfirm={() => confirmRevoke && void onRevoke(confirmRevoke)}
        onCancel={() => setConfirmRevoke(null)}
      />
    </div>
  );
}
