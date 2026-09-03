/**
 * CerebrumDev factory console API client.
 * Real backend only: per-account login tokens (cdt_...), SSE chat,
 * product design state, product package export, billing status.
 */

const API_BASE =
  (import.meta.env.VITE_API_URL as string | undefined)?.replace(/\/$/, '') || ''

const EMAIL_KEY = 'cerebrum.factory.email'
const LEGACY_TOKEN_KEY = 'cerebrum.factory.token'

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message)
    this.name = 'ApiError'
  }
}

/** 403 from credential-gated routes when ACCOUNTS_REQUIRE_VERIFIED_EMAIL=1. */
export function isEmailNotVerifiedError(err: unknown): boolean {
  if (!(err instanceof ApiError) || err.status !== 403) return false
  return err.message === 'email_not_verified' || err.message.includes('email_not_verified')
}

/** Optional Cerebrum-Blocks store: 503 must not look like a dead Factory. */
export function isDomainStoreUnreachable(err: unknown): boolean {
  if (!(err instanceof ApiError) || err.status !== 503) return false
  return /domain store unreachable/i.test(err.message)
}

/**
 * Browser/network races ("Failed to fetch") — not an HTTP status.
 * First paint after a cold HTML load hits these intermittently; a 401/403/5xx
 * is a real API answer and is not a race.
 */
export function isTransientNetworkError(err: unknown): boolean {
  if (err instanceof ApiError) return false
  if (typeof err === 'object' && err && (err as { name?: string }).name === 'AbortError') {
    return false
  }
  if (err instanceof TypeError) return true
  if (err instanceof Error) {
    return /failed to fetch|networkerror|network request failed|load failed/i.test(err.message)
  }
  return false
}

/** Cold-load races: browser fetch failure or a proxy/gateway 5xx after retries. */
export function isTransientBootError(err: unknown): boolean {
  if (isTransientNetworkError(err)) return true
  return err instanceof ApiError && (err.status === 502 || err.status === 503 || err.status === 504)
}

function isRetryableFetch(method: string, err: unknown): boolean {
  if (isTransientNetworkError(err)) return true
  if (
    err instanceof ApiError &&
    (err.status === 502 || err.status === 503 || err.status === 504) &&
    (method === 'GET' || method === 'HEAD')
  ) {
    return true
  }
  return false
}

/** Two retries after the first attempt. Tests use 0ms so suites stay fast. */
const RETRY_DELAYS_MS = import.meta.env.MODE === 'test' ? [0, 0] : [200, 600]

export function getEmail(): string | null {
  return localStorage.getItem(EMAIL_KEY)
}
/** Persist the display email only. The ``cdt_`` login token lives in an HttpOnly cookie. */
export function setSession(email: string): void {
  localStorage.removeItem(LEGACY_TOKEN_KEY)
  localStorage.setItem(EMAIL_KEY, email)
}
export function clearSession(): void {
  localStorage.removeItem(LEGACY_TOKEN_KEY)
  localStorage.removeItem(EMAIL_KEY)
}

/** Drop the display email and ask the API to clear the HttpOnly cookie. */
export async function signOut(): Promise<void> {
  clearSession()
  try {
    await auth.logout()
  } catch {
    /* cookie may already be gone */
  }
}

async function reqOnce<T>(method: string, path: string, body?: unknown): Promise<T> {
  const headers: Record<string, string> = { 'Content-Type': 'application/json' }
  const res = await fetch(`${API_BASE}${path}`, {
    method,
    headers,
    credentials: 'include',
    body: body !== undefined ? JSON.stringify(body) : undefined,
  })
  const text = await res.text()
  let data: unknown
  try {
    data = text ? JSON.parse(text) : null
  } catch {
    data = { raw: text }
  }
  if (!res.ok) {
    const d = data as { detail?: unknown; error?: unknown } | null
    const msg = d?.detail ?? d?.error ?? res.statusText
    throw new ApiError(res.status, typeof msg === 'string' ? msg : JSON.stringify(msg))
  }
  return data as T
}

