import { useCallback, useEffect, useRef, useState, type FormEvent } from 'react'
import {
  ApiError,
  auth,
  billing,
  chatEventText,
  chatStream,
  clearSession,
  downloadProductPackage,
  getEmail,
  getToken,
  product,
  sessions,
  setSession,
  type BillingStatus,
  type ChatEvent,
  type ProductDesign,
} from './api/factory'

type View = 'floor' | 'platforms' | 'subscription' | 'account'

interface ChatMsg {
  role: 'user' | 'factory' | 'system'
  text: string
  card?: 'blueprint' | 'generation' | 'error' | 'info'
}

export default function App() {
  const [authed, setAuthed] = useState<boolean>(!!getToken())
  const [view, setView] = useState<View>('floor')
  const [sessionId, setSessionId] = useState<string | null>(null)
  const [bootError, setBootError] = useState<string | null>(null)
  const [bootNonce, setBootNonce] = useState(0)

  useEffect(() => {
    if (!authed) return
    let cancelled = false
    ;(async () => {
      try {
        await auth.me()
        const list = await sessions.list()
        const arr = Array.isArray(list) ? list : list.sessions ?? []
        let sid = arr[0]?.session_id
        if (!sid) {
          const created = await sessions.create()
          sid = created.session_id
        }
        if (!cancelled) setSessionId(sid ?? null)
      } catch (e) {
        if (!cancelled) {
          if (e instanceof ApiError && e.status === 401) {
            clearSession()
            setAuthed(false)
          } else {
            setBootError(e instanceof Error ? e.message : 'backend unreachable')
          }
        }
      }
    })()
    return () => {
      cancelled = true
    }
  }, [authed, bootNonce])

  if (!authed) return <AuthGate onAuthed={() => setAuthed(true)} />
  if (bootError)
    return (
      <div className="center-screen">
        <div className="panel narrow">
          <h2>Factory unreachable</h2>
          <p className="dim">{bootError}</p>
          <button
            onClick={() => {
              setBootError(null)
              setSessionId(null)
              setBootNonce((n) => n + 1)
            }}
          >
            Retry
          </button>
        </div>
      </div>
    )
  if (!sessionId)
    return (
      <div className="center-screen">
        <div className="loader">Opening your factory floor…</div>
      </div>
    )

  return (
    <div className="shell">
      <aside className="rail">
        <div className="brand">
          <div className="brand-mark">C</div>
          <div>
            <div className="brand-name">CerebrumDev.ai</div>
            <div className="brand-sub">the factory</div>
          </div>
        </div>
        <nav>
          <NavBtn label="Factory Floor" active={view === 'floor'} onClick={() => setView('floor')} />
          <NavBtn label="Your Platforms" active={view === 'platforms'} onClick={() => setView('platforms')} />
          <NavBtn label="Subscription" active={view === 'subscription'} onClick={() => setView('subscription')} />
          <NavBtn label="Account" active={view === 'account'} onClick={() => setView('account')} />
        </nav>
        <div className="rail-foot">
          <span className="dot" /> session {sessionId.slice(0, 12)}…
        </div>
      </aside>
      <main>
        {view === 'floor' && <Floor sessionId={sessionId} goPlatforms={() => setView('platforms')} />}
        {view === 'platforms' && <Platforms sessionId={sessionId} />}
        {view === 'subscription' && <Subscription />}
        {view === 'account' && <Account onLogout={() => { clearSession(); setAuthed(false) }} />}
      </main>
    </div>
  )
}

function NavBtn({ label, active, onClick }: { label: string; active: boolean; onClick: () => void }) {
  return (
    <button className={`nav-btn ${active ? 'active' : ''}`} onClick={onClick}>
      {label}
    </button>
  )
}

/* ---------------------------------- Auth ---------------------------------- */

type AuthMode = 'login' | 'register' | 'forgot' | 'reset' | 'verify'

