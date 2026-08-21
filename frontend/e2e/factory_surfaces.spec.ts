import { expect, test, type Page } from '@playwright/test'

/**
 * Factory Floor / Your Platforms / Subscription / Account — beyond verify-email.
 * Routes are mocked so these pin the SPA contract without SMTP or a live coder.
 */

const BLUEPRINT = {
  product_name: 'Vineyard Platform',
  vertical: 'winery',
  summary: 'Tank, barrel and club operations for a family winery.',
  drafting_mode: 'architect_llm',
  capabilities: [
    { id: 'fermentation_tanks', description: 'Track tanks', strategy_hint: 'GENERATE' },
    { id: 'audit', description: 'Audit trails', strategy_hint: 'REUSE', block_ids: ['audit'] },
  ],
}

async function mockVerifiedFactory(page: Page) {
  await page.addInitScript(() => {
    localStorage.setItem('cerebrum.factory.token', 'cdt_e2e_verified')
    localStorage.setItem('cerebrum.factory.email', 'e2e.floor@factory.dev')
  })
  await page.route('**/v1/auth/me', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        email: 'e2e.floor@factory.dev',
        email_verified: true,
        account_id: 'acct_e2e_floor',
      }),
    })
  })
  await page.route(/\/v1\/sessions\/?$/, async (route) => {
    const method = route.request().method()
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(
        method === 'GET' ? { sessions: [{ session_id: 'sess_e2e_floor' }] } : { session_id: 'sess_e2e_floor' },
      ),
    })
  })
  await page.route(/\/v1\/domains(\/|$)/, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ domains: [] }),
    })
  })
  await page.route('**/v1/sessions/sess_e2e_floor/product', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ session_id: 'sess_e2e_floor' }),
    })
  })
  await page.route('**/v1/billing/status', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        plan: 'trial',
        subscription_status: 'trialing',
        trial_days_left: 3,
        entitled: true,
        checkout_available: false,
      }),
    })
  })
}

function sse(event: string, data: unknown): string {
  return `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`
}

test('Floor drafts a feature list and Approve & build starts the coding agent', async ({ page }) => {
  await mockVerifiedFactory(page)
  await page.route('**/v1/sessions/sess_e2e_floor/chat', async (route) => {
    const posted = route.request().postDataJSON() as { message?: string }
    const message = posted.message ?? ''
    let body = sse('done', '')
    if (message === 'approve') {
      body =
        sse('generation', {
          summary:
            'The chat LLM started the coding agent. Build started for vineyard: the coding agent has taken over the floor and is writing 2 capability(ies).',
          triggered_by: 'chat_llm',
          generation: { engine: 'runner', product_id: 'vineyard', triggered_by: 'chat_llm' },
        }) + sse('done', '')
    } else {
      body =
        sse('blueprint', {
          summary: 'Blueprint drafted: Vineyard Platform (winery). Drafted by the architect LLM.',
          blueprint: BLUEPRINT,
        }) + sse('done', '')
    }
    await route.fulfill({
      status: 200,
      contentType: 'text/event-stream',
      body,
    })
  })
  await page.route('**/v1/sessions/sess_e2e_floor/product/build-status', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        ok: true,
        product_id: 'vineyard',
        build: {
          state: 'building',
          phases_done: 2,
          phases_total: 5,
          activity: 'WRITER handler 2/4',
          completed: ['COLLECTOR', 'CLONER'],
        },
      }),
    })
  })

  await page.goto('/')
  await expect(page.getByRole('heading', { name: 'Factory Floor' })).toBeVisible({ timeout: 20_000 })
  await page.getByPlaceholder(/Try:/).fill('Build me a vineyard management platform for a family winery')
  await page.getByRole('button', { name: 'Send' }).click()
  await expect(page.getByText('architect LLM')).toBeVisible()
  await expect(page.getByText('Vineyard Platform')).toBeVisible()
  await expect(page.getByRole('button', { name: 'Approve & build' })).toBeEnabled()
  await page.getByRole('button', { name: 'Approve & build' }).click()
  await expect(page.getByText('coding agent', { exact: true })).toBeVisible()
  await expect(page.getByText('chat LLM', { exact: true })).toBeVisible()
  await expect(page.getByRole('status')).toContainText(/Coding agent has taken over/)
  await expect(page.getByText('WRITER', { exact: true })).toBeVisible()
  await expect(page.getByPlaceholder(/coding agent has taken over/i)).toBeDisabled()
})

test('Your Platforms shows coder authorship and a zip download', async ({ page }) => {
  await mockVerifiedFactory(page)
  await page.unroute('**/v1/sessions/sess_e2e_floor/product')
  await page.route('**/v1/sessions/sess_e2e_floor/product', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        blueprint: { product_name: 'Vineyard Platform', vertical: 'winery' },
        generation: {
          product_id: 'vineyard',
          engine: 'runner',
          inputs_hash: 'abc123',
          output_dir: '/tmp/vineyard',
        },
      }),
    })
  })
  await page.route('**/v1/sessions/sess_e2e_floor/product/build-status', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        ok: true,
        product_id: 'vineyard',
        build: {
          state: 'succeeded',
          authorship: { artifacts: 19, agent_written: 13, templated: 6 },
        },
      }),
    })
  })

  await page.goto('/')
  await expect(page.getByRole('heading', { name: 'Factory Floor' })).toBeVisible({ timeout: 20_000 })
  await page.getByRole('button', { name: 'Your Platforms' }).click()
  await expect(page.getByRole('heading', { name: 'Your Platforms' })).toBeVisible()
  await expect(page.getByRole('heading', { name: 'vineyard' })).toBeVisible()
  await expect(page.getByText('runner', { exact: true })).toBeVisible()
  await expect(page.getByText('Coding agent wrote 13 of 19 artifacts.')).toBeVisible()
  await expect(page.getByRole('button', { name: 'Download platform export (.zip)' })).toBeEnabled()
})

test('Subscription and Account render plan and verified email', async ({ page }) => {
  await mockVerifiedFactory(page)
  await page.goto('/')
  await expect(page.getByRole('heading', { name: 'Factory Floor' })).toBeVisible({ timeout: 20_000 })

  await page.getByRole('button', { name: 'Subscription' }).click()
  await expect(page.getByRole('heading', { name: 'Subscription' })).toBeVisible()
  await expect(page.getByText('Trial days left')).toBeVisible()
  await expect(page.getByText('trialing')).toBeVisible()
  await expect(page.getByRole('button', { name: 'Upgrade' })).toBeVisible()
  await expect(page.getByText(/being connected/i)).toHaveCount(0)

  await page.getByRole('button', { name: 'Account' }).click()
  await expect(page.getByRole('heading', { name: 'Account' })).toBeVisible()
  await expect(page.getByText('e2e.floor@factory.dev')).toBeVisible()
  await expect(page.getByText('Yes')).toBeVisible()
  await expect(page.getByRole('button', { name: 'Sign out' })).toBeVisible()
})
