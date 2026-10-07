import { describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { AuthProvider } from "./AuthContext";
import { useAuth } from "./auth-context";
import { api, AUTH_EXPIRED_EVENT, type SessionRead } from "../lib/client";

function Probe() {
  const { status, session, roles, logout } = useAuth();
  return (
    <div>
      <span data-testid="status">{status}</span>
      <span data-testid="principal">{session?.principal_id ?? ""}</span>
      <span data-testid="roles">{roles.join(",")}</span>
      <button type="button" onClick={() => void logout()}>
        logout
      </button>
    </div>
  );
}

const session: SessionRead = {
  principal_id: "p1",
  project_id: "proj1",
  authentication_kind: "browser_session",
  browser_session_id: "bs1",
  roles: ["project_admin"],
  issuer: null,
  subject: null,
};

describe("AuthProvider", () => {
  it("marks unauthenticated when the session boot returns 401", async () => {
    vi.spyOn(api, "getSession").mockRejectedValue(Object.assign(new Error("x"), { status: 401 }));
    render(
      <AuthProvider>
        <Probe />
      </AuthProvider>,
    );
    await waitFor(() => expect(screen.getByTestId("status")).toHaveTextContent("unauthenticated"));
  });

  it("marks authenticated and exposes roles on successful boot", async () => {
    vi.spyOn(api, "getSession").mockResolvedValue(session);
    render(
      <AuthProvider>
        <Probe />
      </AuthProvider>,
    );
    await waitFor(() => expect(screen.getByTestId("status")).toHaveTextContent("authenticated"));
    expect(screen.getByTestId("principal")).toHaveTextContent("p1");
    expect(screen.getByTestId("roles")).toHaveTextContent("project_admin");
  });

  it("centrally expires auth state when the auth-expired event fires", async () => {
    vi.spyOn(api, "getSession").mockResolvedValue(session);
    render(
      <AuthProvider>
        <Probe />
      </AuthProvider>,
    );
    await waitFor(() => expect(screen.getByTestId("status")).toHaveTextContent("authenticated"));
    window.dispatchEvent(new Event(AUTH_EXPIRED_EVENT));
    await waitFor(() => expect(screen.getByTestId("status")).toHaveTextContent("unauthenticated"));
  });

  it("calls server logout and clears state", async () => {
    vi.spyOn(api, "getSession").mockResolvedValue(session);
    const logout = vi.spyOn(api, "logout").mockResolvedValue({ revoked: true });
    render(
      <AuthProvider>
        <Probe />
      </AuthProvider>,
    );
    await waitFor(() => expect(screen.getByTestId("status")).toHaveTextContent("authenticated"));
    screen.getByRole("button", { name: "logout" }).click();
    await waitFor(() => expect(screen.getByTestId("status")).toHaveTextContent("unauthenticated"));
    expect(logout).toHaveBeenCalledTimes(1);
  });
});