export function AuthGate({ onAuthed }: { onAuthed: () => void }) {
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
        if (v && v.email_sent === false && v.dev_verification_token) {
          setToken(v.dev_verification_token)
          setNotice('Account created. SMTP is not configured on this deployment — verify with the token below.')
          setMode('verify')
          return
        }
        if (v && v.email_sent) {
          setNotice('Account created. Check your inbox for the verification link, then sign in.')
          setMode('login')
          return
        }
        onAuthed()
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

/* ---------------------------------- Floor ---------------------------------- */

function Floor({ sessionId, goPlatforms }: { sessionId: string; goPlatforms: () => void }) {
  const [msgs, setMsgs] = useState<ChatMsg[]>([
    {
      role: 'factory',
      text: 'This is the factory floor. Describe the platform you need — I will draft a blueprint, and on your word the factory builds it.',
    },
  ])
  const [input, setInput] = useState('')
  const [busy, setBusy] = useState(false)
  const bottomRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [msgs])

  const send = useCallback(
    async (text: string) => {
      const message = text.trim()
      if (!message || busy) return
      setInput('')
      setBusy(true)
      setMsgs((m) => [...m, { role: 'user', text: message }, { role: 'factory', text: '' }])
      try {
        await chatStream(sessionId, message, (ev: ChatEvent) => {
          const token = chatEventText(ev)
          if (token !== null) {
            setMsgs((m) => {
              const copy = [...m]
              const last = copy[copy.length - 1]
              copy[copy.length - 1] = { ...last, text: last.text + token }
              return copy
            })
            return
          }
          if (ev.event === 'blueprint') {
            const d = ev.data as { summary?: string } | string
            const summary = typeof d === 'string' ? d : d?.summary ?? 'Blueprint drafted.'
            setMsgs((m) => [
              ...m.slice(0, -1),
              { role: 'factory', text: summary, card: 'blueprint' },
            ])
          } else if (ev.event === 'generation') {
            const d = ev.data as { summary?: string } | string
            const summary = typeof d === 'string' ? d : d?.summary ?? 'Platform generated.'
            setMsgs((m) => [
              ...m.slice(0, -1),
              { role: 'factory', text: summary, card: 'generation' },
            ])
          } else if (ev.event === 'error') {
            setMsgs((m) => [
              ...m.slice(0, -1),
              { role: 'factory', text: String(ev.data ?? 'Something went wrong.'), card: 'error' },
            ])
          } else if (ev.event === 'chain' || ev.event === 'rules') {
            setMsgs((m) => [
              ...m.slice(0, -1),
              {
                role: 'factory',
                text: 'That sounds like kit configuration. The floor builds whole platforms — describe the platform you want instead.',
                card: 'info',
              },
            ])
          }
        })
      } catch (e) {
        setMsgs((m) => [
          ...m.slice(0, -1),
          { role: 'factory', text: e instanceof Error ? e.message : 'chat failed', card: 'error' },
        ])
      } finally {
        setBusy(false)
      }
    },
    [busy, sessionId],
  )

  return (
    <div className="floor">
      <header className="page-head">
        <h2>Factory Floor</h2>
        <p className="dim">Tell the factory what to build. The architect drafts, you approve, the generator ships.</p>
      </header>
      <div className="chat-scroll">
        {msgs.map((m, i) => (
          <div key={i} className={`bubble-row ${m.role}`}>
            <div className={`bubble ${m.role} ${m.card ?? ''}`}>
              {m.text || (m.role === 'factory' && busy && i === msgs.length - 1 ? <span className="typing">…</span> : null)}
              {m.card === 'blueprint' && (
                <div className="card-actions">
                  <button disabled={busy} onClick={() => send('approve')}>
                    Approve &amp; build
                  </button>
                </div>
              )}
              {m.card === 'generation' && (
                <div className="card-actions">
                  <button onClick={goPlatforms}>Open Your Platforms</button>
                </div>
              )}
            </div>
          </div>
        ))}
        <div ref={bottomRef} />
      </div>
      <form
        className="composer"
        onSubmit={(e) => {
          e.preventDefault()
          send(input)
        }}
      >
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder='Try: "Build me a secure multi-user platform for my team…"'
          disabled={busy}
        />
        <button type="submit" disabled={busy || !input.trim()}>
          Send
        </button>
      </form>
    </div>
  )
}

