import { describe, expect, it, vi, beforeEach } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import CredentialsPage from "./CredentialsPage";
import { api, type ApiCredentialRead } from "../../lib/client";
import { wrap, projectSession } from "../../test/helpers";

function credential(overrides: Partial<ApiCredentialRead> = {}): ApiCredentialRead {
  return {
    audience: "inference",
    created_at: "2026-01-01T00:00:00Z",
    expires_at: null,
    id: "c1",
    is_active: true,
    key_prefix: "agk_abcd",
    name: "inference-key",
    principal_id: "p1",
    project_id: "proj1",
    revoked_at: null,
    scopes: ["inference:invoke"],
    ...overrides,
  };
}

describe("CredentialsPage", () => {
  beforeEach(() => {
    window.localStorage.clear();
    window.sessionStorage.clear();
  });

  it("reveals the raw key once and never persists it to storage", async () => {
    vi.spyOn(api, "listCredentials").mockResolvedValue({ items: [], total: 0, limit: 20, offset: 0 });
    vi.spyOn(api, "listPrincipals").mockResolvedValue({
      items: [{ id: "p1", project_id: "proj1", kind: "user", name: "alice", is_active: true }],
      total: 1,
      limit: 200,
      offset: 0,
    });
    vi.spyOn(api, "createCredential").mockResolvedValue({
      credential: credential(),
      raw_key: "agk_SECRET_RAW",
    });

    render(<CredentialsPage />, { wrapper: wrap({ session: projectSession(), roles: ["project_admin"] }) });

    await waitFor(() => expect(screen.getByText("No credentials.")).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "New credential" }));
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "inference-key" } });
    await waitFor(() => expect(screen.getByText("alice")).toBeInTheDocument());
    fireEvent.change(screen.getByLabelText("Principal"), { target: { value: "p1" } });
    fireEvent.click(screen.getByRole("button", { name: "Create" }));

    await waitFor(() => expect(screen.getByText("agk_SECRET_RAW")).toBeInTheDocument());

    // The raw key must never be written to web storage.
    expect(window.localStorage.length).toBe(0);
    expect(window.sessionStorage.length).toBe(0);

    // Dismissing the reveal destroys the in-memory value.
    fireEvent.click(screen.getByRole("button", { name: "Done" }));
    await waitFor(() => expect(screen.queryByText("agk_SECRET_RAW")).toBeNull());
  });

  it("offers no mutation controls to project_viewer", async () => {
    vi.spyOn(api, "listCredentials").mockResolvedValue({
      items: [credential()],
      total: 1,
      limit: 20,
      offset: 0,
    });

    render(<CredentialsPage />, { wrapper: wrap({ session: projectSession(), roles: ["project_viewer"] }) });

    await waitFor(() => expect(screen.getByText("inference-key")).toBeInTheDocument());
    expect(screen.queryByRole("button", { name: "New credential" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Rotate" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Revoke" })).toBeNull();
  });
});
