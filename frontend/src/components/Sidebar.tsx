import { NavLink } from "react-router-dom";
import {
  LayoutDashboard,
  ListOrdered,
  TriangleAlert,
  LogOut,
  Folder,
  Users,
  KeyRound,
  ShieldCheck,
  Server,
  Landmark,
  Network,
  Gauge,
  Box,
  Route,
  CircleDollarSign,
  Camera,
  Wallet,
  Clock,
  BarChart3,
  BookOpen,
  ScrollText,
} from "lucide-react";
import { useAuth } from "../context/auth-context";
import { isSystemAdmin, isProjectAdmin } from "../lib/roles";

interface NavItem {
  to: string;
  label: string;
  icon: typeof Folder;
  end?: boolean;
}

export default function Sidebar() {
  const { logout, roles } = useAuth();
  const systemAdmin = isSystemAdmin(roles);
  const projectAdmin = isProjectAdmin(roles);
  const showAccounting = systemAdmin || projectAdmin || roles.includes("project_viewer");

  const identity: NavItem[] = [
    { to: "/projects", label: "Projects", icon: Folder },
    { to: "/principals", label: "Principals", icon: Users },
    { to: "/credentials", label: "Credentials", icon: KeyRound },
    { to: "/roles", label: "Roles", icon: ShieldCheck },
  ];

  const catalog: NavItem[] = systemAdmin
    ? [
        { to: "/catalog/providers", label: "Providers", icon: Server },
        { to: "/catalog/provider-accounts", label: "Provider Accounts", icon: Landmark },
        { to: "/catalog/endpoints", label: "Endpoints", icon: Network },
        { to: "/catalog/quotas", label: "Quotas", icon: Gauge },
        { to: "/catalog/models", label: "Model Aliases", icon: Box },
        { to: "/catalog/routes", label: "Route Bindings", icon: Route },
      ]
    : [];

  const accountingConfig: NavItem[] = systemAdmin
    ? [
        { to: "/accounting/pricing", label: "Pricing", icon: CircleDollarSign },
        { to: "/accounting/snapshots", label: "Snapshots", icon: Camera },
      ]
    : [];

  const accounting: NavItem[] = [
    { to: "/accounting/budgets", label: "Budgets", icon: Wallet },
    { to: "/accounting/reservations", label: "Reservations", icon: Clock },
    { to: "/accounting/usage", label: "Usage", icon: BarChart3 },
    { to: "/accounting/ledger", label: "Ledger", icon: BookOpen },
    { to: "/audit", label: "Audit", icon: ScrollText },
  ];

  const operational: NavItem[] = [
    { to: "/", label: "Dashboard", icon: LayoutDashboard, end: true },
    { to: "/queue", label: "Queue", icon: ListOrdered },
    ...(systemAdmin
      ? [{ to: "/outcome-unknown", label: "Outcome Unknown", icon: TriangleAlert }]
      : []),
  ];

  const linkClass = ({ isActive }: { isActive: boolean }) =>
    `flex items-center gap-3 px-3 py-2 rounded-lg text-sm transition-colors ${
      isActive
        ? "bg-[var(--ag-accent)]/15 text-[var(--ag-accent)]"
        : "text-[var(--ag-text-muted)] hover:text-[var(--ag-text)] hover:bg-[var(--ag-surface-2)]"
    }`;

  return (
    <aside className="w-60 shrink-0 h-screen sticky top-0 flex flex-col bg-[var(--ag-surface)] border-r border-[var(--ag-border)]">
      <div className="px-5 py-5 border-b border-[var(--ag-border)]">
        <h1 className="text-lg font-bold tracking-tight">
          <span className="text-[var(--ag-accent)]">Aether</span>Gate
        </h1>
        <p className="text-xs text-[var(--ag-text-muted)] mt-0.5">Operator Console</p>
      </div>

      <nav className="flex-1 px-3 py-4 space-y-4 overflow-y-auto">
        <div className="space-y-1">
          {operational.map(({ to, label, icon: Icon, end }) => (
            <NavLink key={to} to={to} end={end} className={linkClass}>
              <Icon size={18} />
              {label}
            </NavLink>
          ))}
        </div>

        <div className="space-y-1">
          <p className="px-3 text-[10px] uppercase tracking-wider text-[var(--ag-text-muted)]">Identity</p>
          {identity.map(({ to, label, icon: Icon }) => (
            <NavLink key={to} to={to} className={linkClass}>
              <Icon size={18} />
              {label}
            </NavLink>
          ))}
        </div>

        {catalog.length > 0 ? (
          <div className="space-y-1">
            <p className="px-3 text-[10px] uppercase tracking-wider text-[var(--ag-text-muted)]">Catalog</p>
            {catalog.map(({ to, label, icon: Icon }) => (
              <NavLink key={to} to={to} className={linkClass}>
                <Icon size={18} />
                {label}
              </NavLink>
            ))}
          </div>
        ) : null}

        {showAccounting ? (
          <div className="space-y-1">
            <p className="px-3 text-[10px] uppercase tracking-wider text-[var(--ag-text-muted)]">Accounting</p>
            {accountingConfig.map(({ to, label, icon: Icon }) => (
              <NavLink key={to} to={to} className={linkClass}>
                <Icon size={18} />
                {label}
              </NavLink>
            ))}
            {accounting.map(({ to, label, icon: Icon }) => (
              <NavLink key={to} to={to} className={linkClass}>
                <Icon size={18} />
                {label}
              </NavLink>
            ))}
          </div>
        ) : null}
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