/* -------------------------------- Platforms -------------------------------- */

function Platforms({ sessionId }: { sessionId: string }) {
  const [design, setDesign] = useState<ProductDesign | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [downloading, setDownloading] = useState(false)

  const refresh = useCallback(() => {
    product
      .get(sessionId)
      .then(setDesign)
      .catch((e) => setError(e instanceof Error ? e.message : 'failed to load'))
  }, [sessionId])

  useEffect(refresh, [refresh])

  async function download() {
    setDownloading(true)
    setError(null)
    try {
      await downloadProductPackage(sessionId)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'export failed')
    } finally {
      setDownloading(false)
    }
  }

  const bp = design?.blueprint as { product_name?: string; vertical?: string; capabilities?: unknown[] } | null | undefined
  const gen = design?.generation

  return (
    <div className="page">
      <header className="page-head">
        <h2>Your Platforms</h2>
        <p className="dim">What the factory built for you. Download the export and launch it anywhere.</p>
      </header>
      {error && <div className="error-box">{error}</div>}
      {!gen ? (
        <div className="panel empty-state">
          <h3>No platform built yet</h3>
          <p className="dim">Go to the Factory Floor and describe what you need. Your build lands here.</p>
          {design?.blueprint && !design.blueprint_approved && (
            <p className="dim">A blueprint is drafted on the floor — approve it to build.</p>
          )}
          {bp && (
            <p className="dim">
              Draft: <strong>{bp.product_name}</strong> ({bp.vertical})
            </p>
          )}
        </div>
      ) : (
        <div className="panel">
          <h3>{gen.product_id}</h3>
          <dl className="kv">
            <dt>Blueprint</dt>
            <dd>{bp?.product_name ?? '—'}</dd>
            <dt>Inputs hash</dt>
            <dd className="mono">{gen.inputs_hash ?? '—'}</dd>
            <dt>Output</dt>
            <dd className="mono">{gen.output_dir ?? '—'}</dd>
          </dl>
          <button onClick={download} disabled={downloading}>
            {downloading ? 'Packing…' : 'Download platform export (.zip)'}
          </button>
          <button className="ghost" onClick={refresh}>
            Refresh
          </button>
        </div>
      )}
    </div>
  )
}

/* ------------------------------- Subscription ------------------------------- */

function trialDaysLeft(status: BillingStatus): number | null {
  if (typeof status.trial_days_left === 'number') return status.trial_days_left
  if (!status.trial_ends_at) return null
  const end = Date.parse(status.trial_ends_at)
  if (Number.isNaN(end)) return null
  return Math.max(0, Math.ceil((end - Date.now()) / 86_400_000))
}

