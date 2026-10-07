import type { components } from "../generated/schema";

/**
 * Central AetherGate admin request layer.
 *
 * This is the single place the web console talks to the backend. It owns the
 * browser-session credentials (same-origin `credentials: "include"`), CSRF
 * header injection for mutations, structured error parsing, and centralized
 * 401 -> auth-expired transition. No other module calls `fetch` directly.
 *
 * The raw API shapes come from the generated OpenAPI types
 * (`../generated/schema.d.ts`); this file is the thin, application-specific
 * wrapper that owns credentials/CSRF/error handling (never hand-maintained
 * endpoint/path definitions as the source of truth).
 */

export type Schemas = components["schemas"];

export type Role = Schemas["Role"];
export type AdminAuthenticationKind = Schemas["AdminAuthenticationKind"];
export type SessionRead = Schemas["SessionRead"];
export type WhoamiRead = Schemas["WhoamiRead"];
export type QueueSummaryRead = Schemas["QueueSummaryRead"];
export type QueueRequestRead = Schemas["QueueRequestRead"];
export type EndpointRuntimeRead = Schemas["EndpointRuntimeRead"];
export type ProjectRead = Schemas["ProjectRead"];
export type QuotaStatusRead = Schemas["QuotaStatusRead"];
export type BudgetStatusRead = Schemas["BudgetStatusRead"];
export type CancelResult = Schemas["CancelResult"];
export type ReconcileResult = Schemas["ReconcileResult"];
export type LogoutResult = Schemas["LogoutResult"];

export interface Page<T> {
  items: T[];
  limit: number;
  offset: number;
  total: number;
}

/** Structured AetherGate admin error envelope. */
export interface AdminErrorEnvelope {
  error?: {
    code: string;
    message: string;
    request_id?: string | null;
  };
}

/** The fixed, JS-readable CSRF cookie name (never stored in web storage). */
export const CSRF_COOKIE_NAME = "ag_csrf";

/** Event fired on `window` when any API call receives a 401 (session expired). */
export const AUTH_EXPIRED_EVENT = "aethergate:auth-expired";

export class ApiError extends Error {
  status: number;
  code: string;
  requestId: string | null;

  constructor(status: number, code: string, message: string, requestId: string | null) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.requestId = requestId;
  }
}

/** Read the CSRF token from its dedicated cookie (never from web storage). */
export function readCsrfToken(): string | null {
  if (typeof document === "undefined") return null;
  const match = document.cookie.match(new RegExp(`(?:^|;\\s*)${CSRF_COOKIE_NAME}=([^;]*)`));
  if (!match) return null;
  try {
    return decodeURIComponent(match[1]);
  } catch {
    return null;
  }
}

/** Dispatch the centralized auth-expired transition to the AuthContext. */
function dispatchAuthExpired(): void {
  if (typeof window !== "undefined") {
    window.dispatchEvent(new Event(AUTH_EXPIRED_EVENT));
  }
}

type QueryValue = string | number | boolean | undefined | null;
export type Query = Record<string, QueryValue>;

function buildUrl(path: string, query?: Query): string {
  if (!query) return path;
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) {
    if (value !== undefined && value !== null && value !== "") {
      params.set(key, String(value));
    }
  }
  const qs = params.toString();
  return qs ? `${path}?${qs}` : path;
}

export interface RequestOptions {
  method?: "GET" | "POST" | "PUT" | "PATCH" | "DELETE" | "HEAD";
  body?: unknown;
  query?: Query;
  headers?: Record<string, string>;
}

const MUTATING_METHODS = new Set(["POST", "PUT", "PATCH", "DELETE"]);

/**
 * Perform a same-origin admin request with browser credentials + CSRF handling.
 *
 * - `credentials: "include"` carries the HttpOnly `ag_session` cookie.
 * - Mutations attach `X-CSRF-Token` read from the `ag_csrf` cookie.
 * - A 401 always fires the centralized auth-expired transition and throws a
 *   distinguishable `ApiError` (401 vs 403 remain distinct).
 * - No automatic retry of any mutation is performed.
 * - Sensitive headers/cookies/tokens are never logged.
 */
