import { describe, expect, it } from 'vitest'
import type { BuildStatus } from '../api/factory'
import {
  CLIENT_STALL_AFTER_S,
  eventAgeSeconds,
  formatFinishedAuthorship,
  formatHeartbeat,
  formatPhaseCounts,
  formatPhaseHeadline,
  phaseBarFraction,
  withClientStall,
} from '../buildProgress'

const cloner: BuildStatus = {
  state: 'building',
  current_phase: { id: 'CLONER', label: 'Block stocker' },
  phase_index: 2,
  phase_total: 5,
  phases_done: 1,
  phases_total: 5,
  next_phase: { id: 'WRITER', label: 'Platform manufacturer' },
  phase_progress: { done: 3, total: 7, fraction: 0.429, stage: 'blocks' },
  last_event: 'cloned audit',
  last_event_age_s: 12,
  stale: false,
}

describe('build progress copy', () => {
  it('names the current phase instead of a bare 2/5', () => {
    expect(formatPhaseHeadline(cloner)).toBe('CLONER 2/5')
    expect(formatPhaseCounts(cloner)).toBe('3/7 blocks')
    expect(phaseBarFraction(cloner)).toBeCloseTo(0.429)
    expect(formatHeartbeat(cloner)).toBe('still working · 12s ago')
  })

  it('marks a quiet build so the Floor can say it may be stuck', () => {
    const quiet: BuildStatus = {
      ...cloner,
      stale: true,
      last_event_age_s: 240,
    }
    expect(formatHeartbeat(quiet)).toMatch(/quiet/)
    expect(formatHeartbeat(quiet)).toMatch(/4 min/)
  })

  it('advances relative age from last_event_at so a frozen server snapshot cannot stall the ticker', () => {
    const at = '2026-09-03T16:00:00.000Z'
    const t0 = Date.parse(at)
    const build: BuildStatus = {
      ...cloner,
      last_event_at: at,
      last_event_age_s: 120, // stale snapshot that would stay "2 min ago"
      stale: false,
    }
    expect(eventAgeSeconds(build, t0 + 120_000)).toBeCloseTo(120, 0)
    expect(formatHeartbeat(build, t0 + 120_000)).toBe('still working · 2 min ago')
    expect(formatHeartbeat(build, t0 + 420_000)).toMatch(/quiet for 7 min/)
  })

  it('promotes a forever-building snapshot to stalled after the stall window', () => {
    const at = '2026-09-03T16:00:00.000Z'
    const t0 = Date.parse(at)
    const build: BuildStatus = {
      ...cloner,
      last_event_at: at,
      last_event_age_s: 120,
    }
    const stalled = withClientStall(build, t0 + (CLIENT_STALL_AFTER_S + 60) * 1000)
    expect(stalled?.state).toBe('stalled')
    expect(stalled?.detail).toMatch(/no build activity/)
  })

  it('SUCCESS copy is finished, not hang-looking 22 of 28', () => {
    expect(
      formatFinishedAuthorship({ artifacts: 28, agent_written: 22, templated: 6 }),
    ).toBe('Finished — 22 artifacts; 6 templated')
    expect(
      formatFinishedAuthorship({ artifacts: 28, agent_written: 22, templated: 6 }),
    ).not.toMatch(/22 of 28/)
  })

  it('falls back to completed+1 when the API has no current_phase yet', () => {
    expect(
      formatPhaseHeadline({
        state: 'building',
        phases_done: 2,
        phases_total: 5,
      }),
    ).toBe('3/5')
  })
})
