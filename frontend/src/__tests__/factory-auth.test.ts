import { afterEach, describe, expect, it, vi } from 'vitest'
import { clearSession, getEmail, setSession } from '../api/factory'

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
