/**
 * Account must not render the opposite verified boolean while /me settles,
 * and the Account id row stays in the layout before and after settle.
 */
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { Account } from '../App'
import type { AccountInfo } from '../api/factory'

const meMock = vi.fn()
const resendMock = vi.fn()

vi.mock('../api/factory', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/factory')>()
  return {
    ...actual,
    getEmail: () => 'owner@factory.dev',
    auth: {
      ...actual.auth,
      me: (...args: unknown[]) => meMock(...args),
      resendVerification: (...args: unknown[]) => resendMock(...args),
    },
  }
})

describe('Account verified settling', () => {
  beforeEach(() => {
    meMock.mockReset()
    resendMock.mockReset()
  })

  it('does not render Yes or No until /me settles, and keeps the Account row', async () => {
    let resolveMe!: (value: AccountInfo) => void
    meMock.mockImplementation(
      () =>
        new Promise<AccountInfo>((resolve) => {
          resolveMe = resolve
        }),
    )
    render(<Account onLogout={() => {}} />)
    expect(screen.getByRole('heading', { name: 'Account' })).toBeInTheDocument()
    expect(screen.getByText('Email verified')).toBeInTheDocument()
    expect(screen.queryByText('Yes')).not.toBeInTheDocument()
    expect(screen.queryByText('No')).not.toBeInTheDocument()
    const placeholders = screen.getAllByText('—')
    expect(placeholders.length).toBeGreaterThanOrEqual(2)
    expect(screen.getByText('Account', { selector: 'dt' })).toBeInTheDocument()

    resolveMe({
      email: 'owner@factory.dev',
      email_verified: true,
      account_id: 'acct_settled',
    })
    await waitFor(() => expect(screen.getByText('Yes')).toBeInTheDocument())
    expect(screen.queryByText('No')).not.toBeInTheDocument()
    expect(screen.getByText('acct_settled')).toBeInTheDocument()
  })

  it('renders the boot /me value on first paint so Verified does not flash', () => {
    meMock.mockImplementation(() => new Promise(() => {}))
    render(
      <Account
        onLogout={() => {}}
        initialMe={{
          email: 'owner@factory.dev',
          email_verified: true,
          account_id: 'acct_boot',
        }}
      />,
    )
    expect(screen.getByText('Yes')).toBeInTheDocument()
    expect(screen.queryByText('No')).not.toBeInTheDocument()
    expect(screen.getByText('acct_boot')).toBeInTheDocument()
  })

  it('can still show No after settle when the account is unverified', async () => {
    meMock.mockResolvedValue({
      email: 'owner@factory.dev',
      email_verified: false,
      account_id: 'acct_unverified',
    })
    render(<Account onLogout={() => {}} />)
    expect(await screen.findByText('No')).toBeInTheDocument()
    expect(screen.queryByText('Yes')).not.toBeInTheDocument()
    expect(screen.getByText('acct_unverified')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Resend verification email' })).toBeInTheDocument()
  })

  it('resend verification posts to the existing API and fills a dev token', async () => {
    meMock.mockResolvedValue({
      email: 'owner@factory.dev',
      email_verified: false,
      account_id: 'acct_unverified',
    })
    resendMock.mockResolvedValue({
      ok: true,
      already_verified: false,
      verification: {
        mode: 'dev_token',
        email_sent: false,
        note: 'SMTP not configured',
        dev_verification_token: 'cdv_from_account',
      },
    })
    render(<Account onLogout={() => {}} />)
    fireEvent.click(await screen.findByRole('button', { name: 'Resend verification email' }))
    await waitFor(() => expect(resendMock).toHaveBeenCalled())
    expect(screen.getByPlaceholderText('verification token')).toHaveValue('cdv_from_account')
    expect(screen.getByText('SMTP not configured')).toBeInTheDocument()
  })
})