async function req<T>(method: string, path: string, body?: unknown): Promise<T> {
  let last: unknown
  const attempts = RETRY_DELAYS_MS.length + 1
  for (let i = 0; i < attempts; i++) {
    try {
      return await reqOnce<T>(method, path, body)
    } catch (e) {
      last = e
      if (i === attempts - 1 || !isRetryableFetch(method, e)) throw e
      const wait = RETRY_DELAYS_MS[i] ?? 0
      if (wait > 0) await new Promise((r) => setTimeout(r, wait))
    }
  }
  throw last
}

// --- Auth ------------------------------------------------------------------

export interface VerificationInfo {
  mode?: string
  email_sent?: boolean
  note?: string
  dev_verification_token?: string
}
export interface LoginResponse {
  login_token: string
  email_verified?: boolean
}
export interface RegisterResponse extends LoginResponse {
  account_id?: string
  email_verified?: boolean
  verification?: VerificationInfo
}
export interface ResendVerificationResponse {
  ok?: boolean
  already_verified?: boolean
  email?: string
  email_verified?: boolean
  verification?: VerificationInfo
}
export interface ForgotResponse {
  ok?: boolean
  message?: string
  note?: string
  dev_reset_token?: string
}
export interface AccountInfo {
  email?: string
  account_id?: string
  email_verified?: boolean
  created_at?: string
  [k: string]: unknown
}

export const auth = {
  register: (email: string, password: string) =>
    req<RegisterResponse>('POST', '/v1/auth/register', { email, password }),
  login: (email: string, password: string) =>
    req<LoginResponse>('POST', '/v1/auth/login', { email, password }),
  logout: () => req<{ ok?: boolean }>('POST', '/v1/auth/logout'),
  me: () => req<AccountInfo>('GET', '/v1/auth/me'),
  forgotPassword: (email: string) =>
    req<ForgotResponse>('POST', '/v1/auth/forgot-password', { email }),
  resetPassword: (token: string, newPassword: string) =>
    req<{ ok?: boolean; message?: string }>('POST', '/v1/auth/reset-password', {
      token,
      new_password: newPassword,
    }),
  verifyEmail: (token: string) =>
    req<{ ok?: boolean; email_verified?: boolean }>('POST', '/v1/auth/verify-email', { token }),
  resendVerification: () =>
    req<ResendVerificationResponse>('POST', '/v1/auth/resend-verification', {}),
}

// --- Sessions ---------------------------------------------------------------

export interface SessionInfo {
  session_id: string
  [k: string]: unknown
}

export const sessions = {
  create: () => req<SessionInfo>('POST', '/v1/sessions/', {}),
  list: () => req<SessionInfo[] | { sessions?: SessionInfo[] }>('GET', '/v1/sessions/'),
}

export interface ChatEvent {
  event: string
  data: unknown
}

export async function chatStream(
  sessionId: string,
  message: string,
  onEvent: (ev: ChatEvent) => void,
): Promise<void> {
  const res = await fetch(`${API_BASE}/v1/sessions/${sessionId}/chat`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    credentials: 'include',
    body: JSON.stringify({ message }),
  })
  if (!res.ok || !res.body) {
    const txt = await res.text().catch(() => '')
    throw new ApiError(res.status, txt || `chat failed (${res.status})`)
  }
  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buf = ''
  for (;;) {
    const { done, value } = await reader.read()
    if (done) break
    buf += decoder.decode(value, { stream: true })
    let idx: number
    while ((idx = buf.indexOf('\n\n')) >= 0) {
      const block = buf.slice(0, idx)
      buf = buf.slice(idx + 2)
      const lines = block
        .split('\n')
        .map((l) => l.trimEnd())
        .filter(Boolean)
      let event = 'message'
      let data: unknown = ''
      for (const l of lines) {
        if (l.startsWith('event:')) {
          event = l.slice(6).trim()
        } else if (l.startsWith('data:')) {
          const raw = l.slice(5).trim()
          try {
            data = JSON.parse(raw)
          } catch {
            data = raw
          }
        }
      }
      onEvent({ event, data })
    }
  }
}

