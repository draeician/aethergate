import { test, expect, type Browser, type BrowserContext, type Page } from "@playwright/test";
import {
  loginAs,
  setIdpSubject,
  adminJson,
  ensureProject,
  ensurePrincipal,
  ensureIdentityLink,
  ensureRoleAssignment,
  submitInference,
} from "./fixtures";

/**
 * Live management-UI RBAC + credential one-time-reveal browser E2E.
 *
 * Drives real OIDC browser sessions (system_admin / project_admin / project_viewer)
 * against the deterministic dev IdP, plus a raw-key leak canary and a full
 * inference-credential lifecycle. The dev-user subject must already be linked to
 * a system_admin principal (see docs/web-console.md); all other identities are
 * provisioned idempotently here.
 */

interface PageEnvelope<T> {
  items: T[];
  total: number;
}

interface CredentialRead {
  id: string;
  project_id: string;
  principal_id: string;
  name: string;
  key_prefix: string | null;
  audience: string;
  is_active: boolean;
}

interface PrincipalRead {
  id: string;
  project_id: string;
  kind: string;
  name: string;
  is_active: boolean;
}

const SYSTEM_ADMIN_SUBJECT = "dev-user";
const PROJECT_ADMIN_SUBJECT = "e2e-pa-a";
const PROJECT_VIEWER_SUBJECT = "e2e-pv-a";

let projectA: string;
let projectB: string;
let paA: string;
let pvA: string;

test.beforeAll(async ({ browser }) => {
  const session = await openSession(browser, SYSTEM_ADMIN_SUBJECT);
  projectA = await ensureProject(session.context, "e2e-project-a");
  projectB = await ensureProject(session.context, "e2e-project-b");

  paA = await ensurePrincipal(session.context, projectA, "e2e-pa-a");
  pvA = await ensurePrincipal(session.context, projectA, "e2e-pv-a");

  await ensureIdentityLink(session.context, paA, PROJECT_ADMIN_SUBJECT);
  await ensureIdentityLink(session.context, pvA, PROJECT_VIEWER_SUBJECT);

  await ensureRoleAssignment(session.context, paA, "project_admin", projectA);
  await ensureRoleAssignment(session.context, pvA, "project_viewer", projectA);

  await session.context.close();
});

test.afterAll(async () => {
  await setIdpSubject(SYSTEM_ADMIN_SUBJECT);
});

test.describe("system_admin management", () => {
  test("creates a project and a provider through the UI", async ({ page }) => {
    await loginAs(page, SYSTEM_ADMIN_SUBJECT);

    // Catalog nav is present for system_admin.
    await expect(page.getByRole("link", { name: "Providers" })).toBeVisible();

    // Create a project through the UI.
    await page.getByRole("link", { name: "Projects" }).click();
    await expect(page.getByRole("heading", { name: "Projects" })).toBeVisible();

    const projectName = `e2e-mgmt-project-${Date.now()}`;
    await page.getByRole("button", { name: "New project" }).click();
    await page.getByLabel("Name").fill(projectName);
    await page.getByRole("button", { name: "Create" }).click();
    await expect(page.getByText(projectName)).toBeVisible({ timeout: 15_000 });

    // Create a provider through the UI.
    await page.getByRole("link", { name: "Providers" }).click();
    await expect(page.getByRole("heading", { name: "Providers" })).toBeVisible();

    const providerName = `e2e-mgmt-provider-${Date.now()}`;
    await page.getByRole("button", { name: "New provider" }).click();
    await page.getByLabel("Name").fill(providerName);
    await page.getByLabel("Kind").fill("custom");
    await page.getByRole("button", { name: "Create" }).click();
    await expect(page.getByText(providerName)).toBeVisible({ timeout: 15_000 });
  });

  test("reveals a raw credential key only once and never persists it", async ({
    page,
    context,
  }) => {
    await loginAs(page, SYSTEM_ADMIN_SUBJECT);

    const consoleMessages: string[] = [];
    page.on("console", (msg) => consoleMessages.push(msg.text()));

    await page.goto(`/credentials?project=${projectA}`);
    await expect(page.getByRole("heading", { name: "Credentials" })).toBeVisible();

    const credentialName = `e2e-reveal-cred-${Date.now()}`;
    await page.getByRole("button", { name: "New credential" }).click();
    await page.getByLabel("Name").fill(credentialName);
    await page.getByLabel("Principal").selectOption(paA);
    await page.getByRole("button", { name: "Create" }).click();

    // The raw key appears once in the reveal modal.
    const revealCode = page.locator("code");
    await expect(revealCode).toBeVisible({ timeout: 15_000 });
    const rawKey = (await revealCode.textContent())?.trim() ?? "";
    expect(rawKey.length).toBeGreaterThan(16);

    // Not in the URL or browser storage while revealed.
    expect(page.url()).not.toContain(rawKey);
    const storage = await page.evaluate(() => ({
      local: Object.values(window.localStorage),
      session: Object.values(window.sessionStorage),
    }));
    expect(storage.local.join("\n")).not.toContain(rawKey);
    expect(storage.session.join("\n")).not.toContain(rawKey);

    // Dismissing destroys the in-memory value.
    await page.getByRole("button", { name: "Done" }).click();
    await expect(revealCode).toHaveCount(0);
    expect(await page.locator("body").innerText()).not.toContain(rawKey);

    // Reload: the raw key is not recoverable.
    await page.reload();
    await expect(page.getByRole("heading", { name: "Credentials" })).toBeVisible();
    expect(await page.locator("body").innerText()).not.toContain(rawKey);

    // List/detail API responses never carry the raw key.
    const list = await adminJson<PageEnvelope<CredentialRead>>(
      context,
      "GET",
      `/admin/v1/projects/${projectA}/credentials?limit=200`,
    );
    expect(list.status).toBe(200);
    const created = list.body.items.find((c) => c.name === credentialName);
    expect(created).toBeTruthy();
    expect(JSON.stringify(list.body)).not.toContain(rawKey);
    expect(created).not.toHaveProperty("raw_key");

    // The browser console never logged the raw key.
    expect(consoleMessages.join("\n")).not.toContain(rawKey);
  });
});

