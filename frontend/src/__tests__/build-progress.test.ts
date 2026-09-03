import { describe, expect, it } from 'vitest'
import type { BuildStatus } from '../api/factory'
import {
  CLIENT_STALL_AFTER_S,
  eventAgeSeconds,
  exportAffordance,
  formatFinishedAuthorship,
  formatHeartbeat,
  formatPhaseCounts,
  formatPhaseHeadline,
  phaseBarFraction,
  stampBuildObservation,
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
    expect(formatHeartbeat(quiet)).toMatch(/no new progress/)
    expect(formatHeartbeat(quiet)).not.toMatch(/may still be running/)
  })

  it('bounds an in-flight coder call and does not claim handler progress', () => {
    const calling: BuildStatus = {
      ...cloner,
      last_event: 'calling coder LLM for clinical_treatment_notes',
      last_event_age_s: 12,
      model_call_in_progress: true,
      model_call_deadline_s: 390,
      phase_progress: undefined,
    }
    expect(formatHeartbeat(calling)).toMatch(/waiting on coder LLM/)
    expect(formatHeartbeat(calling)).not.toMatch(/still working/)
    expect(formatPhaseCounts(calling)).toBeNull()

    const staleCall: BuildStatus = {
      ...calling,
      stale: true,
      last_event_age_s: 240,
    }
    expect(formatHeartbeat(staleCall)).toMatch(/still inside 390s watchdog/)
    expect(formatHeartbeat(staleCall)).not.toMatch(/may still be running/)

    const overdue: BuildStatus = {
      ...calling,
      last_event_age_s: 400,
      stale: true,
    }
    expect(formatHeartbeat(overdue)).toMatch(/coder LLM timed out/)
    expect(formatHeartbeat(overdue)).toMatch(/deadline 390s/)
    expect(formatHeartbeat(overdue)).not.toMatch(/may still be running/)

    // Live sess_97a1bc6525924e8b: 1965s elapsed vs 480s deadline.
    const liveOvershoot: BuildStatus = {
      ...calling,
      last_event_age_s: 1965,
      model_call_deadline_s: 480,
      stale: true,
    }
    expect(formatHeartbeat(liveOvershoot)).toMatch(/coder LLM timed out after 1965s/)
    expect(formatHeartbeat(liveOvershoot)).toMatch(/deadline 480s/)
    expect(formatHeartbeat(liveOvershoot)).not.toMatch(/may still be running/)
    const failed = withClientStall(overdue)
    expect(failed?.state).toBe('failed')
    expect(failed?.detail).toMatch(/coder LLM timed out/)
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

  it('advances a frozen last_event_age_s across polls via client observation stamp', () => {
    const t0 = Date.parse('2026-09-03T16:00:00.000Z')
    const poll1 = stampBuildObservation(
      {
        ...cloner,
        last_event: 'wrote handler tenancy_application_pipeline',
        last_event_age_s: 120, // field recheck: stuck at "2 min ago"
        last_event_at: null,
      },
      null,
      t0,
    )
    // Same ledger event, same frozen age_s — must keep the first stamp.
    const poll2 = stampBuildObservation(
      {
        ...cloner,
        last_event: 'wrote handler tenancy_application_pipeline',
        last_event_age_s: 120,
        last_event_at: null,
      },
      poll1,
      t0 + 60_000,
    )
    expect(poll2.client_observed_at_ms).toBe(t0)
    expect(eventAgeSeconds(poll2, t0 + 300_000)).toBeCloseTo(420, 0) // 120 + 300s
    expect(formatHeartbeat(poll2, t0 + 300_000)).toMatch(/quiet for 7 min/)
    const stalled = withClientStall(poll2, t0 + (CLIENT_STALL_AFTER_S + 30) * 1000)
    expect(stalled?.state).toBe('stalled')
  })

  it('re-stamps when the ledger event identity changes', () => {
    const t0 = Date.parse('2026-09-03T16:00:00.000Z')
    const first = stampBuildObservation(
      { ...cloner, last_event: 'handler_a', last_event_age_s: 120 },
      null,
      t0,
    )
    const next = stampBuildObservation(
      { ...cloner, last_event: 'handler_b', last_event_age_s: 5 },
      first,
      t0 + 60_000,
    )
    expect(next.client_observed_at_ms).toBe(t0 + 60_000)
    expect(eventAgeSeconds(next, t0 + 65_000)).toBeCloseTo(10, 0)
  })

  it('labels a handler-wave reset so 3/5 → 1/5 does not read as a regression', () => {
    const t0 = Date.parse('2026-09-03T16:00:00.000Z')
    const atThree = stampBuildObservation(
      {
        ...cloner,
        last_event: 'tenancy_application_pipeline',
        phase_progress: { done: 3, total: 5, fraction: 0.6, stage: 'handlers' },
      },
      null,
      t0,
    )
    expect(formatPhaseCounts(atThree)).toBe('3/5 handlers')
    const wave = stampBuildObservation(
      {
        ...cloner,
        last_event: 'unit_registry_and_vacancy_tracking',
        phase_progress: { done: 1, total: 5, fraction: 0.2, stage: 'handlers' },
      },
      atThree,
      t0 + 60_000,
    )
    expect(wave.client_wave_reset).toBe(true)
    expect(formatPhaseCounts(wave)).toBe('1/5 handlers (new pass)')
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

  it('SUCCESS copy is finished only when pilot-ready, not hang-looking 22 of 28', () => {
    expect(
      formatFinishedAuthorship(
        { artifacts: 28, agent_written: 22, templated: 6 },
        { pilotReady: true },
      ),
    ).toBe('Finished — 22 artifacts; 6 templated')
    expect(
      formatFinishedAuthorship({ artifacts: 28, agent_written: 22, templated: 6 }),
    ).toBe('Code-cycle prototype — 22 artifacts; 6 templated. Not yet pilot-ready')
    expect(
      formatFinishedAuthorship({ artifacts: 28, agent_written: 22, templated: 6 }),
    ).not.toMatch(/22 of 28/)
    expect(
      formatFinishedAuthorship(
        { artifacts: 28, agent_written: 22, templated: 6 },
        { pilotReady: false },
      ),
    ).not.toMatch(/Finished/)
  })

  it('gold Download is only for a Store-green success', () => {
    expect(exportAffordance({ state: 'stalled', detail: 'gone' })).toEqual({
      label: 'Export (.zip) — build stalled',
      disabled: true,
      ghost: true,
      title: 'Build stalled — a full-pilot zip will be refused by the server',
    })
    expect(exportAffordance({ state: 'failed', pilot_ready: false })).toMatchObject({
      label: 'Export (.zip) — pilot suite failed',
      disabled: true,
      ghost: true,
    })
    expect(
      exportAffordance({ state: 'succeeded', pilot_ready: false }),
    ).toMatchObject({
      label: 'Download code-cycle prototype (.zip)',
      disabled: false,
      ghost: true,
    })
    expect(exportAffordance({ state: 'succeeded', pilot_ready: true })).toEqual({
      label: 'Download platform export (.zip)',
      disabled: false,
      ghost: false,
    })
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
