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

async function mockVerifiedFactory(
  page: Page,
  billing?: {
    entitled?: boolean
    trial_days_left?: number
    plan?: string
    subscription_status?: string
  },
) {
  await page.addInitScript(() => {
    localStorage.removeItem('cerebrum.factory.token')
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
        ok: true,
        billing: {
          plan: billing?.plan ?? 'trial',
          subscription_status: billing?.subscription_status ?? 'trialing',
          trial_days_left: billing?.trial_days_left ?? 3,
          entitled: billing?.entitled ?? true,
          checkout_available: false,
        },
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
          current_phase: { id: 'WRITER', label: 'Platform manufacturer' },
          phase_index: 3,
          phase_total: 5,
          next_phase: { id: 'TESTER', label: 'Acceptance inspector' },
          phase_progress: { done: 2, total: 4, fraction: 0.5, stage: 'handlers' },
          last_event: 'wrote handler inventory_management',
          last_event_age_s: 8,
          stale: false,
          activity: 'wrote handler inventory_management',
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

test('Floor finished state offers the zip download on the generate surface', async ({ page }) => {
  await mockVerifiedFactory(page)
  await page.unroute('**/v1/sessions/sess_e2e_floor/product')
  await page.route('**/v1/sessions/sess_e2e_floor/product', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        blueprint: BLUEPRINT,
        blueprint_approved: true,
        generation: {
          product_id: 'vineyard',
          engine: 'runner',
          inputs_hash: 'abc123',
          output_dir: '/tmp/vineyard',
          triggered_by: 'chat_llm',
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
  await expect(page.getByRole('heading', { name: 'Coding agent finished' })).toBeVisible({
    timeout: 20_000,
  })
  await expect(page.getByText('Finished — 13 artifacts; 6 templated. Download ready.')).toBeVisible()
  await expect(page.getByRole('button', { name: 'Download platform export (.zip)' })).toBeEnabled()
  await expect(page.getByRole('button', { name: 'Open Your Platforms' })).toBeEnabled()
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
  await expect(page.getByText('Finished — 13 artifacts; 6 templated')).toBeVisible()
  await expect(page.getByRole('button', { name: 'Download platform export (.zip)' })).toBeEnabled()
})

test('Floor hydrate of a pending draft does not flash a leftover coder-takeover banner', async ({ page }) => {
  await mockVerifiedFactory(page)
  await page.unroute('**/v1/sessions/sess_e2e_floor/product')
  await page.route('**/v1/sessions/sess_e2e_floor/product', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        blueprint: BLUEPRINT,
        blueprint_approved: false,
        generation: {
          engine: 'runner',
          product_id: 'old-winery',
          triggered_by: 'chat_llm',
        },
      }),
    })
  })

  await page.goto('/')
  await expect(page.getByRole('heading', { name: 'Factory Floor' })).toBeVisible({ timeout: 20_000 })
  await expect(page.getByText('Vineyard Platform')).toBeVisible()
  await expect(page.getByRole('button', { name: 'Approve & build' })).toBeEnabled()
  await expect(page.getByRole('status')).toHaveCount(0)
  await expect(page.getByPlaceholder(/Try:/)).toBeEnabled()
})

test('Subscription and Account render plan and verified email', async ({ page }) => {
  await mockVerifiedFactory(page)
  await page.goto('/')
  await expect(page.getByRole('heading', { name: 'Factory Floor' })).toBeVisible({ timeout: 20_000 })

  await page.getByRole('button', { name: 'Subscription' }).click()
  await expect(page.getByRole('heading', { name: 'Subscription' })).toBeVisible()
  expect(new URL(page.url()).pathname).toBe('/subscription')
  await expect(page.getByText('Trial days left')).toBeVisible()
  await expect(page.getByText('trialing')).toBeVisible()
  await expect(page.getByRole('button', { name: 'Upgrade' })).toBeEnabled()
  await expect(page.getByText(/being connected/i)).toHaveCount(0)
  await expect(page.getByText(/Payments are not connected on this deployment yet/i)).toBeVisible()
  const trialCard = page.locator('.plan-card[data-plan="trial"]')
  const factoryCard = page.locator('.plan-card[data-plan="factory"]')
  await expect(trialCard).toBeVisible()
  await expect(trialCard).toHaveAttribute('aria-current', 'true')
  await expect(trialCard.getByText('Current')).toBeVisible()
  await expect(factoryCard).toBeVisible()
  await expect(factoryCard).not.toHaveAttribute('aria-current', 'true')
  await expect(factoryCard.getByText('Current')).toHaveCount(0)

  await page.getByRole('button', { name: 'Account' }).click()
  await expect(page.getByRole('heading', { name: 'Account' })).toBeVisible()
  expect(new URL(page.url()).pathname).toBe('/account')
  await expect(page.getByText('e2e.floor@factory.dev')).toBeVisible()
  await expect(page.getByText('Yes')).toBeVisible()
  await expect(page.getByText('acct_e2e_floor')).toBeVisible()
  await expect(page.getByRole('button', { name: 'Sign out' })).toBeVisible()
})

