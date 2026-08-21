import { useEffect, useState, type FormEvent } from 'react'
import { auth, getEmail, getToken, setSession } from './api/factory'

/* ---------------------------------- Auth ---------------------------------- */

export function VerifyEmailGate({
  onVerified,
  onLogout,
  initialDevToken,
}: {
  onVerified: () => void
  onLogout: () => void
  initialDevToken?: string | null
}) {
  const [token, setToken] = useState(initialDevToken ?? '')
  const [exposedDevToken, setExposedDevToken] = useState(initialDevToken ?? null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(
    initialDevToken
      ? 'SMTP is not configured on this deployment — the verification token is filled in below.'
      : null,
  )
  const email = getEmail()

  useEffect(() => {
    const params = new URLSearchParams(window.location.search)
    const linkToken = params.get('token')
    if (!linkToken || window.location.pathname !== '/verify-email') return
    window.history.replaceState(null, '', '/')
    setBusy(true)
    setError(null)
    auth
      .verifyEmail(linkToken)
      .then(() => onVerified())
      .catch((err) => setError(err instanceof Error ? err.message : 'verification failed'))
      .finally(() => setBusy(false))
  }, [onVerified])

  async function submit(e: FormEvent) {
    e.preventDefault()
    setBusy(true)
    setError(null)
    setNotice(null)
    try {
      await auth.verifyEmail(token)
      onVerified()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'verification failed')
    } finally {
      setBusy(false)
    }
  }

  async function resend() {
    setBusy(true)
    setError(null)
    setNotice(null)
    try {
      const res = await auth.resendVerification()
      if (res.already_verified) {
        onVerified()
        return
      }
      const v = res.verification
      if (v?.dev_verification_token) {
        setToken(v.dev_verification_token)
        setExposedDevToken(v.dev_verification_token)
        setNotice(
          v.note ??
            'SMTP is not configured on this deployment — the verification token is filled in below.',
        )
      } else if (v?.email_sent) {
        setNotice('Verification email sent. Check your inbox, then paste the token below.')
      } else {
        setNotice(v?.note ?? 'Could not send a verification email.')
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'resend failed')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="center-screen">
      <form className="panel narrow auth-panel" onSubmit={submit}>
        <div className="brand center">
          <div className="brand-mark">C</div>
        </div>
        <h1>CerebrumDev.ai</h1>
        <h2 className="auth-mode">Verify your email</h2>
        <p className="dim center-text">
          Check your inbox{email ? ` (${email})` : ''} for the verification link, then
          return here or paste the token below. The factory floor opens after your
          email is verified.
        </p>
        {exposedDevToken && (
          <p className="mono notice-box" data-testid="dev-verification-token">
            {exposedDevToken}
          </p>
        )}
        <input
          type="text"
          required
          placeholder="verification token"
          value={token}
          onChange={(e) => setToken(e.target.value)}
        />
        {error && <div className="error-box">{error}</div>}
        {notice && <div className="notice-box">{notice}</div>}
        <button type="submit" disabled={busy}>
          {busy ? 'Working…' : 'Verify email'}
        </button>
        <button type="button" className="ghost" disabled={busy} onClick={() => void resend()}>
          Resend verification email
        </button>
        <button type="button" className="ghost" onClick={onLogout}>
          Sign out
        </button>
      </form>
    </div>
  )
}

type AuthMode = 'login' | 'register' | 'forgot' | 'reset' | 'verify'

export function AuthGate({
  onAuthed,
}: {
  onAuthed: (info?: { devVerificationToken?: string }) => void
}) {
  const [mode, setMode] = useState<AuthMode>('login')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [token, setToken] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)

  function go(m: AuthMode) {
    setMode(m)
    setError(null)
    setNotice(null)
  }

  // The verification and reset emails link to /verify-email?token=… and
  // /reset-password?token=… . The SPA rewrite serves the app for those
  // paths, but nothing read them: clicking the email link landed on the
  // login screen with the token silently ignored. Verify links complete
  // themselves; reset links prefill the token and ask for the new password.
  useEffect(() => {
    const params = new URLSearchParams(window.location.search)
    const linkToken = params.get('token')
    if (!linkToken) return
    const path = window.location.pathname
    if (path !== '/verify-email' && path !== '/reset-password') return
    window.history.replaceState(null, '', '/')
    if (path === '/reset-password') {
      setToken(linkToken)
      setMode('reset')
      setNotice('Choose a new password to finish the reset.')
      return
    }
    setBusy(true)
    auth
      .verifyEmail(linkToken)
      .then(() => {
        setMode('login')
        setNotice('Email verified. Sign in to enter the factory.')
      })
      .catch((err) => {
        setToken(linkToken)
        setMode('verify')
        setError(err instanceof Error ? err.message : 'verification failed')
      })
      .finally(() => setBusy(false))
  }, [])

  async function submit(e: FormEvent) {
    e.preventDefault()
    setBusy(true)
    setError(null)
    setNotice(null)
    try {
      if (mode === 'register') {
        const res = await auth.register(email, password)
        setSession(res.login_token, email)
        const v = res.verification
        onAuthed(
          v?.dev_verification_token
            ? { devVerificationToken: v.dev_verification_token }
            : undefined,
        )
      } else if (mode === 'login') {
        const res = await auth.login(email, password)
        setSession(res.login_token, email)
        onAuthed()
      } else if (mode === 'forgot') {
        const res = await auth.forgotPassword(email)
        if (res.dev_reset_token) {
          setToken(res.dev_reset_token)
          setNotice('SMTP is not configured on this deployment — reset with the token below.')
        } else {
          setNotice(res.message ?? 'If the email is registered, a reset link follows.')
        }
        setMode('reset')
      } else if (mode === 'reset') {
        const res = await auth.resetPassword(token, password)
        setNotice(res.message ?? 'Password updated — sign in again.')
        setPassword('')
        setMode('login')
      } else if (mode === 'verify') {
        await auth.verifyEmail(token)
        if (getToken()) {
          onAuthed()
          return
        }
        setNotice('Email verified. Sign in to enter the factory.')
        setMode('login')
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'request failed')
    } finally {
      setBusy(false)
    }
  }

  const titles: Record<AuthMode, string> = {
    login: 'Sign in',
    register: 'Create your account',
    forgot: 'Reset your password',
    reset: 'Choose a new password',
    verify: 'Verify your email',
  }

  return (
    <div className="center-screen">
      <form className="panel narrow auth-panel" onSubmit={submit}>
        <div className="brand center">
          <div className="brand-mark">C</div>
        </div>
        <h1>CerebrumDev.ai</h1>
        <p className="dim center-text">One account. Tell the factory. Receive your platform.</p>
        <h2 className="auth-mode">{titles[mode]}</h2>

        {(mode === 'login' || mode === 'register' || mode === 'forgot') && (
          <input
            type="email"
            required
            placeholder="you@company.com"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
          />
        )}
        {(mode === 'login' || mode === 'register' || mode === 'reset') && (
          <input
            type="password"
            required
            minLength={8}
            placeholder={mode === 'reset' ? 'new password (8+ characters)' : 'password (8+ characters)'}
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
        )}
        {(mode === 'reset' || mode === 'verify') && (
          <input
            type="text"
            required
            placeholder={mode === 'reset' ? 'reset token' : 'verification token'}
            value={token}
            onChange={(e) => setToken(e.target.value)}
          />
        )}

        {error && <div className="error-box">{error}</div>}
        {notice && <div className="notice-box">{notice}</div>}

        <button type="submit" disabled={busy}>
          {busy
            ? 'Working…'
            : mode === 'login'
              ? 'Enter the factory'
              : mode === 'register'
                ? 'Create your account'
                : mode === 'forgot'
                  ? 'Send reset token'
                  : mode === 'reset'
                    ? 'Update password'
                    : 'Verify email'}
        </button>

        <div className="auth-links">
          {mode !== 'login' && (
            <button type="button" className="link" onClick={() => go('login')}>
              Sign in
            </button>
          )}
          {mode !== 'register' && (
            <button type="button" className="link" onClick={() => go('register')}>
              Create an account
            </button>
          )}
          {mode !== 'forgot' && (
            <button type="button" className="link" onClick={() => go('forgot')}>
              Forgot password?
            </button>
          )}
          {mode !== 'verify' && (
            <button type="button" className="link" onClick={() => go('verify')}>
              Have a verification token?
            </button>
          )}
        </div>
      </form>
    </div>
  )
}
