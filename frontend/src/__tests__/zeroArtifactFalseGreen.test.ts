/**
 * Zero-artifact false-green: a pilot cycle that claims pilot_ready/STORE
 * PASS with zero coding-agent-authored artifacts must never read as
 * Store-green in the glass, and the "0 artifacts" copy must never
 * co-render with a Store-green label for the same build.
 *
 * Mirrors the backend fix (authorship.below_floor, level_grade.py):
 * unmeasured authorship is a refusal, not a silent pass.
 */
import { describe, expect, it } from 'vitest'
import type { BuildStatus } from '../api/factory'
import { formatFinishedAuthorship, honestLevel, levelGradeLabel } from '../buildProgress'

function zeroArtifactPilotBuild(overrides?: Partial<BuildStatus>): BuildStatus {
  return {
    state: 'succeeded',
    cycle: 'pilot',
    pilot_ready: true,
    level_grade: {
      level: 'STORE_GREEN',
      pilot_ready: true,
      full_pilot: false,
      founding_customer_ready: false,
      blockers: [],
      three_gate: { CODE: 'PASS', PRODUCT: 'PASS', STORE: 'PASS' },
    },
    // No `authorship` at all: the templated/keyless WRITER path — zero
    // agent-authored artifacts, never measured.
    ...overrides,
  }
}

describe('zero-artifact false-green — the glass must refuse it', () => {
  it('a claimed pilot_ready build with zero (unmeasured) authorship is never STORE_GREEN', () => {
    const build = zeroArtifactPilotBuild()
    const level = honestLevel(build)
    expect(level).not.toBe('STORE_GREEN')
    expect(level).not.toBe('FOUNDING_CUSTOMER_READY')
  })

  it('the "0 artifacts" copy and a Store-green label cannot co-render for one build', () => {
    const build = zeroArtifactPilotBuild({
      authorship: { artifacts: 0, agent_written: 0, templated: 0 },
    })
    const zeroArtifactCopy = formatFinishedAuthorship(build.authorship)
    const level = honestLevel(build)
    const label = level ? levelGradeLabel(level) : null

    expect(zeroArtifactCopy).toContain('wrote 0 artifacts')
    // The same build must not simultaneously say "0 artifacts" and show the
    // Store-green label — that pairing is exactly the false-green this
    // closes.
    expect(label).not.toBe('Store-green')
    expect(label).not.toBe('Founding-customer-ready')
  })

  it('measured authorship that genuinely meets the floor still reads Store-green', () => {
    const build = zeroArtifactPilotBuild({
      authorship: {
        artifacts: 5,
        agent_written: 5,
        templated: 0,
        cli_authored_ids: ['a', 'b', 'c', 'd', 'e'],
      },
      acceptance: { passed: 12, total: 12, ok: true },
      level_grade: {
        level: 'STORE_GREEN',
        pilot_ready: true,
        full_pilot: true,
        founding_customer_ready: false,
        blockers: [],
        three_gate: { CODE: 'PASS', PRODUCT: 'PASS', STORE: 'PASS' },
        acceptance: { passed: 12, total: 12, ok: true },
      },
    })
    expect(honestLevel(build)).toBe('STORE_GREEN')
  })
})
