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

export function updateUser(
  key: string,
  userId: string,
  data: {
    balance?: number;
    is_active?: boolean;
    organization?: string | null;
    email?: string | null;
  }
) {
  return apiFetch<User>(`/admin/users/${userId}`, key, {
    method: "PUT",
    body: JSON.stringify(data),
  });
}

export function deleteUser(key: string, userId: string) {
  return apiFetch<{ ok: boolean; deleted: string; keys_removed: number }>(
    `/admin/users/${userId}`,
    key,
    { method: "DELETE" }
  );
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

export function deleteKey(key: string, keyId: string) {
  return apiFetch<{ ok: boolean; deleted: string }>(
    `/admin/keys/${keyId}`,
    key,
    { method: "DELETE" }
  );
}

export function rotateKey(key: string, keyId: string) {
  return apiFetch<GeneratedKey>(
    `/admin/keys/${keyId}/rotate`,
    key,
    { method: "POST" }
  );
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

export function updateEndpoint(
  key: string,
  endpointId: number,
  data: {
    name?: string;
    base_url?: string;
    api_key?: string | null;
    rpm_limit?: number | null;
    day_limit?: number | null;
    is_active?: boolean;
  }
) {
  return apiFetch<{ id: number; name: string; action: string }>(
    `/admin/endpoints/${endpointId}`,
    key,
    { method: "PUT", body: JSON.stringify(data) }
  );
}

export function deleteEndpoint(key: string, endpointId: number) {
  return apiFetch<{ ok: boolean; deleted: string; models_unlinked: number }>(
    `/admin/endpoints/${endpointId}`,
    key,
    { method: "DELETE" }
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

export function updateModel(
  key: string,
  modelId: string,
  data: {
    litellm_name?: string;
    price_in?: number;
    price_out?: number;
    endpoint_id?: number | null;
    rpm_limit?: number | null;
    day_limit?: number | null;
    is_active?: boolean;
  }
) {
  return apiFetch<{ model: string; action: string }>(
    `/admin/models/${encodeURIComponent(modelId)}`,
    key,
    { method: "PUT", body: JSON.stringify(data) }
  );
}

export function deleteModel(key: string, modelId: string) {
  return apiFetch<{ ok: boolean; deleted: string }>(
    `/admin/models/${encodeURIComponent(modelId)}`,
    key,
    { method: "DELETE" }
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
