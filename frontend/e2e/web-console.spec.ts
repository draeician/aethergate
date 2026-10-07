import { test, expect } from '@playwright/test'

/**
 * Web-console browser E2E against the deterministic local OIDC provider.
 *
 * Preconditions (see docs/web-console.md):
 * - full v2 stack running (api + worker + postgres + dev-oidc-idp + web);
 * - the dev IdP subject is linked to a principal that has an admin role
 *   (otherwise login succeeds but the dashboard is empty/403).
 */

test('unauthenticated visit lands on the OIDC login page with no credential input', async ({
  page,
}) => {
  await page.goto('/')
  await expect(page.getByRole('link', { name: /sign in with oidc/i })).toBeVisible()
  await expect(page.locator('input[type="password"]')).toHaveCount(0)
  await expect(page.locator('input[type="text"]')).toHaveCount(0)
})

test('OIDC sign-in reaches the operator dashboard without leaking secrets', async ({
  page,
}) => {
  await page.goto('/')
  await page.getByRole('link', { name: /sign in with oidc/i }).click()

  // The backend completes the code+PKCE flow and 302s to /auth/callback, then
  // the frontend bootstraps the session and lands on the dashboard.
  await expect(page.getByRole('heading', { name: 'Dashboard' })).toBeVisible({
    timeout: 30_000,
  })

  // No CSRF/session/OIDC secret may appear in the URL, history, or web storage.
  expect(page.url()).not.toContain('csrf')
  expect(page.url()).not.toContain('code=')
  expect(page.url()).not.toContain('state=')
  expect(page.url()).not.toContain('id_token')

  const storage = await page.evaluate(() => ({
    local: { ...window.localStorage },
    session: { ...window.sessionStorage },
  }))
  const all = JSON.stringify(storage)
  expect(all).not.toContain('ag_session')
  expect(all).not.toContain('ag_csrf')
})

test('signed-in console renders live queue data', async ({ page }) => {
  await page.goto('/')
  // If not authenticated, complete login first.
  if (await page.getByRole('link', { name: /sign in with oidc/i }).isVisible()) {
    await page.getByRole('link', { name: /sign in with oidc/i }).click()
  }
  await expect(page.getByRole('heading', { name: 'Dashboard' })).toBeVisible({
    timeout: 30_000,
  })
  await expect(page.getByText('Queued')).toBeVisible()
  await expect(page.getByText('In Flight')).toBeVisible()
  await expect(page.getByRole('heading', { name: 'Not yet instrumented' })).toBeVisible()
})

test('logout revokes the server session and returns to login', async ({ page }) => {
  await page.goto('/')
  if (await page.getByRole('link', { name: /sign in with oidc/i }).isVisible()) {
    await page.getByRole('link', { name: /sign in with oidc/i }).click()
  }
  await expect(page.getByRole('heading', { name: 'Dashboard' })).toBeVisible({
    timeout: 30_000,
  })

  await page.getByRole('button', { name: 'Sign Out' }).click()
  await expect(page.getByRole('link', { name: /sign in with oidc/i })).toBeVisible({
    timeout: 30_000,
  })

  // A direct session probe after logout must be unauthenticated (server revoked).
  const sessionState = await page.request.get('/admin/v1/auth/session')
  expect(sessionState.status()).toBe(401)
})

