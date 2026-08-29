/**
 * Smoke test: the subscription view must tell the truth about billing
 * (market-readiness audit, gap #3 — no "being connected" hand-waving).
 */
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { Subscription } from '../App'

const statusMock = vi.fn()
const checkoutMock = vi.fn()
const portalMock = vi.fn()

vi.mock('../api/factory', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/factory')>()
  return {
    ...actual,
    billing: {
      status: () => statusMock(),
      checkout: () => checkoutMock(),
      portal: () => portalMock(),
    },
  }
})

describe('Subscription', () => {
  beforeEach(() => {
    statusMock.mockReset()
    checkoutMock.mockReset()
    portalMock.mockReset()
    statusMock.mockResolvedValue({
      plan: 'trial',
      subscription_status: 'trialing',
      trial_days_left: 3,
      entitled: true,
      checkout_available: false,
    })
  })

  it('renders structured plan status', async () => {
    render(<Subscription />)
    expect(await screen.findByText('Trial days left')).toBeInTheDocument()
    expect(screen.getByText('trialing')).toBeInTheDocument()
    expect(screen.getByText('Active')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Upgrade' })).toBeInTheDocument()
    expect(
      screen.getByText(/Payments are not connected on this deployment yet/i),
    ).toBeInTheDocument()
  })

  it('labels Factory access Paused from the entitled flag', async () => {
    statusMock.mockResolvedValue({
      plan: 'trial',
      subscription_status: 'trialing',
      trial_days_left: 0,
      entitled: false,
      checkout_available: false,
    })
    render(<Subscription />)
    expect(await screen.findByText('Paused')).toBeInTheDocument()
    expect(screen.queryByText('Active')).toBeNull()
  })

  it('never uses hand-waving "being connected" copy', async () => {
    render(<Subscription />)
    await screen.findByText('Trial days left')
    expect(screen.queryByText(/being connected/i)).toBeNull()
  })

  it('checkout failure states the truth plainly', async () => {
    checkoutMock.mockRejectedValue(new Error('stripe_not_configured'))
    render(<Subscription />)
    fireEvent.click(await screen.findByRole('button', { name: 'Upgrade' }))
    await waitFor(() =>
      expect(
        screen.getByText(/Payments are not connected on this deployment yet/i),
      ).toBeInTheDocument(),
    )
  })
})
