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
export type ProjectCreate = Schemas["ProjectCreate"];
export type ProjectUpdate = Schemas["ProjectUpdate"];
export type PrincipalRead = Schemas["PrincipalRead"];
export type PrincipalCreate = Schemas["PrincipalCreate"];
export type PrincipalUpdate = Schemas["PrincipalUpdate"];
export type PrincipalKind = Schemas["PrincipalKind"];
export type RoleAssignmentRead = Schemas["RoleAssignmentRead"];
export type RoleAssignmentCreate = Schemas["RoleAssignmentCreate"];
export type ResourceScopeType = Schemas["ResourceScopeType"];
export type ApiCredentialRead = Schemas["ApiCredentialRead"];
export type ApiCredentialCreate = Schemas["ApiCredentialCreate"];
export type ApiCredentialCreateResult = Schemas["ApiCredentialCreateResult"];
export type ApiCredentialRevokeResult = Schemas["ApiCredentialRevokeResult"];
export type CredentialAudience = Schemas["CredentialAudience"];
export type CredentialScope = Schemas["CredentialScope"];
export type ProviderRead = Schemas["ProviderRead"];
export type ProviderCreate = Schemas["ProviderCreate"];
export type ProviderUpdate = Schemas["ProviderUpdate"];
export type SecretRefRead = Schemas["SecretRefRead"];
export type SecretRefCreate = Schemas["SecretRefCreate"];
export type ProviderAccountRead = Schemas["ProviderAccountRead"];
export type ProviderAccountCreate = Schemas["ProviderAccountCreate"];
export type ProviderAccountUpdate = Schemas["ProviderAccountUpdate"];
export type EndpointRead = Schemas["EndpointRead"];
export type EndpointCreate = Schemas["EndpointCreate"];
export type EndpointUpdate = Schemas["EndpointUpdate"];
export type QuotaGroupRead = Schemas["QuotaGroupRead"];
export type QuotaGroupCreate = Schemas["QuotaGroupCreate"];
export type QuotaGroupUpdate = Schemas["QuotaGroupUpdate"];
export type QuotaLimitRead = Schemas["QuotaLimitRead"];
export type QuotaLimitCreate = Schemas["QuotaLimitCreate"];
export type QuotaLimitUpdate = Schemas["QuotaLimitUpdate"];
export type QuotaMetric = Schemas["QuotaMetric"];
export type ModelAliasRead = Schemas["ModelAliasRead"];
export type ModelAliasCreate = Schemas["ModelAliasCreate"];
export type ModelAliasUpdate = Schemas["ModelAliasUpdate"];
export type RouteBindingRead = Schemas["RouteBindingRead"];
export type RouteBindingCreate = Schemas["RouteBindingCreate"];
export type RouteBindingUpdate = Schemas["RouteBindingUpdate"];
export type Capability = Schemas["Capability"];
export type BillingUnit = Schemas["BillingUnit"];
export type BudgetReservationState = Schemas["BudgetReservationState"];
export type LedgerEntryType = Schemas["LedgerEntryType"];
export type PricePolicyRead = Schemas["PricePolicyRead"];
export type PricePolicyCreate = Schemas["PricePolicyCreate"];
export type PricePolicyUpdate = Schemas["PricePolicyUpdate"];
export type PriceSnapshotRead = Schemas["PriceSnapshotRead"];
export type ProjectBudgetPolicyRead = Schemas["ProjectBudgetPolicyRead"];
export type ProjectBudgetPolicyCreate = Schemas["ProjectBudgetPolicyCreate"];
export type ProjectBudgetPolicyUpdate = Schemas["ProjectBudgetPolicyUpdate"];
export type BudgetReservationRead = Schemas["BudgetReservationRead"];
export type UsageRecordRead = Schemas["UsageRecordRead"];
export type LedgerEntryRead = Schemas["LedgerEntryRead"];
export type AuditEventRead = Schemas["AuditEventRead"];

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

