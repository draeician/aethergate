import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import PricingPage from "./PricingPage";
import { api, ApiError, type PricePolicyRead } from "../../lib/client";
import { wrap, systemAdminSession } from "../../test/helpers";

function emptyPage() {
  return { items: [], total: 0, limit: 20, offset: 0 };
}

const routeBinding = {
  id: "rb1",
  model_alias_id: "ma1",
  provider_account_id: "pa1",
  endpoint_id: "ep1",
  is_active: true,
  upstream_model: "qwen3.8",
  quota_group_id: null,
  default_output_tokens: null,
};

const requestPolicy: PricePolicyRead = {
  id: "pp1",
  route_binding_id: "rb1",
  billing_unit: "request",
  currency: "USD",
  unit_scale: 1,
  request_price: "0.000000000123",
  input_price: null,
  output_price: null,
  enabled: true,
  name: null,
};

function renderPage() {
  render(<PricingPage />, { wrapper: wrap({ session: systemAdminSession(), roles: ["system_admin"] }) });
}

async function openModal() {
  fireEvent.click(screen.getByRole("button", { name: "New policy" }));
  const dialog = await screen.findByRole("dialog");
  return within(dialog);
}

describe("PricingPage", () => {
  it("creates a request-priced policy with an exact Decimal string (canary)", async () => {
    vi.spyOn(api, "listPricePolicies").mockResolvedValue(emptyPage());
    vi.spyOn(api, "listRouteBindings").mockResolvedValue({ items: [routeBinding], total: 1, limit: 200, offset: 0 });
    const create = vi.spyOn(api, "createPricePolicy").mockResolvedValue(requestPolicy);

    renderPage();

    const dialog = await openModal();
    await waitFor(() => expect(dialog.getByRole("option", { name: /qwen3\.8/ })).toBeInTheDocument());

    fireEvent.change(dialog.getByLabelText("Route binding"), { target: { value: "rb1" } });
    fireEvent.change(dialog.getByLabelText("Currency"), { target: { value: "USD" } });
    fireEvent.change(dialog.getByLabelText("Request price"), { target: { value: "123456789.123456789012" } });
    fireEvent.click(dialog.getByRole("button", { name: "Create" }));

    await waitFor(() =>
      expect(create).toHaveBeenCalledWith({
        route_binding_id: "rb1",
        billing_unit: "request",
        currency: "USD",
        unit_scale: 1,
        enabled: true,
        request_price: "123456789.123456789012",
      }),
    );
  });

  it("creates a token-priced policy with input/output price strings", async () => {
    vi.spyOn(api, "listPricePolicies").mockResolvedValue(emptyPage());
    vi.spyOn(api, "listRouteBindings").mockResolvedValue({ items: [routeBinding], total: 1, limit: 200, offset: 0 });
    const create = vi.spyOn(api, "createPricePolicy").mockResolvedValue({
      ...requestPolicy,
      billing_unit: "token",
      request_price: null,
      input_price: "0.000000000123",
      output_price: "0.000000000456",
      unit_scale: 1000000,
    });

    renderPage();

    const dialog = await openModal();
    await waitFor(() => expect(dialog.getByRole("option", { name: /qwen3\.8/ })).toBeInTheDocument());

    fireEvent.change(dialog.getByLabelText("Route binding"), { target: { value: "rb1" } });
    fireEvent.change(dialog.getByLabelText("Billing unit"), { target: { value: "token" } });
    fireEvent.change(dialog.getByLabelText("Unit scale"), { target: { value: "1000000" } });
    fireEvent.change(dialog.getByLabelText("Input price"), { target: { value: "0.000000000123" } });
    fireEvent.change(dialog.getByLabelText("Output price"), { target: { value: "0.000000000456" } });
    fireEvent.click(dialog.getByRole("button", { name: "Create" }));

    await waitFor(() =>
      expect(create).toHaveBeenCalledWith({
        route_binding_id: "rb1",
        billing_unit: "token",
        currency: "USD",
        unit_scale: 1000000,
        enabled: true,
        input_price: "0.000000000123",
        output_price: "0.000000000456",
      }),
    );
  });

  it("PATCHes only changed fields", async () => {
    vi.spyOn(api, "listPricePolicies").mockResolvedValue({ items: [requestPolicy], total: 1, limit: 20, offset: 0 });
    vi.spyOn(api, "listRouteBindings").mockResolvedValue({ items: [routeBinding], total: 1, limit: 200, offset: 0 });
    const update = vi.spyOn(api, "updatePricePolicy").mockResolvedValue({ ...requestPolicy, name: "renamed" });

    renderPage();

    await waitFor(() => expect(screen.getByText("rb1")).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "Edit" }));

    const dialog = await screen.findByRole("dialog");
    fireEvent.change(within(dialog).getByLabelText("Name (optional)"), { target: { value: "renamed" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Save changes" }));

    await waitFor(() => expect(update).toHaveBeenCalledWith("pp1", { name: "renamed" }));
  });

  it("renders a 409 conflict as a stable message", async () => {
    vi.spyOn(api, "listPricePolicies").mockResolvedValue(emptyPage());
    vi.spyOn(api, "listRouteBindings").mockResolvedValue({ items: [routeBinding], total: 1, limit: 200, offset: 0 });
    vi.spyOn(api, "createPricePolicy").mockRejectedValue(
      new ApiError(409, "price_policy_conflict", "Only one enabled price policy per route binding.", null),
    );

    renderPage();

    const dialog = await openModal();
    await waitFor(() => expect(dialog.getByRole("option", { name: /qwen3\.8/ })).toBeInTheDocument());

    fireEvent.change(dialog.getByLabelText("Route binding"), { target: { value: "rb1" } });
    fireEvent.change(dialog.getByLabelText("Request price"), { target: { value: "0.0001" } });
    fireEvent.click(dialog.getByRole("button", { name: "Create" }));

    await waitFor(() =>
      expect(dialog.getByRole("alert")).toHaveTextContent("Only one enabled price policy per route binding."),
    );
  });

  it("does not send token prices when billing is request", async () => {
    vi.spyOn(api, "listPricePolicies").mockResolvedValue(emptyPage());
    vi.spyOn(api, "listRouteBindings").mockResolvedValue({ items: [routeBinding], total: 1, limit: 200, offset: 0 });
    const create = vi.spyOn(api, "createPricePolicy").mockResolvedValue(requestPolicy);

    renderPage();

    const dialog = await openModal();
    await waitFor(() => expect(dialog.getByRole("option", { name: /qwen3\.8/ })).toBeInTheDocument());

    fireEvent.change(dialog.getByLabelText("Route binding"), { target: { value: "rb1" } });
    fireEvent.change(dialog.getByLabelText("Request price"), { target: { value: "0.0001" } });
    fireEvent.click(dialog.getByRole("button", { name: "Create" }));

    await waitFor(() => {
      const arg = create.mock.calls[0][0];
      expect(arg).not.toHaveProperty("input_price");
      expect(arg).not.toHaveProperty("output_price");
    });
  });
});
