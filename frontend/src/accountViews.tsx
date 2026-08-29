import { useCallback, useEffect, useState, type FormEvent } from 'react'
import {
  auth,
  awaitBuild,
  billing,
  downloadProductPackage,
  getEmail,
  product,
  factoryAccessPaused,
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
} from './buildProgress'

/* -------------------------------- Platforms -------------------------------- */

export function Platforms({ sessionId }: { sessionId: string }) {
  const [design, setDesign] = useState<ProductDesign | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [downloading, setDownloading] = useState(false)
  const [build, setBuild] = useState<BuildStatus | null>(null)

  const refresh = useCallback(() => {
    setError(null)
    product
      .get(sessionId)
      .then(async (next) => {
        setDesign(next)
        // product GET does not carry live runner progress (phases / last event).
        // Always re-fetch build-status when a generation exists — including while
        // state === 'building'. Never POST generate from this button.
        if (!next.generation) return
        const { build } = await product.buildStatus(sessionId)
        setBuild(build)
      })
      .catch((e) => setError(e instanceof Error ? e.message : 'failed to load'))
  }, [sessionId])

  useEffect(refresh, [refresh])

  // Poll once a generation exists. Depend on a primitive so Refresh (new
  // generation object identity) does not tear down the in-flight poll.
  const watchingBuild = Boolean(design?.generation)
  useEffect(() => {
    if (!watchingBuild) return
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
  }, [watchingBuild, sessionId])

  async function download() {
    setDownloading(true)
    setError(null)
    try {
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
    const headline = formatPhaseHeadline(build)
    const counts = formatPhaseCounts(build)
    const last = build.last_event || build.activity
    const heartbeat = formatHeartbeat(build)
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
              <dd>{factoryAccessPaused(status) ? 'Paused' : 'Active'}</dd>
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
        ) : (
          <p className="dim">Loading…</p>
        )}
        {note && <p className="dim note">{note}</p>}
      </div>
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
    try {
      await auth.verifyEmail(verifyToken)
      setNote('Email verified.')
      const m = await auth.me()
      setMe(m)
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
          <dd>{verifiedLabel(me)}</dd>
          <dt>Account</dt>
          <dd className="mono">{me?.account_id ? String(me.account_id) : '—'}</dd>
        </dl>
        {me && me.email_verified === false && (
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
