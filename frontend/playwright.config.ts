import { defineConfig, devices } from '@playwright/test'

/**
 * Browser E2E for the v2 web console.
 *
 * These tests target a live stack (api + worker + postgres + deterministic dev
 * OIDC IdP + web). The environment must already be bootstrapped so the
 * deterministic IdP subject resolves to a principal with an admin role.
 *
 * Configure with:
 *   WEB_BASE_URL  — the web console origin (default http://127.0.0.1:8080)
 *
 * Run with `npm run test:e2e`.
 */
export default defineConfig({
  testDir: './e2e',
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: 60_000,
  reporter: [['list']],
  use: {
    baseURL: process.env.WEB_BASE_URL ?? 'http://127.0.0.1:8080',
    trace: 'retain-on-failure',
  },
  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
  ],
})
