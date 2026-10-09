import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import ProjectDetailPage from "./ProjectDetailPage";
import { api, type ProjectRead } from "../../lib/client";
import { AuthContext, type AuthState } from "../../context/auth-context";
import { systemAdminSession } from "../../test/helpers";

const project: ProjectRead = { id: "proj1", name: "Alpha", is_active: true };

function renderPage() {
  const auth: AuthState = {
    status: "authenticated",
    session: systemAdminSession(),
    roles: ["system_admin"],
    refreshSession: async () => {},
    logout: async () => {},
  };
  const wrapper = () => (
    <AuthContext.Provider value={auth}>
      <MemoryRouter initialEntries={["/projects/proj1"]}>
        <Routes>
          <Route path="/projects/:projectId" element={<ProjectDetailPage />} />
        </Routes>
      </MemoryRouter>
    </AuthContext.Provider>
  );
  return render(<div />, { wrapper });
}

describe("ProjectDetailPage", () => {
  it("PATCHes only the changed field (name), not untouched fields", async () => {
    vi.spyOn(api, "getProject").mockResolvedValue(project);
    const updateProject = vi.spyOn(api, "updateProject").mockResolvedValue({ ...project, name: "Beta" });

    renderPage();
    await waitFor(() => expect(screen.getByDisplayValue("Alpha")).toBeInTheDocument());

    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Beta" } });
    fireEvent.click(screen.getByRole("button", { name: "Save changes" }));

    await waitFor(() => expect(updateProject).toHaveBeenCalledWith("proj1", { name: "Beta" }));
  });

  it("PATCHes only is_active when toggled off", async () => {
    vi.spyOn(api, "getProject").mockResolvedValue(project);
    const updateProject = vi.spyOn(api, "updateProject").mockResolvedValue({ ...project, is_active: false });

    renderPage();
    await waitFor(() => expect(screen.getByDisplayValue("Alpha")).toBeInTheDocument());

    fireEvent.click(screen.getByLabelText("Active"));
    fireEvent.click(screen.getByRole("button", { name: "Save changes" }));

    await waitFor(() => expect(updateProject).toHaveBeenCalledWith("proj1", { is_active: false }));
  });
});
