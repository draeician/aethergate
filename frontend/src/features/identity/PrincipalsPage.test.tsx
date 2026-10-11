import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import PrincipalsPage from "./PrincipalsPage";
import { api, type PrincipalRead } from "../../lib/client";
import { wrap, projectSession } from "../../test/helpers";

function principal(id: string, name: string, kind: "user" | "service_account" = "user"): PrincipalRead {
  return { id, name, project_id: "proj1", kind, is_active: true };
}

function page(items: PrincipalRead[], total: number) {
  return { items, total, limit: 20, offset: 0 };
}

describe("PrincipalsPage", () => {
  it("lists principals for the session project (project_admin)", async () => {
    const listPrincipals = vi
      .spyOn(api, "listPrincipals")
      .mockResolvedValue(page([principal("p1", "alice")], 1));

    render(<PrincipalsPage />, { wrapper: wrap({ session: projectSession(), roles: ["project_admin"] }) });

    await waitFor(() => expect(screen.getByText("alice")).toBeInTheDocument());
    expect(listPrincipals).toHaveBeenCalledWith("proj1", expect.objectContaining({ limit: 20 }));
    expect(screen.getByText("User")).toBeInTheDocument();
  });

  it("creates a service principal in the session project", async () => {
    vi.spyOn(api, "listPrincipals").mockResolvedValue(page([], 0));
    const createPrincipal = vi.spyOn(api, "createPrincipal").mockResolvedValue(principal("p2", "svc-1", "service_account"));

    render(<PrincipalsPage />, { wrapper: wrap({ session: projectSession(), roles: ["project_admin"] }) });

    fireEvent.click(screen.getByRole("button", { name: "New principal" }));
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "svc-1" } });
    fireEvent.change(screen.getByLabelText("Kind"), { target: { value: "service_account" } });
    fireEvent.click(screen.getByRole("button", { name: "Create" }));

    await waitFor(() =>
      expect(createPrincipal).toHaveBeenCalledWith("proj1", { kind: "service_account", name: "svc-1", is_active: true }),
    );
  });

  it("does not offer creation to project_viewer", async () => {
    vi.spyOn(api, "listPrincipals").mockResolvedValue(page([principal("p1", "alice")], 1));

    render(<PrincipalsPage />, { wrapper: wrap({ session: projectSession(), roles: ["project_viewer"] }) });

    await waitFor(() => expect(screen.getByText("alice")).toBeInTheDocument());
    expect(screen.queryByRole("button", { name: "New principal" })).toBeNull();
  });
});
