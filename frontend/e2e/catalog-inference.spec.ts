import { test, expect, type Browser, type BrowserContext, type Page } from "@playwright/test";
import {
  loginAs,
  adminJson,
  provisionOperatorFixture,
  createInferenceCredential,
  runOfficialSdk,
  goToLastPage,
} from "./fixtures";

/**
 * Live full disposable catalog path -> official OpenAI Python SDK proof.
 *
 * Creates a brand-new provider, provider account, endpoint, model alias, and
 * route binding entirely through the web UI (system_admin OIDC browser session)
 * against the allowlisted nomnom Ollama destination, then proves the official
 * OpenAI Python SDK routes the disposable alias (non-stream and stream),
 * fails closed with ``model_unavailable`` when the route is deactivated through
 * the UI, and succeeds again after it is restored through the UI.
 *
 * The route must use only the newly-created account/endpoint — no dependency on
 * the pre-provisioned ``ollama`` / ``ollama-account`` / ``ollama-endpoint`` path.
 */

interface PageEnvelope<T> {
  items: T[];
  total: number;
}

interface ProviderRead {
  id: string;
  name: string;
  kind: string;
}
interface ProviderAccountRead {
  id: string;
  name: string;
  provider_id: string;
}
interface EndpointRead {
  id: string;
  name: string;
  provider_account_id: string;
}
interface ModelAliasRead {
  id: string;
  name: string;
}
interface RouteBindingRead {
  id: string;
  model_alias_id: string;
  provider_account_id: string;
  endpoint_id: string;
  is_active: boolean;
}

const SYSTEM_ADMIN_SUBJECT = "dev-user";
const UPSTREAM_MODEL = "qwen3.8-2b-distill:Q6_K";
const OLLAMA_DESTINATION = "http://192.168.22.50:11434";

let projectA: string;
let paA: string;

test.beforeAll(async ({ browser }) => {
  const session = await openSession(browser, SYSTEM_ADMIN_SUBJECT);
  const fixture = await provisionOperatorFixture(session.context);
  projectA = fixture.projectA;
  paA = fixture.paA;
  await session.context.close();
});

