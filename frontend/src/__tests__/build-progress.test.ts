import { describe, expect, it } from 'vitest'
import type { BuildStatus } from '../api/factory'
import {
  formatHeartbeat,
  formatPhaseCounts,
  formatPhaseHeadline,
  phaseBarFraction,
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