test.describe("project_admin management scope", () => {
  test("cannot access catalog controls and is denied catalog APIs", async ({
    page,
    context,
  }) => {
    await loginAs(page, PROJECT_ADMIN_SUBJECT);

    // Catalog nav is hidden (usability only; backend is the real boundary).
    await expect(page.getByRole("link", { name: "Providers" })).toHaveCount(0);

    const providers = await adminJson(context, "GET", "/admin/v1/providers?limit=20");
    expect(providers.status).toBe(403);

    const create = await adminJson(context, "POST", "/admin/v1/providers", {
      kind: "custom",
      name: "should-be-denied",
      capabilities: [],
    });
    expect(create.status).toBe(403);
  });

  test("can manage principals in its own project but cannot enumerate B", async ({
    page,
    context,
  }) => {
    await loginAs(page, PROJECT_ADMIN_SUBJECT);

    // Own project principals are listed.
    const own = await adminJson<PageEnvelope<PrincipalRead>>(
      context,
      "GET",
      `/admin/v1/projects/${projectA}/principals?limit=200`,
    );
    expect(own.status).toBe(200);
    expect(own.body.items.every((p) => p.project_id === projectA)).toBe(true);

    // Creating a principal in the authorized project is allowed.
    const name = `e2e-mgmt-principal-${Date.now()}`;
    const created = await adminJson<PrincipalRead>(
      context,
      "POST",
      `/admin/v1/projects/${projectA}/principals`,
      { kind: "user", name },
    );
    expect(created.status).toBe(201);

    // Project B is non-enumerating: its principals are not reachable.
    const cross = await adminJson(
      context,
      "GET",
      `/admin/v1/projects/${projectB}/principals?limit=20`,
    );
    expect(cross.status).toBe(404);

    const crossProject = await adminJson(context, "GET", `/admin/v1/projects/${projectB}`);
    expect(crossProject.status).toBe(404);
  });
});

test.describe("project_viewer read-only", () => {
  test("has no mutation controls and direct mutations are denied", async ({ page, context }) => {
    await loginAs(page, PROJECT_VIEWER_SUBJECT);

    await page.goto("/roles");
    await expect(page.getByRole("heading", { name: "Role Assignments" })).toBeVisible();
    await expect(page.getByRole("button", { name: "Grant role" })).toHaveCount(0);

    await page.goto("/credentials");
    await expect(page.getByRole("heading", { name: "Credentials" })).toBeVisible();
    await expect(page.getByRole("button", { name: "New credential" })).toHaveCount(0);

    const create = await adminJson(context, "POST", `/admin/v1/projects/${projectA}/principals`, {
      kind: "user",
      name: "viewer-should-not-create",
    });
    expect(create.status).toBe(403);
  });
});

test.describe("inference credential lifecycle", () => {
  test("create -> use -> rotate -> old fails/new works -> revoke -> fails", async ({ browser }) => {
    const admin = await openSession(browser, SYSTEM_ADMIN_SUBJECT);

    const name = `e2e-lifecycle-cred-${Date.now()}`;
    const created = await adminJson<{ credential: { id: string }; raw_key: string }>(
      admin.context,
      "POST",
      "/admin/v1/credentials",
      { project_id: projectA, principal_id: paA, name, audience: "inference" },
    );
    expect(created.status).toBe(201);
    const credentialId = created.body.credential.id;
    const originalKey = created.body.raw_key;
    expect(originalKey.length).toBeGreaterThan(16);

    // Original key works.
    const ok = await submitInference(originalKey, [{ role: "user", content: "lifecycle hello" }]);
    expect(ok.status).toBe(200);

    // Rotate: old key is invalidated, new key works. Rotation mints a new
    // credential id, so capture it for the subsequent revoke.
    const rotated = await adminJson<{ credential: { id: string }; raw_key: string }>(
      admin.context,
      "POST",
      `/admin/v1/credentials/${credentialId}/rotate`,
    );
    expect(rotated.status).toBe(200);
    const newCredentialId = rotated.body.credential.id;
    const newKey = rotated.body.raw_key;

    const oldAfterRotate = await submitInference(originalKey, [
      { role: "user", content: "stale key" },
    ]);
    expect(oldAfterRotate.status).toBe(401);

    const newWorks = await submitInference(newKey, [{ role: "user", content: "new key" }]);
    expect(newWorks.status).toBe(200);

    // Revoke: even the rotated key fails immediately.
    const revoked = await adminJson(admin.context, "POST", `/admin/v1/credentials/${newCredentialId}/revoke`);
    expect(revoked.status).toBe(200);

    const afterRevoke = await submitInference(newKey, [{ role: "user", content: "revoked key" }]);
    expect(afterRevoke.status).toBe(401);

    await admin.context.close();
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
