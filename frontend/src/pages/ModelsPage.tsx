import { useEffect, useState, useCallback } from "react";
import { Cpu, RefreshCw, Plus } from "lucide-react";
import { useAuth } from "../context/AuthContext";
import { listModels, listEndpoints, upsertModel } from "../lib/api";
import type { LLMModel, LLMEndpoint } from "../lib/types";

export default function ModelsPage() {
  const { adminKey } = useAuth();
  const [models, setModels] = useState<LLMModel[]>([]);
  const [endpoints, setEndpoints] = useState<LLMEndpoint[]>([]);
  const [loading, setLoading] = useState(true);
  const [showForm, setShowForm] = useState(false);
  const [modelId, setModelId] = useState("");
  const [litellmName, setLitellmName] = useState("");
  const [priceIn, setPriceIn] = useState("0.000001");
  const [priceOut, setPriceOut] = useState("0.000002");
  const [endpointId, setEndpointId] = useState<string>("");
  const [rpmLimit, setRpmLimit] = useState("");
  const [dayLimit, setDayLimit] = useState("");
  const [formError, setFormError] = useState("");
  const [error, setError] = useState("");

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

  useEffect(() => { load(); }, [load]);

  async function handleUpsert(e: React.FormEvent) {
    e.preventDefault();
    if (!adminKey) return;
    setFormError("");
    try {
      await upsertModel(adminKey, {
        id: modelId.trim().toLowerCase().replace(/\s+/g, "-"),
        litellm_name: litellmName.trim(),
        price_in: parseFloat(priceIn),
        price_out: parseFloat(priceOut),
        endpoint_id: endpointId ? parseInt(endpointId) : null,
        rpm_limit: rpmLimit ? parseInt(rpmLimit) : null,
        day_limit: dayLimit ? parseInt(dayLimit) : null,
      });
      setModelId(""); setLitellmName(""); setPriceIn("0.000001"); setPriceOut("0.000002");
      setEndpointId(""); setRpmLimit(""); setDayLimit("");
      setShowForm(false);
      load();
    } catch (err: any) {
      setFormError(err.message);
    }
  }

  return (
    <div className="space-y-6 max-w-5xl">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-xl font-bold">Models</h2>
          <p className="text-sm text-[var(--ag-text-muted)] mt-1">Configure model routing, pricing & endpoint bindings</p>
        </div>
        <div className="flex gap-2">
          <button onClick={load} className="p-2 rounded-lg border border-[var(--ag-border)] text-[var(--ag-text-muted)] hover:text-[var(--ag-text)] hover:border-[var(--ag-border-hover)] transition-colors">
            <RefreshCw size={16} />
          </button>
          <button onClick={() => setShowForm(!showForm)} className="flex items-center gap-2 px-4 py-2 rounded-lg bg-[var(--ag-accent)] text-white text-sm font-medium hover:bg-[var(--ag-accent-hover)] transition-colors">
            <Plus size={16} /> Add Model
          </button>
        </div>
      </div>

      {/* Upsert Form */}
      {showForm && (
        <form onSubmit={handleUpsert} className="bg-[var(--ag-surface)] border border-[var(--ag-border)] rounded-xl p-5 space-y-4">
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            <div>
              <label className="block text-xs font-medium text-[var(--ag-text-muted)] mb-1.5">Public Model ID</label>
              <input value={modelId} onChange={(e) => setModelId(e.target.value)} placeholder="gpt-4-turbo" required className="w-full px-3 py-2 rounded-lg bg-[var(--ag-bg)] border border-[var(--ag-border)] text-sm focus:outline-none focus:border-[var(--ag-accent)] focus:ring-1 focus:ring-[var(--ag-accent)] transition-colors" />
            </div>
            <div>
              <label className="block text-xs font-medium text-[var(--ag-text-muted)] mb-1.5">LiteLLM Internal Name</label>
              <input value={litellmName} onChange={(e) => setLitellmName(e.target.value)} placeholder="ollama/llama3" required className="w-full px-3 py-2 rounded-lg bg-[var(--ag-bg)] border border-[var(--ag-border)] text-sm focus:outline-none focus:border-[var(--ag-accent)] focus:ring-1 focus:ring-[var(--ag-accent)] transition-colors" />
            </div>
          </div>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            <div>
              <label className="block text-xs font-medium text-[var(--ag-text-muted)] mb-1.5">Price / Input Token ($)</label>
              <input type="number" step="any" min="0" value={priceIn} onChange={(e) => setPriceIn(e.target.value)} className="w-full px-3 py-2 rounded-lg bg-[var(--ag-bg)] border border-[var(--ag-border)] text-sm font-mono focus:outline-none focus:border-[var(--ag-accent)] focus:ring-1 focus:ring-[var(--ag-accent)] transition-colors" />
            </div>
            <div>
              <label className="block text-xs font-medium text-[var(--ag-text-muted)] mb-1.5">Price / Output Token ($)</label>
              <input type="number" step="any" min="0" value={priceOut} onChange={(e) => setPriceOut(e.target.value)} className="w-full px-3 py-2 rounded-lg bg-[var(--ag-bg)] border border-[var(--ag-border)] text-sm font-mono focus:outline-none focus:border-[var(--ag-accent)] focus:ring-1 focus:ring-[var(--ag-accent)] transition-colors" />
            </div>
          </div>
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
            <div>
              <label className="block text-xs font-medium text-[var(--ag-text-muted)] mb-1.5">Endpoint</label>
              <select value={endpointId} onChange={(e) => setEndpointId(e.target.value)} className="w-full px-3 py-2 rounded-lg bg-[var(--ag-bg)] border border-[var(--ag-border)] text-sm focus:outline-none focus:border-[var(--ag-accent)] focus:ring-1 focus:ring-[var(--ag-accent)] transition-colors">
                <option value="">Global default</option>
                {endpoints.map((ep) => (
                  <option key={ep.id} value={ep.id}>{ep.name} ({ep.base_url})</option>
                ))}
              </select>
            </div>
            <div>
              <label className="block text-xs font-medium text-[var(--ag-text-muted)] mb-1.5">RPM Override</label>
              <input type="number" min="0" value={rpmLimit} onChange={(e) => setRpmLimit(e.target.value)} placeholder="Inherit" className="w-full px-3 py-2 rounded-lg bg-[var(--ag-bg)] border border-[var(--ag-border)] text-sm font-mono focus:outline-none focus:border-[var(--ag-accent)] focus:ring-1 focus:ring-[var(--ag-accent)] transition-colors" />
            </div>
            <div>
              <label className="block text-xs font-medium text-[var(--ag-text-muted)] mb-1.5">Daily Override</label>
              <input type="number" min="0" value={dayLimit} onChange={(e) => setDayLimit(e.target.value)} placeholder="Inherit" className="w-full px-3 py-2 rounded-lg bg-[var(--ag-bg)] border border-[var(--ag-border)] text-sm font-mono focus:outline-none focus:border-[var(--ag-accent)] focus:ring-1 focus:ring-[var(--ag-accent)] transition-colors" />
            </div>
          </div>
          {formError && <p className="text-sm text-[var(--ag-danger)]">{formError}</p>}
          <button type="submit" className="px-4 py-2 rounded-lg bg-[var(--ag-accent)] text-white text-sm font-medium hover:bg-[var(--ag-accent-hover)] transition-colors">Save Model</button>
        </form>
      )}

      {error && <p className="text-[var(--ag-danger)] text-sm">{error}</p>}

      {/* Cards */}
      {loading ? (
        <p className="text-[var(--ag-text-muted)] text-sm">Loading...</p>
      ) : models.length === 0 ? (
        <div className="bg-[var(--ag-surface)] border border-[var(--ag-border)] rounded-xl p-8 text-center text-[var(--ag-text-muted)]">No models configured yet.</div>
      ) : (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
          {models.map((m) => (
            <div key={m.id} className="bg-[var(--ag-surface)] border border-[var(--ag-border)] rounded-xl p-5 space-y-3 hover:border-[var(--ag-border-hover)] transition-colors">
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <Cpu size={16} className="text-[var(--ag-accent)]" />
                  <span className="font-medium text-sm">{m.id}</span>
                </div>
                <span className={`inline-block px-2 py-0.5 rounded-full text-xs font-medium ${m.is_active ? "bg-[var(--ag-success)]/15 text-[var(--ag-success)]" : "bg-[var(--ag-danger)]/15 text-[var(--ag-danger)]"}`}>
                  {m.is_active ? "Active" : "Inactive"}
                </span>
              </div>
              <p className="text-xs text-[var(--ag-text-muted)] font-mono">{m.litellm_name}</p>
              <div className="flex gap-4 text-xs">
                <div><span className="text-[var(--ag-text-muted)]">In:</span>{" "}<span className="font-mono text-[var(--ag-success)]">${m.price_in}</span></div>
                <div><span className="text-[var(--ag-text-muted)]">Out:</span>{" "}<span className="font-mono text-[var(--ag-warning)]">${m.price_out}</span></div>
              </div>
              <div className="flex flex-wrap gap-2 text-xs text-[var(--ag-text-muted)]">
                <span className="px-1.5 py-0.5 rounded bg-[var(--ag-surface-2)]">{m.endpoint_name ?? "default"}</span>
                <span className="px-1.5 py-0.5 rounded bg-[var(--ag-surface-2)]">{m.capability}</span>
                {m.rpm_limit && <span className="px-1.5 py-0.5 rounded bg-[var(--ag-surface-2)]">{m.rpm_limit} RPM</span>}
                {m.day_limit && <span className="px-1.5 py-0.5 rounded bg-[var(--ag-surface-2)]">{m.day_limit}/day</span>}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
