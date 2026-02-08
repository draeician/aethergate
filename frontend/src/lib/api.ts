import type {
  User,
  APIKeyInfo,
  GeneratedKey,
  LLMEndpoint,
  LLMModel,
  PaginatedLogs,
  Stats,
  HealthStatus,
} from "./types";

const BASE = import.meta.env.VITE_API_URL ?? "";

/** Shared fetch wrapper that injects the admin key header. */
async function apiFetch<T>(
  path: string,
  adminKey: string,
  opts: RequestInit = {}
): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    ...opts,
    headers: {
      "Content-Type": "application/json",
      "x-admin-key": adminKey,
      ...(opts.headers ?? {}),
    },
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(body.detail ?? `HTTP ${res.status}`);
  }
  return res.json() as Promise<T>;
}

// ---- Health (no auth) ----

export async function getHealth(): Promise<HealthStatus> {
  const res = await fetch(`${BASE}/health`);
  return res.json();
}

// ---- Stats ----

export function getStats(key: string) {
  return apiFetch<Stats>("/admin/stats", key);
}

// ---- Users ----

export function listUsers(key: string) {
  return apiFetch<User[]>("/admin/users", key);
}

export function createUser(
  key: string,
  data: { username: string; balance: number }
) {
  return apiFetch<User>("/admin/users", key, {
    method: "POST",
    body: JSON.stringify(data),
  });
}

// ---- Keys ----

export function listKeys(key: string, username?: string) {
  const q = username ? `?username=${encodeURIComponent(username)}` : "";
  return apiFetch<APIKeyInfo[]>(`/admin/keys${q}`, key);
}

export function createKey(
  key: string,
  data: { username: string; name: string; rate_limit?: string }
) {
  return apiFetch<GeneratedKey>("/admin/keys", key, {
    method: "POST",
    body: JSON.stringify(data),
  });
}

// ---- Endpoints (Providers) ----

export function listEndpoints(key: string) {
  return apiFetch<LLMEndpoint[]>("/admin/endpoints", key);
}

export function upsertEndpoint(
  key: string,
  data: {
    name: string;
    base_url: string;
    api_key?: string | null;
    rpm_limit?: number | null;
    day_limit?: number | null;
  }
) {
  return apiFetch<{ id: number; name: string; action: string }>(
    "/admin/endpoints",
    key,
    { method: "POST", body: JSON.stringify(data) }
  );
}

// ---- Models ----

export function listModels(key: string) {
  return apiFetch<LLMModel[]>("/admin/models", key);
}

export function upsertModel(
  key: string,
  data: {
    id: string;
    litellm_name: string;
    price_in: number;
    price_out: number;
    endpoint_id?: number | null;
    rpm_limit?: number | null;
    day_limit?: number | null;
  }
) {
  return apiFetch<{ model: string; litellm_name: string; action: string }>(
    "/admin/models",
    key,
    { method: "POST", body: JSON.stringify(data) }
  );
}

// ---- Logs ----

export function listLogs(
  key: string,
  opts: { limit?: number; offset?: number; username?: string } = {}
) {
  const params = new URLSearchParams();
  if (opts.limit) params.set("limit", String(opts.limit));
  if (opts.offset) params.set("offset", String(opts.offset));
  if (opts.username) params.set("username", opts.username);
  const q = params.toString();
  return apiFetch<PaginatedLogs>(`/admin/logs${q ? `?${q}` : ""}`, key);
}
