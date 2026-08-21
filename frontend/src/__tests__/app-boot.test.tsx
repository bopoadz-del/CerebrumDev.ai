/**
 * Boot path: unverified email must not be shown as "Factory unreachable".
 */
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiError, isDomainStoreUnreachable, isEmailNotVerifiedError } from '../api/factory'
import App from '../App'

const { meMock, verifyEmailMock, resendMock, listMock, createMock, domainsListMock, productGetMock } = vi.hoisted(
  () => ({
    meMock: vi.fn(),
    verifyEmailMock: vi.fn(),
    resendMock: vi.fn(),
    listMock: vi.fn(),
    createMock: vi.fn(),
    domainsListMock: vi.fn(),
    productGetMock: vi.fn(),
  }),
)

vi.mock('../api/factory', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/factory')>()
  return {
    ...actual,
    getToken: () => 'cdt_unverified',
    getEmail: () => 'new@factory.dev',
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
    meMock.mockReset()
    verifyEmailMock.mockReset()
    resendMock.mockReset()
    listMock.mockReset()
    createMock.mockReset()
    domainsListMock.mockReset()
    domainsListMock.mockResolvedValue({ domains: [] })
    productGetMock.mockReset()
    productGetMock.mockResolvedValue({})
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

  it('still shows Factory unreachable when the API is actually down', async () => {
    meMock.mockRejectedValue(new ApiError(503, 'backend down'))
    render(<App />)
    expect(await screen.findByRole('heading', { name: 'Factory unreachable' })).toBeInTheDocument()
    expect(screen.getByText('backend down')).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'Verify your email' })).not.toBeInTheDocument()
    expect(screen.queryByText('Domain store unreachable')).not.toBeInTheDocument()
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
})
