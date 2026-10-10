import { test, expect, type Browser, type BrowserContext, type Page } from "@playwright/test";
import {
  loginAs,
  adminJson,
  provisionOperatorFixture,
  createInferenceCredential,
  runOfficialSdk,
  spawnOfficialSdk,
} from "./fixtures";

/**
 * Live operational-observability browser E2E (AGV2-023).
 *
 * Drives real OIDC browser sessions and the official OpenAI Python SDK against
 * the deterministic dev IdP + real internal Ollama (inference-auth bypass =
 * false). Proves:
 *   - six real requests against the two-slot pre-provisioned endpoint produce
 *     real queue-wait samples (ordered p50 <= p95 <= p99);
 *   - an official streaming SDK call produces a real TTFT sample;
 *   - a nonexistent upstream model records passive upstream-failure evidence and
 *     restoring a valid model records success evidence — with no raw provider
 *     error body exposed;
 *   - system_admin sees deployment upstream health, project roles do not (403);
 *   - the dashboard replaces the "Not yet instrumented" placeholders.
 */

interface PageEnvelope<T> {
  items: T[];
  total: number;
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
interface ProviderAccountRead {
  id: string;
  name: string;
}
interface EndpointRead {
  id: string;
  name: string;
  provider_account_id: string;
}
interface PercentileRead {
  sample_count: number;
  p50_ms: number | null;
  p95_ms: number | null;
  p99_ms: number | null;
}
interface SummaryRead {
  window: { window_seconds: number };
  queue_wait: PercentileRead;
  ttft: PercentileRead;
  retry: {
    attempted_requests: number;
    retried_requests: number;
    retry_attempts: number;
    request_retry_rate: number | null;
  };
}
interface UpstreamHealthRead {
  endpoint_id: string;
  endpoint_name: string;
  succeeded_attempts: number;
  upstream_failed_attempts: number;
  ambiguous_attempts: number;
  rate_limited_attempts: number;
  upstream_success_rate: number | null;
}

const SYSTEM_ADMIN_SUBJECT = "dev-user";
const PROJECT_ADMIN_SUBJECT = "e2e-pa-a";
const VALID_UPSTREAM_MODEL = "qwen3.8-2b-distill:Q6_K";
const NONEXISTENT_UPSTREAM_MODEL = "e2e-nonexistent-model-does-not-exist";
const OLLAMA_DESTINATION = "http://192.168.22.50:11434";

let projectA: string;
let projectB: string;
let paA: string;
let credentialKey: string;
let endpointId: string;
let accountId: string;
let queueAliasName: string;
let badAliasName: string;
let badRouteId: string;
let runId: number;

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
    `e2e-observability-cred-${runId}`,
  );

  // Disable any enabled project-A budget left by prior accounting runs so this
  // spec's requests are never budget-blocked.
  await disableEnabledBudgets(admin.context, projectA);

  const account = await findByName<ProviderAccountRead>(
    admin.context,
    "/admin/v1/provider-accounts",
    "ollama-account",
  );
  if (!account) throw new Error("pre-provisioned ollama-account not found");
  accountId = account.id;

  // Dedicated two-slot endpoint for the queue-wait proof, so the six/two
  // saturation never interferes with (or is interfered with by) other specs.
  const queueEndpoint = await createEndpoint(admin.context, accountId, `e2e-obs-queue-ep-${runId}`, 2);
  endpointId = queueEndpoint;

  // Streaming TTFT route (dedicated alias on the two-slot endpoint).
  queueAliasName = `e2e-obs-queue-${runId}`;
  const queueAliasId = await createAlias(admin.context, queueAliasName);
  await createRoute(admin.context, queueAliasId, endpointId, accountId, VALID_UPSTREAM_MODEL);

  // Disposable route whose upstream model we mutate for the upstream-health proof.
  badAliasName = `e2e-obs-bad-${runId}`;
  const badAliasId = await createAlias(admin.context, badAliasName);
  badRouteId = await createRoute(
    admin.context,
    badAliasId,
    endpointId,
    accountId,
    VALID_UPSTREAM_MODEL,
  );

  await admin.context.close();
});

/**
 * Queue wait: six concurrent real requests against the two-slot endpoint. At most
 * two dispatch immediately; the rest queue until a slot frees, so admitted
 * requests produce real (non-zero) queue-wait samples.
 */
