import { test, expect } from "@playwright/test";
import { loginAs, adminJson, runOfficialSdk, WEB_BASE_URL } from "./fixtures";

/**
 * Offline deterministic Alpha golden-path browser smoke (CI).
 *
 * Targets the CI stack: real AetherGate API + worker + web console, the
 * deterministic DevOidcIdp, and the in-repo fake Ollama upstream. No Internet,
 * no model download, no provider credentials.
 *
 * Preconditions (set up by the CI workflow / scripts/ci/golden_path.py):
 * - the deterministic admin subject logs in as system_admin;
 * - a catalog route ``golden-model`` -> fake Ollama exists;
 * - an inference credential raw key is provided via GOLDEN_INFERENCE_KEY.
 */

const SYSTEM_ADMIN_SUBJECT = "dev-user";

// This spec is the offline CI golden-path smoke. It requires the CI-only fake
// Ollama catalog route and inference key. On the live nomnom stack (no
// GOLDEN_INFERENCE_KEY) it skips so it never interferes with the live suite.
test.skip(
  !process.env.GOLDEN_INFERENCE_KEY,
  "GOLDEN_INFERENCE_KEY not set (offline CI-only golden path)",
);

test.describe("alpha golden path (offline CI)", () => {
  test("OIDC browser login reaches the dashboard with live data", async ({ page }) => {
    await loginAs(page, SYSTEM_ADMIN_SUBJECT);
    await expect(page.getByRole("heading", { name: "Dashboard" })).toBeVisible();
    await expect(page.getByText("Queued", { exact: true })).toBeVisible();
    await expect(page.getByRole("heading", { name: "Performance" })).toBeVisible();

    // A non-secret CSRF cookie is present; no session/csrf value is in the URL.
    expect(page.url()).not.toContain("csrf");
    expect(page.url()).not.toContain("code=");
  });

  test("login readiness survives a delayed unauthenticated session probe", async ({ page }) => {
    // Regression for the golden-path loginAs race: the client renders
    // "Loading…" while its /auth/session probe is in flight, so a single
    // immediate isVisible() poll after goto() can see neither the sign-in
    // link nor the Dashboard and skip the click. Delay the probe to force that
    // transition deterministically; the helper must wait for the sign-in link
    // and click it rather than timing out.
    await page.route("**/admin/v1/auth/session", async (route) => {
      await new Promise((resolve) => setTimeout(resolve, 1500));
      await route.continue();
    });
    await loginAs(page, SYSTEM_ADMIN_SUBJECT);
    await expect(page.getByRole("heading", { name: "Dashboard" })).toBeVisible();
  });

  test("catalog route to the fake Ollama upstream exists", async ({ page, context }) => {
    await loginAs(page, SYSTEM_ADMIN_SUBJECT);
    const aliases = await adminJson<{ items: Array<{ id: string; name: string }> }>(
      context,
      "GET",
      "/admin/v1/model-aliases?limit=200",
    );
    expect(aliases.status).toBe(200);
    const golden = aliases.body.items.find((a) => a.name === "golden-model");
    expect(golden).toBeTruthy();
  });

  test("official SDK non-stream + stream against the fake upstream", async ({ page }) => {
    await loginAs(page, SYSTEM_ADMIN_SUBJECT);
    const key = process.env.GOLDEN_INFERENCE_KEY;
    expect(key, "GOLDEN_INFERENCE_KEY must be set by the CI workflow").toBeTruthy();

    const nonStream = await runOfficialSdk({ key: key!, model: "golden-model" });
    expect(nonStream.status).toBe("ok");
    const stream = await runOfficialSdk({ key: key!, model: "golden-model", stream: true });
    expect(stream.status).toBe("ok_stream");

    // The fake upstream is reachable only through the gateway web origin.
    expect(WEB_BASE_URL).toBeTruthy();
  });

  test("official SDK vision non-stream + stream on the image-capable alias", async ({ page }) => {
    await loginAs(page, SYSTEM_ADMIN_SUBJECT);
    const key = process.env.GOLDEN_INFERENCE_KEY;
    expect(key, "GOLDEN_INFERENCE_KEY must be set by the CI workflow").toBeTruthy();

    const nonStream = await runOfficialSdk({
      key: key!,
      model: "golden-vision-model",
      vision: true,
    });
    expect(nonStream.status).toBe("ok");
    const stream = await runOfficialSdk({
      key: key!,
      model: "golden-vision-model",
      vision: true,
      stream: true,
    });
    expect(stream.status).toBe("ok_stream");
  });

  test("remote image URL and text-only alias image are rejected with 400", async ({ page }) => {
    await loginAs(page, SYSTEM_ADMIN_SUBJECT);
    const key = process.env.GOLDEN_INFERENCE_KEY;
    expect(key, "GOLDEN_INFERENCE_KEY must be set by the CI workflow").toBeTruthy();

    const remote = await runOfficialSdk({
      key: key!,
      model: "golden-vision-model",
      vision: true,
      expect400: true,
    });
    expect(remote.status).toBe("ok_400");

    const textOnly = await runOfficialSdk({
      key: key!,
      model: "golden-model",
      vision: true,
      expect400: true,
    });
    expect(textOnly.status).toBe("ok_400");
  });

  test("explicit auto detail succeeds; non-default detail is rejected with 400", async ({
    page,
  }) => {
    await loginAs(page, SYSTEM_ADMIN_SUBJECT);
    const key = process.env.GOLDEN_INFERENCE_KEY;
    expect(key, "GOLDEN_INFERENCE_KEY must be set by the CI workflow").toBeTruthy();

    const auto = await runOfficialSdk({
      key: key!,
      model: "golden-vision-model",
      vision: true,
      detail: "auto",
    });
    expect(auto.status).toBe("ok");

    const rejected = await runOfficialSdk({
      key: key!,
      model: "golden-vision-model",
      vision: true,
      detail: "low",
      expect400: true,
    });
    expect(rejected.status).toBe("ok_400");
  });
});
