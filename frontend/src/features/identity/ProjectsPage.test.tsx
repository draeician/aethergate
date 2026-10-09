import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import ProjectsPage from "./ProjectsPage";
import { api, type ProjectRead } from "../../lib/client";
import { wrap, systemAdminSession } from "../../test/helpers";

function project(id: string, name: string): ProjectRead {
  return { id, name, is_active: true };
}

function page(items: ProjectRead[], total: number, offset: number) {
  return { items, total, limit: 20, offset };
}

describe("ProjectsPage", () => {
  it("renders server-paginated projects and advances the page", async () => {
    const listProjects = vi
      .spyOn(api, "listProjects")
      .mockImplementation(async (query = {}) => {
        const offset = (query.offset as number) ?? 0;
        return page([project("p1", "Alpha")], 25, offset);
      });

    render(<ProjectsPage />, { wrapper: wrap({ session: systemAdminSession(), roles: ["system_admin"] }) });

    await waitFor(() => expect(screen.getByText("Alpha")).toBeInTheDocument());
    expect(screen.getByText(/Page 1 of 2/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /Next/ }));
    await waitFor(() => expect(listProjects).toHaveBeenCalledWith(expect.objectContaining({ offset: 20 })));
  });  it("creates a project via the modal for system_admin", async () => {
    vi.spyOn(api, "listProjects").mockResolvedValue(page([], 0, 0));
    const createProject = vi.spyOn(api, "createProject").mockResolvedValue(project("p2", "Beta"));

    render(<ProjectsPage />, { wrapper: wrap({ session: systemAdminSession(), roles: ["system_admin"] }) });

    fireEvent.click(screen.getByRole("button", { name: "New project" }));
    const input = screen.getByLabelText("Name") as HTMLInputElement;
    fireEvent.change(input, { target: { value: "Beta" } });

    fireEvent.click(screen.getByRole("button", { name: "Create" }));
    await waitFor(() => expect(createProject).toHaveBeenCalledWith({ name: "Beta", is_active: true }));
  });

  it("hides the create control for project_viewer", async () => {
    vi.spyOn(api, "listProjects").mockResolvedValue(page([project("p1", "Alpha")], 1, 0));

    render(<ProjectsPage />, { wrapper: wrap({ roles: ["project_viewer"] }) });

    await waitFor(() => expect(screen.getByText("Alpha")).toBeInTheDocument());
    expect(screen.queryByRole("button", { name: "New project" })).toBeNull();
  });
});
