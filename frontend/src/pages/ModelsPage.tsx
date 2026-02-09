import { useEffect, useState, useCallback, useMemo } from "react";
import {
  Cpu,
  RefreshCw,
  Plus,
  Pencil,
  Trash2,
  X,
  Search,
  ArrowUpDown,
  ArrowUp,
  ArrowDown,
  ChevronLeft,
  ChevronRight,
} from "lucide-react";
import { useAuth } from "../context/AuthContext";
import {
  listModels,
  listEndpoints,
  upsertModel,
  updateModel,
  deleteModel,
} from "../lib/api";
import type { LLMModel, LLMEndpoint } from "../lib/types";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

type SortKey =
  | "id"
  | "litellm_name"
  | "endpoint_name"
  | "price_in"
  | "price_out"
  | "is_active"
  | "rpm_limit";
type SortDir = "asc" | "desc";

const PAGE_SIZE = 50;

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export default function ModelsPage() {
  const { adminKey } = useAuth();

  // Data
  const [models, setModels] = useState<LLMModel[]>([]);
  const [endpoints, setEndpoints] = useState<LLMEndpoint[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  // Search / Sort / Pagination
  const [search, setSearch] = useState("");
  const [sortKey, setSortKey] = useState<SortKey>("id");
  const [sortDir, setSortDir] = useState<SortDir>("asc");
  const [page, setPage] = useState(0);

  // Modal state (shared for Add & Edit)
  const [modalOpen, setModalOpen] = useState(false);
  const [modalMode, setModalMode] = useState<"add" | "edit">("add");
  const [modalModelId, setModalModelId] = useState("");
  const [modalLitellm, setModalLitellm] = useState("");
  const [modalPriceIn, setModalPriceIn] = useState("0.000001");
  const [modalPriceOut, setModalPriceOut] = useState("0.000002");
  const [modalEndpointId, setModalEndpointId] = useState("");
  const [modalRpm, setModalRpm] = useState("");
  const [modalDay, setModalDay] = useState("");
  const [modalError, setModalError] = useState("");
  const [modalSaving, setModalSaving] = useState(false);

  // Toggle active saving indicator
  const [togglingId, setTogglingId] = useState<string | null>(null);

  // Delete state
  const [confirmDeleteId, setConfirmDeleteId] = useState<string | null>(null);
  const [deleting, setDeleting] = useState(false);

  // ---- Data Loading ----

  const load = useCallback(async () => {
    if (!adminKey) return;
    setLoading(true);
    try {
      const [m, e] = await Promise.all([
        listModels(adminKey),
        listEndpoints(adminKey),
      ]);
      setModels(m);
      setEndpoints(e);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, [adminKey]);

  useEffect(() => {
    load();
  }, [load]);

  // ---- Endpoint lookup map ----

  const endpointMap = useMemo(() => {
    const map = new Map<number, LLMEndpoint>();
    for (const ep of endpoints) map.set(ep.id, ep);
    return map;
  }, [endpoints]);

  // ---- Filter + Sort + Paginate ----

  const filtered = useMemo(() => {
    const q = search.toLowerCase().trim();
    if (!q) return models;
    return models.filter(
      (m) =>
        m.id.toLowerCase().includes(q) ||
        m.litellm_name.toLowerCase().includes(q) ||
        (m.endpoint_name ?? "").toLowerCase().includes(q)
    );
  }, [models, search]);

  const sorted = useMemo(() => {
    const arr = [...filtered];
    arr.sort((a, b) => {
      let cmp = 0;
      switch (sortKey) {
        case "id":
          cmp = a.id.localeCompare(b.id);
          break;
        case "litellm_name":
          cmp = a.litellm_name.localeCompare(b.litellm_name);
          break;
        case "endpoint_name":
          cmp = (a.endpoint_name ?? "").localeCompare(b.endpoint_name ?? "");
          break;
        case "price_in":
          cmp = a.price_in - b.price_in;
          break;
        case "price_out":
          cmp = a.price_out - b.price_out;
          break;
        case "is_active":
          cmp = Number(b.is_active) - Number(a.is_active);
          break;
        case "rpm_limit":
          cmp = (a.rpm_limit ?? 0) - (b.rpm_limit ?? 0);
          break;
      }
      return sortDir === "asc" ? cmp : -cmp;
    });
    return arr;
  }, [filtered, sortKey, sortDir]);

  const totalPages = Math.max(1, Math.ceil(sorted.length / PAGE_SIZE));
  const safePage = Math.min(page, totalPages - 1);
  const paginated = sorted.slice(
    safePage * PAGE_SIZE,
    safePage * PAGE_SIZE + PAGE_SIZE
  );

  // Reset page when search changes
  useEffect(() => {
    setPage(0);
  }, [search]);

  // ---- Sort helpers ----

  function handleSort(key: SortKey) {
    if (sortKey === key) {
      setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    } else {
      setSortKey(key);
      setSortDir("asc");
    }
  }

  function SortIcon({ col }: { col: SortKey }) {
    if (sortKey !== col)
      return <ArrowUpDown size={12} className="opacity-30 ml-1 inline" />;
    return sortDir === "asc" ? (
      <ArrowUp size={12} className="ml-1 inline text-[var(--ag-accent)]" />
    ) : (
      <ArrowDown size={12} className="ml-1 inline text-[var(--ag-accent)]" />
    );
  }

  // ---- Modal helpers ----

  function openAddModal() {
    setModalMode("add");
    setModalModelId("");
    setModalLitellm("");
    setModalPriceIn("0.000001");
    setModalPriceOut("0.000002");
    setModalEndpointId("");
    setModalRpm("");
    setModalDay("");
    setModalError("");
    setModalOpen(true);
  }

  function openEditModal(m: LLMModel) {
    setModalMode("edit");
    setModalModelId(m.id);
    setModalLitellm(m.litellm_name);
    setModalPriceIn(m.price_in.toString());
    setModalPriceOut(m.price_out.toString());
    setModalEndpointId(m.endpoint_id?.toString() ?? "");
    setModalRpm(m.rpm_limit?.toString() ?? "");
    setModalDay(m.day_limit?.toString() ?? "");
    setModalError("");
    setModalOpen(true);
  }

  function closeModal() {
    setModalOpen(false);
    setModalError("");
  }

  async function handleModalSave(e: React.FormEvent) {
    e.preventDefault();
    if (!adminKey) return;
    setModalSaving(true);
    setModalError("");
    try {
      if (modalMode === "add") {
        await upsertModel(adminKey, {
          id: modalModelId.trim().toLowerCase().replace(/\s+/g, "-"),
          litellm_name: modalLitellm.trim(),
          price_in: parseFloat(modalPriceIn),
          price_out: parseFloat(modalPriceOut),
          endpoint_id: modalEndpointId ? parseInt(modalEndpointId) : null,
          rpm_limit: modalRpm ? parseInt(modalRpm) : null,
          day_limit: modalDay ? parseInt(modalDay) : null,
        });
      } else {
        await updateModel(adminKey, modalModelId, {
          litellm_name: modalLitellm.trim(),
          price_in: parseFloat(modalPriceIn),
          price_out: parseFloat(modalPriceOut),
          endpoint_id: modalEndpointId ? parseInt(modalEndpointId) : 0,
          rpm_limit: modalRpm ? parseInt(modalRpm) : 0,
          day_limit: modalDay ? parseInt(modalDay) : 0,
        });
      }
      closeModal();
      await load();
    } catch (err: any) {
      setModalError(err.message);
    } finally {
      setModalSaving(false);
    }
  }

  // ---- Toggle Active ----

  async function handleToggleActive(m: LLMModel) {
    if (!adminKey) return;
    setTogglingId(m.id);
    try {
      await updateModel(adminKey, m.id, { is_active: !m.is_active });
      await load();
    } catch (err: any) {
      setError(err.message);
    } finally {
      setTogglingId(null);
    }
  }

  // ---- Delete ----

  async function handleDelete(id: string) {
    if (!adminKey) return;
    setDeleting(true);
    try {
      await deleteModel(adminKey, id);
      setConfirmDeleteId(null);
      await load();
    } catch (err: any) {
      setError(err.message);
      setConfirmDeleteId(null);
    } finally {
      setDeleting(false);
    }
  }

  // ---- Render ----

  return (
    <div className="space-y-6 max-w-[90rem]">
      {/* Header */}
      <div className="flex items-center justify-between flex-wrap gap-4">
        <div>
          <h2 className="text-xl font-bold">Models</h2>
          <p className="text-sm text-[var(--ag-text-muted)] mt-1">
            Configure model routing, pricing &amp; endpoint bindings
          </p>
        </div>
        <div className="flex gap-2">
          <button
            onClick={load}
            className="p-2 rounded-lg border border-[var(--ag-border)] text-[var(--ag-text-muted)] hover:text-[var(--ag-text)] hover:border-[var(--ag-border-hover)] transition-colors"
            title="Refresh"
          >
            <RefreshCw size={16} />
          </button>
          <button
            onClick={openAddModal}
            className="flex items-center gap-2 px-4 py-2 rounded-lg bg-[var(--ag-accent)] text-white text-sm font-medium hover:bg-[var(--ag-accent-hover)] transition-colors"
          >
            <Plus size={16} /> Add Model
          </button>
        </div>
      </div>

      {/* Search bar */}
      <div className="relative max-w-md">
        <Search
          size={16}
          className="absolute left-3 top-1/2 -translate-y-1/2 text-[var(--ag-text-muted)] pointer-events-none"
        />
        <input
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="Search by Model ID, backend name, or endpoint…"
          className="w-full pl-9 pr-3 py-2 rounded-lg bg-[var(--ag-surface)] border border-[var(--ag-border)] text-sm focus:outline-none focus:border-[var(--ag-accent)] focus:ring-1 focus:ring-[var(--ag-accent)] transition-colors placeholder:text-[var(--ag-text-muted)]"
        />
      </div>

      {/* Delete Confirmation Banner */}
      {confirmDeleteId !== null && (
        <div className="bg-[var(--ag-danger)]/10 border border-[var(--ag-danger)]/30 rounded-xl p-5 flex items-center justify-between">
          <div>
            <p className="text-sm font-medium text-[var(--ag-danger)]">
              Delete model &ldquo;{confirmDeleteId}&rdquo;?
            </p>
            <p className="text-xs text-[var(--ag-text-muted)] mt-1">
              This cannot be undone. Existing logs referencing this model are
              preserved.
            </p>
          </div>
          <div className="flex gap-2">
            <button
              onClick={() => setConfirmDeleteId(null)}
              disabled={deleting}
              className="px-3 py-1.5 rounded-lg border border-[var(--ag-border)] text-sm hover:border-[var(--ag-border-hover)] transition-colors"
            >
              Cancel
            </button>
            <button
              onClick={() => handleDelete(confirmDeleteId)}
              disabled={deleting}
              className="px-3 py-1.5 rounded-lg bg-[var(--ag-danger)] text-white text-sm font-medium hover:opacity-90 transition-opacity disabled:opacity-50"
            >
              {deleting ? "Deleting…" : "Confirm Delete"}
            </button>
          </div>
        </div>
      )}

      {/* Error */}
      {error && <p className="text-[var(--ag-danger)] text-sm">{error}</p>}

      {/* Table */}
      {loading ? (
        <p className="text-[var(--ag-text-muted)] text-sm">Loading…</p>
      ) : (
        <div className="bg-[var(--ag-surface)] border border-[var(--ag-border)] rounded-xl overflow-hidden">
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-[var(--ag-border)] text-[var(--ag-text-muted)] text-xs uppercase tracking-wider">
                  <th
                    onClick={() => handleSort("id")}
                    className="text-left px-4 py-3 font-medium cursor-pointer select-none hover:text-[var(--ag-text)] transition-colors whitespace-nowrap"
                  >
                    Model ID
                    <SortIcon col="id" />
                  </th>
                  <th
                    onClick={() => handleSort("litellm_name")}
                    className="text-left px-4 py-3 font-medium cursor-pointer select-none hover:text-[var(--ag-text)] transition-colors whitespace-nowrap"
                  >
                    Backend Name
                    <SortIcon col="litellm_name" />
                  </th>
                  <th
                    onClick={() => handleSort("endpoint_name")}
                    className="text-left px-4 py-3 font-medium cursor-pointer select-none hover:text-[var(--ag-text)] transition-colors whitespace-nowrap"
                  >
                    Endpoint
                    <SortIcon col="endpoint_name" />
                  </th>
                  <th
                    onClick={() => handleSort("price_in")}
                    className="text-right px-4 py-3 font-medium cursor-pointer select-none hover:text-[var(--ag-text)] transition-colors whitespace-nowrap"
                  >
                    Price In
                    <SortIcon col="price_in" />
                  </th>
                  <th
                    onClick={() => handleSort("price_out")}
                    className="text-right px-4 py-3 font-medium cursor-pointer select-none hover:text-[var(--ag-text)] transition-colors whitespace-nowrap"
                  >
                    Price Out
                    <SortIcon col="price_out" />
                  </th>
                  <th
                    onClick={() => handleSort("rpm_limit")}
                    className="text-right px-4 py-3 font-medium cursor-pointer select-none hover:text-[var(--ag-text)] transition-colors whitespace-nowrap"
                  >
                    RPM
                    <SortIcon col="rpm_limit" />
                  </th>
                  <th
                    onClick={() => handleSort("is_active")}
                    className="text-center px-4 py-3 font-medium cursor-pointer select-none hover:text-[var(--ag-text)] transition-colors whitespace-nowrap"
                  >
                    Status
                    <SortIcon col="is_active" />
                  </th>
                  <th className="text-left px-4 py-3 font-medium whitespace-nowrap">
                    Tags
                  </th>
                  <th className="text-right px-4 py-3 font-medium whitespace-nowrap">
                    Actions
                  </th>
                </tr>
              </thead>
              <tbody>
                {paginated.map((m) => (
                  <tr
                    key={m.id}
                    className="border-b border-[var(--ag-border)] last:border-b-0 hover:bg-[var(--ag-surface-2)] transition-colors"
                  >
                    {/* Model ID */}
                    <td className="px-4 py-3 font-medium whitespace-nowrap">
                      <div className="flex items-center gap-2">
                        <Cpu
                          size={14}
                          className="text-[var(--ag-accent)] shrink-0"
                        />
                        {m.id}
                      </div>
                    </td>

                    {/* Backend / litellm_name */}
                    <td className="px-4 py-3 font-mono text-xs text-[var(--ag-text-muted)] max-w-[240px] truncate">
                      {m.litellm_name}
                    </td>

                    {/* Endpoint */}
                    <td className="px-4 py-3 whitespace-nowrap">
                      <span className="px-1.5 py-0.5 rounded bg-[var(--ag-surface-2)] text-xs text-[var(--ag-text-muted)]">
                        {m.endpoint_name ?? "default"}
                      </span>
                    </td>

                    {/* Price In */}
                    <td className="px-4 py-3 text-right font-mono text-xs text-[var(--ag-success)] whitespace-nowrap">
                      ${m.price_in}
                    </td>

                    {/* Price Out */}
                    <td className="px-4 py-3 text-right font-mono text-xs text-[var(--ag-warning)] whitespace-nowrap">
                      ${m.price_out}
                    </td>

                    {/* RPM */}
                    <td className="px-4 py-3 text-right font-mono text-xs text-[var(--ag-text-muted)] whitespace-nowrap">
                      {m.rpm_limit ?? "—"}
                    </td>

                    {/* Status (active toggle) */}
                    <td className="px-4 py-3 text-center">
                      <button
                        onClick={() => handleToggleActive(m)}
                        disabled={togglingId === m.id}
                        className={`inline-block px-2 py-0.5 rounded-full text-xs font-medium cursor-pointer transition-colors ${
                          m.is_active
                            ? "bg-[var(--ag-success)]/15 text-[var(--ag-success)] hover:bg-[var(--ag-danger)]/15 hover:text-[var(--ag-danger)]"
                            : "bg-[var(--ag-danger)]/15 text-[var(--ag-danger)] hover:bg-[var(--ag-success)]/15 hover:text-[var(--ag-success)]"
                        }`}
                        title={
                          m.is_active
                            ? "Click to deactivate"
                            : "Click to activate"
                        }
                      >
                        {m.is_active ? "Active" : "Inactive"}
                      </button>
                    </td>

                    {/* Tags */}
                    <td className="px-4 py-3">
                      <div className="flex flex-wrap gap-1">
                        <span className="px-1.5 py-0.5 rounded bg-[var(--ag-surface-2)] text-[10px] text-[var(--ag-text-muted)]">
                          {m.capability}
                        </span>
                        {m.day_limit && (
                          <span className="px-1.5 py-0.5 rounded bg-[var(--ag-surface-2)] text-[10px] text-[var(--ag-text-muted)]">
                            {m.day_limit}/day
                          </span>
                        )}
                      </div>
                    </td>

                    {/* Actions */}
                    <td className="px-4 py-3 text-right">
                      <div className="flex items-center justify-end gap-1">
                        <button
                          onClick={() => openEditModal(m)}
                          className="p-1.5 rounded-lg text-[var(--ag-text-muted)] hover:text-[var(--ag-text)] hover:bg-[var(--ag-surface-2)] transition-colors"
                          title="Edit"
                        >
                          <Pencil size={14} />
                        </button>
                        <button
                          onClick={() => setConfirmDeleteId(m.id)}
                          className="p-1.5 rounded-lg text-[var(--ag-text-muted)] hover:text-[var(--ag-danger)] hover:bg-[var(--ag-danger)]/10 transition-colors"
                          title="Delete"
                        >
                          <Trash2 size={14} />
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
                {sorted.length === 0 && (
                  <tr>
                    <td
                      colSpan={9}
                      className="px-5 py-8 text-center text-[var(--ag-text-muted)]"
                    >
                      {models.length === 0
                        ? "No models configured yet."
                        : "No models match your search."}
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>

          {/* Pagination footer */}
          {totalPages > 1 && (
            <div className="flex items-center justify-between px-4 py-3 border-t border-[var(--ag-border)] text-xs text-[var(--ag-text-muted)]">
              <span>
                Showing {safePage * PAGE_SIZE + 1}–
                {Math.min((safePage + 1) * PAGE_SIZE, sorted.length)} of{" "}
                {sorted.length} models
              </span>
              <div className="flex items-center gap-1">
                <button
                  onClick={() => setPage((p) => Math.max(0, p - 1))}
                  disabled={safePage === 0}
                  className="p-1.5 rounded-lg border border-[var(--ag-border)] hover:border-[var(--ag-border-hover)] disabled:opacity-30 transition-colors"
                >
                  <ChevronLeft size={14} />
                </button>
                <span className="px-2">
                  {safePage + 1} / {totalPages}
                </span>
                <button
                  onClick={() =>
                    setPage((p) => Math.min(totalPages - 1, p + 1))
                  }
                  disabled={safePage >= totalPages - 1}
                  className="p-1.5 rounded-lg border border-[var(--ag-border)] hover:border-[var(--ag-border-hover)] disabled:opacity-30 transition-colors"
                >
                  <ChevronRight size={14} />
                </button>
              </div>
            </div>
          )}
        </div>
      )}

      {/* ================================================================ */}
      {/* Add / Edit Modal */}
      {/* ================================================================ */}
      {modalOpen && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center"
          onClick={closeModal}
        >
          {/* Backdrop */}
          <div className="absolute inset-0 bg-black/60 backdrop-blur-sm" />

          {/* Panel */}
          <div
            className="relative bg-[var(--ag-surface)] border border-[var(--ag-border)] rounded-xl shadow-2xl w-full max-w-lg mx-4 max-h-[90vh] overflow-y-auto"
            onClick={(e) => e.stopPropagation()}
          >
            {/* Modal header */}
            <div className="flex items-center justify-between px-6 py-4 border-b border-[var(--ag-border)]">
              <h3 className="text-base font-semibold">
                {modalMode === "add" ? "Add Model" : `Edit — ${modalModelId}`}
              </h3>
              <button
                onClick={closeModal}
                className="p-1 rounded-lg text-[var(--ag-text-muted)] hover:text-[var(--ag-text)] hover:bg-[var(--ag-surface-2)] transition-colors"
              >
                <X size={18} />
              </button>
            </div>

            {/* Modal body */}
            <form onSubmit={handleModalSave} className="px-6 py-5 space-y-4">
              {/* Model ID — editable only on Add */}
              <div>
                <label className="block text-xs font-medium text-[var(--ag-text-muted)] mb-1.5">
                  Public Model ID
                </label>
                <input
                  value={modalModelId}
                  onChange={(e) => setModalModelId(e.target.value)}
                  placeholder="gpt-4-turbo"
                  required
                  disabled={modalMode === "edit"}
                  className="w-full px-3 py-2 rounded-lg bg-[var(--ag-bg)] border border-[var(--ag-border)] text-sm focus:outline-none focus:border-[var(--ag-accent)] focus:ring-1 focus:ring-[var(--ag-accent)] transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
                />
              </div>

              {/* LiteLLM Name */}
              <div>
                <label className="block text-xs font-medium text-[var(--ag-text-muted)] mb-1.5">
                  LiteLLM Internal Name
                </label>
                <input
                  value={modalLitellm}
                  onChange={(e) => setModalLitellm(e.target.value)}
                  placeholder="ollama/llama3"
                  required
                  className="w-full px-3 py-2 rounded-lg bg-[var(--ag-bg)] border border-[var(--ag-border)] text-sm focus:outline-none focus:border-[var(--ag-accent)] focus:ring-1 focus:ring-[var(--ag-accent)] transition-colors"
                />
              </div>

              {/* Pricing row */}
              <div className="grid grid-cols-2 gap-4">
                <div>
                  <label className="block text-xs font-medium text-[var(--ag-text-muted)] mb-1.5">
                    Price / Input Token ($)
                  </label>
                  <input
                    type="number"
                    step="any"
                    min="0"
                    value={modalPriceIn}
                    onChange={(e) => setModalPriceIn(e.target.value)}
                    className="w-full px-3 py-2 rounded-lg bg-[var(--ag-bg)] border border-[var(--ag-border)] text-sm font-mono focus:outline-none focus:border-[var(--ag-accent)] focus:ring-1 focus:ring-[var(--ag-accent)] transition-colors"
                  />
                </div>
                <div>
                  <label className="block text-xs font-medium text-[var(--ag-text-muted)] mb-1.5">
                    Price / Output Token ($)
                  </label>
                  <input
                    type="number"
                    step="any"
                    min="0"
                    value={modalPriceOut}
                    onChange={(e) => setModalPriceOut(e.target.value)}
                    className="w-full px-3 py-2 rounded-lg bg-[var(--ag-bg)] border border-[var(--ag-border)] text-sm font-mono focus:outline-none focus:border-[var(--ag-accent)] focus:ring-1 focus:ring-[var(--ag-accent)] transition-colors"
                  />
                </div>
              </div>

              {/* Endpoint */}
              <div>
                <label className="block text-xs font-medium text-[var(--ag-text-muted)] mb-1.5">
                  Endpoint
                </label>
                <select
                  value={modalEndpointId}
                  onChange={(e) => setModalEndpointId(e.target.value)}
                  className="w-full px-3 py-2 rounded-lg bg-[var(--ag-bg)] border border-[var(--ag-border)] text-sm focus:outline-none focus:border-[var(--ag-accent)] focus:ring-1 focus:ring-[var(--ag-accent)] transition-colors"
                >
                  <option value="">Global default</option>
                  {endpoints.map((ep) => (
                    <option key={ep.id} value={ep.id}>
                      {ep.name} ({ep.base_url})
                    </option>
                  ))}
                </select>
              </div>

              {/* Rate limits row */}
              <div className="grid grid-cols-2 gap-4">
                <div>
                  <label className="block text-xs font-medium text-[var(--ag-text-muted)] mb-1.5">
                    RPM Override
                  </label>
                  <input
                    type="number"
                    min="0"
                    value={modalRpm}
                    onChange={(e) => setModalRpm(e.target.value)}
                    placeholder="Inherit from endpoint"
                    className="w-full px-3 py-2 rounded-lg bg-[var(--ag-bg)] border border-[var(--ag-border)] text-sm font-mono focus:outline-none focus:border-[var(--ag-accent)] focus:ring-1 focus:ring-[var(--ag-accent)] transition-colors"
                  />
                </div>
                <div>
                  <label className="block text-xs font-medium text-[var(--ag-text-muted)] mb-1.5">
                    Daily Override
                  </label>
                  <input
                    type="number"
                    min="0"
                    value={modalDay}
                    onChange={(e) => setModalDay(e.target.value)}
                    placeholder="Inherit from endpoint"
                    className="w-full px-3 py-2 rounded-lg bg-[var(--ag-bg)] border border-[var(--ag-border)] text-sm font-mono focus:outline-none focus:border-[var(--ag-accent)] focus:ring-1 focus:ring-[var(--ag-accent)] transition-colors"
                  />
                </div>
              </div>

              {/* Error */}
              {modalError && (
                <p className="text-sm text-[var(--ag-danger)]">{modalError}</p>
              )}

              {/* Footer buttons */}
              <div className="flex justify-end gap-2 pt-2">
                <button
                  type="button"
                  onClick={closeModal}
                  className="px-4 py-2 rounded-lg border border-[var(--ag-border)] text-sm hover:border-[var(--ag-border-hover)] transition-colors"
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  disabled={modalSaving}
                  className="px-4 py-2 rounded-lg bg-[var(--ag-accent)] text-white text-sm font-medium hover:bg-[var(--ag-accent-hover)] transition-colors disabled:opacity-50"
                >
                  {modalSaving
                    ? "Saving…"
                    : modalMode === "add"
                      ? "Create Model"
                      : "Save Changes"}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
}
