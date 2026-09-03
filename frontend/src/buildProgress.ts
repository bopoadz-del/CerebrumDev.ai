import type { BuildAuthorship, BuildStatus } from './api/factory'

/** Match backend build_jobs._STALE_AFTER_S — quiet model call vs frozen UI. */
export const CLIENT_STALE_AFTER_S = 180
/** Match backend build_jobs._STALL_AFTER_S — process likely gone. */
export const CLIENT_STALL_AFTER_S = 1800

/** SUCCESS copy: never "22 of 28" — that reads as a hang. */
export function formatFinishedAuthorship(
  authorship: BuildAuthorship | null | undefined,
): string | null {
  if (!authorship) return null
  const written = authorship.agent_written
  const templated = authorship.templated
  if (written === 0) {
    return 'Coding agent wrote 0 artifacts — this platform is templated (coder idle or no LLM key).'
  }
  if (typeof written === 'number' && typeof templated === 'number') {
    return `Finished — ${written} artifacts; ${templated} templated`
  }
  if (typeof written === 'number') {
    return `Finished — ${written} artifacts`
  }
  return null
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
    return `${progress.done}/${progress.total} ${unit}`
  }
  if (
    typeof build.activity_done === 'number' &&
    typeof build.activity_total === 'number' &&
    build.activity_total > 0
  ) {
    const unit = build.activity_stage || 'items'
    return `${build.activity_done}/${build.activity_total} ${unit}`
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
    }
  }
  return {
    ...next,
    client_observed_at_ms: nowMs,
    client_base_age_s: next.last_event_age_s,
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
  if (stale) {
    return ago
      ? `quiet for ${ago.replace(' ago', '')} — model call may still be running`
      : 'quiet — model call may still be running'
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