test.describe("queue-wait metrics (six requests / two slots)", () => {
  test("six concurrent SDK requests yield real ordered queue-wait percentiles", async ({
    page,
    context,
  }) => {
    test.setTimeout(600_000);
    await loginAs(page, SYSTEM_ADMIN_SUBJECT);

    const pending = Array.from({ length: 6 }, () =>
      spawnOfficialSdk({ key: credentialKey, model: queueAliasName, timeout: 360 }),
    );
    const results = await Promise.all(pending.map((p) => p.wait()));
    // Every request must eventually succeed (queued work is admitted, not rejected).
    expect(results.every((r) => r.status === "ok")).toBe(true);

    const { status, body } = await adminJson<SummaryRead>(
      context,
      "GET",
      "/admin/v1/observability/summary?window_seconds=3600",
    );
    expect(status).toBe(200);
    // The six/two cohort alone must yield at least six queue-wait samples (some
    // dispatch immediately with ~0 wait, the rest queue behind the two slots).
    expect(body.queue_wait.sample_count).toBeGreaterThanOrEqual(6);
    expect(body.queue_wait.p50_ms).not.toBeNull();
    expect(body.queue_wait.p95_ms).not.toBeNull();
    expect(body.queue_wait.p99_ms).not.toBeNull();
    expect(body.queue_wait.p50_ms!).toBeLessThanOrEqual(body.queue_wait.p95_ms!);
    expect(body.queue_wait.p95_ms!).toBeLessThanOrEqual(body.queue_wait.p99_ms!);
  });
});

test.describe("streaming TTFT (official SDK stream)", () => {
  test("a real stream produces a TTFT sample with no decrypted content", async ({
    page,
    context,
  }) => {
    test.setTimeout(240_000);
    await loginAs(page, SYSTEM_ADMIN_SUBJECT);

    const stream = await runOfficialSdk({
      key: credentialKey,
      model: queueAliasName,
      stream: true,
    });
    expect(stream.status).toBe("ok_stream");

    const { body } = await adminJson<SummaryRead>(
      context,
      "GET",
      "/admin/v1/observability/summary?window_seconds=3600",
    );
    expect(body.ttft.sample_count).toBeGreaterThanOrEqual(1);
    expect(body.ttft.p50_ms).not.toBeNull();
    expect(body.ttft.p50_ms!).toBeGreaterThan(0);
    // The summary is metadata-only; no prompt/completion/provider content leaks.
    const raw = JSON.stringify(body);
    for (const forbidden of ["payload_encrypted", "result_encrypted", "stream_event", "prompt"]) {
      expect(raw).not.toContain(forbidden);
    }
  });
});

test.describe("passive upstream health (real provider failure then success)", () => {
  test("nonexistent upstream model records failure evidence; restore records success", async ({
    page,
    context,
  }) => {
    test.setTimeout(300_000);
    await loginAs(page, SYSTEM_ADMIN_SUBJECT);

    // Baseline for this endpoint's passive health.
    const before = await endpointHealth(context, endpointId);
    const failuresBefore = before.upstream_failed_attempts;

    // 1. Point the disposable route at a deliberately nonexistent model.
    await updateRouteUpstream(context, badRouteId, NONEXISTENT_UPSTREAM_MODEL);
    const failed = await runOfficialSdk({ key: credentialKey, model: badAliasName });
    expect(failed.status).toBe("fail");

    // 2. Passive health increments the factual failure count.
    const afterFailure = await endpointHealth(context, endpointId);
    expect(afterFailure.upstream_failed_attempts).toBeGreaterThan(failuresBefore);

    // 3. Restore a valid upstream model; the SDK succeeds.
    await updateRouteUpstream(context, badRouteId, VALID_UPSTREAM_MODEL);
    const succeeded = await runOfficialSdk({ key: credentialKey, model: badAliasName });
    expect(succeeded.status).toBe("ok");

    // 4. Passive health records success evidence too.
    const afterSuccess = await endpointHealth(context, endpointId);
    expect(afterSuccess.succeeded_attempts).toBeGreaterThanOrEqual(1);

    // 5. No raw provider error body/URL/secret is exposed by the health read.
    const raw = JSON.stringify(afterFailure);
    for (const forbidden of ["http://", "https://", "body", "secret", "api_key", "token"]) {
      expect(raw).not.toContain(forbidden);
    }
  });
});