/** Convert any thrown value into a safe, user-presentable message. */
export function apiErrorMessage(err: unknown): string {
  if (err instanceof ApiError) return err.message;
  if (err instanceof Error) return err.message;
  return String(err);
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

  listProjects(query: Query = {}): Promise<Page<ProjectRead>> {
    return apiRequest<Page<ProjectRead>>("/admin/v1/projects", { query });
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

  // -- identity: projects -----------------------------------------------------

  createProject(body: ProjectCreate): Promise<ProjectRead> {
    return apiRequest<ProjectRead>("/admin/v1/projects", { method: "POST", body });
  },

  getProject(projectId: string): Promise<ProjectRead> {
    return apiRequest<ProjectRead>(`/admin/v1/projects/${projectId}`);
  },

  updateProject(projectId: string, body: ProjectUpdate): Promise<ProjectRead> {
    return apiRequest<ProjectRead>(`/admin/v1/projects/${projectId}`, {
      method: "PATCH",
      body,
    });
  },

  // -- identity: principals ---------------------------------------------------

  listPrincipals(projectId: string, query: Query = {}): Promise<Page<PrincipalRead>> {
    return apiRequest<Page<PrincipalRead>>(`/admin/v1/projects/${projectId}/principals`, {
      query,
    });
  },

  createPrincipal(projectId: string, body: PrincipalCreate): Promise<PrincipalRead> {
    return apiRequest<PrincipalRead>(`/admin/v1/projects/${projectId}/principals`, {
      method: "POST",
      body,
    });
  },

  getPrincipal(principalId: string): Promise<PrincipalRead> {
    return apiRequest<PrincipalRead>(`/admin/v1/principals/${principalId}`);
  },

  updatePrincipal(principalId: string, body: PrincipalUpdate): Promise<PrincipalRead> {
    return apiRequest<PrincipalRead>(`/admin/v1/principals/${principalId}`, {
      method: "PATCH",
      body,
    });
  },

  // -- identity: role assignments ---------------------------------------------

  listRoleAssignments(query: Query = {}): Promise<Page<RoleAssignmentRead>> {
    return apiRequest<Page<RoleAssignmentRead>>("/admin/v1/role-assignments", { query });
  },

  createRoleAssignment(body: RoleAssignmentCreate): Promise<RoleAssignmentRead> {
    return apiRequest<RoleAssignmentRead>("/admin/v1/role-assignments", {
      method: "POST",
      body,
    });
  },

  revokeRoleAssignment(assignmentId: string): Promise<RoleAssignmentRead> {
    return apiRequest<RoleAssignmentRead>(`/admin/v1/role-assignments/${assignmentId}/revoke`, {
      method: "POST",
    });
  },

  // -- identity: credentials --------------------------------------------------

  listCredentials(projectId: string, query: Query = {}): Promise<Page<ApiCredentialRead>> {
    return apiRequest<Page<ApiCredentialRead>>(`/admin/v1/projects/${projectId}/credentials`, {
      query,
    });
  },

  createCredential(body: ApiCredentialCreate): Promise<ApiCredentialCreateResult> {
    return apiRequest<ApiCredentialCreateResult>("/admin/v1/credentials", {
      method: "POST",
      body,
    });
  },

  rotateCredential(credentialId: string): Promise<ApiCredentialCreateResult> {
    return apiRequest<ApiCredentialCreateResult>(`/admin/v1/credentials/${credentialId}/rotate`, {
      method: "POST",
    });
  },

  revokeCredential(credentialId: string): Promise<ApiCredentialRevokeResult> {
    return apiRequest<ApiCredentialRevokeResult>(`/admin/v1/credentials/${credentialId}/revoke`, {
      method: "POST",
    });
  },

  // -- catalog: providers -----------------------------------------------------

  listProviders(query: Query = {}): Promise<Page<ProviderRead>> {
    return apiRequest<Page<ProviderRead>>("/admin/v1/providers", { query });
  },

  createProvider(body: ProviderCreate): Promise<ProviderRead> {
    return apiRequest<ProviderRead>("/admin/v1/providers", { method: "POST", body });
  },

  getProvider(providerId: string): Promise<ProviderRead> {
    return apiRequest<ProviderRead>(`/admin/v1/providers/${providerId}`);
  },

  updateProvider(providerId: string, body: ProviderUpdate): Promise<ProviderRead> {
    return apiRequest<ProviderRead>(`/admin/v1/providers/${providerId}`, {
      method: "PATCH",
      body,
    });
  },

  // -- catalog: secret refs (metadata only) -----------------------------------

  listSecretRefs(query: Query = {}): Promise<Page<SecretRefRead>> {
    return apiRequest<Page<SecretRefRead>>("/admin/v1/secret-refs", { query });
  },

  createSecretRef(body: SecretRefCreate): Promise<SecretRefRead> {
    return apiRequest<SecretRefRead>("/admin/v1/secret-refs", { method: "POST", body });
  },

  // -- catalog: provider accounts ---------------------------------------------

  listProviderAccounts(query: Query = {}): Promise<Page<ProviderAccountRead>> {
    return apiRequest<Page<ProviderAccountRead>>("/admin/v1/provider-accounts", { query });
  },

  createProviderAccount(body: ProviderAccountCreate): Promise<ProviderAccountRead> {
    return apiRequest<ProviderAccountRead>("/admin/v1/provider-accounts", {
      method: "POST",
      body,
    });
  },

  getProviderAccount(accountId: string): Promise<ProviderAccountRead> {
    return apiRequest<ProviderAccountRead>(`/admin/v1/provider-accounts/${accountId}`);
  },

  updateProviderAccount(accountId: string, body: ProviderAccountUpdate): Promise<ProviderAccountRead> {
    return apiRequest<ProviderAccountRead>(`/admin/v1/provider-accounts/${accountId}`, {
      method: "PATCH",
      body,
    });
  },

  // -- catalog: endpoints -----------------------------------------------------

  listEndpoints(query: Query = {}): Promise<Page<EndpointRead>> {
    return apiRequest<Page<EndpointRead>>("/admin/v1/endpoints", { query });
  },

  createEndpoint(body: EndpointCreate): Promise<EndpointRead> {
    return apiRequest<EndpointRead>("/admin/v1/endpoints", { method: "POST", body });
  },

  getEndpoint(endpointId: string): Promise<EndpointRead> {
    return apiRequest<EndpointRead>(`/admin/v1/endpoints/${endpointId}`);
  },

  updateEndpoint(endpointId: string, body: EndpointUpdate): Promise<EndpointRead> {
    return apiRequest<EndpointRead>(`/admin/v1/endpoints/${endpointId}`, {
      method: "PATCH",
      body,
    });
  },

  // -- catalog: quota groups --------------------------------------------------

  listQuotaGroups(query: Query = {}): Promise<Page<QuotaGroupRead>> {
    return apiRequest<Page<QuotaGroupRead>>("/admin/v1/quota-groups", { query });
  },

  createQuotaGroup(body: QuotaGroupCreate): Promise<QuotaGroupRead> {
    return apiRequest<QuotaGroupRead>("/admin/v1/quota-groups", { method: "POST", body });
  },

  getQuotaGroup(groupId: string): Promise<QuotaGroupRead> {
    return apiRequest<QuotaGroupRead>(`/admin/v1/quota-groups/${groupId}`);
  },

  updateQuotaGroup(groupId: string, body: QuotaGroupUpdate): Promise<QuotaGroupRead> {
    return apiRequest<QuotaGroupRead>(`/admin/v1/quota-groups/${groupId}`, {
      method: "PATCH",
      body,
    });
  },

  // -- catalog: quota limits --------------------------------------------------

  listQuotaLimits(query: Query = {}): Promise<Page<QuotaLimitRead>> {
    return apiRequest<Page<QuotaLimitRead>>("/admin/v1/quota-limits", { query });
  },

  createQuotaLimit(body: QuotaLimitCreate): Promise<QuotaLimitRead> {
    return apiRequest<QuotaLimitRead>("/admin/v1/quota-limits", { method: "POST", body });
  },

  getQuotaLimit(limitId: string): Promise<QuotaLimitRead> {
    return apiRequest<QuotaLimitRead>(`/admin/v1/quota-limits/${limitId}`);
  },

  updateQuotaLimit(limitId: string, body: QuotaLimitUpdate): Promise<QuotaLimitRead> {
    return apiRequest<QuotaLimitRead>(`/admin/v1/quota-limits/${limitId}`, {
      method: "PATCH",
      body,
    });
  },

  // -- catalog: model aliases -------------------------------------------------

  listModelAliases(query: Query = {}): Promise<Page<ModelAliasRead>> {
    return apiRequest<Page<ModelAliasRead>>("/admin/v1/model-aliases", { query });
  },

  createModelAlias(body: ModelAliasCreate): Promise<ModelAliasRead> {
    return apiRequest<ModelAliasRead>("/admin/v1/model-aliases", { method: "POST", body });
  },

  getModelAlias(aliasId: string): Promise<ModelAliasRead> {
    return apiRequest<ModelAliasRead>(`/admin/v1/model-aliases/${aliasId}`);
  },

  updateModelAlias(aliasId: string, body: ModelAliasUpdate): Promise<ModelAliasRead> {
    return apiRequest<ModelAliasRead>(`/admin/v1/model-aliases/${aliasId}`, {
      method: "PATCH",
      body,
    });
  },

  // -- catalog: route bindings ------------------------------------------------

  listRouteBindings(query: Query = {}): Promise<Page<RouteBindingRead>> {
    return apiRequest<Page<RouteBindingRead>>("/admin/v1/route-bindings", { query });
  },

  createRouteBinding(body: RouteBindingCreate): Promise<RouteBindingRead> {
    return apiRequest<RouteBindingRead>("/admin/v1/route-bindings", { method: "POST", body });
  },

  getRouteBinding(bindingId: string): Promise<RouteBindingRead> {
    return apiRequest<RouteBindingRead>(`/admin/v1/route-bindings/${bindingId}`);
  },

  updateRouteBinding(bindingId: string, body: RouteBindingUpdate): Promise<RouteBindingRead> {
    return apiRequest<RouteBindingRead>(`/admin/v1/route-bindings/${bindingId}`, {
      method: "PATCH",
      body,
    });
  },

  // -- accounting: price policies (deployment-scoped) -------------------------

  listPricePolicies(query: Query = {}): Promise<Page<PricePolicyRead>> {
    return apiRequest<Page<PricePolicyRead>>("/admin/v1/price-policies", { query });
  },

  createPricePolicy(body: PricePolicyCreate): Promise<PricePolicyRead> {
    return apiRequest<PricePolicyRead>("/admin/v1/price-policies", { method: "POST", body });
  },

  getPricePolicy(policyId: string): Promise<PricePolicyRead> {
    return apiRequest<PricePolicyRead>(`/admin/v1/price-policies/${policyId}`);
  },

  updatePricePolicy(policyId: string, body: PricePolicyUpdate): Promise<PricePolicyRead> {
    return apiRequest<PricePolicyRead>(`/admin/v1/price-policies/${policyId}`, {
      method: "PATCH",
      body,
    });
  },

  // -- accounting: price snapshots (deployment-scoped, immutable) --------------

  listPriceSnapshots(query: Query = {}): Promise<Page<PriceSnapshotRead>> {
    return apiRequest<Page<PriceSnapshotRead>>("/admin/v1/price-snapshots", { query });
  },

  getPriceSnapshot(snapshotId: string): Promise<PriceSnapshotRead> {
    return apiRequest<PriceSnapshotRead>(`/admin/v1/price-snapshots/${snapshotId}`);
  },

  // -- accounting: project budget policies (project-scoped) --------------------

  listProjectBudgetPolicies(query: Query = {}): Promise<Page<ProjectBudgetPolicyRead>> {
    return apiRequest<Page<ProjectBudgetPolicyRead>>("/admin/v1/project-budget-policies", {
      query,
    });
  },

  createProjectBudgetPolicy(body: ProjectBudgetPolicyCreate): Promise<ProjectBudgetPolicyRead> {
    return apiRequest<ProjectBudgetPolicyRead>("/admin/v1/project-budget-policies", {
      method: "POST",
      body,
    });
  },

  getProjectBudgetPolicy(policyId: string): Promise<ProjectBudgetPolicyRead> {
    return apiRequest<ProjectBudgetPolicyRead>(`/admin/v1/project-budget-policies/${policyId}`);
  },

  updateProjectBudgetPolicy(
    policyId: string,
    body: ProjectBudgetPolicyUpdate,
  ): Promise<ProjectBudgetPolicyRead> {
    return apiRequest<ProjectBudgetPolicyRead>(`/admin/v1/project-budget-policies/${policyId}`, {
      method: "PATCH",
      body,
    });
  },

  // -- accounting: budget reservations (project-scoped, read-only) -------------

  listBudgetReservations(query: Query = {}): Promise<Page<BudgetReservationRead>> {
    return apiRequest<Page<BudgetReservationRead>>("/admin/v1/budget-reservations", { query });
  },

  getBudgetReservation(reservationId: string): Promise<BudgetReservationRead> {
    return apiRequest<BudgetReservationRead>(`/admin/v1/budget-reservations/${reservationId}`);
  },

  // -- accounting: usage records (project-scoped, immutable) -------------------

  listUsageRecords(query: Query = {}): Promise<Page<UsageRecordRead>> {
    return apiRequest<Page<UsageRecordRead>>("/admin/v1/usage-records", { query });
  },

  getUsageRecord(recordId: string): Promise<UsageRecordRead> {
    return apiRequest<UsageRecordRead>(`/admin/v1/usage-records/${recordId}`);
  },

  // -- accounting: ledger entries (project-scoped, immutable) ------------------

  listLedgerEntries(query: Query = {}): Promise<Page<LedgerEntryRead>> {
    return apiRequest<Page<LedgerEntryRead>>("/admin/v1/ledger-entries", { query });
  },

  getLedgerEntry(entryId: string): Promise<LedgerEntryRead> {
    return apiRequest<LedgerEntryRead>(`/admin/v1/ledger-entries/${entryId}`);
  },

  // -- accounting: audit events (special scoping) ------------------------------

  listAuditEvents(query: Query = {}): Promise<Page<AuditEventRead>> {
    return apiRequest<Page<AuditEventRead>>("/admin/v1/audit-events", { query });
  },

  getAuditEvent(eventId: string): Promise<AuditEventRead> {
    return apiRequest<AuditEventRead>(`/admin/v1/audit-events/${eventId}`);
  },
};

export type Api = typeof api;
