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

  it('labels an expired trial as expired + Paused, never still trialing', async () => {
    statusMock.mockResolvedValue({
      plan: 'trial',
      subscription_status: 'trialing',
      trial_days_left: 0,
      entitled: false,
      checkout_available: false,
    })
    render(<Subscription />)
    expect(await screen.findByText('Paused')).toBeInTheDocument()
    expect(screen.getByText('expired')).toBeInTheDocument()
    expect(screen.queryByText('trialing')).toBeNull()
    expect(screen.queryByText('Trial days left')).toBeNull()
    expect(screen.queryByText('Active')).toBeNull()
  })

  it('does not hang on Loading after a first-load fetch failure', async () => {
    statusMock.mockRejectedValue(new TypeError('Failed to fetch'))
    render(<Subscription />)
    expect(await screen.findByText('Failed to fetch')).toBeInTheDocument()
    expect(screen.queryByText('Loading…')).not.toBeInTheDocument()
    expect(screen.getByText('Could not load subscription status.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Retry' })).toBeInTheDocument()
  })

  it('Retry after a first-load billing failure paints a live trial', async () => {
    statusMock
      .mockRejectedValueOnce(new TypeError('Failed to fetch'))
      .mockResolvedValue({
        plan: 'trial',
        subscription_status: 'trialing',
        trial_days_left: 3,
        entitled: true,
        checkout_available: false,
      })
    render(<Subscription />)
    fireEvent.click(await screen.findByRole('button', { name: 'Retry' }))
    expect(await screen.findByText('Trial days left')).toBeInTheDocument()
    expect(screen.getByText('trialing')).toBeInTheDocument()
    expect(screen.queryByText('Failed to fetch')).not.toBeInTheDocument()
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
