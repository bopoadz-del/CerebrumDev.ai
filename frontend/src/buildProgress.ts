import type { BuildAuthorship, BuildStatus } from './api/factory'

/** Match backend build_jobs._STALE_AFTER_S — quiet model call vs frozen UI. */
export const CLIENT_STALE_AFTER_S = 180
/** Match backend build_jobs._STALL_AFTER_S — process likely gone. */
export const CLIENT_STALL_AFTER_S = 1800
/** Match backend llm_watchdog.attempt_wall_s default (120s × 3 legs + 30s). */
export const CLIENT_MODEL_CALL_DEADLINE_S = 390

/** SUCCESS copy: never "22 of 28" — that reads as a hang.
 *  Code-cycle SUCCESS is a prototype, not "Finished / Download ready".
 */
export function formatFinishedAuthorship(
  authorship: BuildAuthorship | null | undefined,
  opts?: { pilotReady?: boolean | null },
): string | null {
  if (!authorship) return null
  const written = authorship.agent_written
  const templated = authorship.templated
  if (written === 0) {
    return 'Coding agent wrote 0 artifacts — this platform is templated (coder idle or no LLM key).'
  }
  const counts =
    typeof written === 'number' && typeof templated === 'number'
      ? `${written} artifacts; ${templated} templated`
      : typeof written === 'number'
        ? `${written} artifacts`
        : null
  if (!counts) return null
  if (opts?.pilotReady === true) {
    return `Finished — ${counts}`
  }
  return `Code-cycle prototype — ${counts}. Not yet pilot-ready`
}

/** Named current phase plus 1-based index: "WRITER 3/5", not a bare "2/5". */
export function formatPhaseHeadline(build: BuildStatus): string {
  const id = build.current_phase?.id
  const index = build.phase_index ?? (build.phases_done ?? 0) + 1
  const total = build.phase_total ?? build.phases_total ?? 5
  return id ? `${id} ${index}/${total}` : `${index}/${total}`
}

export function formatPhaseCounts(build: BuildStatus): string | null {
  const progress = build.phase_progress
  if (progress && progress.total > 0) {
    const unit = progress.stage || 'items'
    const base = `${progress.done}/${progress.total} ${unit}`
    // WRITER can restart a handler/route wave at 1/N after finishing 3/N —
    // label it so the drop does not read as a regression.
    return build.client_wave_reset ? `${base} (new pass)` : base
  }
  if (
    typeof build.activity_done === 'number' &&
    typeof build.activity_total === 'number' &&
    build.activity_total > 0
  ) {
    const unit = build.activity_stage || 'items'
    const base = `${build.activity_done}/${build.activity_total} ${unit}`
    return build.client_wave_reset ? `${base} (new pass)` : base
  }
  return null
}

/**
 * Stamp (or preserve) client observation metadata so a frozen server
 * ``last_event_age_s`` still advances on the wall clock between polls.
 * Re-stamp only when the ledger event identity changes.
 */
export function stampBuildObservation(
  next: BuildStatus,
  prev: BuildStatus | null | undefined,
  nowMs = Date.now(),
): BuildStatus {
  const sameEvent =
    Boolean(prev) &&
    prev!.state === next.state &&
    (prev!.last_event_at ?? null) === (next.last_event_at ?? null) &&
    (prev!.last_event ?? null) === (next.last_event ?? null) &&
    (prev!.activity ?? null) === (next.activity ?? null)

  const prevProgress = prev?.phase_progress
  const nextProgress = next.phase_progress
  const waveReset = Boolean(
    prevProgress &&
      nextProgress &&
      (prevProgress.stage || '') === (nextProgress.stage || '') &&
      prevProgress.total === nextProgress.total &&
      nextProgress.done < prevProgress.done,
  )

  if (sameEvent && typeof prev!.client_observed_at_ms === 'number') {
    return {
      ...next,
      client_observed_at_ms: prev!.client_observed_at_ms,
      client_base_age_s:
        typeof prev!.client_base_age_s === 'number'
          ? prev!.client_base_age_s
          : typeof prev!.last_event_age_s === 'number'
            ? prev!.last_event_age_s
            : next.last_event_age_s,
      client_wave_reset: prev!.client_wave_reset,
    }
  }
  return {
    ...next,
    client_observed_at_ms: nowMs,
    client_base_age_s: next.last_event_age_s,
    client_wave_reset: waveReset,
  }
}

/**
 * Relative age of the last ledger event. Prefer wall-clock from
 * ``last_event_at``; otherwise advance a frozen ``last_event_age_s`` from
 * the client observation stamp so "2 min ago" cannot stick for 5 wall minutes.
 */