export function Subscription() {
  const [status, setStatus] = useState<BillingStatus | null>(null)
  const [note, setNote] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    billing
      .status()
      .then(setStatus)
      .catch((e) => setError(e instanceof Error ? e.message : 'failed to load'))
  }, [])

  async function upgrade() {
    setBusy(true)
    setNote(null)
    setError(null)
    try {
      const res = await billing.checkout()
      const url = res.url ?? res.checkout_url
      if (url) {
        window.location.href = url
        return
      }
      setNote('Checkout returned no redirect — the factory team has been notified.')
    } catch {
      setNote(
        'Payments are not connected on this deployment yet — the factory owner links the Stripe account. ' +
          'This is the only piece still pending; your current access is unaffected.',
      )
    } finally {
      setBusy(false)
    }
  }

  async function manage() {
    setBusy(true)
    setNote(null)
    setError(null)
    try {
      const res = await billing.portal()
      const url = res.url ?? res.portal_url
      if (url) {
        window.location.href = url
        return
      }
      setNote('Billing portal returned no redirect.')
    } catch {
      setNote('The billing portal opens once payments are connected on this deployment.')
    } finally {
      setBusy(false)
    }
  }

  const days = status ? trialDaysLeft(status) : null
  const subStatus = (status?.subscription_status ?? status?.status ?? 'trialing') as string

  return (
    <div className="page">
      <header className="page-head">
        <h2>Subscription</h2>
        <p className="dim">Your plan decides how deep the factory builds for you.</p>
      </header>
      {error && <div className="error-box">{error}</div>}
      <div className="panel">
        {status ? (
          <>
            <dl className="kv">
              <dt>Plan</dt>
              <dd className="capitalize">{String(status.plan ?? 'trial')}</dd>
              <dt>Status</dt>
              <dd className="capitalize">{subStatus.replace(/_/g, ' ')}</dd>
              {days !== null && subStatus === 'trialing' && (
                <>
                  <dt>Trial days left</dt>
                  <dd>{days}</dd>
                </>
              )}
              <dt>Factory access</dt>
              <dd>{status.entitled === false ? 'Paused' : 'Active'}</dd>
            </dl>
            <div className="plan-cards">
              <div className="plan-card">
                <h4>Trial</h4>
                <p className="dim">Full factory access while you evaluate. No card required.</p>
              </div>
              <div className="plan-card highlight">
                <h4>Factory</h4>
                <p className="dim">
                  Deeper builds, more sessions, priority generation. Upgrade when you are ready.
                </p>
                <button onClick={upgrade} disabled={busy}>
                  {busy ? 'Working…' : 'Upgrade'}
                </button>
              </div>
            </div>
            {subStatus === 'active' && (
              <button className="ghost" onClick={manage} disabled={busy}>
                Manage billing
              </button>
            )}
          </>
        ) : (
          <p className="dim">Loading…</p>
        )}
        {note && <p className="dim note">{note}</p>}
      </div>
    </div>
  )
}

/* --------------------------------- Account --------------------------------- */

function Account({ onLogout }: { onLogout: () => void }) {
  const [me, setMe] = useState<Record<string, unknown> | null>(null)
  const [verifyToken, setVerifyToken] = useState('')
  const [note, setNote] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    auth.me().then((m) => setMe(m as Record<string, unknown>)).catch(() => setMe(null))
  }, [])

  async function verify(e: FormEvent) {
    e.preventDefault()
    setNote(null)
    setError(null)
    try {
      await auth.verifyEmail(verifyToken)
      setNote('Email verified.')
      const m = await auth.me()
      setMe(m as Record<string, unknown>)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'verification failed')
    }
  }

  return (
    <div className="page">
      <header className="page-head">
        <h2>Account</h2>
      </header>
      <div className="panel">
        <dl className="kv">
          <dt>Email</dt>
          <dd>{me?.email ? String(me.email) : getEmail() ?? '—'}</dd>
          <dt>Email verified</dt>
          <dd>{me?.email_verified ? 'Yes' : 'No'}</dd>
          {!!me?.account_id && (
            <>
              <dt>Account</dt>
              <dd className="mono">{String(me.account_id)}</dd>
            </>
          )}
        </dl>
        {me && !me.email_verified && (
          <form className="verify-row" onSubmit={verify}>
            <input
              type="text"
              required
              placeholder="verification token"
              value={verifyToken}
              onChange={(e) => setVerifyToken(e.target.value)}
            />
            <button type="submit">Verify email</button>
          </form>
        )}
        {note && <p className="dim note">{note}</p>}
        {error && <div className="error-box">{error}</div>}
        <button className="danger" onClick={onLogout}>
          Sign out
        </button>
      </div>
    </div>
  )
}
