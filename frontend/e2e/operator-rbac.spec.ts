import { test, expect, type Browser, type BrowserContext, type Page } from "@playwright/test";
import {
  CSRF_HEADER,
  loginAs,
  readCsrf,
  setIdpSubject,
  adminJson,
  ensureProject,
  ensurePrincipal,
  ensureIdentityLink,
  ensureRoleAssignment,
  createInferenceCredential,
  submitInference,
} from "./fixtures";

/**
 * Live operator-RBAC + CSRF browser E2E.
 *
 * These tests drive real OIDC browser sessions against the deterministic dev IdP
 * and the durable queue. They rely on an already-bootstrapped stack where the
 * ``dev-user`` subject is linked to a system_admin principal (see
 * docs/web-console.md). All other identities are provisioned idempotently
 * through a system_admin session.
 */

interface PageEnvelope<T> {
  items: T[];
  total: number;
}

interface QueueRequest {
  request_id: string;
  project_id: string | null;
  state: string;
}

const SYSTEM_ADMIN_SUBJECT = "dev-user";
const PROJECT_ADMIN_SUBJECT = "e2e-pa-a";
const PROJECT_VIEWER_SUBJECT = "e2e-pv-a";
const PROJECT_ADMIN_B_SUBJECT = "e2e-pa-b";

let projectA: string;
let projectB: string;
let endpointId: string;

test.beforeAll(async ({ browser }) => {
  const session = await openSession(browser, SYSTEM_ADMIN_SUBJECT);
  projectA = await ensureProject(session.context, "e2e-project-a");
  projectB = await ensureProject(session.context, "e2e-project-b");

  const paA = await ensurePrincipal(session.context, projectA, "e2e-pa-a");
  const pvA = await ensurePrincipal(session.context, projectA, "e2e-pv-a");
  const paB = await ensurePrincipal(session.context, projectB, "e2e-pa-b");

  await ensureIdentityLink(session.context, paA, PROJECT_ADMIN_SUBJECT);
  await ensureIdentityLink(session.context, pvA, PROJECT_VIEWER_SUBJECT);
  await ensureIdentityLink(session.context, paB, PROJECT_ADMIN_B_SUBJECT);

  await ensureRoleAssignment(session.context, paA, "project_admin", projectA);
  await ensureRoleAssignment(session.context, pvA, "project_viewer", projectA);
  await ensureRoleAssignment(session.context, paB, "project_admin", projectB);

  // The operator controls target the endpoint that actually serves the live
  // "gpt-4" alias (resolved through the route binding), not an arbitrary first
  // endpoint — the disposable catalog E2E leaves additional endpoints in place.
  endpointId = await resolveRouteEndpointId(session.context, "gpt-4");
  await resume(session.context);
  await drainQueue(session.context);
  await session.context.close();
});

test.afterAll(async () => {
  await setIdpSubject(SYSTEM_ADMIN_SUBJECT);
});

test.describe("system_admin operator controls", () => {
  test("pause and resume an endpoint through the UI", async ({ page }) => {
    await loginAs(page, SYSTEM_ADMIN_SUBJECT);
    page.on("dialog", (dialog) => void dialog.accept());

    // Multiple endpoints may be registered (e.g. the disposable catalog E2E
    // endpoint); pause/resume the first available endpoint rather than
    // assuming a single "Pause" button exists.
    const pause = page.getByRole("button", { name: "Pause" }).first();
    const resume = page.getByRole("button", { name: "Resume" }).first();

    await expect(pause).toBeVisible({ timeout: 15_000 });
    await pause.click();
    await expect(resume).toBeVisible({ timeout: 15_000 });
    await resume.click();
    await expect(pause).toBeVisible({ timeout: 15_000 });
  });
});

