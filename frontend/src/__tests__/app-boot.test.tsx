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
  canonicalSessionLocation,
  isSessionSurfacePath,
  pathFromView,
  pathSessionWins,
  requestedSessionFromLocation,
  resolveBootSession,
  sessionIdFromPath,
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
  watchBuildMock,
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
  watchBuildMock: vi.fn(),
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
    watchBuildStatus: (...args: unknown[]) => watchBuildMock(...args),
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
    watchBuildMock.mockReset()
    watchBuildMock.mockImplementation(async () => {})
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
    expect(window.location.pathname).toBe('/floor/sess_fresh')
    expect(createMock).toHaveBeenCalledTimes(1)
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
    expect(window.location.pathname).toBe('/floor/sess_d5789a91d53b4bae')
    expect(window.location.search).toBe('')
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
    expect(window.location.pathname).toBe('/platforms/sess_d5789a91d53b4bae')
    expect(window.location.search).toBe('')
    expect(createMock).not.toHaveBeenCalled()
  })

  it('?session= thin VetCare never claims Finished + Download — same honesty as /floor/{id}', async () => {
    const thinId = 'sess_cec9a1345b2049bb'
    const readyId = 'sess_45729bb0001'
    window.history.pushState(null, '', `/?session=${thinId}`)
    meMock.mockResolvedValue({
      email: 'new@factory.dev',
      email_verified: true,
      account_id: 'acct_boot',
    })
    listMock.mockResolvedValue([{ session_id: readyId }, { session_id: thinId }])
    productGetMock.mockImplementation(async (sid: string) => {
      if (sid === thinId) {
        return {
          blueprint: {
            product_name: 'VetCare Hub',
            vertical: 'veterinary-care',
            drafting_mode: 'architect_llm',
          },
          blueprint_approved: true,
          generation: {
            engine: 'runner',
            product_id: 'veterinary-care',
            triggered_by: 'chat_llm',
          },
        }
      }
      return {
        blueprint: { product_name: 'Pilot Ready Kit', vertical: 'steward' },
        blueprint_approved: true,
        generation: { engine: 'runner', product_id: 'steward' },
      }
    })
    watchBuildMock.mockImplementation(async (sid: string, onProgress: (s: object) => void) => {
      if (sid === thinId) {
        onProgress({
          state: 'succeeded',
          outcome: 'SUCCESS',
          cycle: 'pilot',
          pilot_ready: false,
          authorship: {
            artifacts: 24,
            agent_written: 6,
            templated: 18,
            action_py: 3,
            agent_artifacts: ['audit', 'vetcare_hub_veterinary_core', 'workflow'],
            cli_authored_ids: ['audit', 'vetcare_hub_veterinary_core', 'workflow'],
          },
          level_grade: {
            level: 'CODE_GREEN',
            pilot_ready: false,
            founding_customer_ready: false,
            full_pilot: false,
            three_gate: { CODE: 'PASS', PRODUCT: 'PASS', STORE: 'PASS' },
          },
        })
        return
      }
      onProgress({
        state: 'succeeded',
        pilot_ready: true,
        acceptance: { passed: 12, total: 12, ok: true },
        cycle: 'pilot',
        authorship: { artifacts: 24, agent_written: 8, templated: 16, action_py: 8 },
        level_grade: {
          level: 'STORE_GREEN',
          founding_customer_ready: false,
          pilot_ready: true,
          acceptance: { passed: 12, total: 12, ok: true },
          three_gate: { CODE: 'PASS', PRODUCT: 'PASS', STORE: 'PASS' },
        },
      })
    })
    render(<App />)
    expect(await screen.findByRole('heading', { name: 'Factory Floor' })).toBeInTheDocument()
    expect(window.location.pathname).toBe(`/floor/${thinId}`)
    expect(window.location.search).toBe('')
    expect(await screen.findByRole('heading', { name: 'Code-cycle prototype ready' })).toBeInTheDocument()
    expect(screen.getByTestId('floor-prototype-pill')).toHaveTextContent('Code-green (prototype)')
    expect(screen.getByText(/session sess_cec9a13/)).toBeInTheDocument()
    expect(screen.queryByText(/session sess_45729bb/)).not.toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'Coding agent finished' })).not.toBeInTheDocument()
    expect(screen.queryByText(/Store-green/)).not.toBeInTheDocument()
    expect(screen.queryByText(/Download ready/)).not.toBeInTheDocument()
    expect(screen.queryByText(/Finished —/)).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Download platform export (.zip)' })).not.toBeInTheDocument()
    expect(
      screen.getByRole('button', { name: 'Export (.zip) — below full-pilot authorship floor' }),
    ).toBeDisabled()
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

  it('/floor/{sessionId} selects that session — not the bound list[0] run', async () => {
    window.history.pushState(null, '', '/floor/sess_fe80bf177a8545e6')
    meMock.mockResolvedValue({
      email: 'new@factory.dev',
      email_verified: true,
      account_id: 'acct_boot',
    })
    listMock.mockResolvedValue([
      { session_id: 'sess_cec9a13active1' },
      { session_id: 'sess_fe80bf177a8545e6' },
      { session_id: 'sess_9d8e9a2dc01b40a1' },
    ])
    productGetMock.mockImplementation(async (sid: string) => {
      if (sid === 'sess_fe80bf177a8545e6') {
        return {
          blueprint: {
            product_name: 'Finished VetCare',
            vertical: 'veterinary-care',
            drafting_mode: 'architect_llm',
          },
          blueprint_approved: true,
          generation: {
            engine: 'runner',
            product_id: 'veterinary-care',
            triggered_by: 'chat_llm',
          },
        }
      }
      if (sid === 'sess_cec9a13active1') {
        return {
          blueprint: { product_name: 'Live VetCare', vertical: 'veterinary-care' },
          blueprint_approved: true,
          generation: { engine: 'runner', product_id: 'veterinary-care' },
        }
      }
      return {}
    })
    watchBuildMock.mockImplementation(async (sid: string, onProgress: (s: object) => void) => {
      if (sid === 'sess_fe80bf177a8545e6') {
        onProgress({
          state: 'succeeded',
          pilot_ready: true,
          acceptance: { passed: 12, total: 12, ok: true },
          cycle: 'pilot',
          authorship: { artifacts: 24, agent_written: 8, templated: 16 },
          level_grade: {
            level: 'STORE_GREEN',
            founding_customer_ready: false,
            pilot_ready: true,
            acceptance: { passed: 12, total: 12, ok: true },
            three_gate: { CODE: 'PASS', PRODUCT: 'PASS', STORE: 'PASS' },
          },
        })
        return
      }
      onProgress({
        state: 'building',
        current_phase: { id: 'WRITER', label: 'Platform manufacturer' },
        phase_index: 3,
        phase_total: 5,
        last_event: 'wrote handler intake',
      })
    })
    render(<App />)
    expect(await screen.findByRole('heading', { name: 'Factory Floor' })).toBeInTheDocument()
    expect(screen.getByText(/session sess_fe80bf1/)).toBeInTheDocument()
    expect(screen.queryByText(/session sess_cec9a13/)).not.toBeInTheDocument()
    expect(await screen.findByRole('heading', { name: 'Coding agent finished' })).toBeInTheDocument()
    expect(screen.getByTestId('floor-pilot-ready-pill')).toHaveTextContent('Store-green')
    expect(screen.queryByText(/WRITER 3\/5/)).not.toBeInTheDocument()
    expect(screen.queryByText('Live VetCare')).not.toBeInTheDocument()
    expect(productGetMock).toHaveBeenCalledWith('sess_fe80bf177a8545e6')
    expect(createMock).not.toHaveBeenCalled()
  })

  it('/floor/{sessionId} for a stopped run hydrates STOPPED — not a blank composer', async () => {
    window.history.pushState(null, '', '/floor/sess_9d8e9a2dc01b40a1')
    meMock.mockResolvedValue({
      email: 'new@factory.dev',
      email_verified: true,
      account_id: 'acct_boot',
    })
    listMock.mockResolvedValue([
      { session_id: 'sess_cec9a13active1' },
      { session_id: 'sess_9d8e9a2dc01b40a1' },
    ])
    productGetMock.mockResolvedValue({
      blueprint: { product_name: 'Stopped VetCare', vertical: 'veterinary-care' },
      blueprint_approved: true,
      generation: { engine: 'runner', product_id: 'veterinary-care', triggered_by: 'chat_llm' },
    })
    watchBuildMock.mockImplementation(async (_sid: string, onProgress: (s: object) => void) => {
      onProgress({
        state: 'failed',
        cycle: 'pilot',
        detail: 'rework budget of 3 exhausted; TESTER gate still failing',
        pilot_ready: false,
      })
    })
    render(<App />)
    expect(await screen.findByText(/session sess_9d8e9a2/)).toBeInTheDocument()
    expect(screen.queryByText(/session sess_cec9a13/)).not.toBeInTheDocument()
    expect(await screen.findByRole('heading', { name: 'Coding agent stopped' })).toBeInTheDocument()
    expect(screen.getByTestId('floor-failed-pill')).toHaveTextContent('Pilot suite failed')
    expect(screen.queryByRole('heading', { name: 'Coding agent has taken over' })).not.toBeInTheDocument()
    expect(productGetMock).toHaveBeenCalledWith('sess_9d8e9a2dc01b40a1')
    expect(createMock).not.toHaveBeenCalled()
  })

  it('popstate from the live bound session to /floor/{finished} remounts that Floor', async () => {
    window.history.pushState(null, '', '/floor/sess_cec9a13active1')
    meMock.mockResolvedValue({
      email: 'new@factory.dev',
      email_verified: true,
      account_id: 'acct_boot',
    })
    listMock.mockResolvedValue([
      { session_id: 'sess_cec9a13active1' },
      { session_id: 'sess_fe80bf177a8545e6' },
    ])
    productGetMock.mockImplementation(async (sid: string) => {
      if (sid === 'sess_fe80bf177a8545e6') {
        return {
          blueprint: { product_name: 'Finished VetCare', vertical: 'veterinary-care' },
          blueprint_approved: true,
          generation: { engine: 'runner', product_id: 'veterinary-care', triggered_by: 'chat_llm' },
        }
      }
      return {
        blueprint: { product_name: 'Live VetCare', vertical: 'veterinary-care' },
        blueprint_approved: true,
        generation: { engine: 'runner', product_id: 'veterinary-care', triggered_by: 'chat_llm' },
      }
    })
    watchBuildMock.mockImplementation(async (sid: string, onProgress: (s: object) => void) => {
      if (sid === 'sess_fe80bf177a8545e6') {
        onProgress({
          state: 'succeeded',
          pilot_ready: true,
          acceptance: { passed: 12, total: 12, ok: true },
          cycle: 'pilot',
          authorship: { artifacts: 24, agent_written: 8, templated: 16 },
          level_grade: {
            level: 'STORE_GREEN',
            founding_customer_ready: false,
            pilot_ready: true,
            acceptance: { passed: 12, total: 12, ok: true },
            three_gate: { CODE: 'PASS', PRODUCT: 'PASS', STORE: 'PASS' },
          },
        })
        return
      }
      onProgress({
        state: 'building',
        current_phase: { id: 'WRITER', label: 'Platform manufacturer' },
        phase_index: 3,
        phase_total: 5,
        last_event: 'wrote handler intake',
      })
    })
    render(<App />)
    expect(await screen.findByText(/session sess_cec9a13/)).toBeInTheDocument()
    expect(await screen.findByText(/WRITER 3\/5/)).toBeInTheDocument()

    window.history.pushState(null, '', '/floor/sess_fe80bf177a8545e6')
    window.dispatchEvent(new PopStateEvent('popstate'))

    expect(await screen.findByText(/session sess_fe80bf1/)).toBeInTheDocument()
    expect(screen.queryByText(/session sess_cec9a13/)).not.toBeInTheDocument()
    expect(await screen.findByRole('heading', { name: 'Coding agent finished' })).toBeInTheDocument()
    expect(screen.queryByText(/WRITER 3\/5/)).not.toBeInTheDocument()
    expect(productGetMock).toHaveBeenCalledWith('sess_fe80bf177a8545e6')
    expect(createMock).not.toHaveBeenCalled()
  })

  it('/floor/{missing} fails closed — does not create or paint list[0]', async () => {
    window.history.pushState(null, '', '/floor/sess_not_in_list')
    meMock.mockResolvedValue({
      email: 'new@factory.dev',
      email_verified: true,
      account_id: 'acct_boot',
    })
    listMock.mockResolvedValue([{ session_id: 'sess_cec9a13active1' }])
    render(<App />)
    expect(await screen.findByRole('heading', { name: 'Session not found' })).toBeInTheDocument()
    expect(screen.getByText(/sess_not_in_list/)).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'Factory Floor' })).not.toBeInTheDocument()
    expect(screen.queryByText(/session sess_cec9a13/)).not.toBeInTheDocument()
    expect(createMock).not.toHaveBeenCalled()

    fireEvent.click(screen.getByRole('button', { name: 'Open Factory Floor' }))
    expect(await screen.findByRole('heading', { name: 'Factory Floor' })).toBeInTheDocument()
    expect(screen.getByText(/session sess_cec9a13/)).toBeInTheDocument()
    expect(window.location.pathname).not.toMatch(/sess_not_in_list/)
  })

  it('/platforms without a session id still binds list[0] — honesty for the active card', async () => {
    window.history.pushState(null, '', '/platforms')
    meMock.mockResolvedValue({
      email: 'new@factory.dev',
      email_verified: true,
      account_id: 'acct_boot',
    })
    listMock.mockResolvedValue([
      { session_id: 'sess_cec9a13active1' },
      { session_id: 'sess_fe80bf177a8545e6' },
    ])
    productGetMock.mockResolvedValue({
      generation: {
        engine: 'runner',
        product_id: 'veterinary-care',
      },
      blueprint: { product_name: 'VetCare', vertical: 'veterinary-care' },
    })
    watchBuildMock.mockImplementation(async (_sid: string, onProgress: (s: object) => void) => {
      onProgress({
        state: 'building',
        current_phase: { id: 'WRITER', label: 'Platform manufacturer' },
        phase_index: 3,
        phase_total: 5,
      })
    })
    render(<App />)
    expect(await screen.findByRole('heading', { name: 'Your Platforms' })).toBeInTheDocument()
    expect(screen.getByText(/session sess_cec9a13/)).toBeInTheDocument()
    expect(screen.queryByText(/session sess_fe80bf1/)).not.toBeInTheDocument()
    expect(productGetMock).toHaveBeenCalledWith('sess_cec9a13active1')
    expect(createMock).not.toHaveBeenCalled()
  })

  it('active WRITER session A bound → navigate to /floor/{B} shows finished B, not A', async () => {
    const writerA = 'sess_d10dfc2890f7487b'
    const finishedB = 'sess_cec9a1345b2049bb'
    window.history.pushState(null, '', `/floor/${writerA}`)
    meMock.mockResolvedValue({
      email: 'new@factory.dev',
      email_verified: true,
      account_id: 'acct_boot',
    })
    listMock.mockResolvedValue([{ session_id: writerA }, { session_id: finishedB }])
    productGetMock.mockImplementation(async (sid: string) => {
      if (sid === finishedB) {
        return {
          blueprint: {
            product_name: 'VetCare',
            vertical: 'veterinary-care',
            drafting_mode: 'architect_llm',
          },
          blueprint_approved: true,
          generation: {
            engine: 'runner',
            product_id: 'veterinary-care',
            triggered_by: 'chat_llm',
          },
        }
      }
      return {
        blueprint: {
          product_name: 'InsureDistribute Platform',
          vertical: 'insurance-distribution',
        },
        blueprint_approved: true,
        generation: {
          engine: 'runner',
          product_id: 'insurance-distribution',
          triggered_by: 'chat_llm',
        },
      }
    })
    watchBuildMock.mockImplementation(async (sid: string, onProgress: (s: object) => void) => {
      if (sid === finishedB) {
        onProgress({
          state: 'succeeded',
          pilot_ready: true,
          acceptance: { passed: 12, total: 12, ok: true },
          cycle: 'pilot',
          authorship: { artifacts: 24, agent_written: 8, templated: 16 },
          level_grade: {
            level: 'STORE_GREEN',
            founding_customer_ready: false,
            pilot_ready: true,
            acceptance: { passed: 12, total: 12, ok: true },
            three_gate: { CODE: 'PASS', PRODUCT: 'PASS', STORE: 'PASS' },
          },
        })
        return
      }
      onProgress({
        state: 'building',
        current_phase: { id: 'WRITER', label: 'Platform manufacturer' },
        phase_index: 3,
        phase_total: 5,
        last_event: 'wrote handler intake',
      })
    })
    render(<App />)
    expect(await screen.findByText(/session sess_d10dfc2/)).toBeInTheDocument()
    expect(await screen.findByText(/WRITER 3\/5/)).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Coding agent has taken over' })).toBeInTheDocument()

    // Live deep-link writes the path without a PopStateEvent. The in-flight
    // WRITER occupant must not keep the Floor.
    window.history.pushState(null, '', `/floor/${finishedB}`)

    expect(await screen.findByText(/session sess_cec9a13/)).toBeInTheDocument()
    expect(screen.queryByText(/session sess_d10dfc2/)).not.toBeInTheDocument()
    expect(await screen.findByRole('heading', { name: 'Coding agent finished' })).toBeInTheDocument()
    expect(screen.getByTestId('floor-pilot-ready-pill')).toHaveTextContent('Store-green')
    expect(screen.queryByText(/WRITER 3\/5/)).not.toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'Coding agent has taken over' })).not.toBeInTheDocument()
    expect(productGetMock).toHaveBeenCalledWith(finishedB)
    expect(createMock).not.toHaveBeenCalled()
  })

  it('/floor/{sessionId} wins over a stale ?session= query', async () => {
    window.history.pushState(
      null,
      '',
      '/floor/sess_fe80bf177a8545e6?session=sess_cec9a13active1',
    )
    meMock.mockResolvedValue({
      email: 'new@factory.dev',
      email_verified: true,
      account_id: 'acct_boot',
    })
    listMock.mockResolvedValue([
      { session_id: 'sess_cec9a13active1' },
      { session_id: 'sess_fe80bf177a8545e6' },
    ])
    render(<App />)
    expect(await screen.findByText(/session sess_fe80bf1/)).toBeInTheDocument()
    expect(screen.queryByText(/session sess_cec9a13/)).not.toBeInTheDocument()
    expect(productGetMock).toHaveBeenCalledWith('sess_fe80bf177a8545e6')
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
    expect(viewFromPath('/floor/sess_fe80bf177a8545e6')).toBe('floor')
    expect(viewFromPath('/platforms/sess_fe80bf177a8545e6')).toBe('platforms')
    expect(viewFromPath('/')).toBe('floor')
    expect(viewFromPath('/login')).toBe('floor')
    expect(pathFromView('account')).toBe('/account')
    expect(pathFromView('subscription')).toBe('/subscription')
    expect(pathFromView('platforms')).toBe('/platforms')
    expect(pathFromView('floor')).toBe('/')
    expect(pathFromView('floor', 'sess_fe80bf177a8545e6')).toBe('/floor/sess_fe80bf177a8545e6')
    expect(pathFromView('platforms', 'sess_fe80bf177a8545e6')).toBe(
      '/platforms/sess_fe80bf177a8545e6',
    )
    expect(pathFromView('account', 'sess_fe80bf177a8545e6')).toBe('/account')
  })
})

