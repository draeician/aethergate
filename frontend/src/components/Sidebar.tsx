import { NavLink } from "react-router-dom";
import {
  LayoutDashboard,
  Users,
  KeyRound,
  Globe,
  Cpu,
  ScrollText,
  LogOut,
} from "lucide-react";
import { useAuth } from "../context/AuthContext";

const links = [
  { to: "/", label: "Dashboard", icon: LayoutDashboard },
  { to: "/users", label: "Users", icon: Users },
  { to: "/keys", label: "API Keys", icon: KeyRound },
  { to: "/endpoints", label: "Endpoints", icon: Globe },
  { to: "/models", label: "Models", icon: Cpu },
  { to: "/logs", label: "Logs", icon: ScrollText },
];

export default function Sidebar() {
  const { logout } = useAuth();

  return (
    <aside className="w-60 shrink-0 h-screen sticky top-0 flex flex-col bg-[var(--ag-surface)] border-r border-[var(--ag-border)]">
      {/* Brand */}
      <div className="px-5 py-5 border-b border-[var(--ag-border)]">
        <h1 className="text-lg font-bold tracking-tight">
          <span className="text-[var(--ag-accent)]">Aether</span>Gate
        </h1>
        <p className="text-xs text-[var(--ag-text-muted)] mt-0.5">Admin Console</p>
      </div>

      {/* Nav Links */}
      <nav className="flex-1 px-3 py-4 space-y-1">
        {links.map(({ to, label, icon: Icon }) => (
          <NavLink
            key={to}
            to={to}
            end={to === "/"}
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

      {/* Logout */}
      <div className="px-3 py-4 border-t border-[var(--ag-border)]">
        <button
          onClick={logout}
          className="flex items-center gap-3 px-3 py-2 rounded-lg text-sm text-[var(--ag-text-muted)] hover:text-[var(--ag-danger)] hover:bg-[var(--ag-surface-2)] transition-colors w-full"
        >
          <LogOut size={18} />
          Sign Out
        </button>
      </div>
    </aside>
  );
}
