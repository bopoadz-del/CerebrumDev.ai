/**
 * Boot path: unverified email must not be shown as "Factory unreachable".
 */
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiError, isEmailNotVerifiedError } from '../api/factory'
import App from '../App'

const { meMock, verifyEmailMock, listMock, createMock } = vi.hoisted(() => ({
  meMock: vi.fn(),
  verifyEmailMock: vi.fn(),
  listMock: vi.fn(),
  createMock: vi.fn(),
}))

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
    },
    sessions: {
      list: (...args: unknown[]) => listMock(...args),
      create: (...args: unknown[]) => createMock(...args),
    },
  }
})

describe('isEmailNotVerifiedError', () => {
  it('matches 403 email_not_verified and ignores other failures', () => {
    expect(isEmailNotVerifiedError(new ApiError(403, 'email_not_verified'))).toBe(true)
    expect(isEmailNotVerifiedError(new ApiError(503, 'backend down'))).toBe(false)
    expect(isEmailNotVerifiedError(new ApiError(401, 'unauthorized'))).toBe(false)
    expect(isEmailNotVerifiedError(new Error('email_not_verified'))).toBe(false)
  })
})

describe('App boot', () => {
  beforeEach(() => {
    meMock.mockReset()
    verifyEmailMock.mockReset()
    listMock.mockReset()
    createMock.mockReset()
  })

  it('shows verify-email — not Factory unreachable — when boot is 403 email_not_verified', async () => {
    meMock.mockRejectedValue(new ApiError(403, 'email_not_verified'))
    render(<App />)
    expect(await screen.findByRole('heading', { name: 'Verify your email' })).toBeInTheDocument()
    expect(screen.getByText(/new@factory.dev/)).toBeInTheDocument()
    expect(screen.getByPlaceholderText('verification token')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Verify email' })).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'Factory unreachable' })).not.toBeInTheDocument()
    expect(listMock).not.toHaveBeenCalled()
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

  it('still shows Factory unreachable when the API is actually down', async () => {
    meMock.mockRejectedValue(new ApiError(503, 'backend down'))
    render(<App />)
    expect(await screen.findByRole('heading', { name: 'Factory unreachable' })).toBeInTheDocument()
    expect(screen.getByText('backend down')).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'Verify your email' })).not.toBeInTheDocument()
  })
})
