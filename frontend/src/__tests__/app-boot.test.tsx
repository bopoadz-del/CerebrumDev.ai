/**
 * Boot path: unverified email must not be shown as "Factory unreachable".
 */
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiError, isDomainStoreUnreachable, isEmailNotVerifiedError } from '../api/factory'
import App from '../App'

const {
  meMock,
  verifyEmailMock,
  resendMock,
  listMock,
  createMock,
  domainsListMock,
  productGetMock,
  billingStatusMock,
} = vi.hoisted(() => ({
  meMock: vi.fn(),
  verifyEmailMock: vi.fn(),
  resendMock: vi.fn(),
  listMock: vi.fn(),
  createMock: vi.fn(),
  domainsListMock: vi.fn(),
  productGetMock: vi.fn(),
  billingStatusMock: vi.fn(),
}))

vi.mock('../api/factory', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/factory')>()
  return {
    ...actual,
    getEmail: () => 'new@factory.dev',
    setSession: vi.fn(),
    clearSession: vi.fn(),
    signOut: vi.fn().mockResolvedValue(undefined),
    auth: {
      ...actual.auth,
      me: (...args: unknown[]) => meMock(...args),
      verifyEmail: (...args: unknown[]) => verifyEmailMock(...args),
      resendVerification: (...args: unknown[]) => resendMock(...args),
    },
    sessions: {
      list: (...args: unknown[]) => listMock(...args),
      create: (...args: unknown[]) => createMock(...args),
    },
    domains: {
      list: (...args: unknown[]) => domainsListMock(...args),
    },
    product: {
      ...actual.product,
      get: (...args: unknown[]) => productGetMock(...args),
    },
    billing: {
      ...actual.billing,
      status: (...args: unknown[]) => billingStatusMock(...args),
    },
  }
})

describe('boot error classes', () => {
  it('matches 403 email_not_verified and ignores other failures', () => {
    expect(isEmailNotVerifiedError(new ApiError(403, 'email_not_verified'))).toBe(true)
    expect(isEmailNotVerifiedError(new ApiError(503, 'backend down'))).toBe(false)
    expect(isEmailNotVerifiedError(new ApiError(503, 'Domain store unreachable'))).toBe(false)
    expect(isEmailNotVerifiedError(new ApiError(401, 'unauthorized'))).toBe(false)
    expect(isEmailNotVerifiedError(new Error('email_not_verified'))).toBe(false)
  })

  it('matches domain-store 503 and ignores a dead API', () => {
    expect(isDomainStoreUnreachable(new ApiError(503, 'Domain store unreachable'))).toBe(true)
    expect(isDomainStoreUnreachable(new ApiError(503, 'backend down'))).toBe(false)
    expect(isDomainStoreUnreachable(new ApiError(403, 'email_not_verified'))).toBe(false)
  })
})