export function eventAgeSeconds(build: BuildStatus, nowMs = Date.now()): number | null {
  const ages: number[] = []
  if (build.last_event_at) {
    const at = Date.parse(build.last_event_at)
    if (!Number.isNaN(at)) {
      ages.push(Math.max(0, (nowMs - at) / 1000))
    }
  }
  if (
    typeof build.client_base_age_s === 'number' &&
    typeof build.client_observed_at_ms === 'number'
  ) {
    ages.push(build.client_base_age_s + Math.max(0, (nowMs - build.client_observed_at_ms) / 1000))
  } else if (typeof build.last_event_age_s === 'number') {
    ages.push(build.last_event_age_s)
  }
  if (ages.length === 0) return null
  return Math.max(...ages)
}

export function formatHeartbeat(build: BuildStatus, nowMs = Date.now()): string | null {
  if (build.state !== 'building') return null
  const age = eventAgeSeconds(build, nowMs)
  const ago =
    age == null
      ? null
      : age < 5
        ? 'just now'
        : age < 60
          ? `${Math.round(age)}s ago`
          : `${Math.round(age / 60)} min ago`
  const stale = Boolean(build.stale) || (age != null && age >= CLIENT_STALE_AFTER_S)
  const inCall = Boolean(build.model_call_in_progress)
  const deadline =
    typeof build.model_call_deadline_s === 'number'
      ? build.model_call_deadline_s
      : inCall
        ? CLIENT_MODEL_CALL_DEADLINE_S
        : null
  if (inCall && deadline != null && age != null && age >= deadline) {
    return `coder LLM timed out after ${Math.round(age)}s — the model call did not finish`
  }
  if (inCall && stale) {
    return ago
      ? `quiet for ${ago.replace(' ago', '')} — model call may still be running (bounded ${Math.round(deadline ?? CLIENT_MODEL_CALL_DEADLINE_S)}s)`
      : 'quiet — model call may still be running'
  }
  if (stale) {
    return ago
      ? `quiet for ${ago.replace(' ago', '')} — no new progress`
      : 'quiet — no new progress'
  }
  if (inCall) {
    return ago ? `waiting on coder LLM · ${ago}` : 'waiting on coder LLM'
  }
  return ago ? `still working · ${ago}` : 'still working'
}

/** Promote a forever-"building" snapshot to stalled when the ledger is dead. */
export function withClientStall(
  build: BuildStatus | null,
  nowMs = Date.now(),
): BuildStatus | null {
  if (!build || build.state !== 'building') return build
  const age = eventAgeSeconds(build, nowMs)
  const deadline =
    typeof build.model_call_deadline_s === 'number'
      ? build.model_call_deadline_s
      : build.model_call_in_progress
        ? CLIENT_MODEL_CALL_DEADLINE_S
        : null
  if (build.model_call_in_progress && deadline != null && age != null && age >= deadline) {
    return {
      ...build,
      state: 'failed',
      detail:
        build.detail && build.detail.includes('timed out')
          ? build.detail
          : `coder LLM timed out after ${Math.round(age)}s — the model call did not finish`,
    }
  }
  if (age == null || age < CLIENT_STALL_AFTER_S) return build
  return {
    ...build,
    state: 'stalled',
    detail:
      build.detail && build.detail !== 'build in progress'
        ? build.detail
        : `no build activity for ${Math.round(age / 60)} min — the build process may be gone; generate again`,
  }
}

/** Honest export CTA. Gold "Download platform export" is only for Store-green. */
export function exportAffordance(build: BuildStatus | null | undefined): {
  label: string
  disabled: boolean
  ghost: boolean
  title?: string
} {
  if (!build || build.state === 'building' || build.state === 'not_started') {
    return { label: 'Building…', disabled: true, ghost: false }
  }
  if (build.state === 'stalled') {
    return {
      label: 'Export (.zip) — build stalled',
      disabled: true,
      ghost: true,
      title: 'Build stalled — a full-pilot zip will be refused by the server',
    }
  }
  if (build.state === 'failed') {
    return {
      label: 'Export (.zip) — pilot suite failed',
      disabled: true,
      ghost: true,
      title: 'Pilot suite failed — export is not pilot-ready and will be refused by the server',
    }
  }
  if (build.state === 'succeeded' && build.pilot_ready !== true) {
    return {
      label: 'Download code-cycle prototype (.zip)',
      disabled: false,
      ghost: true,
      title: 'Code-cycle prototype — not a Store-green / full-pilot zip',
    }
  }
  if (build.state === 'succeeded' && build.pilot_ready === true) {
    return { label: 'Download platform export (.zip)', disabled: false, ghost: false }
  }
  return { label: 'Building…', disabled: true, ghost: false }
}

export function phaseBarFraction(build: BuildStatus): number | null {
  const progress = build.phase_progress
  if (progress && progress.total > 0) {
    return Math.max(0, Math.min(1, progress.fraction ?? progress.done / progress.total))
  }
  if (
    typeof build.activity_done === 'number' &&
    typeof build.activity_total === 'number' &&
    build.activity_total > 0
  ) {
    return Math.max(0, Math.min(1, build.activity_done / build.activity_total))
  }
  return null
}