test("full disposable catalog path -> official SDK -> deactivate -> restore", async ({
  page,
  context,
  browser,
}) => {
  const runId = Date.now();
  const providerName = `e2e-disp-provider-${runId}`;
  const accountName = `e2e-disp-account-${runId}`;
  const endpointName = `e2e-disp-endpoint-${runId}`;
  const aliasName = `e2e-disp-alias-${runId}`;

  await loginAs(page, SYSTEM_ADMIN_SUBJECT);

  // 1. Provider (kind compatible with the existing ollama adapter).
  await page.getByRole("link", { name: "Providers" }).click();
  await expect(page.getByRole("heading", { name: "Providers" })).toBeVisible();
  await page.getByRole("button", { name: "New provider" }).click();
  await page.getByLabel("Kind").fill("ollama");
  await page.getByLabel("Name").fill(providerName);
  await page.getByRole("checkbox", { name: "text" }).check();
  await page.getByRole("button", { name: "Create" }).click();
  await expect(page.getByRole("dialog")).toHaveCount(0, { timeout: 15_000 });

  // 2. Provider account (no secret ref: Ollama needs no provider secret).
  await page.getByRole("link", { name: "Provider Accounts" }).click();
  await expect(page.getByRole("heading", { name: "Provider Accounts" })).toBeVisible();
  await page.getByRole("button", { name: "New account" }).click();
  await expect(page.locator("#account-provider option", { hasText: providerName })).toHaveCount(1, {
    timeout: 15_000,
  });
  await page.getByLabel("Provider", { exact: true }).selectOption({ label: `${providerName} (ollama)` });
  await page.getByLabel("Name", { exact: true }).fill(accountName);
  await page.getByRole("button", { name: "Create" }).click();
  await expect(page.getByRole("dialog")).toHaveCount(0, { timeout: 15_000 });

  // 3. Endpoint (new account -> real allowlisted Ollama destination).
  await page.getByRole("link", { name: "Endpoints" }).click();
  await expect(page.getByRole("heading", { name: "Endpoints" })).toBeVisible();
  await page.getByRole("button", { name: "New endpoint" }).click();
  await expect(page.locator("#endpoint-account option", { hasText: accountName })).toHaveCount(1, {
    timeout: 15_000,
  });
  await page.getByLabel("Provider account").selectOption({ label: accountName });
  await page.getByLabel("Name").fill(endpointName);
  await page.getByLabel("Base destination").fill(OLLAMA_DESTINATION);
  await page.getByLabel("Max concurrency").fill("1");
  await page.getByRole("button", { name: "Create" }).click();
  await expect(page.getByRole("dialog")).toHaveCount(0, { timeout: 15_000 });

  // 4. Model alias (client-visible OpenAI model name).
  await page.getByRole("link", { name: "Model Aliases" }).click();
  await expect(page.getByRole("heading", { name: "Model Aliases" })).toBeVisible();
  await page.getByRole("button", { name: "New alias" }).click();
  await page.getByLabel("Alias name").fill(aliasName);
  await page.getByRole("button", { name: "Create" }).click();
  await expect(page.getByRole("dialog")).toHaveCount(0, { timeout: 15_000 });

  // 5. Route binding (only the newly-created alias/account/endpoint).
  await page.getByRole("link", { name: "Route Bindings" }).click();
  await expect(page.getByRole("heading", { name: "Route Bindings" })).toBeVisible();
  await page.getByRole("button", { name: "New route" }).click();
  await expect(page.locator("#route-alias option", { hasText: aliasName })).toHaveCount(1, {
    timeout: 15_000,
  });
  await page.getByLabel("Model alias").selectOption({ label: aliasName });
  await page.getByLabel("Provider account").selectOption({ label: accountName });
  await page.getByLabel("Endpoint").selectOption({ label: endpointName });
  await page.getByLabel("Upstream model").fill(UPSTREAM_MODEL);
  await page.getByRole("button", { name: "Create" }).click();
  await expect(page.getByRole("dialog")).toHaveCount(0, { timeout: 15_000 });

  // Resolve created IDs + the pre-existing ollama IDs (postcondition inspection).
  const [providerId, accountId, endpointId, aliasId] = await resolveIds(
    context,
    providerName,
    accountName,
    endpointName,
    aliasName,
  );
  expect(providerId).toBeTruthy();
  expect(accountId).toBeTruthy();
  expect(endpointId).toBeTruthy();
  expect(aliasId).toBeTruthy();

  const routes = await adminJson<PageEnvelope<RouteBindingRead>>(
    context,
    "GET",
    "/admin/v1/route-bindings?limit=200",
  );
  const route = routes.body.items.find((r) => r.model_alias_id === aliasId);
  expect(route).toBeTruthy();

  // Prove no dependency on the pre-existing provider/account/endpoint.
  const oldAccountId = (await findByName<ProviderAccountRead>(context, "/admin/v1/provider-accounts", "ollama-account"))?.id;
  const oldEndpointId = (await findByName<EndpointRead>(context, "/admin/v1/endpoints", "ollama-endpoint"))?.id;
  expect(route!.provider_account_id).toBe(accountId);
  expect(route!.endpoint_id).toBe(endpointId);
  expect(route!.provider_account_id).not.toBe(oldAccountId);
  expect(route!.endpoint_id).not.toBe(oldEndpointId);

  // A valid inference credential (fixture; the lifecycle-through-UI proof lives
  // in management-rbac.spec.ts).
  const admin = await openSession(browser, SYSTEM_ADMIN_SUBJECT);
  const key = await createInferenceCredential(
    admin.context,
    projectA,
    paA,
    `e2e-disp-sdk-cred-${runId}`,
  );

  // 6. Official OpenAI Python SDK: non-stream + stream against the alias.
  const nonStream = await runOfficialSdk({ key, model: aliasName });
  expect(nonStream.status).toBe("ok");
  const stream = await runOfficialSdk({ key, model: aliasName, stream: true });
  expect(stream.status).toBe("ok_stream");

  // 7. Deactivate the route through the UI; the SDK must fail closed.
  await page.getByRole("link", { name: "Route Bindings" }).click();
  await expect(page.getByRole("heading", { name: "Route Bindings" })).toBeVisible();
  await goToLastPage(page);
  await setRouteActive(page, route!.endpoint_id, false);

  const afterDeactivate = await runOfficialSdk({ key, model: aliasName });
  expect(afterDeactivate.status).toBe("fail");
  expect(afterDeactivate.errorCode).toBe("model_unavailable");

  // 8. Restore the route through the UI; the SDK succeeds again.
  await goToLastPage(page);
  await setRouteActive(page, route!.endpoint_id, true);

  const afterRestore = await runOfficialSdk({ key, model: aliasName });
  expect(afterRestore.status).toBe("ok");

  // 9. Leave the disposable resources inactive (no DELETE surface exists): the
  // route binding is deactivated, then the endpoint's catalog-active flag.
  await goToLastPage(page);
  await setRouteActive(page, route!.endpoint_id, false);

  await page.getByRole("link", { name: "Endpoints" }).click();
  await expect(page.getByRole("heading", { name: "Endpoints" })).toBeVisible();
  await goToLastPage(page);
  const endpointRow = page.locator("tbody tr").filter({ hasText: endpointName }).first();
  await expect(endpointRow).toBeVisible({ timeout: 15_000 });
  await endpointRow.getByRole("button", { name: "Edit" }).click();
  await expect(page.getByRole("dialog")).toBeVisible();
  await page.getByLabel("Catalog active").uncheck();
  await page.getByRole("button", { name: "Save changes" }).click();
  await expect(page.getByRole("dialog")).toHaveCount(0, { timeout: 15_000 });

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

async function findByName<T extends { id: string; name: string }>(
  context: BrowserContext,
  path: string,
  name: string,
): Promise<T | undefined> {
  const res = await adminJson<PageEnvelope<T>>(context, "GET", `${path}?limit=200`);
  return res.body.items.find((x) => x.name === name);
}

async function resolveIds(
  context: BrowserContext,
  providerName: string,
  accountName: string,
  endpointName: string,
  aliasName: string,
): Promise<[string | undefined, string | undefined, string | undefined, string | undefined]> {
  const provider = await findByName<ProviderRead>(context, "/admin/v1/providers", providerName);
  const account = await findByName<ProviderAccountRead>(context, "/admin/v1/provider-accounts", accountName);
  const endpoint = await findByName<EndpointRead>(context, "/admin/v1/endpoints", endpointName);
  const alias = await findByName<ModelAliasRead>(context, "/admin/v1/model-aliases", aliasName);
  return [provider?.id, account?.id, endpoint?.id, alias?.id];
}

async function setRouteActive(page: Page, endpointId: string, active: boolean): Promise<void> {
  const row = page.locator("tbody tr").filter({ hasText: endpointId }).first();
  await expect(row).toBeVisible({ timeout: 15_000 });
  await row.getByRole("button", { name: "Edit" }).click();
  await expect(page.getByRole("dialog")).toBeVisible();
  const toggle = page.getByLabel("Active");
  if (active) {
    await toggle.check();
  } else {
    await toggle.uncheck();
  }
  await page.getByRole("button", { name: "Save changes" }).click();
  await expect(page.getByRole("dialog")).toHaveCount(0, { timeout: 15_000 });
}
