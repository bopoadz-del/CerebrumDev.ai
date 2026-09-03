import { useEffect, useState } from 'react'
import {
  auth,
  billing,
  clearSession,
  domains,
  factoryAccessPaused,
  isDomainStoreUnreachable,
  isEmailNotVerifiedError,
  isTransientBootError,
  isUnauthenticatedError,
  sessions,
  setSession,
  signOut,
  type AccountInfo,
} from './api/factory'
import { AuthGate, VerifyEmailGate } from './authGates'
import { Account, Floor, Platforms, Subscription } from './factoryViews'

export { AuthGate, VerifyEmailGate } from './authGates'
export { Account, BlueprintCard, Floor, Platforms, Subscription } from './factoryViews'

export type View = 'floor' | 'platforms' | 'subscription' | 'account'

const VIEW_PATHS: Record<View, string> = {
  floor: '/',
  platforms: '/platforms',
  subscription: '/subscription',
  account: '/account',
}

/** Map a browser pathname to the signed-in shell view (deep-link / reload safe). */
export function viewFromPath(pathname: string): View {
  if (pathname === '/account') return 'account'
  if (pathname === '/subscription') return 'subscription'
  if (pathname === '/platforms') return 'platforms'
  return 'floor'
}

export function pathFromView(view: View): string {
  return VIEW_PATHS[view]
}

/** Public auth URLs must not become a full-page "Factory unreachable" on a race. */
export function isPublicAuthPath(pathname: string): boolean {
  return (
    pathname === '/register' ||
    pathname === '/login' ||
    pathname === '/forgot-password' ||
    pathname === '/reset-password' ||
    pathname === '/verify-email'
  )
}

/** Signed-in visits here stay on the floor — do not show the auth form or sign out. */
export function isSignedInAuthRedirectPath(pathname: string): boolean {
  return pathname === '/login' || pathname === '/register'
}

const NAV_ITEMS: { view: View; label: string; shortLabel: string; icon: string }[] = [
  { view: 'floor', label: 'Factory Floor', shortLabel: 'Floor', icon: '⌂' },
  { view: 'platforms', label: 'Your Platforms', shortLabel: 'Platforms', icon: '▣' },
  { view: 'subscription', label: 'Subscription', shortLabel: 'Plan', icon: '◈' },
  { view: 'account', label: 'Account', shortLabel: 'Account', icon: '○' },
]

