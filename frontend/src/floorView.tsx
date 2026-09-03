import { useCallback, useEffect, useRef, useState } from 'react'
import {
  awaitBuild,
  chatEventText,
  chatStream,
  downloadProductPackage,
  product,
  watchBuildStatus,
  type BuildStatus,
  type ChatEvent,
  type ProductDesign,
} from './api/factory'
import {
  formatFinishedAuthorship,
  formatHeartbeat,
  formatPhaseCounts,
  formatPhaseHeadline,
  phaseBarFraction,
  stampBuildObservation,
  withClientStall,
} from './buildProgress'

interface Capability {
  id: string
  description?: string
  strategy_hint?: string
  block_ids?: string[]
}

interface ChatMsg {
  role: 'user' | 'factory' | 'system'
  text: string
  card?: 'blueprint' | 'generation' | 'error' | 'info'
  engine?: string
  triggeredBy?: string
  blueprint?: {
    product_name?: string
    vertical?: string
    summary?: string
    capabilities?: Capability[]
    drafting_mode?: string
    drafting_note?: string
  }
}

function humanize(id: string): string {
  return id
    .replace(/_/g, ' ')
    .replace(/\b\w/g, (ch) => ch.toUpperCase())
}

export function BlueprintCard({
  blueprint,
  busy,
  onApprove,
  onRefine,
  accessPaused = false,
}: {
  blueprint: NonNullable<ChatMsg['blueprint']>
  busy: boolean
  onApprove: (excludedIds: string[]) => void
  onRefine: (text: string) => void
  accessPaused?: boolean
}) {
  const caps = blueprint.capabilities ?? []
  const [ticked, setTicked] = useState<Record<string, boolean>>(() =>
    Object.fromEntries(caps.map((c) => [c.id, true])),
  )
  const excluded = caps.filter((c) => ticked[c.id] === false).map((c) => c.id)
  const selectedCount = caps.length - excluded.length
  return (
    <div className="blueprint-card">
      <div className="bp-header">
        <strong>{blueprint.product_name ?? 'Untitled platform'}</strong>
        <span className="bp-vertical">{blueprint.vertical ?? '—'}</span>
        {blueprint.drafting_mode && (
          <span
            className={'bp-drafting-mode ' + blueprint.drafting_mode}
            title={blueprint.drafting_note ?? undefined}
          >
            {blueprint.drafting_mode === 'architect_llm'
              ? 'architect LLM'
              : blueprint.drafting_mode === 'golden_steward'
                ? 'golden blueprint'
                : 'template fallback — no LLM'}
          </span>
        )}
      </div>
      {blueprint.drafting_mode === 'keyword_fallback' && (
        <p className="bp-summary dim">
          The architect LLM did not draft this blueprint
          {blueprint.drafting_note ? ' (' + blueprint.drafting_note + ')' : ''} — it was
          assembled from deterministic templates.
        </p>
      )}
      {blueprint.summary && <p className="bp-summary">{blueprint.summary}</p>}
      <h4>Capabilities ({caps.length})</h4>
      {!accessPaused && (
        <p className="bp-pick-hint dim">Tick what the platform should include, then approve.</p>
      )}
      <ul className="bp-caps">
        {caps.map((c) => (
          <li key={c.id} className={ticked[c.id] === false ? 'bp-cap-excluded' : ''}>
            <label className="bp-cap-pick">
              <input
                type="checkbox"
                checked={ticked[c.id] !== false}
                disabled={busy || accessPaused}
                onChange={(e) =>
                  setTicked((t) => ({ ...t, [c.id]: e.target.checked }))
                }
              />
              <span className="bp-cap-id">{humanize(c.id)}</span>
              <span className={'bp-strategy ' + (c.strategy_hint ?? 'REUSE')}>{c.strategy_hint ?? 'REUSE'}</span>
            </label>
            {c.description && <p className="bp-cap-desc">{c.description}</p>}
            {c.block_ids && c.block_ids.length > 0 && (
              <p className="bp-cap-blocks">blocks: {c.block_ids.join(', ')}</p>
            )}
          </li>
        ))}
      </ul>
      {!accessPaused && (
        <>
          <div className="card-actions">
            <button disabled={busy || selectedCount === 0} onClick={() => onApprove(excluded)}>
              {excluded.length > 0
                ? 'Approve & build (' + selectedCount + ' of ' + caps.length + ')'
                : 'Approve & build'}
            </button>
          </div>
          <p className="bp-refine-hint">
            Refine:{' '}
            <button className="link" disabled={busy} onClick={() => onRefine('list capabilities')}>
              list capabilities
            </button>{' '}
            ·{' '}
            <button className="link" disabled={busy} onClick={() => onRefine('add capability payments')}>
              add payments
            </button>{' '}
            ·{' '}
            <button className="link" disabled={busy} onClick={() => onRefine('remove capability audit')}>
              remove audit
            </button>
          </p>
        </>
      )}
    </div>
  )
}

