import { execFile } from "node:child_process";
import { fileURLToPath } from "node:url";
import path from "node:path";
import { expect, type APIResponse, type BrowserContext, type Page } from "@playwright/test";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = path.resolve(HERE, "..", "..");
const PYTHON = process.env.AETHERGATE_PYTHON ?? path.join(REPO_ROOT, ".venv", "bin", "python");
const SDK_DRIVER = path.join(HERE, "sdk_inference.py");

/**
 * Shared live-stack helpers for the web-console browser E2E.
 *
 * The deterministic dev IdP mints a single fixed subject per process. To drive
 * distinct real browser identities (system_admin / project_admin / project_viewer)
 * against one running IdP without rotating its signing key, these helpers use the
 * dev-only ``POST /subject`` control endpoint before each login. The raw session
 * and CSRF cookie values are read only from the browser cookie jar and are never
 * logged or asserted verbatim.
 */

export const WEB_BASE_URL = process.env.WEB_BASE_URL ?? "http://127.0.0.1:8080";
export const ISSUER = process.env.OIDC_ISSUER ?? "http://192.168.22.50:8090";
export const CSRF_COOKIE = "ag_csrf";
export const CSRF_HEADER = "X-CSRF-Token";

const MUTATING = new Set(["POST", "PUT", "PATCH", "DELETE"]);

export async function setIdpSubject(subject: string): Promise<void> {
  const resp = await fetch(`${ISSUER}/subject`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ subject }),
  });
  if (!resp.ok) {
    throw new Error(`setIdpSubject(${subject}) failed: HTTP ${resp.status}`);
  }
}

/** Complete the real OIDC authorization-code + PKCE flow for a subject. */
export async function loginAs(page: Page, subject: string): Promise<void> {
  await setIdpSubject(subject);
  await page.goto("/");
  if (await page.getByRole("link", { name: /sign in with oidc/i }).isVisible()) {
    await page.getByRole("link", { name: /sign in with oidc/i }).click();
  }
  await expect(page.getByRole("heading", { name: "Dashboard" })).toBeVisible({
    timeout: 30_000,
  });
}

/** Read the JS-readable CSRF token from the browser cookie jar. */
export async function readCsrf(context: BrowserContext): Promise<string> {
  const cookies = await context.cookies();
  const cookie = cookies.find((c) => c.name === CSRF_COOKIE);
  if (!cookie) throw new Error("ag_csrf cookie not present");
  return cookie.value;
}

/**
 * Make a same-origin admin request carrying the browser context's cookies and,
 * for mutations, the CSRF token from the ``ag_csrf`` cookie. Returns the raw
 * response so callers can assert on status and body without helpers swallowing
 * the outcome.
 */
export async function adminFetch(
  context: BrowserContext,
  method: string,
  path: string,
  body?: unknown,
  extraHeaders: Record<string, string> = {},
): Promise<APIResponse> {
  const headers: Record<string, string> = { Accept: "application/json", ...extraHeaders };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (MUTATING.has(method)) headers[CSRF_HEADER] = await readCsrf(context);
  return context.request.fetch(path, {
    method,
    headers,
    data: body === undefined ? undefined : JSON.stringify(body),
  });
}

export interface ApiResult<T = unknown> {
  status: number;
  body: T;
}

export async function adminJson<T = unknown>(
  context: BrowserContext,
  method: string,
  path: string,
  body?: unknown,
  extraHeaders: Record<string, string> = {},
): Promise<ApiResult<T>> {
  const resp = await adminFetch(context, method, path, body, extraHeaders);
  const parsed = (await resp.json().catch(() => ({}))) as T;
  return { status: resp.status(), body: parsed };
}

interface Project {
  id: string;
  name: string;
  is_active: boolean;
}

interface Principal {
  id: string;
  project_id: string;
  kind: string;
  name: string;
  is_active: boolean;
}

interface PageEnvelope<T> {
  items: T[];
  total: number;
}