export default function App() {
  const [authed, setAuthed] = useState<boolean | null>(null)
  const [view, setView] = useState<View>(() =>
    typeof window === 'undefined' ? 'floor' : viewFromPath(window.location.pathname),
  )
  const [sessionId, setSessionId] = useState<string | null>(null)
  const [bootError, setBootError] = useState<string | null>(null)
  const [needsEmailVerify, setNeedsEmailVerify] = useState(false)
  const [pendingDevToken, setPendingDevToken] = useState<string | null>(null)
  const [domainStoreNotice, setDomainStoreNotice] = useState<string | null>(null)
  const [bootNonce, setBootNonce] = useState(0)
  const [accountMe, setAccountMe] = useState<AccountInfo | null>(null)
  const [accessPaused, setAccessPaused] = useState(false)
  const [alreadySignedInNotice, setAlreadySignedInNotice] = useState(false)

  function go(next: View) {
    setView(next)
    setAlreadySignedInNotice(false)
    const path = pathFromView(next)
    if (typeof window !== 'undefined' && window.location.pathname !== path) {
      window.history.pushState(null, '', path)
    }
  }

  useEffect(() => {
    let cancelled = false
    ;(async () => {
      const params = new URLSearchParams(window.location.search)
      const linkToken = params.get('token')
      if (linkToken && window.location.pathname === '/verify-email') {
        window.history.replaceState(null, '', '/')
        try {
          await auth.verifyEmail(linkToken)
        } catch {
          /* still unverified; boot surfaces the verify gate */
        }
      }
      try {
        const me = await auth.me()
        if (me.email) setSession(String(me.email))
        const [list, bill] = await Promise.all([
          sessions.list(),
          billing.status().catch(() => null),
        ])
        const arr = Array.isArray(list) ? list : list.sessions ?? []
        let sid = arr[0]?.session_id
        if (!sid) {
          const created = await sessions.create()
          sid = created.session_id
        }
        if (!cancelled) {
          setAccountMe(me)
          setAccessPaused(factoryAccessPaused(bill))
          setNeedsEmailVerify(false)
          setBootError(null)
          setSessionId(sid ?? null)
          setAuthed(true)
          if (isSignedInAuthRedirectPath(window.location.pathname)) {
            window.history.replaceState(null, '', '/')
            setView('floor')
            setAlreadySignedInNotice(true)
          } else {
            setView(viewFromPath(window.location.pathname))
          }
        }
      } catch (e) {
        if (!cancelled) {
          if (isUnauthenticatedError(e)) {
            clearSession()
            setNeedsEmailVerify(false)
            setSessionId(null)
            setAuthed(false)
          } else if (isEmailNotVerifiedError(e)) {
            setBootError(null)
            setNeedsEmailVerify(true)
            setAuthed(true)
          } else if (isTransientBootError(e) && isPublicAuthPath(window.location.pathname)) {
            clearSession()
            setNeedsEmailVerify(false)
            setSessionId(null)
            setBootError(null)
            setAuthed(false)
          } else {
            setBootError(e instanceof Error ? e.message : 'backend unreachable')
            setAuthed(true)
          }
        }
      }
    })()
    return () => {
      cancelled = true
    }
  }, [bootNonce])

  useEffect(() => {
    if (!sessionId) return
    let cancelled = false
    domains
      .list()
      .then(() => {
        if (!cancelled) setDomainStoreNotice(null)
      })
      .catch((e) => {
        if (!cancelled && isDomainStoreUnreachable(e)) {
          setDomainStoreNotice('Domain store unreachable')
        }
      })
    return () => {
      cancelled = true
    }
  }, [sessionId])

  useEffect(() => {
    if (!authed || !sessionId) return
    const onPopState = () => {
      setView(viewFromPath(window.location.pathname))
      setAlreadySignedInNotice(false)
    }
    window.addEventListener('popstate', onPopState)
    return () => window.removeEventListener('popstate', onPopState)
  }, [authed, sessionId])

  if (authed === null)
    return (
      <div className="center-screen">
        <div className="loader">Opening your factory floor…</div>
      </div>
    )
  if (!authed)
    return (
      <AuthGate
        onAuthed={(info) => {
          setPendingDevToken(info?.devVerificationToken ?? null)
          setAuthed(true)
          setBootNonce((n) => n + 1)
        }}
      />
    )
  if (needsEmailVerify)
    return (
      <VerifyEmailGate
        initialDevToken={pendingDevToken}
        onVerified={() => {
          setNeedsEmailVerify(false)
          setPendingDevToken(null)
          setBootError(null)
          setSessionId(null)
          setBootNonce((n) => n + 1)
        }}
        onLogout={() => {
          void signOut().then(() => {
            setNeedsEmailVerify(false)
            setPendingDevToken(null)
            setAuthed(false)
          })
        }}
      />
    )
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
          <div className="brand-mark" aria-hidden="true">
            C
          </div>
          <div className="brand-text">
            <div className="brand-name">CerebrumDev.ai</div>
            <div className="brand-sub">the factory</div>
          </div>
        </div>
        <nav aria-label="Factory navigation">
          {NAV_ITEMS.map((item) => (
            <NavBtn
              key={item.view}
              label={item.label}
              shortLabel={item.shortLabel}
              icon={item.icon}
              active={view === item.view}
              onClick={() => go(item.view)}
            />
          ))}
        </nav>
        <div className="rail-foot">
          <span className="dot" aria-hidden="true" />
          <span className="rail-foot-text">session {sessionId.slice(0, 12)}…</span>
        </div>
      </aside>
      <main>
        {view === 'floor' && (
          <Floor
            sessionId={sessionId}
            goPlatforms={() => go('platforms')}
            accessPaused={accessPaused}
            notice={alreadySignedInNotice ? 'Already signed in.' : null}
          />
        )}
        {view === 'platforms' && <Platforms sessionId={sessionId} />}
        {view === 'subscription' && <Subscription />}
        {view === 'account' && (
          <Account
            initialMe={accountMe}
            onLogout={() => {
              void signOut().then(() => setAuthed(false))
            }}
          />
        )}
      </main>
      {domainStoreNotice && (
        <div className="toast" role="status">
          {domainStoreNotice}
        </div>
      )}
    </div>
  )
}

function NavBtn({
  label,
  shortLabel,
  icon,
  active,
  onClick,
}: {
  label: string
  shortLabel: string
  icon: string
  active: boolean
  onClick: () => void
}) {
  return (
    <button
      type="button"
      className={`nav-btn ${active ? 'active' : ''}`}
      onClick={onClick}
      aria-label={label}
      title={label}
      aria-current={active ? 'page' : undefined}
    >
      <span className="nav-icon" aria-hidden="true">
        {icon}
      </span>
      <span className="nav-label nav-label-full">{label}</span>
      <span className="nav-label nav-label-short" aria-hidden="true">
        {shortLabel}
      </span>
    </button>
  )
}
