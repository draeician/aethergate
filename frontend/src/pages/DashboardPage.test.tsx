import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import type { ReactNode } from "react";
import DashboardPage from "./DashboardPage";
import { AuthContext, type AuthState } from "../context/auth-context";
import {
  api,
  type SessionRead,
  type BudgetStatusRead,
  type ObservabilitySummaryRead,
  type UpstreamHealthRead,
} from "../lib/client";

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

function observabilitySummary(
  overrides: Partial<ObservabilitySummaryRead> = {},
): ObservabilitySummaryRead {
  return {
    window: {
      window_start: "2026-01-01T00:00:00Z",
      window_end: "2026-01-01T00:15:00Z",
      window_seconds: 900,
    },
    queue_wait: { sample_count: 6, p50_ms: 120, p95_ms: 340, p99_ms: 500 },
    ttft: { sample_count: 3, p50_ms: 80, p95_ms: 210, p99_ms: 260 },
    retry: {
      attempted_requests: 10,
      retried_requests: 2,
      retry_attempts: 3,
      request_retry_rate: 0.2,
    },
    ...overrides,
  };
}

function mockBase() {
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
  vi.spyOn(api, "budgetStatus").mockResolvedValue([]);
  vi.spyOn(api, "observabilitySummary").mockResolvedValue(observabilitySummary());
  vi.spyOn(api, "observabilityUpstreams").mockResolvedValue({
    items: [],
    total: 0,
    limit: 20,
    offset: 0,
  });
}

describe("DashboardPage", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("renders authoritative queue/endpoint data", async () => {
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
    vi.spyOn(api, "budgetStatus").mockResolvedValue([]);
    vi.spyOn(api, "observabilitySummary").mockResolvedValue(observabilitySummary());
    vi.spyOn(api, "observabilityUpstreams").mockResolvedValue({
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
    expect(screen.queryByText("Not yet instrumented")).not.toBeInTheDocument();
  });

  it("renders queue-wait, TTFT, and retry metrics instead of placeholders", async () => {
    mockBase();
    render(<DashboardPage />, {
      wrapper: wrap({ session: systemAdminSession, roles: ["system_admin"] }),
    });

    await waitFor(() => expect(screen.getByText("Queue wait")).toBeInTheDocument());
    // Queue wait p50/p95/p99 + sample count.
    expect(screen.getByText("120ms")).toBeInTheDocument();
    expect(screen.getByText("340ms")).toBeInTheDocument();
    expect(screen.getByText("500ms")).toBeInTheDocument();
    expect(screen.getByText("6 samples")).toBeInTheDocument();
    // TTFT label says streaming / dispatch-to-first-token.
    expect(screen.getByText(/Dispatch-to-first-token/)).toBeInTheDocument();
    expect(screen.getByText("80ms")).toBeInTheDocument();
    // Retry rate renders denominator/counts.
    expect(screen.getByText("20.0%")).toBeInTheDocument();
    expect(screen.getByText(/2 retried \/ 10 attempted/)).toBeInTheDocument();
  });

  it("shows 'No samples' for no-sample percentiles and retry", async () => {
    mockBase();
    vi.spyOn(api, "observabilitySummary").mockResolvedValue(
      observabilitySummary({
        queue_wait: { sample_count: 0, p50_ms: null, p95_ms: null, p99_ms: null },
        ttft: { sample_count: 0, p50_ms: null, p95_ms: null, p99_ms: null },
        retry: {
          attempted_requests: 0,
          retried_requests: 0,
          retry_attempts: 0,
          request_retry_rate: null,
        },
      }),
    );
    render(<DashboardPage />, {
      wrapper: wrap({ session: systemAdminSession, roles: ["system_admin"] }),
    });
    await waitFor(() => expect(screen.getAllByText("No samples").length).toBeGreaterThanOrEqual(3));
    expect(screen.queryByText("0ms")).not.toBeInTheDocument();
  });

  it("renders system_admin upstream health cards", async () => {
    mockBase();
    const row: UpstreamHealthRead = {
      endpoint_id: "e1",
      endpoint_name: "Ollama endpoint",
      provider_account_id: "a1",
      sample_count: 5,
      succeeded_attempts: 3,
      upstream_failed_attempts: 2,
      ambiguous_attempts: 0,
      rate_limited_attempts: 1,
      upstream_success_rate: 0.6,
      last_success_at: "2026-01-01T00:10:00Z",
      last_failure_at: "2026-01-01T00:11:00Z",
      cooldown_until: null,
    };
    vi.spyOn(api, "observabilityUpstreams").mockResolvedValue({
      items: [row],
      total: 1,
      limit: 20,
      offset: 0,
    });
    render(<DashboardPage />, {
      wrapper: wrap({ session: systemAdminSession, roles: ["system_admin"] }),
    });
    await waitFor(() => expect(screen.getByText("Ollama endpoint")).toBeInTheDocument());
    expect(screen.getByText("60.0%")).toBeInTheDocument();
    expect(screen.getByText(/1 429/)).toBeInTheDocument();
  });

  it("never requests/render deployment upstream health for project roles", async () => {
    mockBase();
    const upstreamSpy = vi.spyOn(api, "observabilityUpstreams");
    render(<DashboardPage />, {
      wrapper: wrap({ session: projectAdminSession, roles: ["project_admin"] }),
    });
    await waitFor(() => expect(screen.getByText("Performance")).toBeInTheDocument());
    expect(upstreamSpy).not.toHaveBeenCalled();
    expect(screen.queryByText("Upstream health")).not.toBeInTheDocument();
  });

  it("changes the summary window when the user selects 1h", async () => {
    mockBase();
    const summarySpy = vi.spyOn(api, "observabilitySummary");
    render(<DashboardPage />, {
      wrapper: wrap({ session: systemAdminSession, roles: ["system_admin"] }),
    });
    await waitFor(() => expect(summarySpy).toHaveBeenCalledWith({ window_seconds: 900 }));
    fireEvent.click(screen.getByRole("button", { name: "1h" }));
    await waitFor(() => expect(summarySpy).toHaveBeenCalledWith({ window_seconds: 3600 }));
  });

  describe("budget headroom classification", () => {
    const renderBudget = (budget: BudgetStatusRead) => {
      mockBase();
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