test.describe("CSRF protection", () => {
  test("mutation requires a valid X-CSRF-Token", async ({ page, context }) => {
    await loginAs(page, SYSTEM_ADMIN_SUBJECT);
    const csrf = await readCsrf(context);

    const pause = (headers: Record<string, string>) =>
      context.request.fetch(`/admin/v1/queue/endpoints/${endpointId}/pause`, {
        method: "POST",
        headers: { Accept: "application/json", ...headers },
      });

    const missing = await pause({});
    expect(missing.status()).toBe(403);
    expect((await missing.json()).error.code).toBe("invalid_csrf_token");

    const wrong = await pause({ [CSRF_HEADER]: "wrong-token-value" });
    expect(wrong.status()).toBe(403);
    expect((await wrong.json()).error.code).toBe("invalid_csrf_token");

    const ok = await pause({ [CSRF_HEADER]: csrf });
    expect(ok.status()).toBe(200);
    expect((await ok.json()).operational_state).toBe("paused");

    await context.request.fetch(`/admin/v1/queue/endpoints/${endpointId}/resume`, {
      method: "POST",
      headers: { Accept: "application/json", [CSRF_HEADER]: csrf },
    });
  });
});

test.describe("project RBAC in the browser", () => {
  test("project_admin cancels own queued work and is denied deployment controls", async ({
    page,
    context,
    browser,
  }) => {
    // Seed durable queued work in project A: pause the endpoint, submit a real
    // request under A's inference key, and confirm it is queued.
    const admin = await openSession(browser, SYSTEM_ADMIN_SUBJECT);
    await pause(admin.context);
    const keyA = await createInferenceCredential(
      admin.context,
      projectA,
      await principalId(admin.context, projectA, "e2e-pa-a"),
      "e2e-inference-a",
    );
    // Fire-and-forget: the API blocks until terminal, so do not await. The
    // request is enqueued durably before the wait begins.
    void submitInference(keyA, [{ role: "user", content: "hello a" }]).catch(() => {});
    await waitForQueuedRequest(admin.context, projectA);
    await admin.context.close();

    // Authenticate as project_admin(A) and cancel the queued request via the UI.
    await loginAs(page, PROJECT_ADMIN_SUBJECT);
    await page.goto("/queue");
    await expect(page.getByRole("heading", { name: "Queue" })).toBeVisible();

    // The queue sorts oldest-first and paginates at 20; filter to the queued
    // state so the freshly submitted request is visible regardless of how much
    // historical traffic has accumulated.
    await page.getByRole("combobox").selectOption("queued");
    const row = page.locator("tbody tr").filter({ hasText: "queued" }).first();
    await expect(row).toBeVisible();
    page.on("dialog", (dialog) => void dialog.accept());
    await row.getByRole("button", { name: "Cancel" }).click();
    // The cancelled request leaves the "queued" filter; switch to "cancelled".
    await page.getByRole("combobox").selectOption("cancelled");
    await expect(page.locator("tbody").getByText("cancelled").first()).toBeVisible({
      timeout: 10_000,
    });

    // Deployment-only endpoint controls are denied (403).
    const denied = await adminJson(context, "POST", `/admin/v1/queue/endpoints/${endpointId}/pause`);
    expect(denied.status).toBe(403);

    // Restore endpoint for later tests.
    const admin2 = await openSession(browser, SYSTEM_ADMIN_SUBJECT);
    await resume(admin2.context);
    await admin2.context.close();
  });

  test("project_admin cannot see or resolve another project's request", async ({
    page,
    context,
    browser,
  }) => {
    const admin = await openSession(browser, SYSTEM_ADMIN_SUBJECT);
    await pause(admin.context);
    const keyB = await createInferenceCredential(
      admin.context,
      projectB,
      await principalId(admin.context, projectB, "e2e-pa-b"),
      "e2e-inference-b",
    );
    void submitInference(keyB, [{ role: "user", content: "hello b" }]).catch(() => {});
    await waitForQueuedRequest(admin.context, projectB);

    // Find the real cross-project request ID from the system_admin view.
    const all = await adminJson<PageEnvelope<QueueRequest>>(
      admin.context,
      "GET",
      "/admin/v1/queue/requests?limit=200",
    );
    const bRequest = all.body.items.find((r) => r.project_id === projectB);
    expect(bRequest).toBeTruthy();
    await resume(admin.context);
    await admin.context.close();

    await loginAs(page, PROJECT_ADMIN_SUBJECT);

    const listA = await adminJson<PageEnvelope<QueueRequest>>(
      context,
      "GET",
      "/admin/v1/queue/requests?limit=200",
    );
    expect(listA.status).toBe(200);
    for (const r of listA.body.items) {
      expect(r.project_id).toBe(projectA);
    }

    const directB = await adminJson(context, "GET", `/admin/v1/queue/requests/${bRequest!.request_id}`);
    expect(directB.status).toBe(404);
    expect(directB.body).toEqual(
      expect.objectContaining({ error: expect.objectContaining({ code: "not_found" }) }),
    );
  });

  test("project_viewer is read-only", async ({ page, context }) => {
    await loginAs(page, PROJECT_VIEWER_SUBJECT);
    await page.goto("/queue");
    await expect(page.getByRole("heading", { name: "Queue" })).toBeVisible();

    await expect(page.getByRole("button", { name: "Cancel" })).toHaveCount(0);

    const list = await adminJson<PageEnvelope<QueueRequest>>(
      context,
      "GET",
      "/admin/v1/queue/requests?limit=200",
    );
    const own = list.body.items.find((r) => r.state === "queued" || r.state === "reserved");
    if (own) {
      const cancel = await adminJson(
        context,
        "POST",
        `/admin/v1/queue/requests/${own.request_id}/cancel`,
      );
      expect(cancel.status).toBe(403);
    }

    const endpoints = await adminJson(context, "GET", "/admin/v1/queue/endpoints");
    expect(endpoints.status).toBe(403);
  });
});