const KERNEL_JOBS: Record<string, { title: string; agent: boolean }> = {
  COLLECTOR: { title: 'Binding surveyor', agent: true },
  CLONER: { title: 'Block stocker', agent: false },
  WRITER: { title: 'Platform manufacturer', agent: true },
  TESTER: { title: 'Acceptance inspector', agent: true },
  STORE_MANAGER: { title: 'Store registrar', agent: false },
}

function KernelStrip({ build }: { build: BuildStatus | null }) {
  const phases = build?.phases?.length
    ? build.phases
    : ['COLLECTOR', 'CLONER', 'WRITER', 'TESTER', 'STORE_MANAGER']
  const done = new Set(build?.completed ?? [])
  const current = build?.current_phase?.id
  return (
    <ol className="kernel-strip">
      {phases.map((phase) => {
        const job = KERNEL_JOBS[phase]
        const cls = done.has(phase) ? 'done' : phase === current ? 'current' : undefined
        return (
          <li key={phase} className={cls}>
            <span className="kernel-id">{phase}</span>
            {job ? <span className="kernel-title">{job.title}</span> : null}
            {job?.agent ? <span className="kernel-agent">agent</span> : null}
          </li>
        )
      })}
    </ol>
  )
}

function CoderProgress({ build, nowMs }: { build: BuildStatus; nowMs: number }) {
  const headline = formatPhaseHeadline(build)
  const counts = formatPhaseCounts(build)
  const heartbeat = formatHeartbeat(build, nowMs)
  const last = build.last_event || build.activity
  const next = build.next_phase?.id
  const fraction = phaseBarFraction(build)
  return (
    <div className="coder-progress">
      <p className="coder-phase">
        Writing your platform — <strong>{headline}</strong>
        {build.current_phase?.label ? (
          <span className="coder-phase-label"> — {build.current_phase.label}</span>
        ) : null}
        {next ? <span className="coder-next"> then {next}</span> : null}
      </p>
      {fraction != null && (
        <div
          className="coder-bar"
          role="progressbar"
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={Math.round(fraction * 100)}
          aria-label={counts ?? headline}
        >
          <span style={{ width: `${Math.round(fraction * 100)}%` }} />
        </div>
      )}
      {counts && <p className="coder-counts">{counts}</p>}
      {last && <p className="coder-last">Last: {last}</p>}
      {heartbeat && <p className="coder-heartbeat">{heartbeat}</p>}
    </div>
  )
}

function coderTakeoverHeading(build: BuildStatus | null): string {
  if (build?.state === 'succeeded') {
    return build.pilot_ready
      ? 'Coding agent finished'
      : 'Code-cycle prototype ready'
  }
  return 'Coding agent has taken over'
}

function coderTakeoverNote(build: BuildStatus | null): string | null {
  if (!build) return null
  if (build.state === 'succeeded') {
    const finished = formatFinishedAuthorship(build.authorship, {
      pilotReady: build.pilot_ready,
    })
    if (build.pilot_ready) {
      if (finished?.startsWith('Finished')) return finished + '. Download ready.'
      return finished ?? 'Coding agent finished. Download it from Your Platforms.'
    }
    if (finished) {
      return (
        finished +
        (build.auto_pilot
          ? '. Download is a code-cycle prototype — the pilot cycle should open automatically.'
          : '. Download is a code-cycle prototype — continue to open a pilot cycle.')
      )
    }
    return build.auto_pilot
      ? 'Code-cycle 5/5 passed. Not yet pilot-ready. The pilot cycle should open automatically.'
      : 'Code-cycle 5/5 passed. Not yet pilot-ready. Continue to open a pilot cycle.'
  }
  if (build.state === 'failed' || build.state === 'stalled') {
    return 'The coding agent stopped: ' + (build.detail ?? 'build did not pass its gates') + '.'
  }
  const headline = formatPhaseHeadline(build)
  const last = build.last_event || build.activity
  if (last) {
    return 'Writing your platform — ' + headline + ' (last: ' + last + ')'
  }
  return 'Writing your platform — ' + headline
}

