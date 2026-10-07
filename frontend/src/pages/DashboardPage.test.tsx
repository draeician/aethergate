import { describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import DashboardPage from "./DashboardPage";
import { AuthContext, type AuthState } from "../context/auth-context";
import { api, type SessionRead } from "../lib/client";

function wrap(auth: Partial<AuthState>) {
  const value = {
    status: "authenticated" as const,
    session: null,
    roles: [],
    refreshSession: async () => {},
    logout: async () => {},
    ...auth,
  };
  return ({ children }: { children: ReactNode }) => (
    <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
  );
}

const systemAdminSession: SessionRead = {
  principal_id: "p1",
  project_id: "proj1",
  authentication_kind: "browser_session",
  browser_session_id: "bs1",
  roles: ["system_admin"],
  issuer: null,
  subject: null,
};

describe("DashboardPage", () => {
  it("renders authoritative queue/endpoint data and marks missing metrics as not instrumented", async () => {
    vi.spyOn(api, "queueSummary").mockResolvedValue({
      queued_total: 4,
      in_flight_total: 2,
      outcome_unknown_total: 1,
      oldest_queued_at: null,
      oldest_wait_seconds: 12,
      counts_by_state: {},
    });
    vi.spyOn(api, "queueEndpoints").mockResolvedValue({
      items: [
        {
          endpoint_id: "e1",
          name: "Endpoint A",
          available_slots: 1,
          occupied_slots: 1,
          max_concurrency: 2,
          is_active: true,
          operational_state: "active",
          draining_complete: false,
          oldest_queued_at: null,
        },
      ],
      total: 1,
      limit: 20,
      offset: 0,
    });
    vi.spyOn(api, "queueQuotaStatus").mockResolvedValue({
      items: [],
      total: 0,
      limit: 20,
      offset: 0,
    });

    render(<DashboardPage />, {
      wrapper: wrap({ session: systemAdminSession, roles: ["system_admin"] }),
    });

    await waitFor(() => expect(screen.getByText("4")).toBeInTheDocument());
    expect(screen.getByText("2")).toBeInTheDocument();
    expect(screen.getByText("Endpoint A")).toBeInTheDocument();
    expect(screen.getByText("1 / 2 slots")).toBeInTheDocument();

    expect(screen.getByText("Queue / TTFT percentiles")).toBeInTheDocument();
    expect(screen.getByText("Upstream health")).toBeInTheDocument();
    expect(screen.getByText("Retry rate")).toBeInTheDocument();
    expect(screen.getAllByText("Not yet instrumented by the backend.")).toHaveLength(3);
  });
});
