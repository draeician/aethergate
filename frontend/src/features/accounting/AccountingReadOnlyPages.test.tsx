import { describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import BudgetReservationsPage from "./BudgetReservationsPage";
import UsagePage from "./UsagePage";
import LedgerPage from "./LedgerPage";
import AuditPage from "./AuditPage";
import { api, ApiError } from "../../lib/client";
import { wrap, projectSession } from "../../test/helpers";

function pageOf<T>(items: T[], total = items.length) {
  return { items, total, limit: 20, offset: 0 };
}

describe("BudgetReservationsPage", () => {
  it("renders a released pre-dispatch reservation with a null snapshot", async () => {
    vi.spyOn(api, "listBudgetReservations").mockResolvedValue(
      pageOf([
        {
          id: "r1",
          request_id: "req1",
          budget_policy_id: "bp1",
          price_snapshot_id: null,
          reserved_amount: "0",
          committed_amount: "0",
          state: "released",
          settlement_reason: "released before dispatch",
        },
      ]),
    );

    render(<BudgetReservationsPage />, { wrapper: wrap({ session: projectSession("proj1"), roles: ["project_admin"] }) });

    await waitFor(() =>
      expect(screen.getByText("No snapshot / released before dispatch")).toBeInTheDocument(),
    );
    expect(screen.getByText("released")).toBeInTheDocument();
    expect(screen.getByText("released before dispatch")).toBeInTheDocument();
  });
});

describe("UsagePage", () => {
  it("renders an exact usage amount", async () => {
    vi.spyOn(api, "listUsageRecords").mockResolvedValue(
      pageOf([
        {
          id: "u1",
          request_id: "req1",
          execution_attempt_id: "ea1",
          project_id: "proj1",
          principal_id: null,
          api_credential_id: null,
          model_alias_id: "ma1",
          route_binding_id: "rb1",
          provider_account_id: "pa1",
          price_snapshot_id: "ps1",
          billing_unit: "token",
          input_units: 12,
          output_units: 34,
          request_units: null,
          amount: "0.000000000123",
          currency: "USD",
          recorded_at: "2026-10-09T00:00:00Z",
          upstream_request_id: null,
        },
      ]),
    );

    render(<UsagePage />, { wrapper: wrap({ session: projectSession("proj1"), roles: ["project_admin"] }) });

    await waitFor(() => expect(screen.getByText("0.000000000123 USD")).toBeInTheDocument());
  });

  it("passes server-side pagination and filters to the client", async () => {
    const list = vi.spyOn(api, "listUsageRecords").mockResolvedValue({ items: [], total: 0, limit: 20, offset: 40 });

    render(<UsagePage />, { wrapper: wrap({ session: projectSession("proj1"), roles: ["project_admin"] }) });

    await waitFor(() =>
      expect(list).toHaveBeenCalledWith(expect.objectContaining({ limit: 20, offset: 0, project_id: "proj1" })),
    );
  });

  it("renders a 403 authorization error as a structured message", async () => {
    vi.spyOn(api, "listUsageRecords").mockRejectedValue(
      new ApiError(403, "admin_authorization_error", "You are not authorized.", null),
    );

    render(<UsagePage />, { wrapper: wrap({ session: projectSession("proj1"), roles: ["project_viewer"] }) });

    await waitFor(() => expect(screen.getByText(/You are not authorized\./)).toBeInTheDocument());
  });
});

describe("LedgerPage", () => {
  it("renders a signed ledger amount exactly", async () => {
    vi.spyOn(api, "listLedgerEntries").mockResolvedValue(
      pageOf([
        {
          id: "l1",
          project_id: "proj1",
          usage_record_id: "u1",
          entry_type: "usage_debit",
          amount: "-0.000000000456",
          currency: "USD",
          created_at: "2026-10-09T00:00:00Z",
          idempotency_key: null,
          reason: null,
        },
      ]),
    );

    render(<LedgerPage />, { wrapper: wrap({ session: projectSession("proj1"), roles: ["project_admin"] }) });

    await waitFor(() => expect(screen.getByText("-0.000000000456 USD")).toBeInTheDocument());
    expect(screen.getByText("usage_debit")).toBeInTheDocument();
  });
});

describe("AuditPage", () => {
  it("distinguishes deployment-scoped (null project) from project-scoped events", async () => {
    vi.spyOn(api, "listAuditEvents").mockResolvedValue(
      pageOf([
        {
          id: "a1",
          actor_principal_id: "p1",
          project_id: null,
          action: "price_policy.created",
          resource_type: "price_policy",
          resource_id: "pp1",
          occurred_at: "2026-10-09T00:00:00Z",
          metadata: { route_binding_id: "rb1" },
        },
        {
          id: "a2",
          actor_principal_id: "p1",
          project_id: "proj1",
          action: "project_budget_policy.created",
          resource_type: "project_budget_policy",
          resource_id: "bp1",
          occurred_at: "2026-10-09T00:01:00Z",
          metadata: {},
        },
      ]),
    );

    render(<AuditPage />, { wrapper: wrap({ session: projectSession("proj1"), roles: ["system_admin"] }) });

    await waitFor(() => expect(screen.getByText("price_policy.created")).toBeInTheDocument());
    expect(screen.getByText("deployment")).toBeInTheDocument();
    expect(screen.getByText("proj1")).toBeInTheDocument();
  });
});
