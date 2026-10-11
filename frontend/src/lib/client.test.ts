import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  api,
  apiRequest,
  ApiError,
  AUTH_EXPIRED_EVENT,
  readCsrfToken,
} from "./client";

function stubResponse(status: number, body: unknown) {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  } as unknown as Response;
}

const fetchMock = vi.fn();

function clearCookies() {
  for (const cookie of document.cookie.split(";")) {
    const name = cookie.split("=")[0].trim();
    if (!name) continue;
    document.cookie = `${name}=; expires=Thu, 01 Jan 1970 00:00:00 GMT; path=/`;
  }
}

beforeEach(() => {
  fetchMock.mockReset();
  vi.stubGlobal("fetch", fetchMock);
  clearCookies();
  window.localStorage.clear();
  window.sessionStorage.clear();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("readCsrfToken", () => {
  it("reads the CSRF token from the ag_csrf cookie", () => {
    document.cookie = "ag_csrf=token123";
    expect(readCsrfToken()).toBe("token123");
  });

  it("returns null when the cookie is absent", () => {
    expect(readCsrfToken()).toBeNull();
  });

  it("does not read from localStorage or sessionStorage", () => {
    window.localStorage.setItem("ag_csrf", "secret");
    window.sessionStorage.setItem("ag_csrf", "secret");
    expect(readCsrfToken()).toBeNull();
  });
});

describe("apiRequest", () => {
  it("does not add a CSRF header for GET requests", async () => {
    fetchMock.mockResolvedValueOnce(stubResponse(200, { ok: true }));
    await apiRequest("/admin/v1/auth/session");
    const [, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(init.headers).toBeDefined();
    expect((init.headers as Record<string, string>)["X-CSRF-Token"]).toBeUndefined();
  });

  it("adds the CSRF header from the cookie for mutations", async () => {
    document.cookie = "ag_csrf=token123";
    fetchMock.mockResolvedValueOnce(stubResponse(200, { request_id: "r", result: "ok", state: "cancelled" }));
    await api.cancelRequest("r1");
    const [, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect((init.headers as Record<string, string>)["X-CSRF-Token"]).toBe("token123");
    expect(init.credentials).toBe("include");
  });

  it("uses same-origin credentials on every request", async () => {
    fetchMock.mockResolvedValueOnce(stubResponse(200, { ok: true }));
    await api.getSession();
    const [, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(init.credentials).toBe("include");
  });

  it("dispatches the auth-expired event and throws ApiError(401) on 401", async () => {
    const onExpired = vi.fn();
    window.addEventListener(AUTH_EXPIRED_EVENT, onExpired);
    fetchMock.mockResolvedValueOnce(
      stubResponse(401, { error: { code: "not_authenticated", message: "expired" } }),
    );
    await expect(api.getSession()).rejects.toMatchObject({ status: 401 });
    expect(onExpired).toHaveBeenCalledTimes(1);
    window.removeEventListener(AUTH_EXPIRED_EVENT, onExpired);
  });

  it("keeps 403 distinct from 401 and does not fire auth-expired", async () => {
    const onExpired = vi.fn();
    window.addEventListener(AUTH_EXPIRED_EVENT, onExpired);
    fetchMock.mockResolvedValueOnce(
      stubResponse(403, { error: { code: "forbidden", message: "no permission" } }),
    );
    await expect(api.cancelRequest("r1")).rejects.toMatchObject({ status: 403, code: "forbidden" });
    expect(onExpired).not.toHaveBeenCalled();
    window.removeEventListener(AUTH_EXPIRED_EVENT, onExpired);
  });

  it("parses the structured AetherGate error envelope", async () => {
    fetchMock.mockResolvedValueOnce(
      stubResponse(409, {
        error: { code: "invalid_transition", message: "request is terminal", request_id: "req-1" },
      }),
    );
    const err = await api.cancelRequest("r1").catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err.code).toBe("invalid_transition");
    expect(err.requestId).toBe("req-1");
  });

  it("never writes auth state to localStorage or sessionStorage", async () => {
    document.cookie = "ag_csrf=token123";
    fetchMock.mockResolvedValueOnce(stubResponse(200, { ok: true }));
    await api.logout();
    expect(window.localStorage.length).toBe(0);
    expect(window.sessionStorage.length).toBe(0);
  });

  it.each([
    ["pause", () => api.pauseEndpoint("e1"), "/admin/v1/queue/endpoints/e1/pause"],
    ["drain", () => api.drainEndpoint("e1"), "/admin/v1/queue/endpoints/e1/drain"],
    ["resume", () => api.resumeEndpoint("e1"), "/admin/v1/queue/endpoints/e1/resume"],
    ["cancel", () => api.cancelRequest("r1"), "/admin/v1/queue/requests/r1/cancel"],
    ["reconcile", () => api.reconcileRequest("r1", "failed"), "/admin/v1/queue/requests/r1/reconcile"],
  ])("posts operator action %s to the correct endpoint with CSRF", async (_name, call, path) => {
    document.cookie = "ag_csrf=token123";
    fetchMock.mockResolvedValueOnce(stubResponse(200, { ok: true }));
    await call();
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe(path);
    expect(init.method).toBe("POST");
    expect((init.headers as Record<string, string>)["X-CSRF-Token"]).toBe("token123");
  });
});

describe("management client methods", () => {
  it("sends PATCH with exactly the provided body for updateProject", async () => {
    document.cookie = "ag_csrf=token123";
    fetchMock.mockResolvedValueOnce(stubResponse(200, { id: "p1", name: "x", is_active: true }));
    await api.updateProject("p1", { name: "x" });
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("/admin/v1/projects/p1");
    expect(init.method).toBe("PATCH");
    expect((init.headers as Record<string, string>)["X-CSRF-Token"]).toBe("token123");
    expect(JSON.parse(init.body as string)).toEqual({ name: "x" });
  });

  it.each([
    [400, "validation_error", "name is required"],
    [404, "not_found", "resource missing"],
  ])("parses structured %s error envelopes", async (status, code, message) => {
    fetchMock.mockResolvedValueOnce(
      stubResponse(status, { error: { code, message, request_id: "r" } }),
    );
    const err = await api.getProject("missing").catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err.status).toBe(status);
    expect(err.code).toBe(code);
  });
});
