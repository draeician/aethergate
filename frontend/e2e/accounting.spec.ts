import { test, expect, type Browser, type BrowserContext, type Page } from "@playwright/test";
import {
  loginAs,
  setIdpSubject,
  adminJson,
  provisionOperatorFixture,
  createInferenceCredential,
  runOfficialSdk,
  goToLastPage,
} from "./fixtures";

/**
 * Live accounting-management UI browser E2E.
 *
 * Drives real OIDC browser sessions (system_admin / project_admin / project_viewer)
 * against the deterministic dev IdP. The disposable route binding and the
 * inference credential are provisioned via admin fixtures; every PricePolicy and
 * ProjectBudgetPolicy mutation is performed through the web UI. Live proofs use
 * the official OpenAI Python SDK (inference-auth bypass = false).
 */

interface PageEnvelope<T> {
  items: T[];
  total: number;
}

interface ProviderAccountRead {
  id: string;
  name: string;
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
  is_active: boolean;
}
interface PriceSnapshotRead {
  id: string;
  source_price_policy_id: string;
  route_binding_id: string;
  request_price: string | null;
}
interface QueueRequestRead {
  id: string;
  project_id: string;
  state: string;
  error_code: string | null;
}
interface BudgetStatusRead {
  budget_policy_id: string;
  limit_amount: string;
  committed_amount: string;
  headroom: string;
}
interface UsageRecordRead {
  route_binding_id: string;
  amount: string;
}
interface LedgerEntryRead {
  entry_type: string;
  amount: string;
  currency: string;
}

const SYSTEM_ADMIN_SUBJECT = "dev-user";
const PROJECT_ADMIN_SUBJECT = "e2e-pa-a";
const PROJECT_VIEWER_SUBJECT = "e2e-pv-a";
const UPSTREAM_MODEL = "qwen3.8-2b-distill:Q6_K";
const PRICE_OLD = "0.000000000123";
const PRICE_NEW = "0.000000000456";

let projectA: string;
let projectB: string;
let paA: string;
let aliasId: string;
let routeId: string;
let aliasName: string;
let credentialKey: string;
let runId: number;
let budgetName: string;

test.beforeAll(async ({ browser }) => {
  const admin = await openSession(browser, SYSTEM_ADMIN_SUBJECT);
  const fixture = await provisionOperatorFixture(admin.context);
  projectA = fixture.projectA;
  projectB = fixture.projectB;
  paA = fixture.paA;

  runId = Date.now();
  credentialKey = await createInferenceCredential(
    admin.context,
    projectA,
    paA,
    `e2e-accounting-cred-${runId}`,
  );

  aliasName = `e2e-acct-alias-${runId}`;
  budgetName = `e2e-budget-${runId}`;
  aliasId = await createAlias(admin.context, aliasName);
  routeId = await createRoute(admin.context, aliasId);

  // Ensure no leftover enabled budget from a prior aborted run gates project A
  // spending while the pricing/budget proofs run (enabled budgets accumulate).
  await disableEnabledBudgets(admin.context, projectA);

  await admin.context.close();
});

test.afterAll(async () => {
  await setIdpSubject(SYSTEM_ADMIN_SUBJECT);
});

