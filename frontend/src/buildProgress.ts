import type {
  BuildAuthorship,
  BuildStatus,
  FactoryCodeCliProbe,
} from './api/factory'
import {
  FACTORY_CODE_CLI_CREDENTIALS_MISSING,
  FACTORY_CODE_CLI_FAILED,
  FACTORY_CODE_CLI_NO_MODEL,
} from './api/factory'

/** Match backend build_jobs._STALE_AFTER_S — quiet model call vs frozen UI. */
export const CLIENT_STALE_AFTER_S = 180
/** Match backend build_jobs._STALL_AFTER_S — process likely gone. */
export const CLIENT_STALL_AFTER_S = 1800
/** Match backend llm_watchdog.attempt_wall_s production default (40 min).
 *  Live Render FACTORY_CODER_TIMEOUT_S=160 → old wall 160×3+30=510s.
 *  Prefer the server's model_call_deadline_s when present. */
export const CLIENT_MODEL_CALL_DEADLINE_S = 2400

export type LevelGradeName =
  | 'SCAFFOLD'
  | 'CODE_GREEN'
  | 'STORE_GREEN'
  | 'FOUNDING_CUSTOMER_READY'

const CLAIMED_LEVELS = new Set<string>([
  'SCAFFOLD',
  'CODE_GREEN',
  'STORE_GREEN',
  'FOUNDING_CUSTOMER_READY',
])

/**
 * Fail-closed glass grade. A false ``pilot_ready`` can never read as
 * Store-green or founding-customer-ready, even if ``level_grade.level``
 * overclaims. Missing grade falls back to cycle + pilot_ready.
 */
export function honestLevel(build: BuildStatus | null | undefined): LevelGradeName | null {
  if (!build) return null
  const claimed = String(build.level_grade?.level || '').toUpperCase()
  const ready = build.pilot_ready === true
  // Authoritative fail fields win over a founding/CLI receipt overclaim.
  // Do not force SCAFFOLD from coder_receipt alone when pilot_ready is true.
  if (
    productSuiteFailed(build) ||
    outcomeFailed(build) ||
    build.state === 'failed' ||
    build.state === 'stalled'
  ) {
    return 'SCAFFOLD'
  }
  if (!ready) {
    if (claimed === 'STORE_GREEN' || claimed === 'FOUNDING_CUSTOMER_READY') {
      return build.state === 'succeeded' ? 'CODE_GREEN' : 'SCAFFOLD'
    }
    if (CLAIMED_LEVELS.has(claimed)) return claimed as LevelGradeName
    if (build.state === 'succeeded') return 'CODE_GREEN'
    return null
  }
  if (
    build.level_grade?.founding_customer_ready === true ||
    claimed === 'FOUNDING_CUSTOMER_READY'
  ) {
    return 'FOUNDING_CUSTOMER_READY'
  }
  if (claimed === 'STORE_GREEN') return 'STORE_GREEN'
  return 'STORE_GREEN'
}

export function hasSourcedLevel(build: BuildStatus | null | undefined): boolean {
  return Boolean(build?.level_grade?.level)
}

export function levelGradeLabel(level: LevelGradeName, sourced = true): string {
  if (!sourced) {
    if (level === 'CODE_GREEN') return 'Code-cycle prototype'
    if (level === 'STORE_GREEN' || level === 'FOUNDING_CUSTOMER_READY') return 'Pilot-ready'
    return 'Scaffold'
  }
  switch (level) {
    case 'SCAFFOLD':
      return 'Scaffold'
    case 'CODE_GREEN':
      return 'Code-green (prototype)'
    case 'STORE_GREEN':
      return 'Store-green'
    case 'FOUNDING_CUSTOMER_READY':
      return 'Founding-customer-ready'
  }
}

/** True only for a Store-green / founding zip — never CODE_GREEN or in-flight. */
export function isPilotZipReady(build: BuildStatus | null | undefined): boolean {
  if (!build || build.state !== 'succeeded' || build.pilot_ready !== true) return false
  if (productSuiteFailed(build) || outcomeFailed(build)) return false
  const level = honestLevel(build)
  return level === 'STORE_GREEN' || level === 'FOUNDING_CUSTOMER_READY'
}