describe('pathSessionWins', () => {
  it('keeps the path id when a live WRITER / list[0] bind disagrees', () => {
    expect(pathSessionWins('sess_cec9a1345b2049bb', 'sess_d10dfc2890f7487b')).toBe(
      'sess_cec9a1345b2049bb',
    )
    expect(pathSessionWins(null, 'sess_d10dfc2890f7487b')).toBe('sess_d10dfc2890f7487b')
    expect(pathSessionWins('sess_cec9a1345b2049bb', null)).toBe('sess_cec9a1345b2049bb')
  })
})

describe('canonicalSessionLocation', () => {
  it('rewrites legacy ?session= onto /floor/{id} and /platforms/{id}', () => {
    expect(canonicalSessionLocation('/', '?session=sess_cec9a1345b2049bb')).toBe(
      '/floor/sess_cec9a1345b2049bb',
    )
    expect(canonicalSessionLocation('/floor', '?session=sess_cec9a1345b2049bb')).toBe(
      '/floor/sess_cec9a1345b2049bb',
    )
    expect(canonicalSessionLocation('/platforms', '?session=sess_fe80bf177a8545e6')).toBe(
      '/platforms/sess_fe80bf177a8545e6',
    )
    expect(canonicalSessionLocation('/', '?session=sess_cec9a1345b2049bb&token=x')).toBe(
      '/floor/sess_cec9a1345b2049bb?token=x',
    )
  })

  it('drops a stale ?session= when the path already names the occupant', () => {
    expect(
      canonicalSessionLocation(
        '/floor/sess_fe80bf177a8545e6',
        '?session=sess_cec9a13active1',
      ),
    ).toBe('/floor/sess_fe80bf177a8545e6')
    expect(canonicalSessionLocation('/floor/sess_fe80bf177a8545e6', '')).toBeNull()
    expect(canonicalSessionLocation('/', '')).toBeNull()
  })

  it('does not rewrite auth or account URLs', () => {
    expect(isSessionSurfacePath('/login')).toBe(false)
    expect(canonicalSessionLocation('/login', '?session=sess_cec9a1345b2049bb')).toBeNull()
    expect(canonicalSessionLocation('/account', '?session=sess_cec9a1345b2049bb')).toBeNull()
  })
})