describe('App boot', () => {
  beforeEach(() => {
    window.history.pushState(null, '', '/')
    meMock.mockReset()
    verifyEmailMock.mockReset()
    resendMock.mockReset()
    listMock.mockReset()
    createMock.mockReset()
    domainsListMock.mockReset()
    domainsListMock.mockResolvedValue({ domains: [] })
    productGetMock.mockReset()
    productGetMock.mockResolvedValue({})
    billingStatusMock.mockReset()
    billingStatusMock.mockResolvedValue({ entitled: true })
  })

  it('shows verify-email — not Factory unreachable — when boot is 403 email_not_verified', async () => {
    meMock.mockRejectedValue(new ApiError(403, 'email_not_verified'))
    render(<App />)
    expect(await screen.findByRole('heading', { name: 'Verify your email' })).toBeInTheDocument()
    expect(screen.getByText(/new@factory.dev/)).toBeInTheDocument()
    expect(screen.getByPlaceholderText('verification token')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Verify email' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Resend verification email' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Sign out' })).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'Factory unreachable' })).not.toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'Factory Floor' })).not.toBeInTheDocument()
    expect(listMock).not.toHaveBeenCalled()
    expect(domainsListMock).not.toHaveBeenCalled()
  })

  it('opens the floor after the user verifies', async () => {
    meMock
      .mockRejectedValueOnce(new ApiError(403, 'email_not_verified'))
      .mockResolvedValue({ email: 'new@factory.dev', email_verified: true })
    listMock.mockResolvedValue([])
    createMock.mockResolvedValue({ session_id: 'sess_verified' })
    verifyEmailMock.mockResolvedValue({ ok: true, email_verified: true })

    render(<App />)
    expect(await screen.findByRole('heading', { name: 'Verify your email' })).toBeInTheDocument()

    fireEvent.change(screen.getByPlaceholderText('verification token'), {
      target: { value: 'vtk_from_inbox' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Verify email' }))

    await waitFor(() => expect(verifyEmailMock).toHaveBeenCalledWith('vtk_from_inbox'))
    expect(await screen.findByRole('heading', { name: 'Factory Floor' })).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'Factory unreachable' })).not.toBeInTheDocument()
    expect(createMock).toHaveBeenCalled()
  })

  it('resend fills a dev_token when the API provides one', async () => {
    meMock.mockRejectedValue(new ApiError(403, 'email_not_verified'))
    resendMock.mockResolvedValue({
      ok: true,
      already_verified: false,
      verification: {
        mode: 'dev_token',
        email_sent: false,
        note: 'SMTP not configured',
        dev_verification_token: 'cdv_from_resend',
      },
    })
    render(<App />)
    expect(await screen.findByRole('heading', { name: 'Verify your email' })).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Resend verification email' }))
    await waitFor(() => expect(resendMock).toHaveBeenCalled())
    expect(screen.getByTestId('dev-verification-token')).toHaveTextContent('cdv_from_resend')
    expect(screen.getByPlaceholderText('verification token')).toHaveValue('cdv_from_resend')
    expect(screen.queryByRole('heading', { name: 'Factory unreachable' })).not.toBeInTheDocument()
  })

  it('shows sign-in when the cookie session is missing', async () => {
    meMock.mockRejectedValue(new ApiError(401, 'Invalid or missing API key'))
    render(<App />)
    expect(await screen.findByRole('heading', { name: 'Sign in' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Enter the factory' })).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'Factory Floor' })).not.toBeInTheDocument()
    expect(listMock).not.toHaveBeenCalled()
  })

  it('still shows Factory unreachable when the API is actually down', async () => {
    meMock.mockRejectedValue(new ApiError(503, 'backend down'))
    render(<App />)
    expect(await screen.findByRole('heading', { name: 'Factory unreachable' })).toBeInTheDocument()
    expect(screen.getByText('backend down')).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'Verify your email' })).not.toBeInTheDocument()
    expect(screen.queryByText('Domain store unreachable')).not.toBeInTheDocument()
  })

  it('does not full-page-error a Failed to fetch race on /register', async () => {
    window.history.pushState(null, '', '/register')
    meMock.mockRejectedValue(new TypeError('Failed to fetch'))
    render(<App />)
    expect(await screen.findByRole('heading', { name: 'Create your account' })).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'Factory unreachable' })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Create your account' })).toBeInTheDocument()
  })

  it('does not full-page-error a proxy 502 on /register', async () => {
    window.history.pushState(null, '', '/register')
    meMock.mockRejectedValue(new ApiError(502, 'Bad Gateway'))
    render(<App />)
    expect(await screen.findByRole('heading', { name: 'Create your account' })).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'Factory unreachable' })).not.toBeInTheDocument()
  })

  it('still shows Factory unreachable for a persistent Failed to fetch on the floor', async () => {
    meMock.mockRejectedValue(new TypeError('Failed to fetch'))
    render(<App />)
    expect(await screen.findByRole('heading', { name: 'Factory unreachable' })).toBeInTheDocument()
    expect(screen.getByText('Failed to fetch')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Retry' })).toBeInTheDocument()
  })

  it('keeps Floor open and toasts when the domain store is 503', async () => {
    meMock.mockResolvedValue({ email: 'new@factory.dev', email_verified: true })
    listMock.mockResolvedValue([])
    createMock.mockResolvedValue({ session_id: 'sess_ok' })
    domainsListMock.mockRejectedValue(new ApiError(503, 'Domain store unreachable'))
    render(<App />)
    expect(await screen.findByRole('heading', { name: 'Factory Floor' })).toBeInTheDocument()
    expect(await screen.findByText('Domain store unreachable')).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'Factory unreachable' })).not.toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'Verify your email' })).not.toBeInTheDocument()
  })

  it('hard-disables Floor Send when billing entitled is false', async () => {
    meMock.mockResolvedValue({
      email: 'new@factory.dev',
      email_verified: true,
      account_id: 'acct_paused',
    })
    listMock.mockResolvedValue([{ session_id: 'sess_ok' }])
    billingStatusMock.mockResolvedValue({
      plan: 'trial',
      subscription_status: 'trialing',
      trial_days_left: 0,
      entitled: false,
    })
    render(<App />)
    expect(await screen.findByRole('heading', { name: 'Factory Floor' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Send' })).toBeDisabled()
    expect(screen.getByPlaceholderText('Factory access is paused')).toBeDisabled()
    expect(screen.getByText('Factory access is paused.')).toBeInTheDocument()
  })

  it('signed-in visit to /login stays on Floor with an already-signed-in notice', async () => {
    window.history.pushState(null, '', '/login')
    meMock.mockResolvedValue({
      email: 'new@factory.dev',
      email_verified: true,
      account_id: 'acct_boot',
    })
    listMock.mockResolvedValue([{ session_id: 'sess_ok' }])
    render(<App />)
    expect(await screen.findByRole('heading', { name: 'Factory Floor' })).toBeInTheDocument()
    expect(screen.getByText('Already signed in.')).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'Sign in' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Enter the factory' })).not.toBeInTheDocument()
    expect(window.location.pathname).toBe('/')
  })

  it('signed-in visit to /register stays on Floor and does not sign out', async () => {
    window.history.pushState(null, '', '/register')
    meMock.mockResolvedValue({
      email: 'new@factory.dev',
      email_verified: true,
      account_id: 'acct_boot',
    })
    listMock.mockResolvedValue([{ session_id: 'sess_ok' }])
    const { clearSession, signOut } = await import('../api/factory')
    render(<App />)
    expect(await screen.findByRole('heading', { name: 'Factory Floor' })).toBeInTheDocument()
    expect(screen.getByText('Already signed in.')).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'Create your account' })).not.toBeInTheDocument()
    expect(window.location.pathname).toBe('/')
    expect(clearSession).not.toHaveBeenCalled()
    expect(signOut).not.toHaveBeenCalled()
  })

  it('Account Verified is Yes on first paint from boot /me — never No', async () => {
    meMock.mockResolvedValue({
      email: 'new@factory.dev',
      email_verified: true,
      account_id: 'acct_boot',
    })
    listMock.mockResolvedValue([{ session_id: 'sess_ok' }])
    render(<App />)
    await screen.findByRole('heading', { name: 'Factory Floor' })
    fireEvent.click(screen.getByRole('button', { name: 'Account' }))
    expect(screen.getByRole('heading', { name: 'Account' })).toBeInTheDocument()
    expect(screen.getByText('Yes')).toBeInTheDocument()
    expect(screen.queryByText('No')).not.toBeInTheDocument()
    expect(screen.getByText('acct_boot')).toBeInTheDocument()
  })
})