function latestProductCard(msgs: ChatMsg[]): ChatMsg | undefined {
  for (let i = msgs.length - 1; i >= 0; i -= 1) {
    const card = msgs[i].card
    if (card === 'blueprint' || (card === 'generation' && msgs[i].engine === 'runner')) {
      return msgs[i]
    }
  }
  return undefined
}

function hydrateFromDesign(design: ProductDesign): {
  msgs: ChatMsg[]
  coderActive: boolean
} {
  const msgs: ChatMsg[] = [
    {
      role: 'factory',
      text: 'This is the factory floor. Describe the platform you need — I will draft a blueprint, and when you approve the feature list the coding agent takes over and writes it.',
    },
  ]
  const bp = design.blueprint as ChatMsg['blueprint'] | null | undefined
  const gen = design.generation
  const pendingDraft = Boolean(bp && !design.blueprint_approved)
  if (pendingDraft && bp) {
    msgs.push({
      role: 'factory',
      text: 'Blueprint drafted: ' + (bp.product_name ?? 'platform') + ' (' + (bp.vertical ?? '—') + '). Approve the feature list to start the coding agent.',
      card: 'blueprint',
      blueprint: bp,
    })
    // A leftover generation from a prior runner must not paint takeover
    // over a newly drafted, still-pending feature list.
    return { msgs, coderActive: false }
  }
  if (gen?.engine === 'runner') {
    msgs.push({
      role: 'factory',
      text: 'The coding agent has taken over the floor.',
      card: 'generation',
      engine: 'runner',
      triggeredBy: gen.triggered_by,
    })
    return { msgs, coderActive: true }
  }
  return { msgs, coderActive: false }
}

