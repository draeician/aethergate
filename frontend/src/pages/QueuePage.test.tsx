import { describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import type { ReactNode } from "react";
import QueuePage from "./QueuePage";
import { AuthContext, type AuthState } from "../context/auth-context";
import { api, type QueueRequestRead } from "../lib/client";

function wrap(auth: Partial<AuthState>) {
  const value = {
    status: "authenticated" as const,
    session: null,
    roles: [],
    refreshSession: async () => {},
    logout: async () => {},
    ...auth,
  };
  return ({ children }: { children: ReactNode }) => (
    <AuthContext.Provider value={value}>
      <MemoryRouter>{children}</MemoryRouter>
    </AuthContext.Provider>
  );
}

function request(overrides: Partial<QueueRequestRead> = {}): QueueRequestRead {
  return {
    api_credential_id: null,
    cancellation_requested: false,
    effective_wait_reason: "endpoint_paused",
    endpoint_id: "e1",
    error_code: null,
    expires_at: null,
    finished_at: null,
    lease_expires_at: null,
    model_alias_id: "model1",
    next_eligible_at: null,
    price_snapshot_id: null,
    principal_id: "p1",
    project_id: "proj1",
    queue_wait_until: null,
    queued_at: "2026-01-01T00:00:00Z",
    quota_group_id: null,
    reconciled_at: null,
    reconciled_by: null,
    reconciled_state: null,
    request_id: "req1",
    started_at: null,
    state: "queued",
    stream: false,
    wait_limit_id: null,
    wait_limit_metric: null,
    wait_reason: null,
    worker_id: null,
    ...overrides,
  };
}

function list(requests: QueueRequestRead[]) {
  return { items: requests, total: requests.length, limit: 20, offset: 0 };
}

describe("QueuePage", () => {
  it("renders effective wait reason and shows cancel for project_admin", async () => {
    vi.spyOn(api, "queueRequests").mockResolvedValue(list([request()]));

    render(<QueuePage />, { wrapper: wrap({ roles: ["project_admin"] }) });

    await waitFor(() => expect(screen.getByText("Endpoint paused")).toBeInTheDocument());
    expect(screen.getByText("Cancel")).toBeInTheDocument();
  });

  it("hides cancel for project_viewer (read-only)", async () => {
    vi.spyOn(api, "queueRequests").mockResolvedValue(list([request()]));

    render(<QueuePage />, { wrapper: wrap({ roles: ["project_viewer"] }) });

    await waitFor(() => expect(screen.getByText("Endpoint paused")).toBeInTheDocument());
    expect(screen.queryByText("Cancel")).toBeNull();
  });

  it("calls the cancel endpoint after confirmation", async () => {
    vi.spyOn(api, "queueRequests").mockResolvedValue(list([request()]));
    const cancel = vi.spyOn(api, "cancelRequest").mockResolvedValue({
      request_id: "req1",
      result: "cancelled_now",
      state: "cancelled",
    });
    vi.spyOn(window, "confirm").mockReturnValue(true);

    render(<QueuePage />, { wrapper: wrap({ roles: ["project_admin"] }) });

    await waitFor(() => expect(screen.getByText("Cancel")).toBeInTheDocument());
    screen.getByText("Cancel").click();
    await waitFor(() => expect(cancel).toHaveBeenCalledWith("req1"));
  });
});