test.describe("accounting RBAC and navigation", () => {
  test("system_admin sees the full accounting surface", async ({ page }) => {
    await loginAs(page, SYSTEM_ADMIN_SUBJECT);

    for (const label of ["Pricing", "Snapshots", "Budgets", "Reservations", "Usage", "Ledger", "Audit"]) {
      await expect(page.getByRole("link", { name: label })).toBeVisible();
    }

    await page.getByRole("link", { name: "Pricing" }).click();
    await expect(page.getByRole("heading", { name: "Pricing" })).toBeVisible();
  });

  test("project_admin cannot access deployment pricing surfaces", async ({ page, context }) => {
    await loginAs(page, PROJECT_ADMIN_SUBJECT);

    await expect(page.getByRole("link", { name: "Pricing" })).toHaveCount(0);
    await expect(page.getByRole("link", { name: "Snapshots" })).toHaveCount(0);
    await expect(page.getByRole("link", { name: "Budgets" })).toBeVisible();

    const policies = await adminJson(context, "GET", "/admin/v1/price-policies?limit=20");
    expect(policies.status).toBe(403);
    const snapshots = await adminJson(context, "GET", "/admin/v1/price-snapshots?limit=20");
    expect(snapshots.status).toBe(403);
  });

  test("project_viewer is read-only on budgets", async ({ page, context }) => {
    await loginAs(page, PROJECT_VIEWER_SUBJECT);

    await page.getByRole("link", { name: "Budgets" }).click();
    await expect(page.getByRole("heading", { name: "Budgets" })).toBeVisible();
    await expect(page.getByRole("button", { name: "New budget" })).toHaveCount(0);

    const create = await adminJson(context, "POST", "/admin/v1/project-budget-policies", {
      project_id: projectA,
      name: "viewer-should-not-create",
      currency: "USD",
      limit_amount: "1.00",
      window_seconds: 3600,
      enabled: true,
    });
    expect(create.status).toBe(403);
  });

  test("project_admin cannot enumerate project B accounting", async ({ page, context }) => {
    await loginAs(page, PROJECT_ADMIN_SUBJECT);

    const crossBudget = await adminJson(
      context,
      "GET",
      `/admin/v1/projects/${projectB}/budget-status`,
    );
    expect(crossBudget.status).toBe(404);
  });

  test("audit scope: project role sees only own-project events", async ({ page, context }) => {
    await loginAs(page, PROJECT_ADMIN_SUBJECT);

    const events = await adminJson<PageEnvelope<{ project_id: string | null }>>(
      context,
      "GET",
      "/admin/v1/audit-events?limit=200",
    );
    expect(events.status).toBe(200);
    // A project-scoped caller must never see deployment-scoped (null-project) events.
    expect(events.body.items.every((e) => e.project_id === projectA)).toBe(true);
  });
});

test.describe("system_admin pricing and immutable snapshots (live SDK proof)", () => {
  test("creates/edits a PricePolicy and proves old/new snapshots via the SDK", async ({
    page,
    context,
  }) => {
    test.setTimeout(240_000);
    await loginAs(page, SYSTEM_ADMIN_SUBJECT);

    // 1. Create a request-priced policy with an awkward exact Decimal through the UI.
    await page.getByRole("link", { name: "Pricing" }).click();
    await expect(page.getByRole("heading", { name: "Pricing" })).toBeVisible();
    await page.getByRole("button", { name: "New policy" }).click();
    const dialog = page.getByRole("dialog");
    await expect(
      dialog.getByLabel("Route binding").locator("option", { hasText: aliasId }),
    ).toHaveCount(1);
    await dialog.getByLabel("Route binding").selectOption(routeId);
    await dialog.getByLabel("Currency").fill("USD");
    await dialog.getByLabel("Request price").fill(PRICE_OLD);
    await dialog.getByRole("button", { name: "Create" }).click();
    await expect(page.getByRole("dialog")).toHaveCount(0, { timeout: 15_000 });

    // 2. The exact Decimal string is rendered verbatim (no float rounding).
    //    Scope to this run's route row: leftover policies from prior aborted
    //    runs also use PRICE_OLD and would trip strict-mode on a bare getByText.
    const createdRow = page.locator("tbody tr").filter({ hasText: routeId }).first();
    await expect(createdRow.getByText(`${PRICE_OLD} USD`)).toBeVisible({ timeout: 15_000 });

    // 3. Official SDK request (non-stream) against the disposable alias.
    const first = await runOfficialSdk({ key: credentialKey, model: aliasName });
    expect(first.status).toBe("ok");

    // 4. A PriceSnapshot was captured with the old price.
    const snapshots1 = await listSnapshots(context, routeId);
    const oldSnap = snapshots1.find((s) => s.request_price === PRICE_OLD);
    expect(oldSnap).toBeTruthy();

    // 5. Edit the policy through the UI to a different exact price.
    await goToLastPage(page);
    const policyRow = page.locator("tbody tr").filter({ hasText: routeId }).first();
    await expect(policyRow).toBeVisible({ timeout: 15_000 });
    await policyRow.getByRole("button", { name: "Edit" }).click();
    await expect(page.getByRole("dialog")).toBeVisible();
    await page.getByRole("dialog").getByLabel("Request price").fill(PRICE_NEW);
    await page.getByRole("dialog").getByRole("button", { name: "Save changes" }).click();
    await expect(page.getByRole("dialog")).toHaveCount(0, { timeout: 15_000 });

    // 6. A new SDK request captures a new snapshot with the new price.
    const second = await runOfficialSdk({ key: credentialKey, model: aliasName });
    expect(second.status).toBe("ok");

    const snapshots2 = await listSnapshots(context, routeId);
    const newSnap = snapshots2.find((s) => s.request_price === PRICE_NEW);
    expect(newSnap).toBeTruthy();

    // 7. The old snapshot is immutable and unchanged.
    const oldSnapAfter = snapshots2.find((s) => s.id === oldSnap!.id);
    expect(oldSnapAfter?.request_price).toBe(PRICE_OLD);
  });
});