export function chatEventText(ev: ChatEvent): string | null {
  const textEvents = ['message', 'token', 'delta', 'text', 'chunk', 'content']
  if (!textEvents.includes(ev.event)) return null
  if (typeof ev.data === 'string') return ev.data
  const d = ev.data as Record<string, unknown> | null
  if (d && typeof d === 'object') {
    for (const k of ['token', 'text', 'delta', 'content', 'message']) {
      if (typeof d[k] === 'string') return d[k] as string
    }
  }
  return null
}

export interface ProductDesign {
  session_id?: string
  mode?: string
  brief?: string | null
  blueprint?: Record<string, unknown> | null
  blueprint_yaml?: string | null
  plan?: Record<string, unknown> | null
  blueprint_approved?: boolean
  generation?: {
    output_dir?: string
    inputs_hash?: string
    product_id?: string
    canonical_output?: string
    engine?: string
    triggered_by?: string
    build?: BuildStatus
  } | null
  last_error?: string | null
}

export const product = {
  get: (sid: string) => req<ProductDesign>('GET', `/v1/sessions/${sid}/product`),
  draft: (sid: string, brief: string, vertical_hint?: string) =>
    req<ProductDesign & { source?: string; yaml?: string }>('POST', `/v1/sessions/${sid}/product/draft`, {
      brief,
      vertical_hint,
    }),
  approve: (sid: string, approve: boolean, blueprint?: Record<string, unknown>) =>
    req<{ ok: boolean; blueprint_approved: boolean }>('POST', `/v1/sessions/${sid}/product/approve`, {
      approve,
      blueprint,
    }),
  generate: (sid: string) =>
    req<ProductDesign & { ok: boolean }>('POST', `/v1/sessions/${sid}/product/generate`, {}),
  buildStatus: (sid: string) =>
    req<{ ok: boolean; product_id?: string; build: BuildStatus }>(
      'GET',
      `/v1/sessions/${sid}/product/build-status`,
    ),
}

export type BuildAuthorship = {
  artifacts?: number
  agent_written?: number
  templated?: number
  agent_artifacts?: string[]
  coder_failures?: Record<string, string>
}

export type BuildPhaseRef = {
  id: string
  label?: string
}

export type BuildPhaseProgress = {
  done: number
  total: number
  fraction?: number
  stage?: string
}

export type BuildStatus = {
  state: 'not_started' | 'unknown' | 'building' | 'succeeded' | 'failed' | 'stalled'
  detail?: string
  findings?: string[]
  phases?: string[]
  completed?: string[]
  phases_done?: number
  phases_total?: number
  current_phase?: BuildPhaseRef | null
  phase_index?: number
  phase_total?: number
  phase_progress?: BuildPhaseProgress
  last_event?: string | null
  last_event_at?: string | null
  last_event_age_s?: number
  next_phase?: BuildPhaseRef | null
  stale?: boolean
  activity?: string
  activity_stage?: string
  activity_done?: number
  activity_total?: number
  authorship?: BuildAuthorship
}

export async function awaitBuild(
  sid: string,
  onProgress?: (s: BuildStatus) => void,
  { intervalMs = 4000, timeoutMs = 45 * 60 * 1000 } = {},
): Promise<BuildStatus> {
  const deadline = Date.now() + timeoutMs
  for (;;) {
    const { build } = await product.buildStatus(sid)
    onProgress?.(build)
    if (build.state === 'succeeded') return build
    if (build.state === 'failed' || build.state === 'stalled') {
      return {
        ...build,
        state: 'failed',
        detail: build.detail ?? 'build stalled',
      }
    }
    if (build.state === 'unknown') {
      return { ...build, state: 'succeeded' }
    }
    if (Date.now() > deadline) {
      return { ...build, state: 'failed', detail: 'build timed out client-side' }
    }
    await new Promise((r) => setTimeout(r, intervalMs))
  }
}

