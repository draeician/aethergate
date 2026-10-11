import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import BudgetsPage from "./BudgetsPage";
import { api, type ProjectBudgetPolicyRead, type Role } from "../../lib/client";
import { wrap, projectSession } from "../../test/helpers";

function emptyPage() {
  return { items: [], total: 0, limit: 20, offset: 0 };
}

const budget: ProjectBudgetPolicyRead = {
  id: "bp1",
  project_id: "proj1",
  name: "prod-budget",
  currency: "USD",
  limit_amount: "123456789.123456789012",
  window_seconds: 3600,
  enabled: true,
};

function renderPage(roles: Role[] = ["project_admin"]) {
  render(<BudgetsPage />, { wrapper: wrap({ session: projectSession("proj1"), roles }) });
}

describe("BudgetsPage", () => {
  it("creates a budget with an exact Decimal limit string (canary)", async () => {
    vi.spyOn(api, "listProjectBudgetPolicies").mockResolvedValue(emptyPage());
    vi.spyOn(api, "budgetStatus").mockResolvedValue([]);
    const create = vi.spyOn(api, "createProjectBudgetPolicy").mockResolvedValue(budget);

    renderPage();

    fireEvent.click(screen.getByRole("button", { name: "New budget" }));
    const dialog = await screen.findByRole("dialog");
    fireEvent.change(within(dialog).getByLabelText("Name"), { target: { value: "prod-budget" } });
    fireEvent.change(within(dialog).getByLabelText("Currency"), { target: { value: "USD" } });
    fireEvent.change(within(dialog).getByLabelText("Limit amount"), { target: { value: "123456789.123456789012" } });
    fireEvent.change(within(dialog).getByLabelText("Window seconds"), { target: { value: "3600" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Create" }));

    await waitFor(() =>
      expect(create).toHaveBeenCalledWith({
        project_id: "proj1",
        name: "prod-budget",
        currency: "USD",
        limit_amount: "123456789.123456789012",
        window_seconds: 3600,
        enabled: true,
      }),
    );
  });

  it("keeps currency and window immutable on edit and PATCHes only mutable fields", async () => {
    vi.spyOn(api, "listProjectBudgetPolicies").mockResolvedValue({ items: [budget], total: 1, limit: 20, offset: 0 });
    vi.spyOn(api, "budgetStatus").mockResolvedValue([]);
    const update = vi.spyOn(api, "updateProjectBudgetPolicy").mockResolvedValue({ ...budget, name: "renamed" });

    renderPage();

    await waitFor(() => expect(screen.getByText("prod-budget")).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "Edit" }));

    const dialog = await screen.findByRole("dialog");
    const currencyInput = within(dialog).getByLabelText("Currency");
    const windowInput = within(dialog).getByLabelText("Window seconds");
    expect(currencyInput).toBeDisabled();
    expect(windowInput).toBeDisabled();

    fireEvent.change(within(dialog).getByLabelText("Name"), { target: { value: "renamed" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Save changes" }));

    await waitFor(() => expect(update).toHaveBeenCalledWith("bp1", { name: "renamed" }));
  });

  it("renders exact budget status values without recomputing headroom", async () => {
    vi.spyOn(api, "listProjectBudgetPolicies").mockResolvedValue({ items: [budget], total: 1, limit: 20, offset: 0 });
    vi.spyOn(api, "budgetStatus").mockResolvedValue([
      {
        project_id: "proj1",
        budget_policy_id: "bp1",
        currency: "USD",
        limit_amount: "100.000000000000",
        committed_amount: "30.000000000000",
        reserved_amount: "10.000000000000",
        headroom: "60.000000000000",
        window_start: "2026-10-09T00:00:00Z",
        window_end: "2026-10-09T01:00:00Z",
        enabled: true,
      },
    ]);

    renderPage();

    await waitFor(() => expect(screen.getByText("100.000000000000 USD")).toBeInTheDocument());
    expect(screen.getByText("30.000000000000 USD")).toBeInTheDocument();
    expect(screen.getByText("10.000000000000 USD")).toBeInTheDocument();
    expect(screen.getByText("60.000000000000 USD")).toBeInTheDocument();
  });

  it("hides budget mutation controls for project_viewer", async () => {
    vi.spyOn(api, "listProjectBudgetPolicies").mockResolvedValue({ items: [budget], total: 1, limit: 20, offset: 0 });
    vi.spyOn(api, "budgetStatus").mockResolvedValue([]);

    renderPage(["project_viewer"]);

    await waitFor(() => expect(screen.getByText("prod-budget")).toBeInTheDocument());
    expect(screen.queryByRole("button", { name: "New budget" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Edit" })).not.toBeInTheDocument();
  });
});