export function Floor({
  sessionId,
  goPlatforms,
  accessPaused = false,
  notice = null,
}: {
  sessionId: string
  goPlatforms: () => void
  accessPaused?: boolean
  notice?: string | null
}) {
  const [msgs, setMsgs] = useState<ChatMsg[]>([
    {
      role: 'factory',
      text: 'This is the factory floor. Describe the platform you need — I will draft a blueprint, and when you approve the feature list the coding agent takes over and writes it.',
    },
  ])
  const [input, setInput] = useState('')
  const [busy, setBusy] = useState(false)
  const [coderBuild, setCoderBuild] = useState<BuildStatus | null>(null)
  const [coderActive, setCoderActive] = useState(false)
  const [watchEpoch, setWatchEpoch] = useState(0)
  const [downloading, setDownloading] = useState(false)
  const [downloadError, setDownloadError] = useState<string | null>(null)
  const [nowMs, setNowMs] = useState(() => Date.now())
  const bottomRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    let cancelled = false
    product
      .get(sessionId)
      .then((design) => {
        if (cancelled || !design) return
        const hydrated = hydrateFromDesign(design)
        setMsgs((current) => (current.length > 1 ? current : hydrated.msgs))
      })
      .catch(() => {})
    return () => {
      cancelled = true
    }
  }, [sessionId])

  useEffect(() => {
    bottomRef.current?.scrollIntoView?.({ behavior: 'smooth' })
  }, [msgs, coderBuild])

  useEffect(() => {
    const latest = latestProductCard(msgs)
    if (latest?.card === 'blueprint') {
      setCoderActive(false)
      setCoderBuild(null)
      return
    }
    if (latest?.card === 'generation' && latest.engine === 'runner') {
      setCoderActive(true)
    }
  }, [msgs])

  useEffect(() => {
    if (!coderActive) return
    const ac = new AbortController()
    void watchBuildStatus(
      sessionId,
      (s) => {
        if (!ac.signal.aborted) {
          setCoderBuild((prev) => stampBuildObservation(s, prev))
        }
      },
      { signal: ac.signal },
    ).catch(() => {})
    return () => ac.abort()
  }, [coderActive, sessionId, watchEpoch])

  const liveCoderBuild = withClientStall(coderBuild, nowMs)
  useEffect(() => {
    if (liveCoderBuild?.state !== 'building') return
    const id = window.setInterval(() => setNowMs(Date.now()), 5000)
    return () => window.clearInterval(id)
  }, [liveCoderBuild?.state])

  const sendCore = useCallback(
    async (message: string) => {
      setMsgs((m) => [...m, { role: 'user', text: message }, { role: 'factory', text: '' }])
      try {
        await chatStream(sessionId, message, (ev: ChatEvent) => {
          const token = chatEventText(ev)
          if (token !== null) {
            setMsgs((m) => {
              const copy = [...m]
              const last = copy[copy.length - 1]
              copy[copy.length - 1] = { ...last, text: last.text + token }
              return copy
            })
            return
          }
          if (ev.event === 'blueprint') {
            const d = (typeof ev.data === 'string' ? JSON.parse(ev.data) : ev.data) as {
              summary?: string
              blueprint?: ChatMsg['blueprint']
            }
            const summary = d?.summary ?? 'Blueprint drafted.'
            setCoderActive(false)
            setCoderBuild(null)
            setMsgs((m) => [
              ...m.slice(0, -1),
              {
                role: 'factory',
                text: summary,
                card: 'blueprint',
                blueprint: d?.blueprint,
              },
            ])
          } else if (ev.event === 'generation') {
            const d = (typeof ev.data === 'string' ? JSON.parse(ev.data) : ev.data) as {
              summary?: string
              triggered_by?: string
              generation?: { engine?: string; triggered_by?: string }
            } | null
            const summary = d?.summary ?? 'Platform generated.'
            const engine = d?.generation?.engine
            const triggeredBy = d?.triggered_by ?? d?.generation?.triggered_by
            if (engine === 'runner') {
              // Clear a prior FINISHED snapshot immediately so a pilot reopen
              // cannot keep "Download ready" pinned while Platforms is Building…
              setCoderBuild((prev) =>
                stampBuildObservation({ state: 'building', detail: 'build in progress' }, prev),
              )
              setCoderActive(true)
              setWatchEpoch((n) => n + 1)
            }
            setMsgs((m) => [
              ...m.slice(0, -1),
              { role: 'factory', text: summary, card: 'generation', engine, triggeredBy },
            ])
          } else if (ev.event === 'error') {
            setMsgs((m) => [
              ...m.slice(0, -1),
              { role: 'factory', text: String(ev.data ?? 'Something went wrong.'), card: 'error' },
            ])
          } else if (ev.event === 'chain' || ev.event === 'rules') {
            setMsgs((m) => {
              const copy = [...m]
              const last = copy[copy.length - 1]
              if (last?.text?.trim()) return copy
              copy[copy.length - 1] = {
                ...last,
                text: 'That sounds like kit configuration. The floor builds whole platforms — describe the platform you want instead.',
                card: 'info',
              }
              return copy
            })
          }
        })
      } catch (e) {
        setMsgs((m) => [
          ...m.slice(0, -1),
          { role: 'factory', text: e instanceof Error ? e.message : 'chat failed', card: 'error' },
        ])
      }
    },
    [sessionId],
  )

  const send = useCallback(
    async (text: string) => {
      const message = text.trim()
      if (!message || busy || accessPaused) return
      setInput('')
      setBusy(true)
      try {
        await sendCore(message)
      } finally {
        setBusy(false)
      }
    },
    [accessPaused, busy, sendCore],
  )

  const approveWithSelection = useCallback(
    async (excludedIds: string[]) => {
      if (busy || accessPaused) return
      setBusy(true)
      try {
        for (const id of excludedIds) {
          await sendCore('remove capability ' + id)
        }
        await sendCore('approve')
      } finally {
        setBusy(false)
      }
    },
    [accessPaused, busy, sendCore],
  )

  async function download() {
    setDownloading(true)
    setDownloadError(null)
    try {
      const status =
        liveCoderBuild?.state === 'succeeded'
          ? liveCoderBuild
          : await awaitBuild(sessionId, (s) =>
              setCoderBuild((prev) => stampBuildObservation(s, prev)),
            )
      if (status.state === 'failed' || status.state === 'stalled') {
        setDownloadError(
          `The build did not pass its gates, so it will not be shipped: ${status.detail ?? 'unknown reason'}`,
        )
        return
      }
      await downloadProductPackage(sessionId)
    } catch (e) {
      setDownloadError(e instanceof Error ? e.message : 'export failed')
    } finally {
      setDownloading(false)
    }
  }

  const coderBuilding =
    coderActive &&
    liveCoderBuild?.state !== 'succeeded' &&
    liveCoderBuild?.state !== 'failed' &&
    liveCoderBuild?.state !== 'stalled'
  const coderSucceeded = liveCoderBuild?.state === 'succeeded'
  const coderFailed = liveCoderBuild?.state === 'failed'
  const coderPilotReady = liveCoderBuild?.pilot_ready === true
  const latestGenerationIdx = (() => {
    for (let i = msgs.length - 1; i >= 0; i -= 1) {
      if (msgs[i].card === 'generation') return i
    }
    return -1
  })()

  return (
    <div className="floor">
      <header className="page-head">
        <h2>Factory Floor</h2>
        {notice && (
          <div className="notice-box already-signed-in" role="status">
            {notice}
          </div>
        )}
        <p className="dim">Describe the platform. Approve the feature list. The coding agent takes over and writes it.</p>
        <p className="dim notice-not-yet">
          What this is not yet: the factory generates a working prototype — real code,
          tests and deploy files — not a finished production system. Third-party
          integrations in generated products are stubs until you connect your own
          credentials, deployment is a step you run rather than something that happens
          for you, and free-trial accounts have server-enforced caps on generations,
          daily chat and exports. Answers are grounding-checked: when a claim can't be
          verified it is withheld, not invented.
        </p>
      </header>
      <div className="chat-scroll">
        {msgs.map((m, i) => (
          <div key={i} className={'bubble-row ' + m.role}>
            <div className={'bubble ' + m.role + ' ' + (m.card ?? '')}>
              {m.text || (m.role === 'factory' && busy && i === msgs.length - 1 ? <span className="typing">…</span> : null)}
              {m.card === 'blueprint' && m.blueprint && (
                <BlueprintCard
                  blueprint={m.blueprint}
                  busy={busy || coderActive}
                  accessPaused={accessPaused}
                  onApprove={(excludedIds) => void approveWithSelection(excludedIds)}
                  onRefine={(text) => send(text)}
                />
              )}
              {m.card === 'generation' && (
                <div className="card-actions">
                  {m.engine === 'runner' && (
                    <span
                      className="bp-drafting-mode architect_llm"
                      title="The coding agent took over after you approved the feature list"
                    >
                      coding agent
                    </span>
                  )}
                  {m.triggeredBy === 'chat_llm' && (
                    <span
                      className="bp-drafting-mode architect_llm"
                      title="The Floor chat LLM called start_coder"
                    >
                      chat LLM
                    </span>
                  )}
                  {i === latestGenerationIdx && (
                    <button type="button" onClick={goPlatforms}>
                      Open Your Platforms
                    </button>
                  )}
                </div>
              )}
            </div>
          </div>
        ))}
        <div ref={bottomRef} />
      </div>
      {coderActive && (
        <div
          className={
            'coder-takeover' +
            (liveCoderBuild?.stale ||
            liveCoderBuild?.state === 'stalled' ||
            liveCoderBuild?.state === 'failed'
              ? ' stale'
              : '')
          }
          role="status"
        >
          <h3>
            {liveCoderBuild?.state === 'succeeded'
              ? coderTakeoverHeading(liveCoderBuild)
              : liveCoderBuild?.state === 'stalled'
                ? 'Coding agent stalled'
                : liveCoderBuild?.state === 'failed'
                  ? 'Coding agent stopped'
                  : 'Coding agent has taken over'}
          </h3>
          <KernelStrip build={liveCoderBuild} />
          {liveCoderBuild && liveCoderBuild.state === 'building' ? (
            <CoderProgress build={liveCoderBuild} nowMs={nowMs} />
          ) : (
            <p>
              {coderTakeoverNote(liveCoderBuild) ??
                'The feature list is approved. The coding agent is starting WRITER now.'}
            </p>
          )}
          {coderSucceeded && (
            <div className="card-actions">
              {!coderPilotReady && (
                <button
                  type="button"
                  data-testid="continue-to-pilot"
                  onClick={() => void send('continue')}
                  disabled={busy || accessPaused}
                >
                  Continue to pilot
                </button>
              )}
              <button
                type="button"
                className={coderPilotReady ? undefined : 'ghost'}
                onClick={() => void download()}
                disabled={downloading}
              >
                {downloading
                  ? 'Packing…'
                  : coderPilotReady
                    ? 'Download platform export (.zip)'
                    : 'Download code-cycle prototype (.zip)'}
              </button>
            </div>
          )}
          {coderFailed && (
            <div className="card-actions">
              <span className="status-pill status-pill-failed" data-testid="floor-failed-pill">
                Pilot suite failed
              </span>
            </div>
          )}
          {downloadError && <div className="error-box">{downloadError}</div>}
        </div>
      )}
      <form
        className="composer"
        onSubmit={(e) => {
          e.preventDefault()
          send(input)
        }}
      >
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder={
            accessPaused
              ? 'Factory access is paused'
              : coderBuilding
                ? 'The coding agent has taken over this floor…'
                : 'Try: "Build me a secure multi-user platform for my team…"'
          }
          disabled={busy || coderBuilding || accessPaused}
        />
        <button type="submit" disabled={busy || coderBuilding || accessPaused || !input.trim()}>
          Send
        </button>
      </form>
      {accessPaused && (
        <p className="dim composer-paused">Factory access is paused.</p>
      )}
    </div>
  )
}
