import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import EndpointsPage from "./EndpointsPage";
import { api, ApiError, type EndpointRead } from "../../lib/client";
import { wrap, systemAdminSession } from "../../test/helpers";

const endpoint: EndpointRead = {
  id: "e1",
  provider_account_id: "pa1",
  name: "prod-endpoint",
  base_destination: "https://upstream.example",
  max_concurrency: 2,
  is_active: true,
};

function accounts() {
  return {
    items: [{ id: "pa1", provider_id: "prov1", name: "prod-account", external_account_id: null, secret_ref_id: null, is_active: true }],
    total: 1,
    limit: 200,
    offset: 0,
  };
}

describe("EndpointsPage", () => {
  it("distinguishes catalog is_active from runtime operational_state", async () => {
    vi.spyOn(api, "listEndpoints").mockResolvedValue({ items: [endpoint], total: 1, limit: 20, offset: 0 });
    vi.spyOn(api, "listProviderAccounts").mockResolvedValue(accounts());
    vi.spyOn(api, "queueEndpoints").mockResolvedValue({
      items: [
        {
          endpoint_id: "e1",
          name: "prod-endpoint",
          operational_state: "paused",
          is_active: true,
          max_concurrency: 2,
          occupied_slots: 1,
          available_slots: 1,
          draining_complete: false,
          oldest_queued_at: null,
        },
      ],
      total: 1,
      limit: 20,
      offset: 0,
    });

    render(<EndpointsPage />, { wrapper: wrap({ session: systemAdminSession(), roles: ["system_admin"] }) });

    await waitFor(() => expect(screen.getByText("prod-endpoint")).toBeInTheDocument());
    expect(screen.getByText("Active")).toBeInTheDocument(); // catalog is_active
    expect(screen.getByText("paused")).toBeInTheDocument(); // runtime operational_state
  });

  it("renders a backend destination-denied error cleanly", async () => {
    vi.spyOn(api, "listEndpoints").mockResolvedValue({ items: [], total: 0, limit: 20, offset: 0 });
    vi.spyOn(api, "listProviderAccounts").mockResolvedValue(accounts());
    vi.spyOn(api, "queueEndpoints").mockResolvedValue({ items: [], total: 0, limit: 20, offset: 0 });
    vi.spyOn(api, "createEndpoint").mockRejectedValue(
      new ApiError(400, "destination_denied", "Destination is not permitted by egress policy", "req-1"),
    );

    render(<EndpointsPage />, { wrapper: wrap({ session: systemAdminSession(), roles: ["system_admin"] }) });

    fireEvent.click(screen.getByRole("button", { name: "New endpoint" }));
    await waitFor(() => expect(screen.getByRole("option", { name: "prod-account" })).toBeInTheDocument());
    fireEvent.change(screen.getByLabelText("Provider account"), { target: { value: "pa1" } });
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "bad" } });
    fireEvent.change(screen.getByLabelText("Base destination"), { target: { value: "https://evil.example" } });
    fireEvent.click(screen.getByRole("button", { name: "Create" }));

    await waitFor(() => expect(screen.getAllByText("Destination is not permitted by egress policy").length).toBeGreaterThan(0));
  });
});
