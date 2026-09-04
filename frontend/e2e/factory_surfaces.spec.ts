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

const LETTINGS_BLUEPRINT = {
  product_name: 'Residential Lettings Platform',
  vertical: 'residential_lettings',
  summary: 'Factory golden for a residential-lettings platform.',
  drafting_mode: 'golden_lettings',
  drafting_note: 'Drafted from the golden residential-lettings blueprint.',
  capabilities: [
    {
      id: 'unit_registry_and_vacancy_tracking',
      description: 'Register a residential unit and track vacancy.',
      strategy_hint: 'REUSE',
      block_ids: ['analytics'],
    },
    {
      id: 'viewing_management',
      description: 'Record a viewing and notify the assigned team.',
      strategy_hint: 'COMPOSE',
      block_ids: ['team', 'workflow', 'notification'],
    },
    {
      id: 'maintenance_issue_tracking',
      description: 'Raise a maintenance issue against a unit.',
      strategy_hint: 'REUSE',
      block_ids: ['team'],
    },
    {
      id: 'tenancy_application_pipeline',
      description: 'Record a tenancy application and attach documents.',
      strategy_hint: 'COMPOSE',
      block_ids: ['team', 'document_engine'],
    },
  ],
}

const LETTINGS_BUILDING = {
  ok: true,
  product_id: 'residential-lettings',
  build: {
    state: 'building',
    phases_done: 2,
    phases_total: 5,
    current_phase: { id: 'WRITER', label: 'Platform manufacturer' },
    phase_index: 3,
    phase_total: 5,
    next_phase: { id: 'TESTER', label: 'Acceptance inspector' },
    phase_progress: { done: 2, total: 4, fraction: 0.5, stage: 'handlers' },
    last_event: 'wrote handler viewing_management',
    last_event_age_s: 8,
    stale: false,
    activity: 'wrote handler viewing_management',
    completed: ['COLLECTOR', 'CLONER'],
  },
}

async function expectNoGoldFinished(page: Page) {
  await expect(page.getByText(/Finished —/)).toHaveCount(0)
  await expect(page.getByText(/Download ready/)).toHaveCount(0)
  await expect(page.getByRole('heading', { name: 'Coding agent finished' })).toHaveCount(0)
  await expect(page.getByRole('button', { name: 'Download platform export (.zip)' })).toHaveCount(0)
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

test('Floor New session starts a clean workspace after a failed run', async ({ page }) => {
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
          product_id: 'residential-lettings',
          engine: 'runner',
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
        product_id: 'residential-lettings',
        build: {
          state: 'failed',
          cycle: 'pilot',
          outcome: 'FAILED_BUDGET_SPENT',
          pilot_ready: false,
          detail:
            'rework budget of 3 exhausted; TESTER gate still failing: PRODUCT (pilot-marked suite): suite is red',
        },
      }),
    })
  })
  const created: string[] = []
  await page.unroute(/\/v1\/sessions\/?$/)
  await page.route(/\/v1\/sessions\/?$/, async (route) => {
    const method = route.request().method()
    if (method === 'POST') {
      created.push('sess_e2e_fresh')
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ session_id: 'sess_e2e_fresh' }),
      })
      return
    }
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ sessions: [{ session_id: 'sess_e2e_floor' }] }),
    })
  })
  await page.route('**/v1/sessions/sess_e2e_fresh/product', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ session_id: 'sess_e2e_fresh' }),
    })
  })

  await page.goto('/')
  await expect(page.getByRole('heading', { name: 'Factory Floor' })).toBeVisible({ timeout: 20_000 })
  await expect(page.getByRole('heading', { name: 'Coding agent stopped' })).toBeVisible()
  await expect(page.getByTestId('floor-failed-pill')).toContainText('Pilot suite failed')
  await expect(page.getByText(/taken over the floor/i)).toHaveCount(0)
  await expect(page.locator('.bp-drafting-mode', { hasText: 'coding agent' })).toHaveCount(0)
  await expect(page.getByRole('button', { name: 'Download platform export (.zip)' })).toHaveCount(0)
  await expect(page.getByRole('button', { name: 'New session' })).toBeEnabled()
  await page.getByRole('button', { name: 'Start a new product' }).click()
  await expect.poll(() => created).toEqual(['sess_e2e_fresh'])
  await expect(page.getByRole('heading', { name: 'Coding agent stopped' })).toHaveCount(0)
  await expect(page.getByTestId('floor-failed-pill')).toHaveCount(0)
  await expect(page.getByPlaceholder(/Try:/)).toBeEnabled()
  await expect(page.getByText('session sess_e2e_fre…')).toBeVisible()
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
          pilot_ready: true,
          cycle: 'pilot',
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
  await expect(page.getByRole('button', { name: 'Continue to pilot' })).toHaveCount(0)
})

