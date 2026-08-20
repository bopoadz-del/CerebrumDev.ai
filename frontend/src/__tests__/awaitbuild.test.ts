/**
 * awaitBuild must not spin for 45 minutes on a finished template product
 * (status "unknown", no ledger) or on a dead runner thread ("stalled").
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { awaitBuild, product, type BuildStatus } from '../api/factory'

describe('awaitBuild', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
  })

  it('returns succeeded builds as-is', async () => {
    vi.spyOn(product, 'buildStatus').mockResolvedValue({
      ok: true,
      build: { state: 'succeeded', authorship: { agent_written: 4, artifacts: 4 } },
    })
    const result = await awaitBuild('sess', undefined, { intervalMs: 1, timeoutMs: 50 })
    expect(result.state).toBe('succeeded')
    expect(result.authorship?.agent_written).toBe(4)
  })

  it('treats stalled as a failed build so the UI cannot download a torn tree', async () => {
    vi.spyOn(product, 'buildStatus').mockResolvedValue({
      ok: true,
      build: { state: 'stalled', detail: 'no build activity for 40 min' },
    })
    const result = await awaitBuild('sess', undefined, { intervalMs: 1, timeoutMs: 50 })
    expect(result.state).toBe('failed')
    expect(result.detail).toMatch(/no build activity/)
  })

  it('treats unknown (template, no ledger) as already finished', async () => {
    vi.spyOn(product, 'buildStatus').mockResolvedValue({
      ok: true,
      build: { state: 'unknown', detail: 'no build ledger for this product' },
    })
    const result = await awaitBuild('sess', undefined, { intervalMs: 1, timeoutMs: 50 })
    expect(result.state).toBe('succeeded')
  })

  it('reports coding-agent progress while building, then succeeds', async () => {
    const seen: BuildStatus[] = []
    vi.spyOn(product, 'buildStatus')
      .mockResolvedValueOnce({
        ok: true,
        build: { state: 'building', phases_done: 1, phases_total: 5, activity: 'WRITER' },
      })
      .mockResolvedValueOnce({
        ok: true,
        build: {
          state: 'succeeded',
          authorship: { agent_written: 2, artifacts: 5 },
        },
      })
    const result = await awaitBuild('sess', (s) => seen.push(s), { intervalMs: 1, timeoutMs: 500 })
    expect(seen[0]?.state).toBe('building')
    expect(seen[0]?.activity).toBe('WRITER')
    expect(result.state).toBe('succeeded')
    expect(result.authorship?.agent_written).toBe(2)
  })
})
