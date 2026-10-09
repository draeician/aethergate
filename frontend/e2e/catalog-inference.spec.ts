import { test, expect, type Browser, type BrowserContext, type Page } from "@playwright/test";
import {
  loginAs,
  adminJson,
  provisionOperatorFixture,
  createInferenceCredential,
  WEB_BASE_URL,
} from "./fixtures";

/**
 * Live catalog configuration -> real inference proof.
 *
 * Drives the web UI as system_admin to create a disposable model alias and its
 * route binding against the already-provisioned Ollama endpoint, then proves the
 * official OpenAI-compatible surface routes it (success), that deactivating the
 * route fails closed (model_unavailable), and that restoring it succeeds again.
 */

interface PageEnvelope<T> {
  items: T[];
  total: number;
}

interface ModelAlias {
  id: string;
  name: string;
  is_active: boolean;
}

interface RouteBinding {
  id: string;
  model_alias_id: string;
  endpoint_id: string;
  is_active: boolean;
}

const SYSTEM_ADMIN_SUBJECT = "dev-user";
const UPSTREAM_MODEL = "qwen3.8-2b-distill:Q6_K";

let projectA: string;
let paA: string;

test.beforeAll(async ({ browser }) => {
  const session = await openSession(browser, SYSTEM_ADMIN_SUBJECT);
  const fixture = await provisionOperatorFixture(session.context);
  projectA = fixture.projectA;
  paA = fixture.paA;
  await session.context.close();
});

test("disposable alias -> inference -> deactivate fails -> restore succeeds", async ({
  page,
  context,
  browser,
}) => {
  const aliasName = `e2e-disp-alias-${Date.now()}`;

  await loginAs(page, SYSTEM_ADMIN_SUBJECT);

  // Create the model alias through the UI.
  await page.getByRole("link", { name: "Model Aliases" }).click();
  await expect(page.getByRole("heading", { name: "Model Aliases" })).toBeVisible();
  await page.getByRole("button", { name: "New alias" }).click();
  await page.getByLabel("Alias name").fill(aliasName);
  await page.getByRole("button", { name: "Create" }).click();
  await expect(page.getByText(aliasName)).toBeVisible({ timeout: 15_000 });

  // Create a route binding for the alias against the provisioned Ollama endpoint.
  await page.getByRole("link", { name: "Route Bindings" }).click();
  await expect(page.getByRole("heading", { name: "Route Bindings" })).toBeVisible();
  await page.getByRole("button", { name: "New route" }).click();
  await page.getByLabel("Model alias").selectOption({ label: aliasName });
  await page.getByLabel("Provider account").selectOption({ label: "ollama-account" });
  await page.getByLabel("Endpoint").selectOption({ label: "ollama-endpoint" });
  await page.getByLabel("Upstream model").fill(UPSTREAM_MODEL);
  await page.getByRole("button", { name: "Create" }).click();
  await expect(page.getByRole("heading", { name: "New route binding" })).toHaveCount(0, {
    timeout: 15_000,
  });

  // Resolve the created alias + route IDs for the deactivate/restore steps.
  const aliases = await adminJson<PageEnvelope<ModelAlias>>(
    context,
    "GET",
    "/admin/v1/model-aliases?limit=200",
  );
  const alias = aliases.body.items.find((a) => a.name === aliasName);
  expect(alias).toBeTruthy();

  const routes = await adminJson<PageEnvelope<RouteBinding>>(
    context,
    "GET",
    "/admin/v1/route-bindings?limit=200",
  );
  const route = routes.body.items.find((r) => r.model_alias_id === alias!.id);
  expect(route).toBeTruthy();

  // An inference credential for the disposable flow.
  const admin = await openSession(browser, SYSTEM_ADMIN_SUBJECT);
  const key = await createInferenceCredential(
    admin.context,
    projectA,
    paA,
    `e2e-disp-cred-${Date.now()}`,
  );

  // Prove the new alias routes to real inference.
  const ok = await submit(aliasName, key);
  expect(ok.status).toBe(200);

  // Deactivate the route; inference must fail closed (model_unavailable).
  const deactivate = await adminJson(
    admin.context,
    "PATCH",
    `/admin/v1/route-bindings/${route!.id}`,
    { is_active: false },
  );
  expect(deactivate.status).toBe(200);

  const afterDeactivate = await submit(aliasName, key);
  expect(afterDeactivate.status).toBe(400);
  expect((await afterDeactivate.json()).error.code).toBe("model_unavailable");

  // Restore the route; inference succeeds again.
  const restore = await adminJson(
    admin.context,
    "PATCH",
    `/admin/v1/route-bindings/${route!.id}`,
    { is_active: true },
  );
  expect(restore.status).toBe(200);

  const afterRestore = await submit(aliasName, key);
  expect(afterRestore.status).toBe(200);

  await admin.context.close();
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

async function submit(model: string, key: string): Promise<Response> {
  return fetch(`${WEB_BASE_URL.replace(/\/$/, "")}/v1/chat/completions`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${key}`,
    },
    body: JSON.stringify({
      model,
      messages: [{ role: "user", content: "catalog inference proof" }],
    }),
  });
}