async function findProjectByName(context: BrowserContext, name: string): Promise<Project | null> {
  const { body } = await adminJson<PageEnvelope<Project>>(
    context,
    "GET",
    "/admin/v1/projects?limit=200",
  );
  return body.items.find((p) => p.name === name) ?? null;
}

async function findPrincipalByName(
  context: BrowserContext,
  projectId: string,
  name: string,
): Promise<Principal | null> {
  const { body } = await adminJson<PageEnvelope<Principal>>(
    context,
    "GET",
    `/admin/v1/projects/${projectId}/principals?limit=200`,
  );
  return body.items.find((p) => p.name === name) ?? null;
}

/** Create (or reuse) a project, returning its ID. */
export async function ensureProject(context: BrowserContext, name: string): Promise<string> {
  const existing = await findProjectByName(context, name);
  if (existing) return existing.id;
  const { status, body } = await adminJson<Project>(context, "POST", "/admin/v1/projects", {
    name,
  });
  if (status !== 201) throw new Error(`create project ${name} failed: HTTP ${status}`);
  return body.id;
}

/** Create (or reuse) a USER principal in a project, returning its ID. */
export async function ensurePrincipal(
  context: BrowserContext,
  projectId: string,
  name: string,
): Promise<string> {
  const existing = await findPrincipalByName(context, projectId, name);
  if (existing) return existing.id;
  const { status, body } = await adminJson<Principal>(
    context,
    "POST",
    `/admin/v1/projects/${projectId}/principals`,
    { kind: "user", name },
  );
  if (status !== 201) throw new Error(`create principal ${name} failed: HTTP ${status}`);
  return body.id;
}

/** Link an OIDC ``(issuer, subject)`` identity to a principal (idempotent). */
export async function ensureIdentityLink(
  context: BrowserContext,
  principalId: string,
  subject: string,
): Promise<void> {
  const { status } = await adminJson(context, "POST", "/admin/v1/oidc/identities", {
    principal_id: principalId,
    issuer: ISSUER,
    subject,
  });
  // 201 = created; 400 with a duplicate message is an existing link.
  if (status !== 201) {
    if (status === 400) return;
    throw new Error(`link identity ${subject} failed: HTTP ${status}`);
  }
}

/** Grant a project-scoped role to a principal (idempotent). */
export async function ensureRoleAssignment(
  context: BrowserContext,
  principalId: string,
  role: "project_admin" | "project_viewer",
  projectId: string,
): Promise<void> {
  const { status } = await adminJson(context, "POST", "/admin/v1/role-assignments", {
    principal_id: principalId,
    role,
    resource_scope_type: "project",
    resource_id: projectId,
  });
  // 201 = created; 200 = existing equivalent assignment returned idempotently.
  if (status !== 201 && status !== 200) {
    throw new Error(`grant ${role} failed: HTTP ${status}`);
  }
}

/** Create an inference credential for a project/principal; returns the raw key once. */
export async function createInferenceCredential(
  context: BrowserContext,
  projectId: string,
  principalId: string,
  name: string,
): Promise<string> {
  const { status, body } = await adminJson<{ credential: { id: string }; raw_key: string }>(
    context,
    "POST",
    "/admin/v1/credentials",
    {
      project_id: projectId,
      principal_id: principalId,
      name,
      audience: "inference",
    },
  );
  if (status !== 201) throw new Error(`create credential failed: HTTP ${status}`);
  return body.raw_key;
}

/**
 * Provision the standard multi-role fixture used by the operator RBAC specs.
 * Requires an authenticated system_admin browser context (dev-user). Returns the
 * project IDs for A and B and the inference credential raw key for project A.
 */
