import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import RoutesPage from "./RoutesPage";
import { api, ApiError } from "../../lib/client";
import { wrap, systemAdminSession } from "../../test/helpers";

function setup() {
  vi.spyOn(api, "listRouteBindings").mockResolvedValue({ items: [], total: 0, limit: 20, offset: 0 });
  vi.spyOn(api, "listModelAliases").mockResolvedValue({
    items: [{ id: "m1", name: "gpt-4", capabilities: ["text"], is_active: true }],
    total: 1,
    limit: 200,
    offset: 0,
  });
  vi.spyOn(api, "listProviderAccounts").mockResolvedValue({
    items: [{ id: "pa1", provider_id: "prov1", name: "prod-account", external_account_id: null, secret_ref_id: null, is_active: true }],
    total: 1,
    limit: 200,
    offset: 0,
  });
  vi.spyOn(api, "listEndpoints").mockResolvedValue({
    items: [{ id: "e1", provider_account_id: "pa1", name: "prod-endpoint", base_destination: "https://upstream.example", max_concurrency: 2, is_active: true }],
    total: 1,
    limit: 200,
    offset: 0,
  });
  vi.spyOn(api, "listQuotaGroups").mockResolvedValue({ items: [], total: 0, limit: 200, offset: 0 });
}

describe("RoutesPage", () => {
  it("creates a route binding with account-consistent relationships", async () => {
    setup();
    const createRouteBinding = vi.spyOn(api, "createRouteBinding").mockResolvedValue({
      id: "r1",
      model_alias_id: "m1",
      endpoint_id: "e1",
      provider_account_id: "pa1",
      upstream_model: null,
      quota_group_id: null,
      default_output_tokens: null,
      is_active: true,
    });

    render(<RoutesPage />, { wrapper: wrap({ session: systemAdminSession(), roles: ["system_admin"] }) });

    fireEvent.click(screen.getByRole("button", { name: "New route" }));
    await waitFor(() => expect(screen.getByRole("option", { name: "gpt-4" })).toBeInTheDocument());
    fireEvent.change(screen.getByLabelText("Model alias"), { target: { value: "m1" } });
    await waitFor(() => expect(screen.getByRole("option", { name: "prod-account" })).toBeInTheDocument());
    fireEvent.change(screen.getByLabelText("Provider account"), { target: { value: "pa1" } });
    await waitFor(() => expect(screen.getByRole("option", { name: "prod-endpoint" })).toBeInTheDocument());
    fireEvent.change(screen.getByLabelText("Endpoint"), { target: { value: "e1" } });
    fireEvent.click(screen.getByRole("button", { name: "Create" }));

    await waitFor(() =>
      expect(createRouteBinding).toHaveBeenCalledWith({
        model_alias_id: "m1",
        provider_account_id: "pa1",
        endpoint_id: "e1",
        is_active: true,
      }),
    );
  });

  it("renders an active-route 409 conflict cleanly", async () => {
    setup();
    vi.spyOn(api, "createRouteBinding").mockRejectedValue(
      new ApiError(409, "route_conflict", "An active route already exists for this model alias", "req-2"),
    );

    render(<RoutesPage />, { wrapper: wrap({ session: systemAdminSession(), roles: ["system_admin"] }) });

    fireEvent.click(screen.getByRole("button", { name: "New route" }));
    await waitFor(() => expect(screen.getByRole("option", { name: "gpt-4" })).toBeInTheDocument());
    fireEvent.change(screen.getByLabelText("Model alias"), { target: { value: "m1" } });
    await waitFor(() => expect(screen.getByRole("option", { name: "prod-account" })).toBeInTheDocument());
    fireEvent.change(screen.getByLabelText("Provider account"), { target: { value: "pa1" } });
    await waitFor(() => expect(screen.getByRole("option", { name: "prod-endpoint" })).toBeInTheDocument());
    fireEvent.change(screen.getByLabelText("Endpoint"), { target: { value: "e1" } });
    fireEvent.click(screen.getByRole("button", { name: "Create" }));

    await waitFor(() => expect(screen.getAllByText("An active route already exists for this model alias").length).toBeGreaterThan(0));
  });
});
