import { describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import DashboardPage from "./DashboardPage";
import { AuthContext, type AuthState } from "../context/auth-context";
import { api, type SessionRead, type BudgetStatusRead } from "../lib/client";

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

const projectAdminSession: SessionRead = {
  ...systemAdminSession,
  roles: ["project_admin"],
};

function budgetStatus(overrides: Partial<BudgetStatusRead>): BudgetStatusRead {
  return {
    budget_policy_id: "bp1",
    project_id: "proj1",
    currency: "USD",
    limit_amount: "100",
    committed_amount: "0",
    reserved_amount: "0",
    headroom: "100",
    window_start: "2026-01-01T00:00:00Z",
    window_end: "2026-01-02T00:00:00Z",
    enabled: true,
    ...overrides,
  };
}

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

  describe("budget headroom classification", () => {
    const renderBudget = (budget: BudgetStatusRead) => {
      vi.spyOn(api, "queueSummary").mockResolvedValue({
        queued_total: 0,
        in_flight_total: 0,
        outcome_unknown_total: 0,
        oldest_queued_at: null,
        oldest_wait_seconds: 0,
        counts_by_state: {},
      });
      vi.spyOn(api, "queueEndpoints").mockResolvedValue({
        items: [],
        total: 0,
        limit: 20,
        offset: 0,
      });
      vi.spyOn(api, "queueQuotaStatus").mockResolvedValue({
        items: [],
        total: 0,
        limit: 20,
        offset: 0,
      });
      vi.spyOn(api, "budgetStatus").mockResolvedValue([budget]);
      render(<DashboardPage />, {
        wrapper: wrap({ session: projectAdminSession, roles: ["project_admin"] }),
      });
    };

    it("marks a tiny exact positive headroom as ok", async () => {
      renderBudget(budgetStatus({ headroom: "0.000000000001" }));
      await waitFor(() => expect(screen.getByText("ok")).toBeInTheDocument());
      expect(screen.getByText("0.000000000001 USD headroom of 100 USD")).toBeInTheDocument();
    });

    it("marks a zero headroom as exhausted", async () => {
      renderBudget(budgetStatus({ headroom: "0" }));
      await waitFor(() => expect(screen.getByText("exhausted")).toBeInTheDocument());
    });

    it("marks a tiny exact negative headroom as exhausted", async () => {
      renderBudget(budgetStatus({ headroom: "-0.000000000001" }));
      await waitFor(() => expect(screen.getByText("exhausted")).toBeInTheDocument());
    });

    it("renders a very large precise positive headroom verbatim", async () => {
      renderBudget(budgetStatus({ headroom: "123456789.123456789012" }));
      await waitFor(() => expect(screen.getByText("ok")).toBeInTheDocument());
      expect(
        screen.getByText("123456789.123456789012 USD headroom of 100 USD"),
      ).toBeInTheDocument();
    });

    it("renders a very large precise negative headroom verbatim as exhausted", async () => {
      renderBudget(budgetStatus({ headroom: "-123456789.123456789012" }));
      await waitFor(() => expect(screen.getByText("exhausted")).toBeInTheDocument());
      expect(
        screen.getByText("-123456789.123456789012 USD headroom of 100 USD"),
      ).toBeInTheDocument();
    });

    it("does not mark a disabled budget exhausted solely for non-positive headroom", async () => {
      renderBudget(budgetStatus({ enabled: false, headroom: "-123456789.123456789012" }));
      await waitFor(() => expect(screen.getByText("ok")).toBeInTheDocument());
      expect(screen.queryByText("exhausted")).not.toBeInTheDocument();
    });
  });
});