export async function downloadProductPackage(sid: string): Promise<void> {
  const res = await fetch(`${API_BASE}/v1/sessions/${sid}/product/package`, {
    credentials: 'include',
  })
  if (!res.ok) {
    const txt = await res.text().catch(() => '')
    throw new ApiError(res.status, txt || `export failed (${res.status})`)
  }
  const blob = await res.blob()
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  const cd = res.headers.get('content-disposition') || ''
  const m = cd.match(/filename="?([^"";]+)?"?/)
  a.href = url
  a.download = m ? m[1] : 'cerebrumdev-product.zip'
  document.body.appendChild(a)
  a.click()
  a.remove()
  URL.revokeObjectURL(url)
}

export interface BillingStatus {
  plan?: string
  subscription_status?: string
  trial_ends_at?: string | null
  trial_days_left?: number | null
  entitled?: boolean
  checkout_available?: boolean
  enforcement?: boolean
  [k: string]: unknown
}

/** Same flag Subscription uses for Factory access: Paused iff entitled === false. */
export function factoryAccessPaused(
  status: Pick<BillingStatus, 'entitled'> | null | undefined,
): boolean {
  return status?.entitled === false
}

export function trialDaysLeft(status: BillingStatus): number | null {
  if (typeof status.trial_days_left === 'number') return status.trial_days_left
  if (!status.trial_ends_at) return null
  const end = Date.parse(status.trial_ends_at)
  if (Number.isNaN(end)) return null
  return Math.max(0, Math.ceil((end - Date.now()) / 86_400_000))
}

export type SubscriptionDisplay = {
  planLabel: string
  statusLabel: string
  showTrialDays: boolean
  trialDaysLeft: number | null
  accessLabel: 'Active' | 'Paused'
}

/**
 * One coherent read of /v1/billing/status.
 * The store keeps subscription_status='trialing' after the clock runs out
 * (entitled=false, trial_days_left=0). Do not paint that as a live trial.
 */
export function subscriptionDisplay(status: BillingStatus): SubscriptionDisplay {
  const raw = String(status.subscription_status ?? status.status ?? 'none')
    .toLowerCase()
    .replace(/_/g, ' ')
  const days = trialDaysLeft(status)
  const paused = factoryAccessPaused(status)
  const planFromApi = status.plan ? String(status.plan) : ''
  const expiredTrial = raw === 'trialing' && status.entitled === false

  if (expiredTrial) {
    return {
      planLabel: planFromApi || 'trial',
      statusLabel: 'expired',
      showTrialDays: false,
      trialDaysLeft: days,
      accessLabel: 'Paused',
    }
  }

  let planLabel = planFromApi
  if (!planLabel) {
    if (raw === 'active') planLabel = 'factory'
    else if (raw === 'trialing') planLabel = 'trial'
    else planLabel = raw
  }

  return {
    planLabel,
    statusLabel: raw,
    showTrialDays: raw === 'trialing' && days !== null && days > 0,
    trialDaysLeft: days,
    accessLabel: paused ? 'Paused' : 'Active',
  }
}

export const billing = {
  status: async () => {
    const raw = await req<BillingStatus & { billing?: BillingStatus; ok?: boolean }>(
      'GET',
      '/v1/billing/status',
    )
    if (raw.billing && typeof raw.billing === 'object') {
      return { ...raw.billing, ok: raw.ok }
    }
    return raw
  },
  checkout: () =>
    req<{ url?: string; checkout_url?: string }>('POST', '/v1/billing/checkout', {}),
  portal: () => req<{ url?: string; portal_url?: string }>('POST', '/v1/billing/portal', {}),
}

export const domains = {
  list: () => req<{ domains?: unknown[] }>('GET', '/v1/domains/'),
}
