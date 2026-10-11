import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import ProviderAccountsPage from "./ProviderAccountsPage";
import { api, type ProviderAccountRead } from "../../lib/client";
import { wrap, systemAdminSession } from "../../test/helpers";

const account: ProviderAccountRead = {
  id: "pa1",
  provider_id: "prov1",
  name: "prod-account",
  external_account_id: "ext-1",
  secret_ref_id: "sr1",
  is_active: true,
};

describe("ProviderAccountsPage", () => {
  it("offers secret-reference metadata in the selector and creates a new ref", async () => {
    vi.spyOn(api, "listProviderAccounts").mockResolvedValue({ items: [], total: 0, limit: 20, offset: 0 });
    vi.spyOn(api, "listProviders").mockResolvedValue({ items: [{ id: "prov1", kind: "openai", name: "OpenAI", capabilities: ["text"], is_active: true }], total: 1, limit: 200, offset: 0 });
    vi.spyOn(api, "listSecretRefs").mockResolvedValue({ items: [{ id: "sr1", name: "prod-secret", created_at: "2026-01-01T00:00:00Z" }], total: 1, limit: 200, offset: 0 });
    const createSecretRef = vi.spyOn(api, "createSecretRef").mockResolvedValue({ id: "sr2", name: "new-secret", created_at: "2026-01-01T00:00:00Z" });

    render(<ProviderAccountsPage />, { wrapper: wrap({ session: systemAdminSession(), roles: ["system_admin"] }) });

    fireEvent.click(screen.getByRole("button", { name: "New account" }));
    await waitFor(() => expect(screen.getByText("prod-secret")).toBeInTheDocument());

    fireEvent.change(screen.getByLabelText("New secret ref name"), { target: { value: "new-secret" } });
    fireEvent.click(screen.getByRole("button", { name: "Add ref" }));
    await waitFor(() => expect(createSecretRef).toHaveBeenCalledWith({ name: "new-secret" }));
  });

  it("PATCHes secret_ref_id as explicit null when cleared", async () => {
    vi.spyOn(api, "listProviderAccounts").mockResolvedValue({ items: [account], total: 1, limit: 20, offset: 0 });
    vi.spyOn(api, "listProviders").mockResolvedValue({ items: [{ id: "prov1", kind: "openai", name: "OpenAI", capabilities: ["text"], is_active: true }], total: 1, limit: 200, offset: 0 });
    vi.spyOn(api, "listSecretRefs").mockResolvedValue({ items: [{ id: "sr1", name: "prod-secret", created_at: "2026-01-01T00:00:00Z" }], total: 1, limit: 200, offset: 0 });
    const update = vi.spyOn(api, "updateProviderAccount").mockResolvedValue({ ...account, secret_ref_id: null });

    render(<ProviderAccountsPage />, { wrapper: wrap({ session: systemAdminSession(), roles: ["system_admin"] }) });

    await waitFor(() => expect(screen.getByText("prod-account")).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "Edit" }));
    fireEvent.change(screen.getByLabelText("Secret reference"), { target: { value: "" } });
    fireEvent.click(screen.getByRole("button", { name: "Save changes" }));

    await waitFor(() => expect(update).toHaveBeenCalledWith("pa1", { secret_ref_id: null }));
  });
});
