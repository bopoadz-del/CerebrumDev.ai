import { useCallback, useEffect, useRef, useState, type FormEvent } from 'react'
import {
  ApiError,
  auth,
  billing,
  chatEventText,
  chatStream,
  clearSession,
  awaitBuild,
  downloadProductPackage,
  getEmail,
  getToken,
  product,
  sessions,
  setSession,
  type BillingStatus,
  type BuildStatus,
  type ChatEvent,
  type ProductDesign,
} from './api/factory'

type View = 'floor' | 'platforms' | 'subscription' | 'account'

interface Capability {
  id: string
  description?: string
  strategy_hint?: string
  block_ids?: string[]
}

interface ChatMsg {
  role: 'user' | 'factory' | 'system'
  text: string
  card?: 'blueprint' | 'generation' | 'error' | 'info'
  engine?: string
  triggeredBy?: string
  blueprint?: {
    product_name?: string
    vertical?: string
    summary?: string
    capabilities?: Capability[]
    drafting_mode?: string
    drafting_note?: string
  }
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

/* ------------------------------ Blueprint card ----------------------------- */

function humanize(id: string): string {
  return id
    .replace(/_/g, ' ')
    .replace(/\b\w/g, (ch) => ch.toUpperCase())
}

export function BlueprintCard({
  blueprint,
  busy,
  onApprove,
  onRefine,
}: {
  blueprint: NonNullable<ChatMsg['blueprint']>
  busy: boolean
  onApprove: (excludedIds: string[]) => void
  onRefine: (text: string) => void
}) {
  const caps = blueprint.capabilities ?? []
  // Tick what you want in the build — not every platform needs every
  // proposed capability. Unticked ones are removed before generation.
  const [ticked, setTicked] = useState<Record<string, boolean>>(() =>
    Object.fromEntries(caps.map((c) => [c.id, true])),
  )
  const excluded = caps.filter((c) => ticked[c.id] === false).map((c) => c.id)
  const selectedCount = caps.length - excluded.length
  return (
    <div className="blueprint-card">
      <div className="bp-header">
        <strong>{blueprint.product_name ?? 'Untitled platform'}</strong>
        <span className="bp-vertical">{blueprint.vertical ?? '—'}</span>
        {blueprint.drafting_mode && (
          <span
            className={`bp-drafting-mode ${blueprint.drafting_mode}`}
            title={blueprint.drafting_note ?? undefined}
          >
            {blueprint.drafting_mode === 'architect_llm'
              ? 'architect LLM'
              : blueprint.drafting_mode === 'golden_steward'
                ? 'golden blueprint'
                : 'template fallback — no LLM'}
          </span>
        )}
      </div>
      {blueprint.drafting_mode === 'keyword_fallback' && (
        <p className="bp-summary dim">
          The architect LLM did not draft this blueprint
          {blueprint.drafting_note ? ` (${blueprint.drafting_note})` : ''} — it was
          assembled from deterministic templates.
        </p>
      )}
      {blueprint.summary && <p className="bp-summary">{blueprint.summary}</p>}
      <h4>Capabilities ({caps.length})</h4>
      <p className="bp-pick-hint dim">Tick what the platform should include, then approve.</p>
      <ul className="bp-caps">
        {caps.map((c) => (
          <li key={c.id} className={ticked[c.id] === false ? 'bp-cap-excluded' : ''}>
            <label className="bp-cap-pick">
              <input
                type="checkbox"
                checked={ticked[c.id] !== false}
                disabled={busy}
                onChange={(e) =>
                  setTicked((t) => ({ ...t, [c.id]: e.target.checked }))
                }
              />
              <span className="bp-cap-id">{humanize(c.id)}</span>
              <span className={`bp-strategy ${c.strategy_hint ?? 'REUSE'}`}>{c.strategy_hint ?? 'REUSE'}</span>
            </label>
            {c.description && <p className="bp-cap-desc">{c.description}</p>}
            {c.block_ids && c.block_ids.length > 0 && (
              <p className="bp-cap-blocks">blocks: {c.block_ids.join(', ')}</p>
            )}
          </li>
        ))}
      </ul>
      <div className="card-actions">
        <button disabled={busy || selectedCount === 0} onClick={() => onApprove(excluded)}>
          {excluded.length > 0
            ? `Approve & build (${selectedCount} of ${caps.length})`
            : 'Approve & build'}
        </button>
      </div>
      <p className="bp-refine-hint">
        Refine:{' '}
        <button className="link" disabled={busy} onClick={() => onRefine('list capabilities')}>
          list capabilities
        </button>{' '}
        ·{' '}
        <button className="link" disabled={busy} onClick={() => onRefine('add capability payments')}>
          add payments
        </button>{' '}
        ·{' '}
        <button className="link" disabled={busy} onClick={() => onRefine('remove capability audit')}>
          remove audit
        </button>
      </p>
    </div>
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

const KERNEL_JOBS: Record<string, { title: string; agent: boolean }> = {
  COLLECTOR: { title: 'Binding surveyor', agent: true },
  CLONER: { title: 'Block stocker', agent: false },
  WRITER: { title: 'Platform manufacturer', agent: true },
  TESTER: { title: 'Acceptance inspector', agent: true },
  STORE_MANAGER: { title: 'Store registrar', agent: false },
}

function KernelStrip({ build }: { build: BuildStatus | null }) {
  const phases = build?.phases?.length
    ? build.phases
    : ['COLLECTOR', 'CLONER', 'WRITER', 'TESTER', 'STORE_MANAGER']
  const done = new Set(build?.completed ?? [])
  return (
    <ol className="kernel-strip">
      {phases.map((phase) => {
        const job = KERNEL_JOBS[phase]
        return (
          <li key={phase} className={done.has(phase) ? 'done' : undefined}>
            <span className="kernel-id">{phase}</span>
            {job ? <span className="kernel-title">{job.title}</span> : null}
            {job?.agent ? <span className="kernel-agent">agent</span> : null}
          </li>
        )
      })}
    </ol>
  )
}

function coderTakeoverNote(build: BuildStatus | null): string | null {
  if (!build) return null
  if (build.state === 'succeeded') {
    const n = build.authorship?.agent_written
    return n != null
      ? `Coding agent finished — ${n} agent-written artifact(s). Download it from Your Platforms.`
      : 'Coding agent finished. Download it from Your Platforms.'
  }
  if (build.state === 'failed' || build.state === 'stalled') {
    return `The coding agent stopped: ${build.detail ?? 'build did not pass its gates'}.`
  }
  const done = build.phases_done ?? 0
  const total = build.phases_total ?? 5
  if (build.activity) {
    return `Writing your platform — ${done}/${total} phases (${build.activity})`
  }
  const phase = build.completed?.length ? build.completed[build.completed.length - 1] : 'starting'
  return `Writing your platform — ${done}/${total} phases (last: ${phase})`
}

/* ---------------------------------- Floor ---------------------------------- */

export function Floor({ sessionId, goPlatforms }: { sessionId: string; goPlatforms: () => void }) {
  const [msgs, setMsgs] = useState<ChatMsg[]>([
    {
      role: 'factory',
      text: 'This is the factory floor. Describe the platform you need — I will draft a blueprint, and when you approve the feature list the coding agent takes over and writes it.',
    },
  ])
  const [input, setInput] = useState('')
  const [busy, setBusy] = useState(false)
  const [coderBuild, setCoderBuild] = useState<BuildStatus | null>(null)
  const [coderActive, setCoderActive] = useState(false)
  const bottomRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    bottomRef.current?.scrollIntoView?.({ behavior: 'smooth' })
  }, [msgs, coderBuild])

  useEffect(() => {
    if (!coderActive) return
    let cancelled = false
    void awaitBuild(sessionId, (s) => {
      if (!cancelled) setCoderBuild(s)
    })
      .then((s) => {
        if (!cancelled) setCoderBuild(s)
      })
      .catch(() => {
        /* buildStatus 404/unknown is shown on Your Platforms; keep the takeover panel. */
      })
    return () => {
      cancelled = true
    }
  }, [coderActive, sessionId])

  // Core send without the busy guard — reused by `send` (single message)
  // and `approveWithSelection` (sequential remove-capability commands +
  // approve, driven by the blueprint card's checkboxes).
  const sendCore = useCallback(
    async (message: string) => {
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
            const d = (typeof ev.data === 'string' ? JSON.parse(ev.data) : ev.data) as {
              summary?: string
              blueprint?: ChatMsg['blueprint']
            }
            const summary = d?.summary ?? 'Blueprint drafted.'
            setMsgs((m) => [
              ...m.slice(0, -1),
              {
                role: 'factory',
                text: summary,
                card: 'blueprint',
                blueprint: d?.blueprint,
              },
            ])
          } else if (ev.event === 'generation') {
            // Same double-encoded payload as 'blueprint': parse before use so
            // the bubble shows the summary, not the raw JSON blob.
            const d = (typeof ev.data === 'string' ? JSON.parse(ev.data) : ev.data) as {
              summary?: string
              triggered_by?: string
              generation?: { engine?: string; triggered_by?: string }
            } | null
            const summary = d?.summary ?? 'Platform generated.'
            const engine = d?.generation?.engine
            const triggeredBy = d?.triggered_by ?? d?.generation?.triggered_by
            if (engine === 'runner') setCoderActive(true)
            setMsgs((m) => [
              ...m.slice(0, -1),
              { role: 'factory', text: summary, card: 'generation', engine, triggeredBy },
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
      }
    },
    [sessionId],
  )

  const send = useCallback(
    async (text: string) => {
      const message = text.trim()
      if (!message || busy) return
      setInput('')
      setBusy(true)
      try {
        await sendCore(message)
      } finally {
        setBusy(false)
      }
    },
    [busy, sendCore],
  )

  // Blueprint card checkboxes: unticked capabilities are removed from the
  // pending blueprint (one refine command each), then the build is approved.
  const approveWithSelection = useCallback(
    async (excludedIds: string[]) => {
      if (busy) return
      setBusy(true)
      try {
        for (const id of excludedIds) {
          await sendCore(`remove capability ${id}`)
        }
        await sendCore('approve')
      } finally {
        setBusy(false)
      }
    },
    [busy, sendCore],
  )

  const coderBuilding =
    coderActive &&
    coderBuild?.state !== 'succeeded' &&
    coderBuild?.state !== 'failed' &&
    coderBuild?.state !== 'stalled'

  return (
    <div className="floor">
      <header className="page-head">
        <h2>Factory Floor</h2>
        <p className="dim">Describe the platform. Approve the feature list. The coding agent takes over and writes it.</p>
        <p className="dim notice-not-yet">
          What this is not yet: the factory generates a working prototype — real code,
          tests and deploy files — not a finished production system. Third-party
          integrations in generated products are stubs until you connect your own
          credentials, deployment is a step you run rather than something that happens
          for you, and free-trial accounts have server-enforced caps on generations,
          daily chat and exports. Answers are grounding-checked: when a claim can't be
          verified it is withheld, not invented.
        </p>
      </header>
      <div className="chat-scroll">
        {msgs.map((m, i) => (
          <div key={i} className={`bubble-row ${m.role}`}>
            <div className={`bubble ${m.role} ${m.card ?? ''}`}>
              {m.text || (m.role === 'factory' && busy && i === msgs.length - 1 ? <span className="typing">…</span> : null)}
              {m.card === 'blueprint' && m.blueprint && (
                <BlueprintCard
                  blueprint={m.blueprint}
                  busy={busy || coderActive}
                  onApprove={(excludedIds) => void approveWithSelection(excludedIds)}
                  onRefine={(text) => send(text)}
                />
              )}
              {m.card === 'generation' && (
                <div className="card-actions">
                  {m.engine === 'runner' && (
                    <span
                      className="bp-drafting-mode architect_llm"
                      title="The coding agent took over after you approved the feature list"
                    >
                      coding agent
                    </span>
                  )}
                  {m.triggeredBy === 'chat_llm' && (
                    <span
                      className="bp-drafting-mode architect_llm"
                      title="The Floor chat LLM called start_coder"
                    >
                      chat LLM
                    </span>
                  )}
                  <button onClick={goPlatforms}>Open Your Platforms</button>
                </div>
              )}
            </div>
          </div>
        ))}
        <div ref={bottomRef} />
      </div>
      {coderActive && (
        <div className="coder-takeover" role="status">
          <h3>Coding agent has taken over</h3>
          <KernelStrip build={coderBuild} />
          <p>
            {coderTakeoverNote(coderBuild) ??
              'The feature list is approved. The coding agent is starting WRITER now.'}
          </p>
        </div>
      )}
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
          placeholder={
            coderBuilding
              ? 'The coding agent has taken over this floor…'
              : 'Try: "Build me a secure multi-user platform for my team…"'
          }
          disabled={busy || coderBuilding}
        />
        <button type="submit" disabled={busy || coderBuilding || !input.trim()}>
          Send
        </button>
      </form>
    </div>
  )
}

/* -------------------------------- Platforms -------------------------------- */

export function Platforms({ sessionId }: { sessionId: string }) {
  const [design, setDesign] = useState<ProductDesign | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [downloading, setDownloading] = useState(false)
  const [build, setBuild] = useState<BuildStatus | null>(null)

  const refresh = useCallback(() => {
    product
      .get(sessionId)
      .then(setDesign)
      .catch((e) => setError(e instanceof Error ? e.message : 'failed to load'))
  }, [sessionId])

  useEffect(refresh, [refresh])

  // Poll the ledger as soon as a generation exists so "Your Platforms"
  // shows the coding agent at work instead of a Download button that
  // only starts telling the truth after a click.
  useEffect(() => {
    if (!design?.generation) return
    let cancelled = false
    void awaitBuild(sessionId, (s) => {
      if (!cancelled) setBuild(s)
    }).then((s) => {
      if (!cancelled) setBuild(s)
    }).catch((e) => {
      if (!cancelled) setError(e instanceof Error ? e.message : 'build status failed')
    })
    return () => {
      cancelled = true
    }
  }, [design?.generation, sessionId])

  async function download() {
    setDownloading(true)
    setError(null)
    try {
      // A runner build is a background job -- the coding agent writes the
      // platform, which takes minutes. Wait for it (reporting phase
      // progress) instead of hitting the package endpoint and getting a 409,
      // and never present a failed build as a download.
      const status =
        build?.state === 'succeeded'
          ? build
          : await awaitBuild(sessionId, setBuild)
      if (status.state === 'failed' || status.state === 'stalled') {
        setError(
          `The build did not pass its gates, so it will not be shipped: ${status.detail ?? 'unknown reason'}`,
        )
        return
      }
      await downloadProductPackage(sessionId)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'export failed')
    } finally {
      setDownloading(false)
    }
  }

  const bp = design?.blueprint as { product_name?: string; vertical?: string; capabilities?: unknown[] } | null | undefined
  const gen = design?.generation
  const authorship = build?.authorship
  const building = build?.state === 'building'
  const buildNote = (() => {
    if (!build || build.state !== 'building') return null
    const done = build.phases_done ?? 0
    const total = build.phases_total ?? 5
    if (build.activity) {
      return `Coding agent at work — ${done}/${total} phases (${build.activity})`
    }
    const phase = build.completed?.length ? build.completed[build.completed.length - 1] : 'starting'
    return `Building your platform — ${done}/${total} phases (last: ${phase})`
  })()

  return (
    <div className="page">
      <header className="page-head">
        <h2>Your Platforms</h2>
        <p className="dim">What the factory built for you. Download the export and launch it anywhere.</p>
      </header>
      {error && <div className="error-box">{error}</div>}
      {buildNote && <div className="panel dim">{buildNote}</div>}
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
            <dt>Engine</dt>
            <dd>{typeof gen.engine === 'string' ? gen.engine : (build?.state === 'succeeded' ? 'runner' : '—')}</dd>
            <dt>Inputs hash</dt>
            <dd className="mono">{gen.inputs_hash ?? '—'}</dd>
            <dt>Output</dt>
            <dd className="mono">{gen.output_dir ?? '—'}</dd>
          </dl>
          {build?.state === 'succeeded' && authorship && (
            <>
              <p className="bp-summary">
                {typeof authorship.agent_written === 'number' && authorship.agent_written > 0
                  ? `Coding agent wrote ${authorship.agent_written} of ${authorship.artifacts ?? '?'} artifacts.`
                  : 'Coding agent wrote 0 artifacts — this platform is templated (coder idle or no LLM key).'}
              </p>
              {authorship.coder_failures &&
                Object.keys(authorship.coder_failures).length > 0 && (
                  <p className="dim">
                    Coder skip: {Object.values(authorship.coder_failures)[0]}
                  </p>
                )}
            </>
          )}
          <button onClick={download} disabled={downloading || building}>
            {building ? 'Building…' : downloading ? 'Packing…' : 'Download platform export (.zip)'}
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