test('Floor code-cycle SUCCESS is a prototype with Continue to pilot — never Finished', async ({
  page,
}) => {
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
          product_id: 'residential-lettings',
          engine: 'runner',
          inputs_hash: 'b36090a4',
          output_dir: '/tmp/residential-lettings',
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
        product_id: 'residential-lettings',
        build: {
          state: 'succeeded',
          pilot_ready: false,
          cycle: 'code',
          auto_pilot: false,
          authorship: { artifacts: 24, agent_written: 11, templated: 13 },
        },
      }),
    })
  })
  const continuePosts: string[] = []
  await page.route('**/v1/sessions/sess_e2e_floor/chat', async (route) => {
    const posted = route.request().postDataJSON() as { message?: string }
    continuePosts.push(posted.message ?? '')
    await route.fulfill({
      status: 200,
      contentType: 'text/event-stream',
      body:
        sse('generation', {
          summary: 'Opening pilot cycle for residential-lettings on the same workspace/hash.',
          triggered_by: 'chat_llm',
          generation: {
            engine: 'runner',
            product_id: 'residential-lettings',
            triggered_by: 'chat_llm',
          },
        }) + sse('done', ''),
    })
  })

  await page.goto('/')
  await expect(page.getByRole('heading', { name: 'Code-cycle prototype ready' })).toBeVisible({
    timeout: 20_000,
  })
  await expect(page.getByText(/Code-cycle prototype — 11 artifacts; 13 templated. Not yet pilot-ready/)).toBeVisible()
  await expect(page.getByText(/Finished —/)).toHaveCount(0)
  await expect(page.getByText(/Download ready/)).toHaveCount(0)
  await expect(page.getByRole('heading', { name: 'Coding agent finished' })).toHaveCount(0)
  await expect(page.getByRole('button', { name: 'Download code-cycle prototype (.zip)' })).toBeEnabled()
  await expect(page.getByRole('button', { name: 'Download platform export (.zip)' })).toHaveCount(0)
  await page.getByRole('button', { name: 'Continue to pilot' }).click()
  await expect(page.getByText('Opening pilot cycle for residential-lettings')).toBeVisible()
  expect(continuePosts).toEqual(['continue'])
})

test('Your Platforms shows a loading skeleton — never empty-state — while product fetch is in flight', async ({
  page,
}) => {
  await mockVerifiedFactory(page)
  await page.unroute('**/v1/sessions/sess_e2e_floor/product')
  let releaseProduct: (() => void) | undefined
  const productGate = new Promise<void>((resolve) => {
    releaseProduct = resolve
  })
  await page.route('**/v1/sessions/sess_e2e_floor/product', async (route) => {
    await productGate
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
          pilot_ready: true,
          cycle: 'pilot',
          authorship: { artifacts: 19, agent_written: 13, templated: 6 },
        },
      }),
    })
  })

  await page.goto('/')
  await expect(page.getByRole('heading', { name: 'Factory Floor' })).toBeVisible({ timeout: 20_000 })
  await page.getByRole('button', { name: 'Your Platforms' }).click()
  await expect(page.getByRole('heading', { name: 'Your Platforms' })).toBeVisible()
  await expect(page.getByTestId('loading-skeleton')).toBeVisible()
  await expect(page.getByText('No platform built yet')).toHaveCount(0)
  releaseProduct?.()
  await expect(page.getByRole('heading', { name: 'vineyard' })).toBeVisible()
  await expect(page.getByTestId('loading-skeleton')).toHaveCount(0)
  await expect(page.getByText('No platform built yet')).toHaveCount(0)
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
          pilot_ready: true,
          cycle: 'pilot',
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
  await expect(page.getByRole('button', { name: 'Continue to pilot on Factory Floor' })).toHaveCount(0)
})

