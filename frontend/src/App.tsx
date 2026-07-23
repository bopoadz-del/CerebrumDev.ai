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
  }, [authed])

  if (!authed) return <AuthGate onAuthed={() => setAuthed(true)} />
  if (bootError)
    return (
      <div className="center-screen">
        <div className="panel narrow">
          <h2>Factory unreachable</h2>
          <p className="dim">{bootError}</p>
          <button onClick={() => setBootError(null)}>Retry</button>
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

function AuthGate({ onAuthed }: { onAuthed: () => void }) {
  const [mode, setMode] = useState<'login' | 'register'>('login')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function submit(e: FormEvent) {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      const res = mode === 'login' ? await auth.login(email, password) : await auth.register(email, password)
      setSession(res.login_token, email)
      onAuthed()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'authentication failed')
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
        <p className="dim center-text">One account. Tell the factory. Receive your platform.</p>
        <input
          type="email"
          required
          placeholder="you@company.com"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
        />
        <input
          type="password"
          required
          minLength={8}
          placeholder="password (8+ characters)"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
        />
        {error && <div className="error-box">{error}</div>}
        <button type="submit" disabled={busy}>
          {busy ? 'Working…' : mode === 'login' ? 'Enter the factory' : 'Create your account'}
        </button>
        <button
          type="button"
          className="link"
          onClick={() => setMode(mode === 'login' ? 'register' : 'login')}
        >
          {mode === 'login' ? 'New here? Create an account' : 'Have an account? Sign in'}
        </button>
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

function Subscription() {
  const [status, setStatus] = useState<BillingStatus | null>(null)
  const [note, setNote] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    billing
      .status()
      .then(setStatus)
      .catch((e) => setError(e instanceof Error ? e.message : 'failed to load'))
  }, [])

  async function upgrade() {
    setNote(null)
    setError(null)
    try {
      const res = await billing.checkout()
      const url = res.url ?? res.checkout_url
      if (url) {
        window.open(url, '_blank')
      } else {
        setNote('Billing is being connected — checkout opens here when the factory flips it on.')
      }
    } catch (e) {
      setNote(
        `Billing is being connected — the only piece still pending at the factory. (${e instanceof Error ? e.message : 'checkout unavailable'})`,
      )
    }
  }

  return (
    <div className="page">
      <header className="page-head">
        <h2>Subscription</h2>
        <p className="dim">Your plan decides how deep the factory builds for you.</p>
      </header>
      {error && <div className="error-box">{error}</div>}
      <div className="panel">
        {status ? (
          <dl className="kv">
            {Object.entries(status).map(([k, v]) => (
              <div className="kv-row" key={k}>
                <dt>{k.replace(/_/g, ' ')}</dt>
                <dd>{v === null || v === undefined ? '—' : String(v)}</dd>
              </div>
            ))}
          </dl>
        ) : (
          <p className="dim">Loading…</p>
        )}
        <button onClick={upgrade}>Upgrade</button>
        {note && <p className="dim note">{note}</p>}
      </div>
    </div>
  )
}

/* --------------------------------- Account --------------------------------- */

function Account({ onLogout }: { onLogout: () => void }) {
  const [me, setMe] = useState<Record<string, unknown> | null>(null)

  useEffect(() => {
    auth.me().then((m) => setMe(m as Record<string, unknown>)).catch(() => setMe(null))
  }, [])

  return (
    <div className="page">
      <header className="page-head">
        <h2>Account</h2>
      </header>
      <div className="panel">
        <dl className="kv">
          <dt>Email</dt>
          <dd>{me?.email ? String(me.email) : getEmail() ?? '—'}</dd>
          {!!me?.account_id && (
            <>
              <dt>Account</dt>
              <dd className="mono">{String(me.account_id)}</dd>
            </>
          )}
        </dl>
        <button className="danger" onClick={onLogout}>
          Sign out
        </button>
      </div>
    </div>
  )
}