describe('sessionIdFromPath / requestedSessionFromLocation', () => {
  it('reads /floor/{id} and /platforms/{id}', () => {
    expect(sessionIdFromPath('/floor/sess_fe80bf177a8545e6')).toBe('sess_fe80bf177a8545e6')
    expect(sessionIdFromPath('/platforms/sess_9d8e9a2dc01b40a1')).toBe('sess_9d8e9a2dc01b40a1')
    expect(sessionIdFromPath('/floor/sess_fe80bf177a8545e6/')).toBe('sess_fe80bf177a8545e6')
    expect(sessionIdFromPath('/floor')).toBeNull()
    expect(sessionIdFromPath('/platforms')).toBeNull()
    expect(sessionIdFromPath('/')).toBeNull()
    expect(sessionIdFromPath('/account')).toBeNull()
  })

  it('path param wins over ?session=', () => {
    expect(
      requestedSessionFromLocation(
        '/floor/sess_fe80bf177a8545e6',
        '?session=sess_cec9a13active1',
      ),
    ).toBe('sess_fe80bf177a8545e6')
    expect(requestedSessionFromLocation('/platforms', '?session=sess_cec9a13active1')).toBe(
      'sess_cec9a13active1',
    )
    expect(requestedSessionFromLocation('/', '')).toBeNull()
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