test('Your Platforms code-cycle SUCCESS is a prototype — never Finished', async ({ page }) => {
  await mockVerifiedFactory(page)
  await page.unroute('**/v1/sessions/sess_e2e_floor/product')
  await page.route('**/v1/sessions/sess_e2e_floor/product', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        blueprint: { product_name: 'Cerebrum Residential Lettings Hub', vertical: 'lettings' },
        generation: {
          product_id: 'residential-lettings',
          engine: 'runner',
          inputs_hash: 'b36090a4',
          output_dir: '/tmp/residential-lettings',
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
        product_id: 'residential-lettings',
        build: {
          state: 'succeeded',
          pilot_ready: false,
          cycle: 'code',
          auto_pilot: false,
          authorship: { artifacts: 24, agent_written: 11, templated: 13 },
        },
      }),
    })
  })

  await page.goto('/platforms')
  await expect(page.getByRole('heading', { name: 'Your Platforms' })).toBeVisible({
    timeout: 20_000,
  })
  await expect(page.getByRole('heading', { name: 'residential-lettings' })).toBeVisible()
  await expect(
    page.getByText('Code-cycle prototype — 11 artifacts; 13 templated. Not yet pilot-ready'),
  ).toBeVisible()
  await expect(page.getByText(/Finished —/)).toHaveCount(0)
  await expect(page.getByText(/Download ready/)).toHaveCount(0)
  await expect(page.getByTestId('platforms-pilot-ready-pill')).toHaveCount(0)
  await expect(page.getByTestId('platforms-prototype-pill')).toContainText('Code-cycle prototype')
  await expect(page.getByRole('button', { name: 'Download code-cycle prototype (.zip)' })).toBeEnabled()
  await expect(page.getByRole('button', { name: 'Download platform export (.zip)' })).toHaveCount(0)
  await expect(page.getByRole('button', { name: 'Continue to pilot on Factory Floor' })).toBeEnabled()
})

test('Your Platforms shows Pilot suite failed — never a success Download — when TESTER is red', async ({
  page,
}) => {
  await mockVerifiedFactory(page)
  await page.unroute('**/v1/sessions/sess_e2e_floor/product')
  await page.route('**/v1/sessions/sess_e2e_floor/product', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        blueprint: { product_name: 'Cerebrum Residential Lettings Hub', vertical: 'lettings' },
        generation: {
          product_id: 'residential-lettings',
          engine: 'runner',
          inputs_hash: 'b36090a4',
          output_dir: '/tmp/residential-lettings',
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
        product_id: 'residential-lettings',
        build: {
          state: 'failed',
          cycle: 'pilot',
          outcome: 'FAILED_BUDGET_SPENT',
          pilot_ready: false,
          detail:
            'rework budget of 3 exhausted; TESTER gate still failing: PRODUCT (pilot-marked suite): suite is red',
          findings: ['FAILED tests/test_smoke.py::test_every_capability_executes_end_to_end'],
        },
      }),
    })
  })

  await page.goto('/platforms')
  await expect(page.getByTestId('platforms-failed-pill')).toBeVisible({ timeout: 20_000 })
  await expect(page.getByTestId('platforms-failed-badge')).toContainText(/Build failed/)
  await expect(page.getByRole('button', { name: 'Download platform export (.zip)' })).toHaveCount(0)
  const refused = page.getByRole('button', { name: 'Export (.zip) — pilot suite failed' })
  await expect(refused).toBeVisible()
  await expect(refused).toBeDisabled()
})

test('Floor CODE_GREEN level_grade never says Finished or founding-ready', async ({ page }) => {
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
          product_id: 'residential-lettings',
          engine: 'runner',
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
        product_id: 'residential-lettings',
        build: {
          state: 'succeeded',
          pilot_ready: false,
          cycle: 'code',
          authorship: { artifacts: 24, agent_written: 11, templated: 13 },
          level_grade: {
            level: 'CODE_GREEN',
            pilot_ready: false,
            founding_customer_ready: false,
            three_gate: { CODE: 'PASS', PRODUCT: 'NOT_RUN', STORE: 'NOT_RUN' },
          },
        },
      }),
    })
  })
  await page.goto('/')
  await expect(page.getByRole('heading', { name: 'Code-cycle prototype ready' })).toBeVisible({
    timeout: 20_000,
  })
  await expect(page.getByTestId('floor-prototype-pill')).toContainText('Code-green (prototype)')
  await expect(page.getByTestId('floor-gate-product')).toContainText('PRODUCT NOT RUN')
  await expect(page.getByText(/Finished —/)).toHaveCount(0)
  await expect(page.getByText(/Download ready/)).toHaveCount(0)
  await expect(page.getByText(/Founding-customer-ready/)).toHaveCount(0)
  await expect(page.getByRole('button', { name: 'Download platform export (.zip)' })).toHaveCount(0)
  await expect(page.getByRole('button', { name: 'Download code-cycle prototype (.zip)' })).toBeEnabled()
})