test.describe("live role revocation", () => {
  test("revoking a role assignment reflects on the next protected request", async ({
    page,
    context,
    browser,
  }) => {
    const admin = await openSession(browser, SYSTEM_ADMIN_SUBJECT);
    const revokeProject = await ensureProject(admin.context, "e2e-revoke-project");
    const victim = await ensurePrincipal(admin.context, revokeProject, "e2e-revoke-admin");
    await ensureIdentityLink(admin.context, victim, "e2e-revoke-admin");
    await ensureRoleAssignment(admin.context, victim, "project_admin", revokeProject);

    await loginAs(page, "e2e-revoke-admin");
    const before = await adminJson<PageEnvelope<QueueRequest>>(
      context,
      "GET",
      "/admin/v1/queue/requests?limit=1",
    );
    expect(before.status).toBe(200);

    const assignment = await findRoleAssignment(admin.context, victim, "project_admin");
    expect(assignment).toBeTruthy();
    const revoke = await adminJson(
      admin.context,
      "POST",
      `/admin/v1/role-assignments/${assignment}/revoke`,
    );
    expect(revoke.status).toBe(200);
    await admin.context.close();

    const after = await adminJson(context, "GET", "/admin/v1/queue/requests?limit=1");
    expect(after.status).toBe(403);
  });

  test("deactivating a principal returns the browser to login", async ({
    page,
    context,
    browser,
  }) => {
    const admin = await openSession(browser, SYSTEM_ADMIN_SUBJECT);
    const project = await ensureProject(admin.context, "e2e-deactivate-project");
    const victim = await ensurePrincipal(admin.context, project, "e2e-deactivate-admin");
    await ensureIdentityLink(admin.context, victim, "e2e-deactivate-admin");
    await ensureRoleAssignment(admin.context, victim, "project_admin", project);

    try {
      await loginAs(page, "e2e-deactivate-admin");
      const before = await adminJson<PageEnvelope<QueueRequest>>(
        context,
        "GET",
        "/admin/v1/queue/requests?limit=1",
      );
      expect(before.status).toBe(200);

      const deactivate = await adminJson(
        admin.context,
        "PATCH",
        `/admin/v1/principals/${victim}`,
        { is_active: false },
      );
      expect(deactivate.status).toBe(200);

      const after = await adminJson(context, "GET", "/admin/v1/queue/requests?limit=1");
      expect(after.status).toBe(401);

      // The centralized auth-expired transition returns the console to login.
      await expect(page.getByRole("link", { name: /sign in with oidc/i })).toBeVisible({
        timeout: 15_000,
      });
    } finally {
      await adminJson(admin.context, "PATCH", `/admin/v1/principals/${victim}`, {
        is_active: true,
      });
      await admin.context.close();
    }
  });
});

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

