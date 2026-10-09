import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import QuotasPage from "./QuotasPage";
import { api } from "../../lib/client";
import { wrap, systemAdminSession } from "../../test/helpers";

function emptyPage() {
  return { items: [], total: 0, limit: 20, offset: 0 };
}

describe("QuotasPage", () => {
  it("creates a quota group", async () => {
    vi.spyOn(api, "listQuotaGroups").mockResolvedValue(emptyPage());
    vi.spyOn(api, "listQuotaLimits").mockResolvedValue(emptyPage());
    vi.spyOn(api, "listProviderAccounts").mockResolvedValue({
      items: [{ id: "pa1", provider_id: "prov1", name: "prod-account", external_account_id: null, secret_ref_id: null, is_active: true }],
      total: 1,
      limit: 200,
      offset: 0,
    });
    const createQuotaGroup = vi.spyOn(api, "createQuotaGroup").mockResolvedValue({
      id: "qg1",
      provider_account_id: "pa1",
      name: "prod-quota",
      description: "main",
    });

    render(<QuotasPage />, { wrapper: wrap({ session: systemAdminSession(), roles: ["system_admin"] }) });

    fireEvent.click(screen.getByRole("button", { name: "New group" }));
    await waitFor(() => expect(screen.getByRole("option", { name: "prod-account" })).toBeInTheDocument());
    fireEvent.change(screen.getByLabelText("Provider account"), { target: { value: "pa1" } });
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "prod-quota" } });
    fireEvent.change(screen.getByLabelText("Description"), { target: { value: "main" } });
    fireEvent.click(screen.getByRole("button", { name: "Create" }));

    await waitFor(() =>
      expect(createQuotaGroup).toHaveBeenCalledWith({
        provider_account_id: "pa1",
        name: "prod-quota",
        description: "main",
      }),
    );
  });

  it("creates a request quota limit", async () => {
    vi.spyOn(api, "listQuotaGroups").mockResolvedValue({
      items: [{ id: "qg1", provider_account_id: "pa1", name: "prod-quota", description: null }],
      total: 1,
      limit: 20,
      offset: 0,
    });
    vi.spyOn(api, "listQuotaLimits").mockResolvedValue(emptyPage());
    const createQuotaLimit = vi.spyOn(api, "createQuotaLimit").mockResolvedValue({
      id: "ql1",
      quota_group_id: "qg1",
      metric: "requests",
      limit_units: 100,
      window_seconds: 3600,
      enabled: true,
      name: null,
    });

    render(<QuotasPage />, { wrapper: wrap({ session: systemAdminSession(), roles: ["system_admin"] }) });

    fireEvent.click(screen.getByRole("button", { name: "New limit" }));
    const quotaGroupSelect = screen.getByLabelText("Quota group");
    await waitFor(() => expect(within(quotaGroupSelect).getByRole("option", { name: "prod-quota" })).toBeInTheDocument());
    fireEvent.change(screen.getByLabelText("Quota group"), { target: { value: "qg1" } });
    fireEvent.change(screen.getByLabelText("Metric"), { target: { value: "requests" } });
    fireEvent.change(screen.getByLabelText("Limit units"), { target: { value: "100" } });
    fireEvent.click(screen.getByRole("button", { name: "Create" }));

    await waitFor(() =>
      expect(createQuotaLimit).toHaveBeenCalledWith({
        quota_group_id: "qg1",
        metric: "requests",
        limit_units: 100,
        window_seconds: 3600,
        enabled: true,
      }),
    );
  });
});
