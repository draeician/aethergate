import { useEffect, useState, useCallback } from "react";
import { RefreshCw, ChevronLeft, ChevronRight } from "lucide-react";
import { useAuth } from "../context/AuthContext";
import { listLogs } from "../lib/api";
import type { RequestLogEntry } from "../lib/types";

const PAGE_SIZE = 25;

export default function LogsPage() {
  const { adminKey } = useAuth();
  const [logs, setLogs] = useState<RequestLogEntry[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    if (!adminKey) return;
    setLoading(true);
    try {
      const data = await listLogs(adminKey, { limit: PAGE_SIZE, offset: page * PAGE_SIZE });
      setLogs(data.items);
      setTotal(data.total);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, [adminKey, page]);

  useEffect(() => { load(); }, [load]);

  const totalPages = Math.ceil(total / PAGE_SIZE);

  return (
    <div className="space-y-6 max-w-6xl">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-xl font-bold">Request Logs</h2>
          <p className="text-sm text-[var(--ag-text-muted)] mt-1">
            {total.toLocaleString()} total requests
          </p>
        </div>
        <button onClick={load} className="p-2 rounded-lg border border-[var(--ag-border)] text-[var(--ag-text-muted)] hover:text-[var(--ag-text)] hover:border-[var(--ag-border-hover)] transition-colors">
          <RefreshCw size={16} />
        </button>
      </div>

      {error && <p className="text-[var(--ag-danger)] text-sm">{error}</p>}

      {loading ? (
        <p className="text-[var(--ag-text-muted)] text-sm">Loading...</p>
      ) : (
        <>
          <div className="bg-[var(--ag-surface)] border border-[var(--ag-border)] rounded-xl overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-[var(--ag-border)] text-[var(--ag-text-muted)] text-xs uppercase tracking-wider">
                  <th className="text-left px-5 py-3 font-medium">Timestamp</th>
                  <th className="text-left px-5 py-3 font-medium">User</th>
                  <th className="text-left px-5 py-3 font-medium">Model</th>
                  <th className="text-right px-5 py-3 font-medium">Input</th>
                  <th className="text-right px-5 py-3 font-medium">Output</th>
                  <th className="text-right px-5 py-3 font-medium">Cost</th>
                </tr>
              </thead>
              <tbody>
                {logs.map((log) => (
                  <tr key={log.id} className="border-b border-[var(--ag-border)] last:border-b-0 hover:bg-[var(--ag-surface-2)] transition-colors">
                    <td className="px-5 py-3 text-[var(--ag-text-muted)] text-xs font-mono whitespace-nowrap">
                      {new Date(log.timestamp).toLocaleString()}
                    </td>
                    <td className="px-5 py-3">{log.username}</td>
                    <td className="px-5 py-3 font-mono text-[var(--ag-accent)]">{log.model_used}</td>
                    <td className="px-5 py-3 text-right font-mono">{Math.round(log.input_units).toLocaleString()}</td>
                    <td className="px-5 py-3 text-right font-mono">{Math.round(log.output_units).toLocaleString()}</td>
                    <td className="px-5 py-3 text-right font-mono text-[var(--ag-warning)]">
                      ${log.total_cost.toFixed(6)}
                    </td>
                  </tr>
                ))}
                {logs.length === 0 && (
                  <tr>
                    <td colSpan={6} className="px-5 py-8 text-center text-[var(--ag-text-muted)]">
                      No logs yet.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>

          {/* Pagination */}
          {totalPages > 1 && (
            <div className="flex items-center justify-between">
              <p className="text-xs text-[var(--ag-text-muted)]">
                Page {page + 1} of {totalPages}
              </p>
              <div className="flex gap-1">
                <button
                  onClick={() => setPage((p) => Math.max(0, p - 1))}
                  disabled={page === 0}
                  className="p-2 rounded-lg border border-[var(--ag-border)] text-[var(--ag-text-muted)] hover:text-[var(--ag-text)] hover:border-[var(--ag-border-hover)] disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
                >
                  <ChevronLeft size={16} />
                </button>
                <button
                  onClick={() => setPage((p) => Math.min(totalPages - 1, p + 1))}
                  disabled={page >= totalPages - 1}
                  className="p-2 rounded-lg border border-[var(--ag-border)] text-[var(--ag-text-muted)] hover:text-[var(--ag-text)] hover:border-[var(--ag-border-hover)] disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
                >
                  <ChevronRight size={16} />
                </button>
              </div>
            </div>
          )}
        </>
      )}
    </div>
  );
}