async function openSession(
  browser: Browser,
  subject: string,
): Promise<{ context: BrowserContext; page: Page }> {
  const context = await browser.newContext();
  const page = await context.newPage();
  await loginAs(page, subject);
  return { context, page };
}

async function principalId(
  context: BrowserContext,
  projectId: string,
  name: string,
): Promise<string> {
  const { body } = await adminJson<PageEnvelope<{ id: string; name: string }>>(
    context,
    "GET",
    `/admin/v1/projects/${projectId}/principals?limit=200`,
  );
  const found = body.items.find((p) => p.name === name);
  if (!found) throw new Error(`principal ${name} not found`);
  return found.id;
}

async function resolveRouteEndpointId(
  context: BrowserContext,
  aliasName: string,
): Promise<string> {
  const aliases = await adminJson<PageEnvelope<{ id: string; name: string }>>(
    context,
    "GET",
    "/admin/v1/model-aliases?limit=200",
  );
  const alias = aliases.body.items.find((a) => a.name === aliasName);
  if (!alias) throw new Error(`model alias ${aliasName} not found`);

  const routes = await adminJson<
    PageEnvelope<{ id: string; model_alias_id: string; endpoint_id: string; is_active: boolean }>
  >(context, "GET", "/admin/v1/route-bindings?limit=200");
  const route =
    routes.body.items.find((r) => r.model_alias_id === alias.id && r.is_active) ??
    routes.body.items.find((r) => r.model_alias_id === alias.id);
  if (!route) throw new Error(`no route binding for alias ${aliasName}`);
  return route.endpoint_id;
}

async function findRoleAssignment(
  context: BrowserContext,
  principalId: string,
  role: string,
): Promise<string | null> {
  const { body } = await adminJson<
    PageEnvelope<{ id: string; principal_id: string; role: string; is_active: boolean }>
  >(context, "GET", "/admin/v1/role-assignments?limit=200");
  const found = body.items.find(
    (a) => a.principal_id === principalId && a.role === role && a.is_active,
  );
  return found ? found.id : null;
}

async function pause(context: BrowserContext): Promise<void> {
  await adminJson(context, "POST", `/admin/v1/queue/endpoints/${endpointId}/pause`);
}

async function resume(context: BrowserContext): Promise<void> {
  await adminJson(context, "POST", `/admin/v1/queue/endpoints/${endpointId}/resume`);
}

async function waitForQueuedRequest(context: BrowserContext, projectId: string): Promise<void> {
  // The queue list sorts oldest-first and paginates, so historical traffic can
  // push the freshly submitted request off a naive first page. Filter to the
  // queued state (only this run's request queues while the endpoint is paused).
  for (let i = 0; i < 20; i += 1) {
    const list = await adminJson<PageEnvelope<QueueRequest>>(
      context,
      "GET",
      `/admin/v1/queue/requests?project_id=${projectId}&state=queued&limit=200`,
    );
    if (list.body.items.some((r) => r.state === "queued")) return;
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  throw new Error(`no queued request appeared for project ${projectId}`);
}

const IN_FLIGHT = new Set(["validated", "queued", "reserved", "dispatched", "streaming"]);

async function drainQueue(context: BrowserContext): Promise<void> {
  for (let i = 0; i < 40; i += 1) {
    const list = await adminJson<PageEnvelope<QueueRequest>>(
      context,
      "GET",
      "/admin/v1/queue/requests?limit=200",
    );
    if (!list.body.items.some((r) => IN_FLIGHT.has(r.state))) return;
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  throw new Error("queue did not drain");
}
