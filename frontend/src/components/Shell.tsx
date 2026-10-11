import { Outlet } from "react-router-dom";
import { ShieldCheck } from "lucide-react";
import Sidebar from "./Sidebar";
import { useAuth } from "../context/auth-context";

function roleLabel(role: string): string {
  switch (role) {
    case "system_admin":
      return "System Admin";
    case "project_admin":
      return "Project Admin";
    case "project_viewer":
      return "Project Viewer";
    default:
      return role;
  }
}

export default function Shell() {
  const { session, roles } = useAuth();

  return (
    <div className="flex min-h-screen">
      <Sidebar />
      <div className="flex-1 min-w-0 flex flex-col">
        <header className="h-14 shrink-0 flex items-center justify-between px-6 border-b border-[var(--ag-border)] bg-[var(--ag-surface)]">
          <div className="text-xs text-[var(--ag-text-muted)] flex items-center gap-2 min-w-0">
            <ShieldCheck size={14} className="text-[var(--ag-accent)] shrink-0" />
            <span className="truncate">
              {session?.principal_id ?? "—"}
            </span>
          </div>
          <div className="flex items-center gap-2">
            {roles.map((role) => (
              <span
                key={role}
                className="text-xs px-2 py-1 rounded-md bg-[var(--ag-accent)]/10 text-[var(--ag-accent)]"
              >
                {roleLabel(role)}
              </span>
            ))}
          </div>
        </header>
        <main className="flex-1 p-8 overflow-auto">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