test('Your Platforms FOUNDING_CUSTOMER_READY shows founding chip and gold export', async ({
  page,
}) => {
  await mockVerifiedFactory(page)
  await page.unroute('**/v1/sessions/sess_e2e_floor/product')
  await page.route('**/v1/sessions/sess_e2e_floor/product', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        blueprint: { product_name: 'Cerebrum Residential Lettings Hub', vertical: 'lettings' },
        generation: {
          product_id: 'residential-lettings',
          engine: 'runner',
          inputs_hash: 'b36090a4',
          output_dir: '/tmp/residential-lettings',
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
        product_id: 'residential-lettings',
        build: {
          state: 'succeeded',
          pilot_ready: true,
          cycle: 'pilot',
          authorship: { artifacts: 28, agent_written: 22, templated: 6 },
          level_grade: {
            level: 'FOUNDING_CUSTOMER_READY',
            pilot_ready: true,
            founding_customer_ready: true,
            three_gate: { CODE: 'PASS', PRODUCT: 'PASS', STORE: 'PASS' },
          },
        },
      }),
    })
  })
  await page.goto('/platforms')
  await expect(page.getByTestId('platforms-pilot-ready-pill')).toContainText('Founding-customer-ready', {
    timeout: 20_000,
  })
  await expect(page.getByTestId('platforms-gate-product')).toContainText('PRODUCT PASS')
  await expect(page.getByTestId('platforms-gate-store')).toContainText('STORE PASS')
  await expect(page.getByRole('button', { name: 'Download platform export (.zip)' })).toBeEnabled()
})

test('Floor and Your Platforms name a pending golden residential_lettings draft — never a gold Download', async ({
  page,
}) => {
  await mockVerifiedFactory(page)
  await page.unroute('**/v1/sessions/sess_e2e_floor/product')
  await page.route('**/v1/sessions/sess_e2e_floor/product', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        blueprint: LETTINGS_BLUEPRINT,
        blueprint_approved: false,
      }),
    })
  })

  await page.goto('/')
  await expect(page.getByRole('heading', { name: 'Factory Floor' })).toBeVisible({ timeout: 20_000 })
  await expect(page.getByTestId('bp-drafting-mode')).toHaveText(/golden blueprint/i)
  await expect(page.getByText('Residential Lettings Platform')).toBeVisible()
  await expect(page.locator('.bp-vertical')).toHaveText('residential_lettings')
  await expect(page.locator('.bp-strategy.REUSE')).toHaveCount(2)
  await expect(page.locator('.bp-strategy.COMPOSE')).toHaveCount(2)
  await expect(page.getByRole('button', { name: 'Approve & build' })).toBeEnabled()
  await expectNoGoldFinished(page)

  await page.getByRole('button', { name: 'Your Platforms', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'Your Platforms' })).toBeVisible()
  await expect(page.getByTestId('platforms-empty-state')).toBeVisible()
  await expect(page.getByText('No platform built yet')).toBeVisible()
  await expect(page.getByTestId('platforms-draft-hint')).toContainText(
    'Draft: Residential Lettings Platform (residential_lettings)',
  )
  await expect(page.getByRole('button', { name: 'Download platform export (.zip)' })).toHaveCount(0)
  await expect(page.getByRole('button', { name: 'Building…' })).toHaveCount(0)
})

