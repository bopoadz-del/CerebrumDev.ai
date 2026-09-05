/**
 * Boot path: unverified email must not be shown as "Factory unreachable".
 */
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import {
  ApiError,
  clearSession,
  isDomainStoreUnreachable,
  isEmailNotVerifiedError,
  isUnauthenticatedError,
  signOut,
} from '../api/factory'
import App, {
  pathFromView,
  resolveBootSession,
  sessionQueryParam,
  viewFromPath,
} from '../App'

const {
  meMock,
  verifyEmailMock,
  resendMock,
  listMock,
  createMock,
  domainsListMock,
  productGetMock,
  billingStatusMock,
  getHealthMock,
} = vi.hoisted(() => ({
  meMock: vi.fn(),
  verifyEmailMock: vi.fn(),
  resendMock: vi.fn(),
  listMock: vi.fn(),
  createMock: vi.fn(),
  domainsListMock: vi.fn(),
  productGetMock: vi.fn(),
  billingStatusMock: vi.fn(),
  getHealthMock: vi.fn(),
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
    getHealth: (...args: unknown[]) => getHealthMock(...args),
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

  it('treats missing-session 401/403 as Sign in, not Factory unreachable', () => {
    expect(isUnauthenticatedError(new ApiError(401, 'Invalid or missing API key'))).toBe(true)
    expect(
      isUnauthenticatedError(
        new ApiError(403, 'This endpoint requires an account credential (login token or API key)'),
      ),
    ).toBe(true)
    expect(isUnauthenticatedError(new ApiError(403, 'email_not_verified'))).toBe(false)
    expect(isUnauthenticatedError(new ApiError(503, 'backend down'))).toBe(false)
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
    getHealthMock.mockReset()
    getHealthMock.mockResolvedValue({
      factory_code_cli: { available: true, credentials_file_present: true },
    })
    ;(clearSession as ReturnType<typeof vi.fn>).mockClear()
    ;(signOut as ReturnType<typeof vi.fn>).mockClear()
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

  it('shows sign-in for legacy /me 403 credential-required (not Factory unreachable)', async () => {
    meMock.mockRejectedValue(
      new ApiError(403, 'This endpoint requires an account credential (login token or API key)'),
    )
    render(<App />)
    expect(await screen.findByRole('heading', { name: 'Sign in' })).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'Factory unreachable' })).not.toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'Factory Floor' })).not.toBeInTheDocument()
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
    expect(screen.queryByRole('button', { name: 'New session' })).not.toBeInTheDocument()
  })

  it('shows New session on Floor and switches to the created session', async () => {
    meMock.mockResolvedValue({
      email: 'new@factory.dev',
      email_verified: true,
      account_id: 'acct_boot',
    })
    listMock.mockResolvedValue([{ session_id: 'sess_ok' }])
    createMock.mockResolvedValue({ session_id: 'sess_fresh' })
    render(<App />)
    expect(await screen.findByRole('heading', { name: 'Factory Floor' })).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'New session' }))
    await waitFor(() => expect(createMock).toHaveBeenCalled())
    expect(await screen.findByText(/session sess_fresh/)).toBeInTheDocument()
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
    expect(window.location.pathname).toBe('/account')
  })

  it('direct /account boot renders Account — not Factory Floor', async () => {
    window.history.pushState(null, '', '/account')
    meMock.mockResolvedValue({
      email: 'new@factory.dev',
      email_verified: true,
      account_id: 'acct_boot',
    })
    listMock.mockResolvedValue([{ session_id: 'sess_ok' }])
    render(<App />)
    expect(await screen.findByRole('heading', { name: 'Account' })).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'Factory Floor' })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Account' })).toHaveAttribute('aria-current', 'page')
    expect(screen.getByRole('button', { name: 'Factory Floor' })).not.toHaveAttribute(
      'aria-current',
    )
    expect(window.location.pathname).toBe('/account')
  })

  it('direct /subscription and /platforms boot to the matching views', async () => {
    meMock.mockResolvedValue({
      email: 'new@factory.dev',
      email_verified: true,
      account_id: 'acct_boot',
    })
    listMock.mockResolvedValue([{ session_id: 'sess_ok' }])
    billingStatusMock.mockResolvedValue({
      plan: 'trial',
      subscription_status: 'trialing',
      trial_days_left: 3,
      entitled: true,
      checkout_available: false,
    })

    window.history.pushState(null, '', '/subscription')
    const sub = render(<App />)
    expect(await screen.findByRole('heading', { name: 'Subscription' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Subscription' })).toHaveAttribute(
      'aria-current',
      'page',
    )
    expect(window.location.pathname).toBe('/subscription')
    sub.unmount()

    window.history.pushState(null, '', '/platforms')
    productGetMock.mockResolvedValue({})
    render(<App />)
    expect(await screen.findByRole('heading', { name: 'Your Platforms' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Your Platforms' })).toHaveAttribute(
      'aria-current',
      'page',
    )
    expect(window.location.pathname).toBe('/platforms')
  })

  it('?session= selects that session when it is in the list — not list[0]', async () => {
    window.history.pushState(null, '', '/?session=sess_d5789a91d53b4bae')
    meMock.mockResolvedValue({
      email: 'new@factory.dev',
      email_verified: true,
      account_id: 'acct_boot',
    })
    listMock.mockResolvedValue([
      { session_id: 'sess_45729bb0001' },
      { session_id: 'sess_d5789a91d53b4bae' },
    ])
    render(<App />)
    expect(await screen.findByRole('heading', { name: 'Factory Floor' })).toBeInTheDocument()
    expect(screen.getByText(/session sess_d5789a9/)).toBeInTheDocument()
    expect(screen.queryByText(/session sess_45729bb/)).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Download platform export (.zip)' })).not.toBeInTheDocument()
    expect(screen.queryByText(/Download ready/)).not.toBeInTheDocument()
    expect(createMock).not.toHaveBeenCalled()
  })

  it('?session= on /platforms keeps path routing and still selects that session', async () => {
    window.history.pushState(null, '', '/platforms?session=sess_d5789a91d53b4bae')
    meMock.mockResolvedValue({
      email: 'new@factory.dev',
      email_verified: true,
      account_id: 'acct_boot',
    })
    listMock.mockResolvedValue([
      { session_id: 'sess_45729bb0001' },
      { session_id: 'sess_d5789a91d53b4bae' },
    ])
    render(<App />)
    expect(await screen.findByRole('heading', { name: 'Your Platforms' })).toBeInTheDocument()
    expect(screen.getByText(/session sess_d5789a9/)).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'Factory Floor' })).not.toBeInTheDocument()
    expect(window.location.pathname).toBe('/platforms')
    expect(createMock).not.toHaveBeenCalled()
  })

  it('missing ?session= id fails closed — no other session Download card', async () => {
    window.history.pushState(null, '', '/?session=sess_not_in_list')
    meMock.mockResolvedValue({
      email: 'new@factory.dev',
      email_verified: true,
      account_id: 'acct_boot',
    })
    listMock.mockResolvedValue([{ session_id: 'sess_45729bb0001' }])
    render(<App />)
    expect(await screen.findByRole('heading', { name: 'Session not found' })).toBeInTheDocument()
    expect(screen.getByText(/sess_not_in_list/)).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'Factory Floor' })).not.toBeInTheDocument()
    expect(screen.queryByText(/session sess_45729bb/)).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Download platform export (.zip)' })).not.toBeInTheDocument()
    expect(screen.queryByText(/Download ready/)).not.toBeInTheDocument()
    expect(createMock).not.toHaveBeenCalled()

    fireEvent.click(screen.getByRole('button', { name: 'Open Factory Floor' }))
    expect(await screen.findByRole('heading', { name: 'Factory Floor' })).toBeInTheDocument()
    expect(screen.getByText(/session sess_45729bb/)).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'Session not found' })).not.toBeInTheDocument()
  })

  it('rail nav buttons expose icons, labels, and accessible names', async () => {
    meMock.mockResolvedValue({
      email: 'new@factory.dev',
      email_verified: true,
      account_id: 'acct_boot',
    })
    listMock.mockResolvedValue([{ session_id: 'sess_ok' }])
    render(<App />)
    await screen.findByRole('heading', { name: 'Factory Floor' })
    const nav = screen.getByRole('navigation', { name: 'Factory navigation' })
    expect(nav).toBeInTheDocument()
    for (const label of ['Factory Floor', 'Your Platforms', 'Subscription', 'Account']) {
      const btn = screen.getByRole('button', { name: label })
      expect(btn).toHaveAttribute('title', label)
      expect(btn.querySelector('.nav-icon')).not.toBeNull()
      expect(btn.querySelector('.nav-label-full')).toHaveTextContent(label)
      expect(btn.textContent?.trim().length).toBeGreaterThan(0)
    }
  })
})

