/**
 * Smoke test: the auth gate — every account screen must be reachable
 * from the live client (market-readiness audit, gap #1).
 */
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { AuthGate } from '../App'

vi.mock('../api/factory', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/factory')>()
  return {
    ...actual,
    setSession: vi.fn(),
    clearSession: vi.fn(),
    auth: {
      ...actual.auth,
      login: vi.fn().mockResolvedValue({ login_token: 'cdt_test' }),
      register: vi.fn().mockResolvedValue({
        login_token: 'cdt_test',
        account_id: 'acct_test',
        verification: {
          mode: 'dev_token',
          email_sent: false,
          note: 'SMTP not configured',
          dev_verification_token: 'vtk_test_123',
        },
      }),
    },
  }
})

describe('AuthGate', () => {
  it('shows sign-in by default', () => {
    render(<AuthGate onAuthed={() => {}} />)
    expect(screen.getByRole('button', { name: 'Enter the factory' })).toBeInTheDocument()
  })

  it('exposes password reset and email verification entry points', () => {
    render(<AuthGate onAuthed={() => {}} />)
    expect(screen.getByRole('button', { name: 'Forgot password?' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Have a verification token?' })).toBeInTheDocument()
  })

  it('forgot-password flow asks for the token', () => {
    render(<AuthGate onAuthed={() => {}} />)
    fireEvent.click(screen.getByRole('button', { name: 'Forgot password?' }))
    expect(screen.getByRole('button', { name: 'Send reset token' })).toBeInTheDocument()
  })

  it('register with unconfigured SMTP hands the dev token to the app', async () => {
    const onAuthed = vi.fn()
    render(<AuthGate onAuthed={onAuthed} />)
    fireEvent.click(screen.getByRole('button', { name: 'Create an account' }))
    fireEvent.change(screen.getByPlaceholderText('you@company.com'), {
      target: { value: 'new@factory.dev' },
    })
    fireEvent.change(screen.getByPlaceholderText('password (8+ characters)'), {
      target: { value: 'supersecret1' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Create your account' }))
    await waitFor(() =>
      expect(onAuthed).toHaveBeenCalledWith({ devVerificationToken: 'vtk_test_123' }),
    )
  })

  it('SMTP register enters the app so boot can show verify-email (not a dead Factory)', async () => {
    const { auth } = await import('../api/factory')
    ;(auth.register as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
      login_token: 'cdt_smtp',
      email_verified: false,
      verification: { mode: 'smtp', email_sent: true },
    })
    const onAuthed = vi.fn()
    render(<AuthGate onAuthed={onAuthed} />)
    fireEvent.click(screen.getByRole('button', { name: 'Create an account' }))
    fireEvent.change(screen.getByPlaceholderText('you@company.com'), {
      target: { value: 'smtp@factory.dev' },
    })
    fireEvent.change(screen.getByPlaceholderText('password (8+ characters)'), {
      target: { value: 'supersecret1' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Create your account' }))
    await waitFor(() => expect(onAuthed).toHaveBeenCalled())
  })
})

describe('email deep links', () => {
  it('completes verification when opened from the email link', async () => {
    const { auth } = await import('../api/factory')
    ;(auth.verifyEmail as ReturnType<typeof vi.fn>) = vi
      .fn()
      .mockResolvedValue({ ok: true })
    window.history.pushState(null, '', '/verify-email?token=vtk_from_email')

    render(<AuthGate onAuthed={() => {}} />)

    await waitFor(() =>
      expect(auth.verifyEmail).toHaveBeenCalledWith('vtk_from_email'),
    )
    await waitFor(() =>
      expect(
        screen.getByText('Email verified. Sign in to enter the factory.'),
      ).toBeInTheDocument(),
    )
    expect(window.location.pathname).toBe('/')
  })

  it('prefills the reset form when opened from the reset link', async () => {
    window.history.pushState(null, '', '/reset-password?token=rst_from_email')

    render(<AuthGate onAuthed={() => {}} />)

    await waitFor(() =>
      expect(
        screen.getByText('Choose a new password to finish the reset.'),
      ).toBeInTheDocument(),
    )
    expect(window.location.pathname).toBe('/')
  })
})