test('Floor keeps coding chrome after golden lettings Approve before the first status poll', async ({
  page,
}) => {
  await mockVerifiedFactory(page)
  let approved = false
  let releaseChat: (() => void) | undefined
  const chatGate = new Promise<void>((resolve) => {
    releaseChat = resolve
  })
  let releaseStatus: (() => void) | undefined
  const statusGate = new Promise<void>((resolve) => {
    releaseStatus = resolve
  })
  await page.unroute('**/v1/sessions/sess_e2e_floor/product')
  await page.route('**/v1/sessions/sess_e2e_floor/product', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(
        approved
          ? {
              blueprint: LETTINGS_BLUEPRINT,
              blueprint_approved: true,
              generation: {
                product_id: 'residential-lettings',
                engine: 'runner',
                triggered_by: 'chat_llm',
              },
            }
          : {
              blueprint: LETTINGS_BLUEPRINT,
              blueprint_approved: false,
            },
      ),
    })
  })
  await page.route('**/v1/sessions/sess_e2e_floor/chat', async (route) => {
    approved = true
    await chatGate
    await route.fulfill({
      status: 200,
      contentType: 'text/event-stream',
      body:
        sse('generation', {
          summary:
            'The chat LLM started the coding agent. Build started for residential-lettings: the coding agent has taken over the floor and is writing 4 capability(ies).',
          triggered_by: 'chat_llm',
          generation: {
            engine: 'runner',
            product_id: 'residential-lettings',
            triggered_by: 'chat_llm',
          },
        }) + sse('done', ''),
    })
  })
  await page.route('**/v1/sessions/sess_e2e_floor/product/build-status', async (route) => {
    await statusGate
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(LETTINGS_BUILDING),
    })
  })

  await page.goto('/')
  await expect(page.getByRole('button', { name: 'Approve & build' })).toBeEnabled({
    timeout: 20_000,
  })
  await page.getByRole('button', { name: 'Approve & build' }).click()
  await expect(page.getByTestId('floor-coder-takeover')).toBeVisible()
  await expect(page.getByRole('heading', { name: 'Coding agent has taken over' })).toBeVisible()
  await expectNoGoldFinished(page)
  releaseChat?.()
  await expect(page.getByText('coding agent', { exact: true })).toBeVisible()
  await expect(page.getByTestId('floor-coder-takeover')).toBeVisible()
  await expectNoGoldFinished(page)
  releaseStatus?.()
  await expect(page.getByText('WRITER', { exact: true })).toBeVisible()
  await expect(page.getByRole('heading', { name: 'Coding agent has taken over' })).toBeVisible()
  await expectNoGoldFinished(page)
})

test('Floor 510s coder call inside the 40 min wall stays Building — never STOPPED or Download', async ({
  page,
}) => {
  await mockVerifiedFactory(page)
  await page.unroute('**/v1/sessions/sess_e2e_floor/product')
  await page.route('**/v1/sessions/sess_e2e_floor/product', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        blueprint: LETTINGS_BLUEPRINT,
        blueprint_approved: true,
        generation: {
          product_id: 'residential-lettings',
          engine: 'runner',
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
        product_id: 'residential-lettings',
        build: {
          state: 'building',
          cycle: 'pilot',
          pilot_ready: false,
          current_phase: { id: 'WRITER', label: 'Platform manufacturer' },
          phase_index: 3,
          phase_total: 5,
          last_event: 'calling coder LLM for tenancy_application_pipeline',
          last_event_age_s: 510,
          model_call_in_progress: true,
          model_call_deadline_s: 2400,
          stale: true,
        },
      }),
    })
  })

  await page.goto('/')
  await expect(page.getByTestId('floor-coder-takeover')).toBeVisible({ timeout: 20_000 })
  await expect(page.getByRole('heading', { name: 'Coding agent has taken over' })).toBeVisible()
  await expect(page.getByText(/still inside 2400s watchdog/)).toBeVisible()
  await expect(page.getByRole('heading', { name: 'Coding agent stopped' })).toHaveCount(0)
  await expect(page.getByText(/coder LLM timed out/)).toHaveCount(0)
  await expectNoGoldFinished(page)

  await page.getByRole('button', { name: 'Your Platforms', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'Your Platforms' })).toBeVisible()
  const building = page.getByRole('button', { name: 'Building…' })
  await expect(building).toBeVisible()
  await expect(building).toBeDisabled()
  await expect(page.getByRole('button', { name: 'Download platform export (.zip)' })).toHaveCount(0)
})