export async function provisionOperatorFixture(context: BrowserContext) {
  const projectA = await ensureProject(context, "e2e-project-a");
  const projectB = await ensureProject(context, "e2e-project-b");

  const paA = await ensurePrincipal(context, projectA, "e2e-pa-a");
  const pvA = await ensurePrincipal(context, projectA, "e2e-pv-a");

  await ensureIdentityLink(context, paA, "e2e-pa-a");
  await ensureIdentityLink(context, pvA, "e2e-pv-a");

  await ensureRoleAssignment(context, paA, "project_admin", projectA);
  await ensureRoleAssignment(context, pvA, "project_viewer", projectA);

  return { projectA, projectB, paA, pvA };
}

/** Submit a real chat-completion request with an inference bearer key. */
export async function submitInference(
  key: string,
  messages: Array<{ role: string; content: string }>,
  extra: Record<string, unknown> = {},
): Promise<Response> {
  return fetch(`${WEB_BASE_URL.replace(/\/$/, "")}/v1/chat/completions`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${key}`,
    },
    body: JSON.stringify({
      model: "gpt-4",
      messages,
      ...extra,
    }),
  });
}

/**
 * Navigate a list page to its last page by clicking "Next" until disabled. The
 * list pages sort oldest-first (newest resource last), so this is how the specs
 * locate a resource just created through the UI without depending on page size.
 */
export async function goToLastPage(page: Page): Promise<void> {
  const next = page.getByRole("button", { name: "Next" });
  await expect(next).toBeVisible({ timeout: 15_000 });
  await page.waitForTimeout(400);
  for (let i = 0; i < 100; i += 1) {
    if (await next.isDisabled()) break;
    await next.click();
    await page.waitForTimeout(250);
  }
}

export interface SdkResult {
  status: "ok" | "ok_stream" | "fail" | "missing_env" | "error";
  exitCode: number;
  httpStatus: number | null;
  errorCode: string | null;
  raw: string;
}

/**
 * Invoke the official OpenAI Python SDK (repo venv) against the AetherGate
 * OpenAI-compatible surface. The inference key and model are passed through the
 * child environment (never argv) and the output is redacted — the raw key is
 * never printed by the driver or by this helper.
 */
export async function runOfficialSdk(opts: {
  key: string;
  model: string;
  stream?: boolean;
  baseUrl?: string;
}): Promise<SdkResult> {
  const baseUrl = opts.baseUrl ?? `${WEB_BASE_URL.replace(/\/$/, "")}/v1`;
  const env = {
    ...process.env,
    AETHERGATE_BASE_URL: baseUrl,
    AETHERGATE_API_KEY: opts.key,
    AETHERGATE_MODEL: opts.model,
    AETHERGATE_STREAM: opts.stream ? "1" : "0",
  };
  const stdout = await new Promise<string>((resolve) => {
    execFile(PYTHON, [SDK_DRIVER], { env, timeout: 120_000 }, (_error, out) => {
      // A non-zero exit (e.g. model_unavailable) is still a valid result; the
      // driver prints a FAIL line. Only absence of stdout indicates a spawn or
      // timeout failure, which maps to `status: "error"` below.
      resolve(out ?? "");
    });
  });

  const line = stdout.trim().split("\n").pop() ?? "";
  if (line.startsWith("OK_STREAM")) return { status: "ok_stream", exitCode: 0, httpStatus: null, errorCode: null, raw: stdout };
  if (line.startsWith("OK")) return { status: "ok", exitCode: 0, httpStatus: null, errorCode: null, raw: stdout };
  if (line.startsWith("MISSING_ENV")) return { status: "missing_env", exitCode: 2, httpStatus: null, errorCode: null, raw: stdout };
  if (line.startsWith("FAIL")) {
    const statusMatch = /status=(\S+)/.exec(line);
    const codeMatch = /code=(\S+)/.exec(line);
    const httpStatus = statusMatch && statusMatch[1] !== "None" ? Number(statusMatch[1]) : null;
    const errorCode = codeMatch && codeMatch[1] !== "None" ? codeMatch[1] : null;
    return { status: "fail", exitCode: 1, httpStatus, errorCode, raw: stdout };
  }
  return { status: "error", exitCode: 1, httpStatus: null, errorCode: null, raw: stdout };
}