test.describe("project budget block/unblock (live SDK proof)", () => {
  test("blocks an over-budget request and unblocks after a limit increase", async ({
    page,
    context,
  }) => {
    test.setTimeout(240_000);
    await loginAs(page, SYSTEM_ADMIN_SUBJECT);

    // Read the route's current enabled request price (set by the pricing test).
    // A budget limit of 1e-10 is below either price (blocks "too_large"); a
    // limit of 1e-9 is above both (admits one request).
    const routePrice = await getRouteRequestPrice(context, routeId);
    const TIGHT_LIMIT = "0.0000000001";
    const RELAXED_LIMIT = "0.000000001";

    // 1. Create a budget policy on project A through the UI (select the project
    //    in the page filter first so the "New budget" control becomes available).
    await page.getByRole("link", { name: "Budgets" }).click();
    await expect(page.getByRole("heading", { name: "Budgets" })).toBeVisible();
    await page.getByLabel("Project").selectOption(projectA);
    await page.getByRole("button", { name: "New budget" }).click();
    const dialog = page.getByRole("dialog");
    await dialog.getByLabel("Name").fill(budgetName);
    await dialog.getByLabel("Currency").fill("USD");
    await dialog.getByLabel("Limit amount").fill(TIGHT_LIMIT);
    await dialog.getByLabel("Window seconds").fill("3600");
    await dialog.getByRole("button", { name: "Create" }).click();
    await expect(page.getByRole("dialog")).toHaveCount(0, { timeout: 15_000 });

    // 2. Budget status shows the exact limit and zero committed spend.
    const statusBefore = await budgetStatus(context, projectA, budgetName);
    expect(statusBefore.limit_amount).toBe(TIGHT_LIMIT);
    expect(statusBefore.committed_amount).toBe("0");

    // 3. A request whose price exceeds the limit is blocked (fails closed).
    const blocked = await runOfficialSdk({ key: credentialKey, model: aliasName });
    expect(blocked.status).toBe("fail");

    // The queue records the budget rejection reason.
    const failed = await adminJson<PageEnvelope<QueueRequestRead>>(
      context,
      "GET",
      `/admin/v1/queue/requests?project_id=${projectA}&state=failed&limit=50`,
    );
    const budgetFail = failed.body.items.find((r) => r.error_code === "budget_request_too_large");
    expect(budgetFail).toBeTruthy();

    // The blocked request created no spend (historical records unchanged).
    const statusAfterBlock = await budgetStatus(context, projectA, budgetName);
    expect(statusAfterBlock.committed_amount).toBe("0");

    // 4. Increase the budget limit through the UI.
    await goToLastPage(page);
    const row = page.locator("tbody tr").filter({ hasText: TIGHT_LIMIT }).first();
    await expect(row).toBeVisible({ timeout: 15_000 });
    await row.getByRole("button", { name: "Edit" }).click();
    await expect(page.getByRole("dialog")).toBeVisible();
    await page.getByRole("dialog").getByLabel("Limit amount").fill(RELAXED_LIMIT);
    await page.getByRole("dialog").getByRole("button", { name: "Save changes" }).click();
    await expect(page.getByRole("dialog")).toHaveCount(0, { timeout: 15_000 });

    // 5. The same request now fits and succeeds.
    const after = await runOfficialSdk({ key: credentialKey, model: aliasName });
    expect(after.status).toBe("ok");

    // 6. Committed spend and headroom reflect the exact captured price.
    const statusAfter = await budgetStatus(context, projectA, budgetName);
    expect(statusAfter.limit_amount).toBe(RELAXED_LIMIT);
    expect(statusAfter.committed_amount).toBe(routePrice);

    // 7. The successful request produced immutable usage and ledger records.
    const usage = await adminJson<PageEnvelope<UsageRecordRead>>(
      context,
      "GET",
      `/admin/v1/usage-records?route_binding_id=${routeId}&limit=200`,
    );
    expect(usage.body.items.some((u) => u.amount === routePrice)).toBe(true);

    const ledger = await adminJson<PageEnvelope<LedgerEntryRead>>(
      context,
      "GET",
      `/admin/v1/ledger-entries?project_id=${projectA}&entry_type=usage_debit&limit=200`,
    );
    expect(ledger.body.items.some((e) => e.amount === `-${routePrice}` && e.currency === "USD")).toBe(
      true,
    );
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

async function findByName<T extends { id: string; name: string }>(
  context: BrowserContext,
  path: string,
  name: string,
): Promise<T | undefined> {
  const res = await adminJson<PageEnvelope<T>>(context, "GET", `${path}?limit=200`);
  return res.body.items.find((x) => x.name === name);
}

async function createAlias(context: BrowserContext, name: string): Promise<string> {
  const { status, body } = await adminJson<ModelAliasRead>(context, "POST", "/admin/v1/model-aliases", {
    name,
    is_active: true,
    capabilities: ["text"],
  });
  if (status !== 201) throw new Error(`create alias failed: HTTP ${status}`);
  return body.id;
}

async function createRoute(context: BrowserContext, aliasId: string): Promise<string> {
  const account = await findByName<ProviderAccountRead>(
    context,
    "/admin/v1/provider-accounts",
    "ollama-account",
  );
  const endpoint = await findByName<EndpointRead>(context, "/admin/v1/endpoints", "ollama-endpoint");
  if (!account || !endpoint) throw new Error("pre-provisioned ollama account/endpoint not found");

  const { status, body } = await adminJson<RouteBindingRead>(context, "POST", "/admin/v1/route-bindings", {
    model_alias_id: aliasId,
    provider_account_id: account.id,
    endpoint_id: endpoint.id,
    upstream_model: UPSTREAM_MODEL,
    is_active: true,
  });
  if (status !== 201) throw new Error(`create route failed: HTTP ${status}`);
  return body.id;
}

async function listSnapshots(
  context: BrowserContext,
  routeBindingId: string,
): Promise<PriceSnapshotRead[]> {
  const res = await adminJson<PageEnvelope<PriceSnapshotRead>>(
    context,
    "GET",
    `/admin/v1/price-snapshots?route_binding_id=${routeBindingId}&limit=200`,
  );
  return res.body.items;
}

async function getRouteRequestPrice(
  context: BrowserContext,
  routeBindingId: string,
): Promise<string> {
  const res = await adminJson<
    PageEnvelope<{ request_price: string | null; enabled: boolean }>
  >(
    context,
    "GET",
    `/admin/v1/price-policies?route_binding_id=${routeBindingId}&enabled=true&limit=50`,
  );
  const policy = res.body.items.find((p) => p.request_price != null);
  if (!policy || policy.request_price == null) {
    throw new Error(`no enabled request-priced policy for route ${routeBindingId}`);
  }
  return policy.request_price;
}

async function budgetStatus(
  context: BrowserContext,
  projectId: string,
  name: string,
): Promise<BudgetStatusRead> {
  const policies = await adminJson<PageEnvelope<{ id: string; name: string }>>(
    context,
    "GET",
    `/admin/v1/project-budget-policies?project_id=${projectId}&limit=200`,
  );
  const policy = policies.body.items.find((p) => p.name === name);
  if (!policy) throw new Error(`budget policy ${name} not found`);
  const res = await adminJson<BudgetStatusRead[]>(
    context,
    "GET",
    `/admin/v1/projects/${projectId}/budget-status`,
  );
  const status = res.body.find((s) => s.budget_policy_id === policy.id);
  if (!status) throw new Error(`no budget status for ${name}`);
  return status;
}

async function disableEnabledBudgets(context: BrowserContext, projectId: string): Promise<void> {
  const res = await adminJson<PageEnvelope<{ id: string; enabled: boolean }>>(
    context,
    "GET",
    `/admin/v1/project-budget-policies?project_id=${projectId}&limit=200`,
  );
  for (const policy of res.body.items) {
    if (policy.enabled) {
      await adminJson(context, "PATCH", `/admin/v1/project-budget-policies/${policy.id}`, {
        enabled: false,
      });
    }
  }
}
