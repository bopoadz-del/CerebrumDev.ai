import { expect, test, type Page } from '@playwright/test'

/**
 * Live Factory UI against PLAYWRIGHT_BASE_URL / BASE_URL (production:
 * https://www.cerebrum-dev.com). These are browser tests, not API smoke.
 */

test.describe.configure({ mode: 'serial' })

async function assertAuthGate(page: Page) {
  await expect(page.getByRole('heading', { name: 'CerebrumDev.ai' })).toBeVisible()
  await expect(page.getByRole('heading', { name: 'Sign in' })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Enter the factory' })).toBeVisible()
  await expect(page.getByPlaceholder('you@company.com')).toBeVisible()
}

test('auth gate loads on the live UI', async ({ page }) => {
  await page.goto('/')
  await assertAuthGate(page)
  await expect(page.getByText('One account. Tell the factory. Receive your platform.')).toBeVisible()
})

test('register, forgot-password, and verify screens are reachable', async ({ page }) => {
  await page.goto('/')
  await page.getByRole('button', { name: 'Create an account' }).click()
  await expect(page.getByRole('heading', { name: 'Create your account' })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Create your account' })).toBeVisible()

  await page.getByRole('button', { name: 'Forgot password?' }).click()
  await expect(page.getByRole('heading', { name: 'Reset your password' })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Send reset token' })).toBeVisible()

  await page.getByRole('button', { name: 'Have a verification token?' }).click()
  await expect(page.getByRole('heading', { name: 'Verify your email' })).toBeVisible()
  await expect(page.getByPlaceholder('verification token')).toBeVisible()

  await page.getByRole('button', { name: 'Sign in' }).click()
  await assertAuthGate(page)
})

test('invalid login hits the live API and surfaces an error', async ({ page }) => {
  await page.goto('/')
  await page.getByPlaceholder('you@company.com').fill(`no-such-user-${Date.now()}@example.com`)
  await page.getByPlaceholder('password (8+ characters)').fill('definitely-wrong-password')
  await page.getByRole('button', { name: 'Enter the factory' }).click()
  await expect(page.locator('.error-box')).toBeVisible({ timeout: 30_000 })
  await expect(page.getByRole('heading', { name: 'Sign in' })).toBeVisible()
})

test('register a throwaway account and land on verify-email', async ({ page }) => {
  const email = `pw.prod.${Date.now()}@example.com`
  const password = `Pw${Date.now()}Aa1`

  await page.goto('/')
  await page.getByRole('button', { name: 'Create an account' }).click()
  await page.getByPlaceholder('you@company.com').fill(email)
  await page.getByPlaceholder('password (8+ characters)').fill(password)
  await page.getByRole('button', { name: 'Create your account' }).click()

  const verifyHeading = page.getByRole('heading', { name: 'Verify your email' })
  const inboxNotice = page.getByText(/Check your inbox/i)
  const floorHeading = page.getByRole('heading', { name: 'Factory Floor' })
  const factoryUnreachable = page.getByRole('heading', { name: 'Factory unreachable' })

  await expect(verifyHeading.or(inboxNotice).or(floorHeading)).toBeVisible({
    timeout: 45_000,
  })
  await expect(factoryUnreachable).toHaveCount(0)

  if (await floorHeading.isVisible().catch(() => false)) {
    await expect(page.getByText(/This is the factory floor/i)).toBeVisible()
    return
  }

  await expect(verifyHeading).toBeVisible()
  await expect(page.getByPlaceholder('verification token')).toBeVisible()
  await expect(page.getByRole('button', { name: 'Verify email' })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Resend verification email' })).toBeVisible()
  await expect(page.getByText(/check your inbox|verification link|paste the token/i)).toBeVisible()
})