describe('viewFromPath / pathFromView', () => {
  it('round-trips signed-in shell routes', () => {
    expect(viewFromPath('/account')).toBe('account')
    expect(viewFromPath('/subscription')).toBe('subscription')
    expect(viewFromPath('/platforms')).toBe('platforms')
    expect(viewFromPath('/floor')).toBe('floor')
    expect(viewFromPath('/')).toBe('floor')
    expect(viewFromPath('/login')).toBe('floor')
    expect(pathFromView('account')).toBe('/account')
    expect(pathFromView('subscription')).toBe('/subscription')
    expect(pathFromView('platforms')).toBe('/platforms')
    expect(pathFromView('floor')).toBe('/')
  })
})

describe('sessionQueryParam / resolveBootSession', () => {
  it('reads ?session= and ignores blank values', () => {
    expect(sessionQueryParam('?session=sess_d5789a91d53b4bae')).toBe('sess_d5789a91d53b4bae')
    expect(sessionQueryParam('session=sess_d5789a91d53b4bae&token=x')).toBe(
      'sess_d5789a91d53b4bae',
    )
    expect(sessionQueryParam('?token=only')).toBeNull()
    expect(sessionQueryParam('?session=')).toBeNull()
    expect(sessionQueryParam('?session=%20')).toBeNull()
  })

  it('selects the requested id when it is in the list — never list[0]', () => {
    expect(
      resolveBootSession('sess_d5789a91d53b4bae', [
        { session_id: 'sess_45729bb0001' },
        { session_id: 'sess_d5789a91d53b4bae' },
      ]),
    ).toEqual({ status: 'selected', sessionId: 'sess_d5789a91d53b4bae' })
  })

  it('fail-closes when the requested id is missing — does not invent list[0]', () => {
    expect(
      resolveBootSession('sess_missing', [{ session_id: 'sess_45729bb0001' }]),
    ).toEqual({ status: 'missing', requested: 'sess_missing' })
    expect(resolveBootSession('sess_ghost', [])).toEqual({
      status: 'missing',
      requested: 'sess_ghost',
    })
  })

  it('keeps current boot when no query param: first listed session, else create', () => {
    expect(resolveBootSession(null, [{ session_id: 'sess_45729bb0001' }])).toEqual({
      status: 'selected',
      sessionId: 'sess_45729bb0001',
    })
    expect(resolveBootSession(null, [])).toEqual({ status: 'create' })
    expect(resolveBootSession('', [{ session_id: 'sess_ok' }])).toEqual({
      status: 'selected',
      sessionId: 'sess_ok',
    })
  })
})