test('paused Factory access hard-disables Floor Send and does not offer Approve & build', async ({
  page,
}) => {
  await mockVerifiedFactory(page, { entitled: false, trial_days_left: 0 })
  await page.unroute('**/v1/sessions/sess_e2e_floor/product')
  await page.route('**/v1/sessions/sess_e2e_floor/product', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        blueprint: BLUEPRINT,
        blueprint_approved: false,
      }),
    })
  })
  let chatPosts = 0
  await page.route('**/v1/sessions/sess_e2e_floor/chat', async (route) => {
    chatPosts += 1
    await route.fulfill({ status: 500, body: 'should not send' })
  })

  await page.goto('/')
  await expect(page.getByRole('heading', { name: 'Factory Floor' })).toBeVisible({ timeout: 20_000 })
  await expect(page.getByText('Vineyard Platform')).toBeVisible()
  await expect(page.getByRole('button', { name: 'Send' })).toBeDisabled()
  await expect(page.getByPlaceholder('Factory access is paused')).toBeDisabled()
  await expect(page.getByText('Factory access is paused.')).toBeVisible()
  await expect(page.getByRole('button', { name: /Approve & build/ })).toHaveCount(0)
  expect(chatPosts).toBe(0)

  await page.getByRole('button', { name: 'Subscription' }).click()
  await expect(page.getByText('Paused')).toBeVisible()
  await expect(page.getByText('expired')).toBeVisible()
  await expect(page.getByText('trialing')).toHaveCount(0)
  await expect(page.getByText('Trial days left')).toHaveCount(0)
  await expect(page.locator('.plan-card[data-plan="trial"]')).toHaveAttribute('aria-current', 'true')
})

test('Factory / Active subscription does not present Trial as a second current plan', async ({
  page,
}) => {
  await mockVerifiedFactory(page, {
    plan: 'factory',
    subscription_status: 'active',
    entitled: true,
  })
  await page.goto('/')
  await expect(page.getByRole('heading', { name: 'Factory Floor' })).toBeVisible({ timeout: 20_000 })
  await page.getByRole('button', { name: 'Subscription' }).click()
  await expect(page.getByRole('heading', { name: 'Subscription' })).toBeVisible()
  await expect(page.locator('.kv dt', { hasText: 'Plan' }).locator('+ dd')).toHaveText('factory')
  await expect(page.locator('.kv dt', { hasText: 'Status' }).locator('+ dd')).toHaveText('active')
  await expect(page.locator('.plan-card[data-plan="trial"]')).toHaveCount(0)
  const factoryCard = page.locator('.plan-card[data-plan="factory"]')
  await expect(factoryCard).toHaveAttribute('aria-current', 'true')
  await expect(factoryCard.getByText('Current')).toBeVisible()
  await expect(page.getByRole('button', { name: 'Upgrade' })).toHaveCount(0)
  await expect(page.getByRole('button', { name: 'Manage billing' })).toBeVisible()
  await expect(page.getByText(/Payments are not connected on this deployment yet/i)).toBeVisible()
})

test('signed-in /login and /register stay on Floor with a one-line notice', async ({ page }) => {
  await mockVerifiedFactory(page)
  await page.goto('/login')
  await expect(page.getByRole('heading', { name: 'Factory Floor' })).toBeVisible({ timeout: 20_000 })
  await expect(page.getByText('Already signed in.')).toBeVisible()
  await expect(page.getByRole('heading', { name: 'Sign in' })).toHaveCount(0)
  expect(new URL(page.url()).pathname).toBe('/')

  await page.goto('/register')
  await expect(page.getByRole('heading', { name: 'Factory Floor' })).toBeVisible({ timeout: 20_000 })
  await expect(page.getByText('Already signed in.')).toBeVisible()
  await expect(page.getByRole('heading', { name: 'Create your account' })).toHaveCount(0)
  expect(new URL(page.url()).pathname).toBe('/')
})

test('deep links /account /subscription /platforms render the matching views', async ({ page }) => {
  await mockVerifiedFactory(page)
  await page.goto('/account')
  await expect(page.getByRole('heading', { name: 'Account' })).toBeVisible({ timeout: 20_000 })
  await expect(page.getByRole('heading', { name: 'Factory Floor' })).toHaveCount(0)
  await expect(page.getByRole('button', { name: 'Account' })).toHaveAttribute('aria-current', 'page')
  expect(new URL(page.url()).pathname).toBe('/account')

  await page.goto('/subscription')
  await expect(page.getByRole('heading', { name: 'Subscription' })).toBeVisible({ timeout: 20_000 })
  await expect(page.getByRole('button', { name: 'Subscription' })).toHaveAttribute(
    'aria-current',
    'page',
  )
  expect(new URL(page.url()).pathname).toBe('/subscription')

  await page.goto('/platforms')
  await expect(page.getByRole('heading', { name: 'Your Platforms' })).toBeVisible({
    timeout: 20_000,
  })
  await expect(page.getByRole('button', { name: 'Your Platforms' })).toHaveAttribute(
    'aria-current',
    'page',
  )
  expect(new URL(page.url()).pathname).toBe('/platforms')
})

test('narrow viewport keeps nav icons and accessible labels (no blank pills)', async ({ page }) => {
  await mockVerifiedFactory(page)
  await page.setViewportSize({ width: 390, height: 844 })
  await page.goto('/subscription')
  await expect(page.getByRole('heading', { name: 'Subscription' })).toBeVisible({ timeout: 20_000 })

  const nav = page.getByRole('navigation', { name: 'Factory navigation' })
  await expect(nav).toBeVisible()
  for (const label of ['Factory Floor', 'Your Platforms', 'Subscription', 'Account']) {
    const btn = page.getByRole('button', { name: label })
    await expect(btn).toBeVisible()
    await expect(btn.locator('.nav-icon')).toBeVisible()
    const box = await btn.boundingBox()
    expect(box).not.toBeNull()
    expect((box?.width ?? 0) > 0 && (box?.height ?? 0) > 0).toBeTruthy()
    const text = (await btn.innerText()).trim()
    expect(text.length).toBeGreaterThan(0)
  }
  await expect(page.getByRole('button', { name: 'Subscription' })).toHaveAttribute(
    'aria-current',
    'page',
  )
})
