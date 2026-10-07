import { expect, test } from '@playwright/test'

// Runs against a server started with BENCHTRACE_AUTH=1 at AUTH_URL, with user AUTH_EMAIL / AUTH_PASSWORD.
const url = process.env.AUTH_URL
test.skip(!url, 'AUTH_URL not set')

test('sign in, create an API key, store a secret, sign out', async ({ page }) => {
  await page.goto(url!)
  await expect(page.getByText('Sign in to your workspace')).toBeVisible()
  await page.locator('#email').fill(process.env.AUTH_EMAIL!)
  await page.locator('#password').fill('wrong password!')
  await page.getByRole('button', { name: 'Sign in' }).click()
  await expect(page.getByText('Email or password is incorrect.')).toBeVisible()
  await page.locator('#password').fill(process.env.AUTH_PASSWORD!)
  await page.getByRole('button', { name: 'Sign in' }).click()
  await expect(page.getByRole('heading', { name: 'Runs' })).toBeVisible()

  await page.getByRole('link', { name: 'Settings' }).click()
  await page.locator('#key-name').fill('ci')
  await page.getByRole('button', { name: 'Create key' }).click()
  await expect(page.getByText(/Copy this key now/)).toBeVisible()
  await expect(page.locator('code').filter({ hasText: /^bt_/ })).toBeVisible()

  await page.locator('#secret-value').fill('sk-test-value')
  await page.getByRole('button', { name: 'Save' }).click()
  await expect(page.getByRole('cell', { name: 'OPENAI_API_KEY', exact: true })).toBeVisible()
  if (process.env.SHOTS_DIR) await page.screenshot({ path: `${process.env.SHOTS_DIR}/settings.png`, fullPage: true })

  await page.getByRole('button', { name: 'Sign out' }).click()
  await expect(page.getByText('Sign in to your workspace')).toBeVisible()
})
