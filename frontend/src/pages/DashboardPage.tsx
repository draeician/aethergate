import { useEffect, useState } from "react";
import {
  Users,
  KeyRound,
  Cpu,
  ScrollText,
  DollarSign,
  Activity,
  Zap,
  CircleCheck,
  CircleX,
} from "lucide-react";
import { useAuth } from "../context/AuthContext";
import { getStats, getHealth } from "../lib/api";
import type { Stats, HealthStatus } from "../lib/types";

function StatCard({
  label,
  value,
  icon: Icon,
  accent = false,
}: {
  label: string;
  value: string | number;
  icon: React.ElementType;
  accent?: boolean;
}) {
  return (
    <div className="bg-[var(--ag-surface)] border border-[var(--ag-border)] rounded-xl p-5">
      <div className="flex items-center justify-between mb-3">
        <span className="text-xs font-medium uppercase tracking-wider text-[var(--ag-text-muted)]">
          {label}
        </span>
        <Icon
          size={18}
          className={accent ? "text-[var(--ag-accent)]" : "text-[var(--ag-text-muted)]"}
        />
      </div>
      <p className={`text-2xl font-bold ${accent ? "text-[var(--ag-accent)]" : ""}`}>
        {value}
      </p>
    </div>
  );
}

export default function DashboardPage() {
  const { adminKey } = useAuth();
  const [stats, setStats] = useState<Stats | null>(null);
  const [health, setHealth] = useState<HealthStatus | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!adminKey) return;
    Promise.all([getStats(adminKey), getHealth()])
      .then(([s, h]) => {
        setStats(s);
        setHealth(h);
      })
      .catch((e) => setError(e.message));
  }, [adminKey]);

  if (error)
    return <p className="text-[var(--ag-danger)]">Error: {error}</p>;
  if (!stats || !health)
    return <p className="text-[var(--ag-text-muted)]">Loading...</p>;

  return (
    <div className="space-y-8 max-w-6xl">
      <div>
        <h2 className="text-xl font-bold">Dashboard</h2>
        <p className="text-sm text-[var(--ag-text-muted)] mt-1">System overview</p>
      </div>

      {/* Health Banner */}
      <div className="bg-[var(--ag-surface)] border border-[var(--ag-border)] rounded-xl p-5 flex items-center gap-4">
        {health.status === "online" ? (
          <CircleCheck size={22} className="text-[var(--ag-success)]" />
        ) : (
          <CircleX size={22} className="text-[var(--ag-danger)]" />
        )}
        <div>
          <p className="text-sm font-medium">
            System{" "}
            <span
              className={
                health.status === "online"
                  ? "text-[var(--ag-success)]"
                  : "text-[var(--ag-danger)]"
              }
            >
              {health.status}
            </span>
          </p>
          <p className="text-xs text-[var(--ag-text-muted)] mt-0.5">
            Backend: <code className="text-[var(--ag-accent)]">{health.target_inference_engine}</code>
          </p>
        </div>
      </div>

      {/* Stat Grid */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
        <StatCard label="Users" value={stats.users} icon={Users} />
        <StatCard label="API Keys" value={stats.api_keys} icon={KeyRound} />
        <StatCard label="Models" value={stats.models} icon={Cpu} />
        <StatCard label="Requests" value={stats.total_requests.toLocaleString()} icon={ScrollText} />
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
        <StatCard
          label="Total Revenue"
          value={`$${stats.total_revenue.toFixed(6)}`}
          icon={DollarSign}
          accent
        />
        <StatCard
          label="Input Tokens"
          value={stats.total_input_tokens.toLocaleString()}
          icon={Zap}
        />
        <StatCard
          label="Output Tokens"
          value={stats.total_output_tokens.toLocaleString()}
          icon={Activity}
        />
      </div>
    </div>
  );
}