export async function apiRequest<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const method = options.method ?? "GET";
  const headers: Record<string, string> = { Accept: "application/json", ...options.headers };
  if (options.body !== undefined) {
    headers["Content-Type"] = "application/json";
  }
  if (MUTATING_METHODS.has(method)) {
    const csrf = readCsrfToken();
    if (csrf) headers["X-CSRF-Token"] = csrf;
  }

  let response: Response;
  try {
    response = await fetch(buildUrl(path, options.query), {
      method,
      credentials: "include",
      headers,
      body: options.body === undefined ? undefined : JSON.stringify(options.body),
    });
  } catch {
    throw new ApiError(0, "network_error", "The gateway is unreachable.", null);
  }

  if (response.status === 401) {
    dispatchAuthExpired();
    const parsed = await readJson(response);
    throw new ApiError(
      401,
      parsed?.error?.code ?? "not_authenticated",
      parsed?.error?.message ?? "Your session has expired.",
      parsed?.error?.request_id ?? null,
    );
  }

  if (!response.ok) {
    const parsed = await readJson(response);
    throw new ApiError(
      response.status,
      parsed?.error?.code ?? "http_error",
      parsed?.error?.message ?? `Request failed (HTTP ${response.status}).`,
      parsed?.error?.request_id ?? null,
    );
  }

  if (response.status === 204) {
    return undefined as T;
  }
  return (await response.json()) as T;
}

async function readJson(response: Response): Promise<AdminErrorEnvelope | null> {
  try {
    return (await response.json()) as AdminErrorEnvelope;
  } catch {
    return null;
  }
}

// ---------------------------------------------------------------------------
// Typed admin operations (shapes from the generated OpenAPI schema)
// ---------------------------------------------------------------------------

export const api = {
  /** Resolve the current browser session (401 when unauthenticated). */
  getSession(): Promise<SessionRead> {
    return apiRequest<SessionRead>("/admin/v1/auth/session");
  },

  /** Revoke the server session and clear the browser cookies. */
  logout(): Promise<LogoutResult> {
    return apiRequest<LogoutResult>("/admin/v1/auth/logout", { method: "POST" });
  },

  queueSummary(): Promise<QueueSummaryRead> {
    return apiRequest<QueueSummaryRead>("/admin/v1/queue/summary");
  },

  queueEndpoints(): Promise<Page<EndpointRuntimeRead>> {
    return apiRequest<Page<EndpointRuntimeRead>>("/admin/v1/queue/endpoints");
  },

  listProjects(): Promise<Page<ProjectRead>> {
    return apiRequest<Page<ProjectRead>>("/admin/v1/projects");
  },

  queueQuotaStatus(): Promise<Page<QuotaStatusRead>> {
    return apiRequest<Page<QuotaStatusRead>>("/admin/v1/queue/quota-status");
  },

  queueRequests(query: Query = {}): Promise<Page<QueueRequestRead>> {
    return apiRequest<Page<QueueRequestRead>>("/admin/v1/queue/requests", { query });
  },

  queueRequest(requestId: string): Promise<QueueRequestRead> {
    return apiRequest<QueueRequestRead>(`/admin/v1/queue/requests/${requestId}`);
  },

  outcomeUnknown(query: Query = {}): Promise<Page<QueueRequestRead>> {
    return apiRequest<Page<QueueRequestRead>>("/admin/v1/queue/outcome-unknown", { query });
  },

  cancelRequest(requestId: string): Promise<CancelResult> {
    return apiRequest<CancelResult>(`/admin/v1/queue/requests/${requestId}/cancel`, {
      method: "POST",
    });
  },

  reconcileRequest(requestId: string, disposition: "failed" | "cancelled"): Promise<ReconcileResult> {
    return apiRequest<ReconcileResult>(`/admin/v1/queue/requests/${requestId}/reconcile`, {
      method: "POST",
      body: { disposition },
    });
  },

  pauseEndpoint(endpointId: string): Promise<EndpointRuntimeRead> {
    return apiRequest<EndpointRuntimeRead>(`/admin/v1/queue/endpoints/${endpointId}/pause`, {
      method: "POST",
    });
  },

  drainEndpoint(endpointId: string): Promise<EndpointRuntimeRead> {
    return apiRequest<EndpointRuntimeRead>(`/admin/v1/queue/endpoints/${endpointId}/drain`, {
      method: "POST",
    });
  },

  resumeEndpoint(endpointId: string): Promise<EndpointRuntimeRead> {
    return apiRequest<EndpointRuntimeRead>(`/admin/v1/queue/endpoints/${endpointId}/resume`, {
      method: "POST",
    });
  },

  budgetStatus(projectId: string): Promise<BudgetStatusRead[]> {
    return apiRequest<BudgetStatusRead[]>(`/admin/v1/projects/${projectId}/budget-status`);
  },
};

export type Api = typeof api;
