import { useCallback, useEffect, useState, type FormEvent } from 'react'
import {
  auth,
  awaitBuild,
  billing,
  downloadProductPackage,
  getEmail,
  product,
  subscriptionDisplay,
  watchBuildStatus,
  type AccountInfo,
  type BillingStatus,
  type BuildStatus,
  type ProductDesign,
} from './api/factory'
import {
  formatFinishedAuthorship,
  formatHeartbeat,
  formatPhaseCounts,
  formatPhaseHeadline,
  stampBuildObservation,
  withClientStall,
} from './buildProgress'
import { LoadingSkeleton } from './LoadingSkeleton'

/* -------------------------------- Platforms -------------------------------- */

export function Platforms({ sessionId }: { sessionId: string }) {
  const [design, setDesign] = useState<ProductDesign | null>(null)
  const [loading, setLoading] = useState(true)
  const [refreshing, setRefreshing] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [downloading, setDownloading] = useState(false)
  const [build, setBuild] = useState<BuildStatus | null>(null)
  const [nowMs, setNowMs] = useState(() => Date.now())

  const refresh = useCallback(
    (opts?: { initial?: boolean }) => {
      setError(null)
      if (opts?.initial) setLoading(true)
      else setRefreshing(true)
      return product
        .get(sessionId)
        .then(async (next) => {
          setDesign(next)
          // product GET does not carry live runner progress (phases / last event).
          // Refresh re-fetches build-status (including while building). Never POST
          // generate from this button. Initial load skips build-status — the
          // watchBuildStatus effect owns the live snapshot; a slow not_started
          // reply must not clobber a succeeded/building tick from the watcher.
          if (!next.generation) {
            setBuild(null)
            return
          }
          if (opts?.initial) return
          const { build: nextBuild } = await product.buildStatus(sessionId)
          setBuild((prev) => stampBuildObservation(nextBuild, prev))
        })
        .catch((e) => setError(e instanceof Error ? e.message : 'failed to load'))
        .finally(() => {
          setLoading(false)
          setRefreshing(false)
        })
    },
    [sessionId],
  )

  useEffect(() => {
    void refresh({ initial: true })
  }, [refresh])

  // Keep watching across succeed → building (pilot reopen). Depend on a
  // primitive so Refresh (new generation object identity) does not tear down
  // the in-flight poll.
  const watchingBuild = Boolean(design?.generation)
  useEffect(() => {
    if (!watchingBuild) return
    const ac = new AbortController()
    void watchBuildStatus(
      sessionId,
      (s) => {
        if (!ac.signal.aborted) {
          setBuild((prev) => stampBuildObservation(s, prev))
        }
      },
      { signal: ac.signal },
    ).catch((e) => {
      if (!ac.signal.aborted) {
        setError(e instanceof Error ? e.message : 'build status failed')
      }
    })
    return () => ac.abort()
  }, [watchingBuild, sessionId])

  const liveBuild = withClientStall(build, nowMs)
  useEffect(() => {
    if (liveBuild?.state !== 'building') return
    const id = window.setInterval(() => setNowMs(Date.now()), 5000)
    return () => window.clearInterval(id)
  }, [liveBuild?.state])

  async function download() {
    setDownloading(true)
    setError(null)
    try {
      const status =
        liveBuild?.state === 'succeeded'
          ? liveBuild
          : await awaitBuild(sessionId, (s) =>
              setBuild((prev) => stampBuildObservation(s, prev)),
            )
      if (!status || status.state === 'failed' || status.state === 'stalled') {
        setError(
          `The build did not pass its gates, so it will not be shipped: ${status?.detail ?? 'unknown reason'}`,
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

  const bp = design?.blueprint as
    | { product_name?: string; vertical?: string; capabilities?: unknown[] }
    | null
    | undefined
  const gen = design?.generation
  const authorship = liveBuild?.authorship
  // Gen present but no status tick yet — keep Download disabled (Building…)
  // so a click cannot race ahead of the watcher into awaitBuild(undefined).
  const building =
    liveBuild?.state === 'building' ||
    liveBuild?.state === 'not_started' ||
    (Boolean(gen) && !liveBuild)
  const stalled = liveBuild?.state === 'stalled'
  const buildNote = (() => {
    if (!liveBuild) return null
    if (liveBuild.state === 'stalled') {
      return `Build stalled — ${liveBuild.detail ?? 'no recent activity'}`
    }
    if (liveBuild.state !== 'building') return null
    const headline = formatPhaseHeadline(liveBuild)
    const counts = formatPhaseCounts(liveBuild)
    const last = liveBuild.last_event || liveBuild.activity
    const heartbeat = formatHeartbeat(liveBuild, nowMs)
    const bits = [`Coding agent at work — ${headline}`]
    if (counts) bits.push(counts)
    if (last) bits.push(`last: ${last}`)
    if (heartbeat) bits.push(heartbeat)
    return bits.join(' · ')
  })()

  return (
    <div className="page">
      <header className="page-head">
        <h2>Your Platforms</h2>
        <p className="dim">What the factory built for you. Download the export and launch it anywhere.</p>
      </header>
      {error && <div className="error-box">{error}</div>}
      {buildNote && (
        <div className={'panel dim' + (stalled ? ' error-box' : '')} role="status">
          {buildNote}
        </div>
      )}
      {loading ? (
        <LoadingSkeleton label="Loading platforms" />
      ) : !gen ? (
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
            <dd>
              {typeof gen.engine === 'string'
                ? gen.engine
                : liveBuild?.state === 'succeeded'
                  ? 'runner'
                  : '—'}
            </dd>
            <dt>Inputs hash</dt>
            <dd className="mono">{gen.inputs_hash ?? '—'}</dd>
            <dt>Output</dt>
            <dd className="mono">{gen.output_dir ?? '—'}</dd>
          </dl>
          {liveBuild?.state === 'succeeded' && authorship && (
            <>
              <p className="bp-summary">
                {formatFinishedAuthorship(authorship) ??
                  'Coding agent finished. Download it from Your Platforms.'}
              </p>
              {authorship.coder_failures &&
                Object.keys(authorship.coder_failures).length > 0 && (
                  <p className="dim">
                    Coder skip: {Object.values(authorship.coder_failures)[0]}
                  </p>
                )}
            </>
          )}
          <button onClick={download} disabled={downloading || building || stalled || refreshing}>
            {building ? 'Building…' : downloading ? 'Packing…' : 'Download platform export (.zip)'}
          </button>
          <button
            className="ghost"
            type="button"
            onClick={() => void refresh()}
            disabled={refreshing}
            aria-busy={refreshing || undefined}
          >
            {refreshing ? 'Refreshing…' : 'Refresh'}
          </button>
        </div>
      )}
    </div>
  )
}

export function Subscription() {
  const [status, setStatus] = useState<BillingStatus | null>(null)
  const [loading, setLoading] = useState(true)
  const [note, setNote] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const loadStatus = useCallback(() => {
    setError(null)
    setLoading(true)
    billing
      .status()
      .then(setStatus)
      .catch((e) => {
        setStatus(null)
        setError(e instanceof Error ? e.message : 'failed to load')
      })
      .finally(() => setLoading(false))
  }, [])

  useEffect(loadStatus, [loadStatus])

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

  const view = status ? subscriptionDisplay(status) : null
  const subStatus = view?.statusLabel ?? ''

  return (
    <div className="page">
      <header className="page-head">
        <h2>Subscription</h2>
        <p className="dim">Your plan decides how deep the factory builds for you.</p>
      </header>
      {error && (
        <div className="error-box">
          {error}
          <div>
            <button className="ghost" type="button" onClick={loadStatus}>
              Retry
            </button>
          </div>
        </div>
      )}
      {loading ? (
        <LoadingSkeleton label="Loading subscription" lines={3} />
      ) : (
        <div className="panel">
          {status && view ? (
            <>
              <dl className="kv">
                <dt>Plan</dt>
                <dd className="capitalize">{view.planLabel}</dd>
                <dt>Status</dt>
                <dd className="capitalize">{view.statusLabel}</dd>
                {view.showTrialDays && (
                  <>
                    <dt>Trial days left</dt>
                    <dd>{view.trialDaysLeft}</dd>
                  </>
                )}
                <dt>Factory access</dt>
                <dd>{view.accessLabel}</dd>
              </dl>
              <div className="plan-cards">
                {view.currentPlan === 'trial' && (
                  <div className="plan-card highlight" data-plan="trial" aria-current="true">
                    <p className="plan-current">Current</p>
                    <h4>Trial</h4>
                    <p className="dim">Full factory access while you evaluate. No card required.</p>
                  </div>
                )}
                <div
                  className={`plan-card${view.currentPlan === 'factory' ? ' highlight' : ''}`}
                  data-plan="factory"
                  aria-current={view.currentPlan === 'factory' ? 'true' : undefined}
                >
                  {view.currentPlan === 'factory' && <p className="plan-current">Current</p>}
                  <h4>Factory</h4>
                  <p className="dim">
                    Deeper builds, more sessions, priority generation.
                    {view.currentPlan !== 'factory' ? ' Upgrade when you are ready.' : ''}
                  </p>
                  {view.currentPlan !== 'factory' && (
                    <button onClick={upgrade} disabled={busy}>
                      {busy ? 'Working…' : 'Upgrade'}
                    </button>
                  )}
                </div>
              </div>
              {subStatus === 'active' && (
                <button className="ghost" onClick={manage} disabled={busy}>
                  Manage billing
                </button>
              )}
              {status.checkout_available === false && (
                <p className="dim note">
                  Payments are not connected on this deployment yet — the factory owner
                  links the Stripe account. Upgrade still says so instead of opening a
                  blank checkout.{" "}
                  {status.enforcement
                    ? 'Billing enforcement is on for this deployment, so sessions and factory runs stop once a trial ends — until the owner connects Stripe or updates your subscription.'
                    : 'Your current access is unaffected.'}
                </p>
              )}
            </>
          ) : error ? (
            <p className="dim">Could not load subscription status.</p>
          ) : (
            <p className="dim">Could not load subscription status.</p>
          )}
          {note && <p className="dim note">{note}</p>}
        </div>
      )}
    </div>
  )
}

function verifiedLabel(me: AccountInfo | null): string {
  if (typeof me?.email_verified !== 'boolean') return '—'
  return me.email_verified ? 'Yes' : 'No'
}

export function Account({
  onLogout,
  initialMe,
}: {
  onLogout: () => void
  initialMe?: AccountInfo | null
}) {
  const [me, setMe] = useState<AccountInfo | null>(initialMe ?? null)
  const [verifyToken, setVerifyToken] = useState('')
  const [note, setNote] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    let cancelled = false
    auth
      .me()
      .then((m) => {
        if (!cancelled) setMe(m)
      })
      .catch(() => {
        if (!cancelled && !initialMe) setMe(null)
      })
    return () => {
      cancelled = true
    }
  }, [initialMe])

  async function verify(e: FormEvent) {
    e.preventDefault()
    setNote(null)
    setError(null)
    setBusy(true)
    try {
      await auth.verifyEmail(verifyToken)
      setNote('Email verified.')
      const m = await auth.me()
      setMe(m)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'verification failed')
    } finally {
      setBusy(false)
    }
  }

  async function resend() {
    setNote(null)
    setError(null)
    setBusy(true)
    try {
      const res = await auth.resendVerification()
      if (res.already_verified) {
        setNote('Email already verified.')
        const m = await auth.me()
        setMe(m)
        return
      }
      const v = res.verification
      if (v?.dev_verification_token) {
        setVerifyToken(v.dev_verification_token)
        setNote(
          v.note ??
            'SMTP is not configured on this deployment — the verification token is filled in below.',
        )
      } else if (v?.email_sent) {
        setNote('Verification email sent. Check your inbox.')
      } else {
        setNote(v?.note ?? 'Could not send a verification email.')
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'resend failed')
    } finally {
      setBusy(false)
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
          <dd>{verifiedLabel(me)}</dd>
          <dt>Account</dt>
          <dd className="mono">{me?.account_id ? String(me.account_id) : '—'}</dd>
        </dl>
        {me && me.email_verified === false && (
          <>
            <form className="verify-row" onSubmit={verify}>
              <input
                type="text"
                required
                placeholder="verification token"
                value={verifyToken}
                onChange={(e) => setVerifyToken(e.target.value)}
              />
              <button type="submit" disabled={busy}>
                {busy ? 'Working…' : 'Verify email'}
              </button>
            </form>
            <button type="button" className="ghost" disabled={busy} onClick={() => void resend()}>
              Resend verification email
            </button>
          </>
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
