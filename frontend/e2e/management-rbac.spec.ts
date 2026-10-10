import { test, expect, type Browser, type BrowserContext, type Page } from "@playwright/test";
import {
  loginAs,
  setIdpSubject,
  adminJson,
  ensureProject,
  ensurePrincipal,
  ensureIdentityLink,
  ensureRoleAssignment,
  runOfficialSdk,
  goToLastPage,
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
const LIVE_ALIAS = "gpt-4";

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
    // Projects sort oldest-first and paginate at 20; the new project lands on
    // the last page, so navigate there instead of assuming page 1.
    await goToLastPage(page);
    await expect(page.getByText(projectName)).toBeVisible({ timeout: 15_000 });

    // Create a provider through the UI.
    await page.getByRole("link", { name: "Providers" }).click();
    await expect(page.getByRole("heading", { name: "Providers" })).toBeVisible();

    const providerName = `e2e-mgmt-provider-${Date.now()}`;
    await page.getByRole("button", { name: "New provider" }).click();
    await page.getByLabel("Name").fill(providerName);
    await page.getByLabel("Kind").fill("custom");
    await page.getByRole("button", { name: "Create" }).click();
    // Providers also accumulate across runs; paginate to the last page.
    await goToLastPage(page);
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
  test("create/rotate/revoke through the UI with official SDK outcomes", async ({
    page,
    browser,
  }) => {
    // Multiple live official-SDK calls route through slow Ollama generation; the
    // default 60s Playwright timeout is too tight for the full lifecycle.
    test.setTimeout(240_000);
    // A disposable project + principal keep the credential list on page 1.
    const runId = Date.now();
    const admin = await openSession(browser, SYSTEM_ADMIN_SUBJECT);
    const lifecycleProject = await ensureProject(admin.context, `e2e-lifecycle-project-${runId}`);
    const principalName = `e2e-lifecycle-principal-${runId}`;
    await ensurePrincipal(admin.context, lifecycleProject, principalName);
    await admin.context.close();

    const credentialName = `e2e-lifecycle-cred-${runId}`;
    const consoleMessages: string[] = [];

    await loginAs(page, SYSTEM_ADMIN_SUBJECT);
    page.on("console", (msg) => consoleMessages.push(msg.text()));

    // Create through the UI and capture the one-time reveal.
    await page.goto(`/credentials?project=${lifecycleProject}`);
    await expect(page.getByRole("heading", { name: "Credentials" })).toBeVisible();
    await page.getByRole("button", { name: "New credential" }).click();
    await page.getByLabel("Name").fill(credentialName);
    await expect(page.locator("#credential-principal option", { hasText: principalName })).toHaveCount(
      1,
      { timeout: 15_000 },
    );
    await page.getByLabel("Principal").selectOption({ label: principalName });
    await page.getByRole("dialog").getByRole("button", { name: "Create" }).click();

    const revealCode = page.getByRole("dialog").locator("code");
    await expect(revealCode).toBeVisible({ timeout: 15_000 });
    const originalKey = (await revealCode.textContent())?.trim() ?? "";
    expect(originalKey.length).toBeGreaterThan(16);

    // One-time reveal canary: never in URL/storage/console.
    expect(page.url()).not.toContain(originalKey);
    const storage = await page.evaluate(() => ({
      local: Object.values(window.localStorage),
      session: Object.values(window.sessionStorage),
    }));
    expect(storage.local.join("\n")).not.toContain(originalKey);
    expect(storage.session.join("\n")).not.toContain(originalKey);

    // Closing the reveal destroys the in-memory value.
    await page.getByRole("dialog").getByRole("button", { name: "Done" }).click();
    await expect(revealCode).toHaveCount(0);
    expect(await page.locator("body").innerText()).not.toContain(originalKey);
    expect(consoleMessages.join("\n")).not.toContain(originalKey);

    // Official SDK with the new key succeeds.
    const createdOk = await runOfficialSdk({ key: originalKey, model: LIVE_ALIAS });
    expect(createdOk.status).toBe("ok");

    // Rotate through the UI.
    await clickCredentialAction(page, credentialName, "Rotate", "Rotate credential?", "Rotate");
    await expect(page.getByRole("dialog").locator("code")).toBeVisible({ timeout: 15_000 });
    const rotatedKey = (await page.getByRole("dialog").locator("code").textContent())?.trim() ?? "";
    expect(rotatedKey.length).toBeGreaterThan(16);
    expect(rotatedKey).not.toBe(originalKey);
    await page.getByRole("dialog").getByRole("button", { name: "Done" }).click();

    // Old key fails immediately; new key succeeds.
    const oldAfterRotate = await runOfficialSdk({ key: originalKey, model: LIVE_ALIAS });
    expect(oldAfterRotate.status).toBe("fail");
    expect(oldAfterRotate.httpStatus).toBe(401);

    const newWorks = await runOfficialSdk({ key: rotatedKey, model: LIVE_ALIAS });
    expect(newWorks.status).toBe("ok");

    // Revoke through the UI.
    await clickCredentialAction(page, credentialName, "Revoke", "Revoke credential?", "Revoke");

    // Revoked key fails immediately.
    const afterRevoke = await runOfficialSdk({ key: rotatedKey, model: LIVE_ALIAS });
    expect(afterRevoke.status).toBe("fail");
    expect(afterRevoke.httpStatus).toBe(401);
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

async function clickCredentialAction(
  page: Page,
  credentialName: string,
  actionLabel: string,
  dialogTitle: string,
  confirmLabel: string,
): Promise<void> {
  const row = page
    .locator("tbody tr")
    .filter({ hasText: credentialName })
    .filter({ has: page.getByRole("button", { name: actionLabel }) })
    .first();
  await expect(row).toBeVisible({ timeout: 15_000 });
  await row.getByRole("button", { name: actionLabel }).click();
  await expect(page.getByRole("dialog", { name: dialogTitle })).toBeVisible();
  await page.getByRole("dialog").getByRole("button", { name: confirmLabel }).click();
}
