import { test, expect, type Browser, type BrowserContext, type Page } from "@playwright/test";
import {
  loginAs,
  setIdpSubject,
  adminJson,
  provisionOperatorFixture,
  createInferenceCredential,
  runOfficialSdk,
  spawnOfficialSdk,
  goToLastPage,
  runReleaseHarness,
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
  request_id: string;
  project_id: string;
  state: string;
  error_code: string | null;
  wait_reason: string | null;
  effective_wait_reason: string | null;
  wait_limit_id: string | null;
  wait_limit_metric: string | null;
}
interface BudgetStatusRead {
  budget_policy_id: string;
  limit_amount: string;
  committed_amount: string;
  headroom: string;
}
interface UsageRecordRead {
  id: string;
  request_id: string;
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
    // Policy rows accumulate across runs and paginate at 20 (oldest-first), so
    // scope the pricing table to this run's route instead of assuming the new
    // policy is on page 1.
    await page.getByLabel("Route binding").selectOption(routeId);
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

test.describe("project budget window exhaustion -> same request unblocks (live SDK proof)", () => {
  test("queues a second request on budget_window_exhausted and unblocks the SAME request", async ({
    page,
    context,
  }) => {
    test.setTimeout(360_000);
    await loginAs(page, SYSTEM_ADMIN_SUBJECT);

    // Read the route's current enabled request price P. A budget whose limit
    // equals P admits exactly one request in the window; the second is queued
    // (budget_window_exhausted), not rejected (budget_request_too_large).
    const routePrice = await getRouteRequestPrice(context, routeId);
    const RELAXED = "0.000000001"; // admits the queued second request

    // 1. Create a budget policy on project A through the UI whose limit is the
    //    exact request price (permits exactly one request).
    await page.getByRole("link", { name: "Budgets" }).click();
    await expect(page.getByRole("heading", { name: "Budgets" })).toBeVisible();
    await page.getByLabel("Project").selectOption(projectA);
    await page.getByRole("button", { name: "New budget" }).click();
    const dialog = page.getByRole("dialog");
    await dialog.getByLabel("Name").fill(budgetName);
    await dialog.getByLabel("Currency").fill("USD");
    await dialog.getByLabel("Limit amount").fill(routePrice);
    await dialog.getByLabel("Window seconds").fill("3600");
    await dialog.getByRole("button", { name: "Create" }).click();
    await expect(page.getByRole("dialog")).toHaveCount(0, { timeout: 15_000 });

    const statusBefore = await budgetStatus(context, projectA, budgetName);
    const budgetPolicyId = statusBefore.budget_policy_id;
    expect(statusBefore.limit_amount).toBe(routePrice);
    expect(statusBefore.committed_amount).toBe("0");

    // 2. The first official SDK request consumes and commits exactly P.
    const first = await runOfficialSdk({ key: credentialKey, model: aliasName });
    expect(first.status).toBe("ok");
    const statusAfterFirst = await budgetStatus(context, projectA, budgetName);
    expect(statusAfterFirst.committed_amount).toBe(routePrice);

    // 3. Start a SECOND official SDK request and keep that same call pending.
    const pending = spawnOfficialSdk({ key: credentialKey, model: aliasName, timeout: 300 });

    // 4. Capture the second request's stable request_id and prove it is queued
    //    with budget_window_exhausted (temporary), not a terminal rejection.
    const secondRequestId = await waitForBudgetQueuedRequest(context, aliasId, budgetPolicyId);

    const queued = await adminJson<QueueRequestRead>(
      context,
      "GET",
      `/admin/v1/queue/requests/${secondRequestId}`,
    );
    expect(queued.status).toBe(200);
    expect(queued.body.state).toBe("queued");
    expect(queued.body.wait_reason).toBe("budget_window_exhausted");
    expect(queued.body.effective_wait_reason).toBe("budget_window_exhausted");
    expect(queued.body.wait_limit_metric).toBe("budget");
    expect(queued.body.error_code).toBeNull();

    // 5. The queued second request has no usage/ledger settlement yet.
    const noUsage = await adminJson<PageEnvelope<UsageRecordRead>>(
      context,
      "GET",
      `/admin/v1/usage-records?request_id=${secondRequestId}&limit=10`,
    );
    expect(noUsage.body.total).toBe(0);

    // 6. Raise the SAME budget policy limit through the UI so the queued request
    //    becomes eligible. Target this run's budget by its unique name; filtering
    //    by the price string is ambiguous because committed_amount equals P on
    //    older leftover budget rows (and the list is ordered oldest-first).
    await goToLastPage(page);
    const row = page.locator("tbody tr").filter({ hasText: budgetName }).first();
    await expect(row).toBeVisible({ timeout: 15_000 });
    await row.getByRole("button", { name: "Edit" }).click();
    await expect(page.getByRole("dialog")).toBeVisible();
    await page.getByRole("dialog").getByLabel("Limit amount").fill(RELAXED);
    await page.getByRole("dialog").getByRole("button", { name: "Save changes" }).click();
    await expect(page.getByRole("dialog")).toHaveCount(0, { timeout: 15_000 });

    // 7. Await the original pending SDK call: the SAME request_id leaves the
    //    queue and succeeds (no replacement request was submitted).
    const second = await pending.wait();
    expect(second.status).toBe("ok");

    const succeeded = await adminJson<QueueRequestRead>(
      context,
      "GET",
      `/admin/v1/queue/requests/${secondRequestId}`,
    );
    expect(succeeded.body.state).toBe("succeeded");
    expect(succeeded.body.effective_wait_reason).toBeNull();
    expect(succeeded.body.error_code).toBeNull();

    // 8. Exactly one UsageRecord and one signed negative usage_debit for that
    //    request; no duplicate settlement.
    const usage = await adminJson<PageEnvelope<UsageRecordRead>>(
      context,
      "GET",
      `/admin/v1/usage-records?request_id=${secondRequestId}&limit=10`,
    );
    expect(usage.body.total).toBe(1);
    expect(usage.body.items[0].amount).toBe(routePrice);
    const usageRecordId = usage.body.items[0].id;

    const ledger = await adminJson<PageEnvelope<LedgerEntryRead>>(
      context,
      "GET",
      `/admin/v1/ledger-entries?usage_record_id=${usageRecordId}&limit=10`,
    );
    expect(ledger.body.total).toBe(1);
    expect(ledger.body.items[0].entry_type).toBe("usage_debit");
    expect(ledger.body.items[0].amount).toBe(`-${routePrice}`);
    expect(ledger.body.items[0].currency).toBe("USD");

    // 9. Budget status/headroom reflect the exact server values: two requests
    //    committed exactly 2*P and headroom is RELAXED - 2*P.
    const statusAfter = await budgetStatus(context, projectA, budgetName);
    expect(statusAfter.limit_amount).toBe(RELAXED);
    expect(statusAfter.committed_amount).toBe(doubleDecimal(routePrice));
    expect(statusAfter.headroom).toBe(subtractDecimal(RELAXED, doubleDecimal(routePrice)));
  });
});

test.describe("pre-dispatch released reservation (live harness + browser proof)", () => {
  test("a released reservation renders with null snapshot and zero amounts", async ({
    page,
    context,
  }) => {
    test.setTimeout(360_000);
    await loginAs(page, SYSTEM_ADMIN_SUBJECT);

    // 1. Run the real-lifecycle harness: enqueue -> claim/reserve -> revert to
    //    the reserved (pre-dispatch) window -> cancel -> released. Workers are
    //    stopped and restarted around the harness so no live worker races the
    //    single queued request during its reserved window. The harness asserts
    //    the released invariants and that the provider adapter was never
    //    invoked (no upstream dispatch) before it prints the identifiers.
    const harness = await runReleaseHarness();

    // 2. The released request reached the cancelled state with no usage record.
    const req = await adminJson<QueueRequestRead>(
      context,
      "GET",
      `/admin/v1/queue/requests/${harness.request_id}`,
    );
    expect(req.status).toBe(200);
    expect(req.body.state).toBe("cancelled");
    expect(req.body.error_code).toBeNull();

    const usage = await adminJson<PageEnvelope<UsageRecordRead>>(
      context,
      "GET",
      `/admin/v1/usage-records?request_id=${harness.request_id}&limit=10`,
    );
    expect(usage.body.total).toBe(0);

    // 3. The SAME real reservation renders in the browser UI with a null
    //    snapshot ("released before dispatch") and zero reserved/committed
    //    amounts.
    await page.getByRole("link", { name: "Reservations" }).click();
    await expect(page.getByRole("heading", { name: "Budget Reservations" })).toBeVisible();
    await page.getByLabel("Request").fill(harness.request_id);

    const row = page.locator("tbody tr").filter({ hasText: harness.request_id }).first();
    await expect(row).toBeVisible({ timeout: 15_000 });
    await expect(row.getByText("released", { exact: true })).toBeVisible();
    await expect(row.getByText("No snapshot / released before dispatch")).toBeVisible();
    const cells = row.locator("td");
    await expect(cells.nth(3)).toHaveText("0");
    await expect(cells.nth(4)).toHaveText("0");
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

async function waitForBudgetQueuedRequest(
  context: BrowserContext,
  aliasId: string,
  budgetPolicyId: string,
  timeoutMs = 120_000,
): Promise<string> {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const res = await adminJson<PageEnvelope<QueueRequestRead>>(
      context,
      "GET",
      `/admin/v1/queue/requests?model_alias_id=${aliasId}&state=queued&wait_reason=budget_window_exhausted&limit=50`,
    );
    const found = res.body.items.find((r) => r.wait_limit_id === budgetPolicyId);
    if (found) return found.request_id;
    await new Promise((resolve) => setTimeout(resolve, 500));
  }
  throw new Error("queued budget_window_exhausted request not found within timeout");
}

// Exact fixed-point helpers for asserting canonical money strings without ever
// parsing them through a binary float (mirrors the backend Decimal semantics).
function scaleDecimal(s: string): bigint {
  const [whole = "0", frac = ""] = s.split(".");
  return BigInt(whole + frac.padEnd(12, "0"));
}

function formatScaled(n: bigint): string {
  const digits = n.toString().padStart(13, "0");
  const whole = digits.slice(0, digits.length - 12) || "0";
  const frac = digits.slice(digits.length - 12).replace(/0+$/, "");
  return frac ? `${whole}.${frac}` : whole;
}

function doubleDecimal(s: string): string {
  return formatScaled(scaleDecimal(s) * 2n);
}

function subtractDecimal(a: string, b: string): string {
  const result = scaleDecimal(a) - scaleDecimal(b);
  return formatScaled(result < 0n ? 0n : result);
}