export function threeGateEntries(
  build: BuildStatus | null | undefined,
): { name: string; verdict: string }[] | null {
  const gates = build?.level_grade?.three_gate
  if (!gates || typeof gates !== 'object') return null
  const names = ['CODE', 'PRODUCT', 'STORE'] as const
  if (!names.some((name) => name in gates)) return null
  return names.map((name) => ({
    name,
    verdict: String(gates[name] || 'UNKNOWN').replace(/_/g, ' '),
  }))
}

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
    return `coder LLM timed out after ${Math.round(age)}s (deadline ${Math.round(deadline)}s) — the model call did not finish`
  }
  if (inCall && stale && (deadline == null || age == null || age < deadline)) {
    return ago
      ? `quiet for ${ago.replace(' ago', '')} — model call still inside ${Math.round(deadline ?? CLIENT_MODEL_CALL_DEADLINE_S)}s watchdog`
      : 'quiet — model call still inside watchdog'
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

export function isUnreadableLedger(build: BuildStatus | null | undefined): boolean {
  const detail = build?.detail || ''
  return /LEDGER_UNREADABLE|ledger unreadable/i.test(detail)
}

const CLI_FAIL_TEXT =
  /FACTORY_CODE_CLI_FAILED|FACTORY_CODE_CLI_UNAVAILABLE|FACTORY_CODE_CLI_MODEL_DENIED|CLI exited/i

/** Receipt or ledger says the Kimi Code CLI failed. Not enough alone to refuse Export. */
export function isCoderCliFailed(build: BuildStatus | null | undefined): boolean {
  if (!build) return false
  const receipt = build.coder_receipt
  if (receipt) {
    if (receipt.ok === false) return true
    if (CLI_FAIL_TEXT.test(String(receipt.blocker || ''))) return true
    if (CLI_FAIL_TEXT.test(String(receipt.detail || ''))) return true
  }
  const blobs = [
    build.detail ?? '',
    ...(build.findings ?? []),
    ...(build.level_grade?.blockers ?? []),
    ...Object.values(build.authorship?.coder_failures ?? {}),
  ]
  return blobs.some((text) => CLI_FAIL_TEXT.test(text))
}

export function isScaffoldClaim(build: BuildStatus | null | undefined): boolean {
  return String(build?.level_grade?.level || '').toUpperCase() === 'SCAFFOLD'
}

/** PRODUCT three-gate is red — never a founding / gold Export. */
export function productSuiteFailed(build: BuildStatus | null | undefined): boolean {
  const verdict = build?.level_grade?.three_gate?.PRODUCT
  return Boolean(verdict && /FAIL|RED/i.test(String(verdict)))
}

/** Ledger outcome is a named failure (FAILED_BUDGET_SPENT, …). */
export function outcomeFailed(build: BuildStatus | null | undefined): boolean {
  const outcome = String(build?.outcome || '').trim()
  if (!outcome) return false
  if (/^(SUCCESS|PASS|SUCCEEDED)$/i.test(outcome)) return false
  return /FAIL/i.test(outcome)
}

/**
 * Authoritative pilot-ready SUCCESS. CLI billing miss + factory-grounded
 * REUSE keep-path (#338) is an allowed honesty class when these fields hold.
 */
export function isAuthoritativePilotReady(build: BuildStatus | null | undefined): boolean {
  if (!build) return false
  if (build.state !== 'succeeded') return false
  if (build.pilot_ready !== true) return false
  if (outcomeFailed(build)) return false
  if (productSuiteFailed(build)) return false
  return true
}

/**
 * Refuse Finished / gold Download / founding chips. Prefer authoritative
 * build fields (pilot_ready, outcome, state, PRODUCT) over a raw
 * FACTORY_CODE_CLI_FAILED receipt.
 */
export function shouldRefuseExport(build: BuildStatus | null | undefined): boolean {
  if (!build) return false
  if (build.state === 'building' || build.state === 'not_started') return false
  if (isUnreadableLedger(build)) return true
  if (build.state === 'failed') return true
  if (productSuiteFailed(build)) return true
  if (outcomeFailed(build)) return true
  if (isAuthoritativePilotReady(build)) return false
  if (isCoderCliFailed(build)) return true
  if (isScaffoldClaim(build) && build.pilot_ready !== true) return true
  return false
}

function refuseExportDetail(build: BuildStatus): string {
  if (build.detail) return build.detail
  const receipt = build.coder_receipt
  if (receipt?.detail) return receipt.detail
  if (receipt?.blocker) return String(receipt.blocker)
  if (productSuiteFailed(build)) return 'PRODUCT suite failed'
  if (outcomeFailed(build)) return String(build.outcome)
  const fromFailures = Object.values(build.authorship?.coder_failures ?? {}).find((value) =>
    CLI_FAIL_TEXT.test(value),
  )
  return fromFailures || FACTORY_CODE_CLI_FAILED
}

/**
 * Demote Finished / Downloadable paint only when the build is not
 * actually pilot-ready. A FACTORY_CODE_CLI_FAILED receipt after a
 * SUCCESS + pilot_ready keep-path must not strip Export.
 */
export function withExportHonesty(build: BuildStatus | null): BuildStatus | null {
  if (!build) return build
  if (build.state === 'building' || build.state === 'not_started') return build
  if (isAuthoritativePilotReady(build)) return build
  if (!shouldRefuseExport(build)) return build
  const grade = {
    ...(build.level_grade ?? {}),
    level: 'SCAFFOLD',
    pilot_ready: false,
    founding_customer_ready: false,
  }
  if (build.state === 'failed' || build.state === 'stalled') {
    if (build.pilot_ready === true || build.level_grade?.founding_customer_ready === true) {
      return { ...build, pilot_ready: false, level_grade: grade }
    }
    return build
  }
  return {
    ...build,
    state: 'failed',
    pilot_ready: false,
    level_grade: grade,
    detail: refuseExportDetail(build),
  }
}

/** Unreadable / crash honesty: never paint as an active coding run. */
export function withLedgerHonesty(build: BuildStatus | null): BuildStatus | null {
  if (!build) return build
  if (build.state === 'unknown' && isUnreadableLedger(build)) {
    return { ...build, state: 'failed', pilot_ready: false }
  }
  return build
}

/** Promote a forever-"building" snapshot to stalled when the ledger is dead. */
export function withClientStall(
  build: BuildStatus | null,
  nowMs = Date.now(),
): BuildStatus | null {
  build = withExportHonesty(withLedgerHonesty(build))
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
          : `coder LLM timed out after ${Math.round(age)}s (deadline ${Math.round(deadline)}s) — the model call did not finish`,
    }
  }
  // A 20–40 min handler write is still coding. Do not stall the Floor
  // at 30 min while the calling-NOTE is inside its watchdog wall.
  if (build.model_call_in_progress && deadline != null && age != null && age < deadline) {
    return build
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

/**
 * Platforms page-head. Never invite a successful Download when the zip
 * is failed, stalled, in-flight, missing, or not pilot-ready.
 */
export function platformsLeadCopy(
  build: BuildStatus | null | undefined,
  hasGeneration: boolean,
): string {
  if (!hasGeneration) {
    return 'What the factory built for you. A downloadable export appears here only after a pilot-ready run succeeds.'
  }
  if (isUnreadableLedger(build)) {
    return 'The last build crashed with an unreadable ledger. Download unavailable — build failed. Export is refused until a pilot-ready run succeeds.'
  }
  if (shouldRefuseExport(build)) {
    return 'The last build did not pass its gates. Download unavailable — build failed. Export is refused until a pilot-ready run succeeds.'
  }
  if (!build || build.state === 'building' || build.state === 'not_started' || build.state === 'unknown') {
    return 'The coding agent is writing this platform. Export stays closed until a pilot-ready run succeeds.'
  }
  if (build.state === 'failed') {
    return 'The last build did not pass its gates. Download unavailable — build failed. Export is refused until a pilot-ready run succeeds.'
  }
  if (build.state === 'stalled') {
    return 'The last build stalled. Download unavailable — build stalled. Export is refused until a fresh run succeeds.'
  }
  if (build.state === 'succeeded' && !isPilotZipReady(build)) {
    return 'A code-cycle prototype is on this page. This is not a full-pilot export — do not treat it as launch-ready.'
  }
  return 'What the factory built for you. Download the export and launch it anywhere.'
}

const MISSING_KIMI_CLI_CREDS =
  'Kimi Code CLI credentials are missing (FACTORY_CODE_CLI_CREDENTIALS_MISSING). ' +
  'Set KIMI_CODE_API_KEY so boot writes ~/.kimi-code/config.toml. ' +
  'The CLI binary can be present while credentials_file_present is false — ' +
  'CEREBRUM_LLM_API_KEY and HTTP architect keys do not authenticate the Kimi Code CLI.'

const MISSING_KIMI_CLI_MODEL =
  'Kimi Code CLI has no usable default_model (FACTORY_CODE_CLI_NO_MODEL). ' +
  'config.toml can be present (credentials_file_present=true) while default_model ' +
  'or its [models] entry is missing. Set KIMI_CODE_API_KEY so boot writes ' +
  'default_model (KIMI_CODE_MODEL, default kimi-k3). Headless Floor cannot run ' +
  'kimi /login. CEREBRUM_LLM_API_KEY does not configure the Kimi Code model.'

/**
 * Operator copy from GET /health factory_code_cli. Named blocker wins;
 * credentials_file_present=false also fires unless Kimi credentials are
 * explicitly not required (Claude login). A credentials file without
 * default_model is FACTORY_CODE_CLI_NO_MODEL, not a successful probe.
 */
export function factoryCodeCliHonesty(
  probe: FactoryCodeCliProbe | null | undefined,
): string | null {
  if (!probe) return null
  if (probe.blocker === FACTORY_CODE_CLI_NO_MODEL) {
    return MISSING_KIMI_CLI_MODEL
  }
  if (
    probe.credentials_file_present === true &&
    probe.default_model_configured === false &&
    probe.requires_kimi_credentials !== false
  ) {
    return MISSING_KIMI_CLI_MODEL
  }
  if (probe.blocker === FACTORY_CODE_CLI_CREDENTIALS_MISSING) {
    return MISSING_KIMI_CLI_CREDS
  }
  if (probe.credentials_file_present === false && probe.requires_kimi_credentials !== false) {
    return MISSING_KIMI_CLI_CREDS
  }
  return null
}

/** Floor / Platforms status-pill label for the /health CLI probe. */
export function factoryCodeCliStatusTitle(
  probeOrMessage: FactoryCodeCliProbe | string | null | undefined,
): string {
  const text =
    typeof probeOrMessage === 'string'
      ? probeOrMessage
      : factoryCodeCliHonesty(probeOrMessage) || ''
  if (text.includes(FACTORY_CODE_CLI_NO_MODEL) || text.includes('default_model')) {
    return 'Kimi Code CLI has no model'
  }
  return 'Kimi Code CLI credentials missing'
}

/** Honest export CTA. Gold "Download platform export" is only for Store-green. */
export function exportAffordance(build: BuildStatus | null | undefined): {
  label: string
  disabled: boolean
  ghost: boolean
  title?: string
} {
  if (isUnreadableLedger(build) || build?.state === 'failed' || shouldRefuseExport(build)) {
    return {
      label: 'Export (.zip) — pilot suite failed',
      disabled: true,
      ghost: true,
      title: 'Pilot suite failed — export is not pilot-ready and will be refused by the server',
    }
  }
  if (!build || build.state === 'building' || build.state === 'not_started') {
    return { label: 'Building…', disabled: true, ghost: true }
  }
  if (build.state === 'stalled') {
    return {
      label: 'Export (.zip) — build stalled',
      disabled: true,
      ghost: true,
      title: 'Build stalled — a full-pilot zip will be refused by the server',
    }
  }
  if (build.state === 'succeeded' && !isPilotZipReady(build)) {
    return {
      label: 'Download code-cycle prototype (.zip)',
      disabled: false,
      ghost: true,
      title: 'Code-cycle prototype — not a Store-green / full-pilot zip',
    }
  }
  if (build.state === 'succeeded' && isPilotZipReady(build)) {
    return { label: 'Download platform export (.zip)', disabled: false, ghost: false }
  }
  return { label: 'Building…', disabled: true, ghost: true }
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