test.describe("observability RBAC", () => {
  test("system_admin sees upstream health; project roles are denied", async ({
    page,
    context,
  }) => {
    await loginAs(page, SYSTEM_ADMIN_SUBJECT);
    const asSystem = await adminJson<PageEnvelope<UpstreamHealthRead>>(
      context,
      "GET",
      "/admin/v1/observability/upstreams?window_seconds=3600",
    );
    expect(asSystem.status).toBe(200);
    expect(Array.isArray(asSystem.body.items)).toBe(true);
  });

  test("project_admin sees own-project summary but not deployment upstreams", async ({
    page,
    context,
  }) => {
    await loginAs(page, PROJECT_ADMIN_SUBJECT);
    const summary = await adminJson<SummaryRead>(
      context,
      "GET",
      "/admin/v1/observability/summary?window_seconds=3600",
    );
    expect(summary.status).toBe(200);

    const upstreams = await adminJson(context, "GET", "/admin/v1/observability/upstreams");
    expect(upstreams.status).toBe(403);

    // Cross-project explicit project id is non-enumerating (404/403); a
    // project_admin cannot read another project's summary.
    const cross = await adminJson(
      context,
      "GET",
      `/admin/v1/observability/summary?project_id=${projectB}`,
    );
    expect([403, 404]).toContain(cross.status);
  });
});

test.describe("dashboard renders real observability metrics", () => {
  test("placeholders are replaced by real metric sections", async ({ page }) => {
    await loginAs(page, SYSTEM_ADMIN_SUBJECT);
    await expect(page.getByRole("heading", { name: "Performance" })).toBeVisible();
    await expect(page.getByText("Queue wait")).toBeVisible();
    await expect(page.getByText(/Dispatch-to-first-token/)).toBeVisible();
    await expect(page.getByText("Retry rate")).toBeVisible();
    await expect(page.getByText("Upstream health")).toBeVisible();
    await expect(page.getByText("Not yet instrumented")).toHaveCount(0);

    // The window selector is present.
    await expect(page.getByRole("button", { name: "15m" })).toBeVisible();
    await expect(page.getByRole("button", { name: "1h" })).toBeVisible();
    await expect(page.getByRole("button", { name: "24h" })).toBeVisible();
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
  const { status, body } = await adminJson<ModelAliasRead>(
    context,
    "POST",
    "/admin/v1/model-aliases",
    { name, is_active: true, capabilities: ["text"] },
  );
  if (status !== 201) throw new Error(`create alias failed: HTTP ${status}`);
  return body.id;
}

async function createEndpoint(
  context: BrowserContext,
  providerAccountId: string,
  name: string,
  maxConcurrency: number,
): Promise<string> {
  const { status, body } = await adminJson<EndpointRead>(
    context,
    "POST",
    "/admin/v1/endpoints",
    {
      provider_account_id: providerAccountId,
      name,
      base_destination: OLLAMA_DESTINATION,
      max_concurrency: maxConcurrency,
    },
  );
  if (status !== 201) throw new Error(`create endpoint failed: HTTP ${status}`);
  return body.id;
}

async function createRoute(
  context: BrowserContext,
  aliasId: string,
  endpointId: string,
  accountId: string,
  upstreamModel: string,
): Promise<string> {
  const { status, body } = await adminJson<RouteBindingRead>(
    context,
    "POST",
    "/admin/v1/route-bindings",
    {
      model_alias_id: aliasId,
      provider_account_id: accountId,
      endpoint_id: endpointId,
      upstream_model: upstreamModel,
      is_active: true,
    },
  );
  if (status !== 201) throw new Error(`create route failed: HTTP ${status}`);
  return body.id;
}

async function updateRouteUpstream(
  context: BrowserContext,
  routeId: string,
  upstreamModel: string,
): Promise<void> {
  const { status } = await adminJson(context, "PATCH", `/admin/v1/route-bindings/${routeId}`, {
    upstream_model: upstreamModel,
  });
  if (status !== 200) throw new Error(`update route upstream failed: HTTP ${status}`);
}

async function endpointHealth(
  context: BrowserContext,
  endpointId: string,
): Promise<UpstreamHealthRead> {
  const res = await adminJson<PageEnvelope<UpstreamHealthRead>>(
    context,
    "GET",
    "/admin/v1/observability/upstreams?window_seconds=86400",
  );
  const row = res.body.items.find((x) => x.endpoint_id === endpointId);
  if (!row) throw new Error(`no upstream-health row for endpoint ${endpointId}`);
  return row;
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
