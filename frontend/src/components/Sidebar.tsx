import { NavLink } from "react-router-dom";
import {
  LayoutDashboard,
  ListOrdered,
  TriangleAlert,
  LogOut,
} from "lucide-react";
import { useAuth } from "../context/auth-context";
import { isSystemAdmin } from "../lib/roles";

export default function Sidebar() {
  const { logout, roles } = useAuth();

  const links = [
    { to: "/", label: "Dashboard", icon: LayoutDashboard, end: true },
    { to: "/queue", label: "Queue", icon: ListOrdered, end: false },
    ...(isSystemAdmin(roles)
      ? [{ to: "/outcome-unknown", label: "Outcome Unknown", icon: TriangleAlert, end: false }]
      : []),
  ];

  return (
    <aside className="w-60 shrink-0 h-screen sticky top-0 flex flex-col bg-[var(--ag-surface)] border-r border-[var(--ag-border)]">
      <div className="px-5 py-5 border-b border-[var(--ag-border)]">
        <h1 className="text-lg font-bold tracking-tight">
          <span className="text-[var(--ag-accent)]">Aether</span>Gate
        </h1>
        <p className="text-xs text-[var(--ag-text-muted)] mt-0.5">Operator Console</p>
      </div>

      <nav className="flex-1 px-3 py-4 space-y-1">
        {links.map(({ to, label, icon: Icon, end }) => (
          <NavLink
            key={to}
            to={to}
            end={end}
            className={({ isActive }) =>
              `flex items-center gap-3 px-3 py-2 rounded-lg text-sm transition-colors ${
                isActive
                  ? "bg-[var(--ag-accent)]/15 text-[var(--ag-accent)]"
                  : "text-[var(--ag-text-muted)] hover:text-[var(--ag-text)] hover:bg-[var(--ag-surface-2)]"
              }`
            }
          >
            <Icon size={18} />
            {label}
          </NavLink>
        ))}
      </nav>

      <div className="px-3 py-4 border-t border-[var(--ag-border)]">
        <button
          onClick={() => void logout()}
          className="flex items-center gap-3 px-3 py-2 rounded-lg text-sm text-[var(--ag-text-muted)] hover:text-[var(--ag-danger)] hover:bg-[var(--ag-surface-2)] transition-colors w-full"
        >
          <LogOut size={18} />
          Sign Out
        </button>
      </div>
    </aside>
  );
}
