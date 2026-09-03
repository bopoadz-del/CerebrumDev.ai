import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  clearSession,
  factoryAccessPaused,
  getEmail,
  isTransientNetworkError,
  setSession,
  subscriptionDisplay,
} from '../api/factory'

describe('factory auth storage', () => {
  afterEach(() => {
    localStorage.clear()
  })

  it('never writes a login token to localStorage', () => {
    localStorage.setItem('cerebrum.factory.token', 'cdt_leftover')
    setSession('owner@factory.dev')
    expect(localStorage.getItem('cerebrum.factory.token')).toBeNull()
    expect(getEmail()).toBe('owner@factory.dev')
  })

  it('clearSession drops leftover tokens and the email hint', () => {
    localStorage.setItem('cerebrum.factory.token', 'cdt_leftover')
    setSession('owner@factory.dev')
    clearSession()
    expect(localStorage.getItem('cerebrum.factory.token')).toBeNull()
    expect(getEmail()).toBeNull()
  })

  it('sends credentials: include and no Authorization header', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      text: async () => JSON.stringify({ email: 'owner@factory.dev' }),
    })
    vi.stubGlobal('fetch', fetchMock)
    const { auth } = await import('../api/factory')
    await auth.me()
    expect(fetchMock).toHaveBeenCalled()
    const init = fetchMock.mock.calls[0][1] as RequestInit
    expect(init.credentials).toBe('include')
    const headers = init.headers as Record<string, string>
    expect(headers.Authorization).toBeUndefined()
    vi.unstubAllGlobals()
  })
})

describe('factoryAccessPaused', () => {
  it('matches Subscription Factory access: paused only when entitled is false', () => {
    expect(factoryAccessPaused({ entitled: false })).toBe(true)
    expect(factoryAccessPaused({ entitled: true })).toBe(false)
    expect(factoryAccessPaused({})).toBe(false)
    expect(factoryAccessPaused(null)).toBe(false)
    expect(factoryAccessPaused(undefined)).toBe(false)
  })
})

describe('isTransientNetworkError', () => {
  it('treats Failed to fetch as a race, not an API status', () => {
    expect(isTransientNetworkError(new TypeError('Failed to fetch'))).toBe(true)
    expect(isTransientNetworkError(new Error('NetworkError when attempting to fetch resource.'))).toBe(
      true,
    )
    expect(isTransientNetworkError(new Error('backend down'))).toBe(false)
    expect(isTransientNetworkError({ name: 'AbortError', message: 'aborted' })).toBe(false)
  })
})

describe('subscriptionDisplay', () => {
  it('keeps a live trial internally consistent', () => {
    const view = subscriptionDisplay({
      plan: 'trial',
      subscription_status: 'trialing',
      trial_days_left: 3,
      entitled: true,
    })
    expect(view).toMatchObject({
      planLabel: 'trial',
      statusLabel: 'trialing',
      showTrialDays: true,
      trialDaysLeft: 3,
      accessLabel: 'Active',
    })
  })

  it('does not paint expired trial as still trialing with 0 days and Paused', () => {
    const view = subscriptionDisplay({
      plan: 'trial',
      subscription_status: 'trialing',
      trial_days_left: 0,
      entitled: false,
    })
    expect(view.statusLabel).toBe('expired')
    expect(view.accessLabel).toBe('Paused')
    expect(view.showTrialDays).toBe(false)
    expect(view.planLabel).toBe('trial')
  })

  it('labels an active paid subscription as factory + Active', () => {
    const view = subscriptionDisplay({
      subscription_status: 'active',
      entitled: true,
    })
    expect(view.planLabel).toBe('factory')
    expect(view.statusLabel).toBe('active')
    expect(view.accessLabel).toBe('Active')
    expect(view.showTrialDays).toBe(false)
  })
})

describe('req retry', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('retries Failed to fetch then succeeds', async () => {
    const fetchMock = vi
      .fn()
      .mockRejectedValueOnce(new TypeError('Failed to fetch'))
      .mockResolvedValueOnce({
        ok: true,
        status: 200,
        text: async () => JSON.stringify({ email: 'owner@factory.dev' }),
      })
    vi.stubGlobal('fetch', fetchMock)
    const { auth } = await import('../api/factory')
    await expect(auth.me()).resolves.toEqual({ email: 'owner@factory.dev' })
    expect(fetchMock).toHaveBeenCalledTimes(2)
  })

  it('does not retry 401', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: false,
      status: 401,
      statusText: 'Unauthorized',
      text: async () => JSON.stringify({ detail: 'unauthorized' }),
    })
    vi.stubGlobal('fetch', fetchMock)
    const { auth } = await import('../api/factory')
    await expect(auth.me()).rejects.toMatchObject({ status: 401 })
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })

  it('retries GET 503 then succeeds', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce({
        ok: false,
        status: 503,
        statusText: 'Service Unavailable',
        text: async () => JSON.stringify({ detail: 'backend down' }),
      })
      .mockResolvedValueOnce({
        ok: true,
        status: 200,
        text: async () => JSON.stringify({ email: 'owner@factory.dev' }),
      })
    vi.stubGlobal('fetch', fetchMock)
    const { auth } = await import('../api/factory')
    await expect(auth.me()).resolves.toEqual({ email: 'owner@factory.dev' })
    expect(fetchMock).toHaveBeenCalledTimes(2)
  })
})
