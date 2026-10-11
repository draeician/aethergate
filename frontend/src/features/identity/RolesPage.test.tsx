import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import RolesPage from "./RolesPage";
import { api, type RoleAssignmentRead } from "../../lib/client";
import { wrap, systemAdminSession } from "../../test/helpers";

function assignment(id: string, role: string, active = true): RoleAssignmentRead {
  return {
    id,
    principal_id: "p1",
    role: role as RoleAssignmentRead["role"],
    resource_scope_type: "project",
    resource_id: "proj1",
    created_at: "2026-01-01T00:00:00Z",
    created_by: "p-admin",
    revoked_at: active ? null : "2026-01-02T00:00:00Z",
    is_active: active,
  };
}

function page(items: RoleAssignmentRead[], total = items.length) {
  return { items, total, limit: 20, offset: 0 };
}

describe("RolesPage", () => {
  it("grants a project role for a principal", async () => {
    vi.spyOn(api, "listRoleAssignments").mockResolvedValue(page([]));
    vi.spyOn(api, "listProjects").mockResolvedValue({ items: [{ id: "proj1", name: "A", is_active: true }], total: 1, limit: 200, offset: 0 });
    vi.spyOn(api, "listPrincipals").mockResolvedValue({ items: [{ id: "p1", project_id: "proj1", kind: "user", name: "alice", is_active: true }], total: 1, limit: 200, offset: 0 });
    const createRoleAssignment = vi.spyOn(api, "createRoleAssignment").mockResolvedValue(assignment("a1", "project_admin"));

    render(<RolesPage />, { wrapper: wrap({ session: systemAdminSession(), roles: ["system_admin"] }) });

    await waitFor(() => expect(screen.getByText("No role assignments.")).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "Grant role" }));
    fireEvent.change(screen.getByLabelText("Project"), { target: { value: "proj1" } });
    await waitFor(() => expect(screen.getByText("alice")).toBeInTheDocument());
    fireEvent.change(screen.getByLabelText("Principal"), { target: { value: "p1" } });
    fireEvent.change(screen.getByLabelText("Role"), { target: { value: "project_admin" } });
    fireEvent.click(screen.getByRole("button", { name: "Grant" }));

    await waitFor(() =>
      expect(createRoleAssignment).toHaveBeenCalledWith({
        principal_id: "p1",
        role: "project_admin",
        resource_scope_type: "project",
        resource_id: "proj1",
      }),
    );
  });

  it("revokes a role assignment after explicit confirmation", async () => {
    vi.spyOn(api, "listRoleAssignments").mockResolvedValue(page([assignment("a1", "project_viewer")]));
    const revoke = vi.spyOn(api, "revokeRoleAssignment").mockResolvedValue(assignment("a1", "project_viewer", false));

    render(<RolesPage />, { wrapper: wrap({ session: systemAdminSession(), roles: ["system_admin"] }) });

    await waitFor(() => expect(screen.getByText("Project Viewer")).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "Revoke" }));

    const dialog = await screen.findByRole("dialog", { name: "Revoke role?" });
    fireEvent.click(within(dialog).getByRole("button", { name: "Revoke" }));

    await waitFor(() => expect(revoke).toHaveBeenCalledWith("a1"));
  });
});