test('Floor overdue coder call is STOPPED and Platforms refuses export', async ({ page }) => {
  await mockVerifiedFactory(page)
  await page.unroute('**/v1/sessions/sess_e2e_floor/product')
  await page.route('**/v1/sessions/sess_e2e_floor/product', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        blueprint: LETTINGS_BLUEPRINT,
        blueprint_approved: true,
        generation: {
          product_id: 'residential-lettings',
          engine: 'runner',
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
        product_id: 'residential-lettings',
        build: {
          state: 'failed',
          cycle: 'pilot',
          pilot_ready: false,
          detail:
            'coder LLM timed out after 510s (deadline 480s) — calling coder LLM for class_and_event_scheduling',
        },
      }),
    })
  })

  await page.goto('/')
  await expect(page.getByRole('heading', { name: 'Coding agent stopped' })).toBeVisible({
    timeout: 20_000,
  })
  await expect(page.getByRole('button', { name: 'Download platform export (.zip)' })).toHaveCount(0)
  await expect(page.getByRole('button', { name: 'Building…' })).toHaveCount(0)

  await page.getByRole('button', { name: 'Your Platforms', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'Your Platforms' })).toBeVisible()
  const refused = page.getByRole('button', { name: 'Export (.zip) — pilot suite failed' })
  await expect(refused).toBeVisible()
  await expect(refused).toBeDisabled()
  await expect(page.getByRole('button', { name: 'Download platform export (.zip)' })).toHaveCount(0)
})

test('Your Platforms stays Building after golden lettings Approve — never a gold Download', async ({
  page,
}) => {
  await mockVerifiedFactory(page)
  await page.unroute('**/v1/sessions/sess_e2e_floor/product')
  await page.route('**/v1/sessions/sess_e2e_floor/product', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        blueprint: LETTINGS_BLUEPRINT,
        blueprint_approved: true,
        generation: {
          product_id: 'residential-lettings',
          engine: 'runner',
          triggered_by: 'chat_llm',
        },
      }),
    })
  })
  await page.route('**/v1/sessions/sess_e2e_floor/product/build-status', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(LETTINGS_BUILDING),
    })
  })

  await page.goto('/')
  await expect(page.getByTestId('floor-coder-takeover')).toBeVisible({ timeout: 20_000 })
  await expect(page.getByRole('heading', { name: 'Coding agent has taken over' })).toBeVisible()
  await expectNoGoldFinished(page)

  await page.getByRole('button', { name: 'Your Platforms', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'Your Platforms' })).toBeVisible()
  await expect(page.getByTestId('platforms-empty-state')).toHaveCount(0)
  await expect(page.getByText('No platform built yet')).toHaveCount(0)
  await expect(page.getByRole('heading', { name: 'residential-lettings' })).toBeVisible()
  const building = page.getByRole('button', { name: 'Building…' })
  await expect(building).toBeVisible()
  await expect(building).toBeDisabled()
  await expect(page.getByRole('button', { name: 'Download platform export (.zip)' })).toHaveCount(0)
  await expect(page.getByText(/Finished —/)).toHaveCount(0)
  await expect(page.getByText(/Download ready/)).toHaveCount(0)
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
  await expect(page.getByRole('button', { name: 'Send password reset' })).toBeEnabled()
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
  await expect(page.getByText(/Upgrade still says so/i)).toHaveCount(0)
  await expect(page.getByText(/Your current access is unaffected/i)).toBeVisible()
})

test('Account Send password reset posts forgot-password for the signed-in email', async ({
  page,
}) => {
  await mockVerifiedFactory(page)
  const forgotPosts: { email?: string }[] = []
  await page.route('**/v1/auth/forgot-password', async (route) => {
    const posted = route.request().postDataJSON() as { email?: string }
    forgotPosts.push(posted)
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        ok: true,
        message: 'If the email is registered, a reset link follows.',
      }),
    })
  })
  await page.goto('/account')
  await expect(page.getByRole('heading', { name: 'Account' })).toBeVisible({ timeout: 20_000 })
  await expect(page.getByText('e2e.floor@factory.dev')).toBeVisible()
  await page.getByRole('button', { name: 'Send password reset' }).click()
  await expect(page.getByText('If the email is registered, a reset link follows.')).toBeVisible()
  expect(forgotPosts).toEqual([{ email: 'e2e.floor@factory.dev' }])
  await expect(page.getByRole('heading', { name: 'Account' })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Sign out' })).toBeVisible()
  await expect(page.getByRole('heading', { name: 'Sign in' })).toHaveCount(0)
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
