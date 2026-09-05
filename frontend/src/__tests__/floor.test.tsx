/**
 * Factory Floor contract: the architect LLM drafts, the user approves
 * the feature list, the coding agent takes over and manufactures it.
 */
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { Floor } from '../App'

const chatStreamMock = vi.fn()
const awaitBuildMock = vi.fn()
const watchBuildMock = vi.fn()
const getMock = vi.fn()
const downloadMock = vi.fn()
const coderControlMock = vi.fn()
const getHealthMock = vi.fn()

vi.mock('../api/factory', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/factory')>()
  return {
    ...actual,
    chatStream: (...args: unknown[]) => chatStreamMock(...args),
    awaitBuild: (...args: unknown[]) => awaitBuildMock(...args),
    watchBuildStatus: (...args: unknown[]) => watchBuildMock(...args),
    downloadProductPackage: (...args: unknown[]) => downloadMock(...args),
    getHealth: (...args: unknown[]) => getHealthMock(...args),
    product: {
      ...actual.product,
      get: (...args: unknown[]) => getMock(...args),
      coderControl: (...args: unknown[]) => coderControlMock(...args),
    },
  }
})

const LLM_BLUEPRINT = {
  product_name: 'Vineyard Platform',
  vertical: 'winery',
  summary: 'Tank, barrel and club operations for a family winery.',
  drafting_mode: 'architect_llm',
  capabilities: [
    { id: 'fermentation_tanks', description: 'Track tanks', strategy_hint: 'GENERATE' },
    { id: 'audit', description: 'Audit trails', strategy_hint: 'REUSE', block_ids: ['audit'] },
  ],
}

describe('Factory Floor — architect LLM then coding agent', () => {
  beforeEach(() => {
    chatStreamMock.mockReset()
    awaitBuildMock.mockReset()
    watchBuildMock.mockReset()
    getMock.mockReset()
    downloadMock.mockReset()
    coderControlMock.mockReset()
    coderControlMock.mockResolvedValue({ ok: true, control: { action: 'pause' } })
    getHealthMock.mockReset()
    getHealthMock.mockResolvedValue({
      factory_code_cli: { available: true, credentials_file_present: true },
    })
    getMock.mockResolvedValue({})
    watchBuildMock.mockImplementation(async (_sid: string, onProgress: (s: object) => void) => {
      onProgress({
        state: 'building',
        activity: 'wrote handler payments',
        last_event: 'wrote handler payments',
        current_phase: { id: 'WRITER', label: 'Platform manufacturer' },
        phase_index: 3,
        phase_total: 5,
        phases_done: 2,
        phases_total: 5,
        next_phase: { id: 'TESTER', label: 'Acceptance inspector' },
        phase_progress: { done: 7, total: 7, fraction: 1, stage: 'handlers' },
        last_event_age_s: 8,
        stale: false,
        coder_log: 'dispatching compiled brief via FACTORY_CODE_CLI\nSTEP 0 inventory ok\n',
        coder_log_present: true,
        coder_control: 'run',
      })
    })
  })

  it('drafts with the architect LLM and labels the blueprint', async () => {
    chatStreamMock.mockImplementation(async (_sid: string, _msg: string, onEvent: (ev: { event: string; data: unknown }) => void) => {
      onEvent({
        event: 'blueprint',
        data: {
          summary: 'Blueprint drafted: Vineyard Platform (winery). Drafted by the architect LLM.',
          blueprint: LLM_BLUEPRINT,
        },
      })
    })
    render(<Floor sessionId="sess_ui" goPlatforms={() => {}} />)
    fireEvent.change(screen.getByPlaceholderText(/Try:/), {
      target: { value: 'Build me a vineyard management platform for a family winery' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Send' }))
    expect(await screen.findByText('architect LLM')).toBeInTheDocument()
    expect(screen.getByText('Vineyard Platform')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Approve & build' })).toBeEnabled()
    expect(chatStreamMock).toHaveBeenCalledWith(
      'sess_ui',
      'Build me a vineyard management platform for a family winery',
      expect.any(Function),
    )
  })

  it('approve starts a coding-agent runner build', async () => {
    const goPlatforms = vi.fn()
    chatStreamMock.mockImplementation(async (_sid: string, message: string, onEvent: (ev: { event: string; data: unknown }) => void) => {
      if (message === 'approve') {
        onEvent({
          event: 'generation',
          data: {
            summary:
              'The chat LLM started the coding agent. Build started for vineyard: the coding agent has taken over the floor and is writing 2 capability(ies).',
            triggered_by: 'chat_llm',
            generation: { engine: 'runner', product_id: 'vineyard', triggered_by: 'chat_llm' },
          },
        })
        return
      }
      onEvent({
        event: 'blueprint',
        data: { summary: 'Blueprint drafted.', blueprint: LLM_BLUEPRINT },
      })
    })
    render(<Floor sessionId="sess_ui" goPlatforms={goPlatforms} />)
    fireEvent.change(screen.getByPlaceholderText(/Try:/), {
      target: { value: 'Build me a vineyard management platform' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Send' }))
    await waitFor(() =>
      expect(screen.getByRole('button', { name: 'Approve & build' })).toBeEnabled(),
    )
    fireEvent.click(screen.getByRole('button', { name: 'Approve & build' }))
    expect(await screen.findByText('coding agent')).toBeInTheDocument()
    expect(screen.getByText('chat LLM')).toBeInTheDocument()
    expect(await screen.findByRole('status')).toHaveTextContent(/Coding agent has taken over/)
    expect(await screen.findByText('COLLECTOR')).toBeInTheDocument()
    expect(screen.getByText('Binding surveyor')).toBeInTheDocument()
    expect(screen.getByText('WRITER')).toBeInTheDocument()
    expect(screen.getByText('Platform manufacturer')).toBeInTheDocument()
    expect(screen.getByText('TESTER')).toBeInTheDocument()
    expect(screen.getByText('Acceptance inspector')).toBeInTheDocument()
    expect(screen.getByText('Block stocker')).toBeInTheDocument()
    expect(screen.getByText('Store registrar')).toBeInTheDocument()
    expect(await screen.findByText(/Writing your platform/)).toBeInTheDocument()
    expect(screen.getByText('WRITER 3/5')).toBeInTheDocument()
    expect(screen.getByText(/then TESTER/)).toBeInTheDocument()
    expect(screen.getByText('7/7 handlers')).toBeInTheDocument()
    expect(screen.getByText(/Last: wrote handler payments/)).toBeInTheDocument()
    expect(screen.getByText(/still working/)).toBeInTheDocument()
    expect(screen.getByPlaceholderText(/coding agent has taken over/i)).toBeDisabled()
    fireEvent.click(screen.getByRole('button', { name: 'Open Your Platforms' }))
    expect(goPlatforms).toHaveBeenCalled()
    expect(chatStreamMock).toHaveBeenCalledWith('sess_ui', 'approve', expect.any(Function))
    expect(watchBuildMock).toHaveBeenCalled()
  })

  it('labels keyword fallback when the architect LLM did not draft', async () => {
    chatStreamMock.mockImplementation(async (_sid: string, _msg: string, onEvent: (ev: { event: string; data: unknown }) => void) => {
      onEvent({
        event: 'blueprint',
        data: {
          summary: 'Drafted by deterministic templates (no LLM).',
          blueprint: {
            ...LLM_BLUEPRINT,
            drafting_mode: 'keyword_fallback',
            drafting_note: 'LLM drafting disabled',
          },
        },
      })
    })
    render(<Floor sessionId="sess_ui" goPlatforms={() => {}} />)
    fireEvent.change(screen.getByPlaceholderText(/Try:/), {
      target: { value: 'Build me a vineyard management platform' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Send' }))
    expect(await screen.findByText(/template fallback — no LLM/)).toBeInTheDocument()
    expect(screen.getByText(/The architect LLM did not draft this blueprint/)).toBeInTheDocument()
  })

  it('labels a golden lettings draft as a golden blueprint, not template fallback', async () => {
    chatStreamMock.mockImplementation(async (_sid: string, _msg: string, onEvent: (ev: { event: string; data: unknown }) => void) => {
      onEvent({
        event: 'blueprint',
        data: {
          summary: 'Drafted from the golden residential-lettings blueprint.',
          blueprint: {
            product_name: 'Residential Lettings Platform',
            vertical: 'residential_lettings',
            summary: 'Factory golden for a residential-lettings platform.',
            drafting_mode: 'golden_lettings',
            capabilities: [
              { id: 'viewing_management', description: 'Record a viewing', strategy_hint: 'COMPOSE' },
            ],
          },
        },
      })
    })
    render(<Floor sessionId="sess_lettings" goPlatforms={() => {}} />)
    fireEvent.change(screen.getByPlaceholderText(/Try:/), {
      target: { value: 'Build a platform for residential lettings' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Send' }))
    expect(await screen.findByText(/golden blueprint/)).toBeInTheDocument()
    expect(screen.queryByText(/template fallback — no LLM/)).not.toBeInTheDocument()
    expect(screen.queryByText(/The architect LLM did not draft this blueprint/)).not.toBeInTheDocument()
  })

  it('restores the feature list and coder takeover from the session product', async () => {
    getMock.mockResolvedValue({
      blueprint: LLM_BLUEPRINT,
      blueprint_approved: true,
      generation: { engine: 'runner', product_id: 'vineyard', triggered_by: 'chat_llm' },
    })
    render(<Floor sessionId="sess_hydrate" goPlatforms={() => {}} />)
    expect(await screen.findByText('coding agent')).toBeInTheDocument()
    expect(screen.getByText('chat LLM')).toBeInTheDocument()
    expect(await screen.findByRole('status')).toHaveTextContent(/Coding agent has taken over/)
    expect(screen.getByPlaceholderText(/coding agent has taken over/i)).toBeDisabled()
  })

  it('restores a pending blueprint after remount so Approve & build is still there', async () => {
    getMock.mockResolvedValue({
      blueprint: LLM_BLUEPRINT,
      blueprint_approved: false,
    })
    render(<Floor sessionId="sess_pending" goPlatforms={() => {}} />)
    expect(await screen.findByText('architect LLM')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Approve & build' })).toBeEnabled()
  })

  it('does not show coder takeover when hydrating a pending draft over leftover generation', async () => {
    getMock.mockResolvedValue({
      blueprint: LLM_BLUEPRINT,
      blueprint_approved: false,
      generation: { engine: 'runner', product_id: 'old-winery', triggered_by: 'chat_llm' },
    })
    render(<Floor sessionId="sess_stale_runner" goPlatforms={() => {}} />)
    expect(await screen.findByText('Vineyard Platform')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Approve & build' })).toBeEnabled()
    expect(screen.queryByRole('status')).not.toBeInTheDocument()
    expect(screen.getByPlaceholderText(/Try:/)).toBeEnabled()
  })

  it('SUCCESS takeover heading is finished only when pilot-ready', async () => {
    watchBuildMock.mockImplementation(async (_sid: string, onProgress: (s: object) => void) => {
      onProgress({
        state: 'succeeded',
        pilot_ready: true,
        cycle: 'pilot',
        authorship: { artifacts: 28, agent_written: 22, templated: 6 },
      })
    })
    getMock.mockResolvedValue({
      blueprint: LLM_BLUEPRINT,
      blueprint_approved: true,
      generation: { engine: 'runner', product_id: 'automotive-retail', triggered_by: 'chat_llm' },
    })
    render(<Floor sessionId="sess_done" goPlatforms={() => {}} />)
    expect(await screen.findByRole('heading', { name: 'Coding agent finished' })).toBeInTheDocument()
    expect(screen.getByText('Finished — 22 artifacts; 6 templated. Download ready.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Download platform export (.zip)' })).toBeEnabled()
    expect(screen.queryByText(/22 of 28/)).not.toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'Coding agent has taken over' })).not.toBeInTheDocument()
    expect(screen.getByPlaceholderText(/Try:/)).toBeEnabled()
  })

  it('code-cycle SUCCESS is a prototype, not Finished / Download ready', async () => {
    watchBuildMock.mockImplementation(async (_sid: string, onProgress: (s: object) => void) => {
      onProgress({
        state: 'succeeded',
        pilot_ready: false,
        cycle: 'code',
        authorship: { artifacts: 24, agent_written: 11, templated: 13 },
      })
    })
    getMock.mockResolvedValue({
      blueprint: LLM_BLUEPRINT,
      blueprint_approved: true,
      generation: { engine: 'runner', product_id: 'residential-lettings', triggered_by: 'chat_llm' },
    })
    render(<Floor sessionId="sess_proto" goPlatforms={() => {}} />)
    expect(await screen.findByRole('heading', { name: 'Code-cycle prototype ready' })).toBeInTheDocument()
    expect(
      screen.getByText(/Code-cycle prototype — 11 artifacts; 13 templated. Not yet pilot-ready/),
    ).toBeInTheDocument()
    expect(screen.queryByText(/Download ready/)).not.toBeInTheDocument()
    expect(screen.queryByText(/Finished —/)).not.toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'Coding agent finished' })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Download code-cycle prototype (.zip)' })).toBeEnabled()
    expect(screen.getByRole('button', { name: 'Continue to pilot' })).toBeEnabled()
    expect(screen.getByPlaceholderText(/Try:/)).toBeEnabled()
  })

  it('surfaces CODE_GREEN level_grade — never Finished / founding / Download ready', async () => {
    watchBuildMock.mockImplementation(async (_sid: string, onProgress: (s: object) => void) => {
      onProgress({
        state: 'succeeded',
        pilot_ready: false,
        cycle: 'code',
        authorship: { artifacts: 24, agent_written: 11, templated: 13 },
        level_grade: {
          level: 'CODE_GREEN',
          pilot_ready: false,
          founding_customer_ready: false,
          three_gate: { CODE: 'PASS', PRODUCT: 'NOT_RUN', STORE: 'NOT_RUN' },
          blockers: ['pilot_ready is false'],
        },
      })
    })
    getMock.mockResolvedValue({
      blueprint: LLM_BLUEPRINT,
      blueprint_approved: true,
      generation: { engine: 'runner', product_id: 'residential-lettings', triggered_by: 'chat_llm' },
    })
    render(<Floor sessionId="sess_code_green" goPlatforms={() => {}} />)
    expect(await screen.findByRole('heading', { name: 'Code-cycle prototype ready' })).toBeInTheDocument()
    expect(screen.getByTestId('floor-prototype-pill')).toHaveTextContent('Code-green (prototype)')
    expect(screen.getByTestId('floor-gate-code')).toHaveTextContent('CODE PASS')
    expect(screen.getByTestId('floor-gate-product')).toHaveTextContent('PRODUCT NOT RUN')
    expect(screen.getByTestId('floor-gate-store')).toHaveTextContent('STORE NOT RUN')
    expect(screen.queryByText(/Founding-customer-ready/)).not.toBeInTheDocument()
    expect(screen.queryByText(/Download ready/)).not.toBeInTheDocument()
    expect(screen.queryByText(/Finished —/)).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Download platform export (.zip)' })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Download code-cycle prototype (.zip)' })).toBeEnabled()
  })

  it('surfaces FOUNDING_CUSTOMER_READY and keeps the gold export enabled', async () => {
    watchBuildMock.mockImplementation(async (_sid: string, onProgress: (s: object) => void) => {
      onProgress({
        state: 'succeeded',
        pilot_ready: true,
        cycle: 'pilot',
        authorship: { artifacts: 28, agent_written: 22, templated: 6 },
        level_grade: {
          level: 'FOUNDING_CUSTOMER_READY',
          pilot_ready: true,
          founding_customer_ready: true,
          three_gate: { CODE: 'PASS', PRODUCT: 'PASS', STORE: 'PASS' },
          blockers: [],
        },
      })
    })
    getMock.mockResolvedValue({
      blueprint: LLM_BLUEPRINT,
      blueprint_approved: true,
      generation: { engine: 'runner', product_id: 'residential-lettings', triggered_by: 'chat_llm' },
    })
    render(<Floor sessionId="sess_founding" goPlatforms={() => {}} />)
    expect(await screen.findByRole('heading', { name: 'Coding agent finished' })).toBeInTheDocument()
    expect(screen.getByTestId('floor-pilot-ready-pill')).toHaveTextContent('Founding-customer-ready')
    expect(screen.getByTestId('floor-gate-product')).toHaveTextContent('PRODUCT PASS')
    expect(screen.getByTestId('floor-gate-store')).toHaveTextContent('STORE PASS')
    expect(screen.getByText(/Founding-customer-ready. Download ready./)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Download platform export (.zip)' })).toBeEnabled()
    expect(screen.queryByRole('button', { name: 'Continue to pilot' })).not.toBeInTheDocument()
  })

  it('does not gild a STORE_GREEN zip as founding-customer-ready', async () => {
    watchBuildMock.mockImplementation(async (_sid: string, onProgress: (s: object) => void) => {
      onProgress({
        state: 'succeeded',
        pilot_ready: true,
        cycle: 'pilot',
        authorship: { artifacts: 28, agent_written: 22, templated: 6 },
        level_grade: {
          level: 'STORE_GREEN',
          pilot_ready: true,
          founding_customer_ready: false,
          three_gate: { CODE: 'PASS', PRODUCT: 'PASS', STORE: 'PASS' },
          blockers: ['handlers call the store over HTTP: viewing_management.py'],
        },
      })
    })
    getMock.mockResolvedValue({
      blueprint: LLM_BLUEPRINT,
      blueprint_approved: true,
      generation: { engine: 'runner', product_id: 'residential-lettings', triggered_by: 'chat_llm' },
    })
    render(<Floor sessionId="sess_store" goPlatforms={() => {}} />)
    expect(await screen.findByTestId('floor-pilot-ready-pill')).toHaveTextContent('Store-green')
    expect(screen.getByText(/Store-green zip ready — not founding-customer-ready/)).toBeInTheDocument()
    expect(screen.queryByText(/Founding-customer-ready/)).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Download platform export (.zip)' })).toBeEnabled()
  })

  it('refuses Floor Download when coder_receipt is FACTORY_CODE_CLI_FAILED', async () => {
    watchBuildMock.mockImplementation(async (_sid: string, onProgress: (s: object) => void) => {
      onProgress({
        state: 'succeeded',
        pilot_ready: true,
        cycle: 'pilot',
        authorship: { artifacts: 24, agent_written: 1, templated: 23 },
        level_grade: {
          level: 'FOUNDING_CUSTOMER_READY',
          founding_customer_ready: true,
          pilot_ready: true,
        },
        coder_receipt: {
          ok: false,
          blocker: 'FACTORY_CODE_CLI_FAILED',
          detail: 'FACTORY_CODE_CLI_FAILED: CLI exited 1',
        },
      })
    })
    getMock.mockResolvedValue({
      blueprint: LLM_BLUEPRINT,
      blueprint_approved: true,
      generation: { engine: 'runner', product_id: 'veterinary-care', triggered_by: 'chat_llm' },
    })
    render(<Floor sessionId="sess_45729bb662cf4a5d" goPlatforms={() => {}} />)
    expect(await screen.findByRole('heading', { name: 'Coding agent stopped' })).toBeInTheDocument()
    expect(screen.getByTestId('floor-failed-pill')).toHaveTextContent('Pilot suite failed')
    expect(screen.getByText(/FACTORY_CODE_CLI_FAILED: CLI exited 1/)).toBeInTheDocument()
    expect(screen.queryByText(/Founding-customer-ready/)).not.toBeInTheDocument()
    expect(screen.queryByText(/Finished —/)).not.toBeInTheDocument()
    expect(screen.queryByText(/Download ready/)).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Download platform export (.zip)' })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Export (.zip) — pilot suite failed' })).toBeDisabled()
  })

  it('honesty-locks a founding claim when pilot_ready is false', async () => {
    watchBuildMock.mockImplementation(async (_sid: string, onProgress: (s: object) => void) => {
      onProgress({
        state: 'succeeded',
        pilot_ready: false,
        cycle: 'code',
        authorship: { artifacts: 10, agent_written: 4, templated: 6 },
        level_grade: {
          level: 'FOUNDING_CUSTOMER_READY',
          pilot_ready: false,
          founding_customer_ready: true,
          three_gate: { CODE: 'PASS', PRODUCT: 'PASS', STORE: 'PASS' },
        },
      })
    })
    getMock.mockResolvedValue({
      blueprint: LLM_BLUEPRINT,
      blueprint_approved: true,
      generation: { engine: 'runner', product_id: 'residential-lettings', triggered_by: 'chat_llm' },
    })
    render(<Floor sessionId="sess_lock" goPlatforms={() => {}} />)
    expect(await screen.findByRole('heading', { name: 'Code-cycle prototype ready' })).toBeInTheDocument()
    expect(screen.getByTestId('floor-prototype-pill')).toHaveTextContent('Code-green (prototype)')
    expect(screen.queryByText(/Founding-customer-ready/)).not.toBeInTheDocument()
    expect(screen.queryByText(/Download ready/)).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Download platform export (.zip)' })).not.toBeInTheDocument()
  })

  it('Continue to pilot sends continue when auto-pilot is blocked', async () => {
    chatStreamMock.mockImplementation(async (_sid: string, _msg: string, onEvent: (ev: { event: string; data: unknown }) => void) => {
      onEvent({
        event: 'generation',
        data: {
          summary: 'Opening pilot cycle for residential-lettings on the same workspace/hash.',
          triggered_by: 'chat_llm',
          generation: { engine: 'runner', product_id: 'residential-lettings', triggered_by: 'chat_llm' },
        },
      })
    })
    watchBuildMock
      .mockImplementationOnce(async (_sid: string, onProgress: (s: object) => void) => {
        onProgress({
          state: 'succeeded',
          pilot_ready: false,
          cycle: 'code',
          auto_pilot: false,
          authorship: { artifacts: 24, agent_written: 11, templated: 13 },
        })
      })
      .mockImplementation(async (_sid: string, onProgress: (s: object) => void) => {
        onProgress({
          state: 'building',
          phases_done: 2,
          phases_total: 5,
          current_phase: { id: 'TESTER', label: 'Acceptance inspector' },
          phase_index: 4,
          phase_total: 5,
        })
      })
    getMock.mockResolvedValue({
      blueprint: LLM_BLUEPRINT,
      blueprint_approved: true,
      generation: { engine: 'runner', product_id: 'residential-lettings', triggered_by: 'chat_llm' },
    })
    render(<Floor sessionId="sess_cta" goPlatforms={() => {}} />)
    fireEvent.click(await screen.findByRole('button', { name: 'Continue to pilot' }))
    await waitFor(() => expect(chatStreamMock).toHaveBeenCalledWith('sess_cta', 'continue', expect.any(Function)))
    expect(await screen.findByRole('heading', { name: 'Coding agent has taken over' })).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'Coding agent finished' })).not.toBeInTheDocument()
  })

  it('downloads the zip from the Floor after the coding agent finishes', async () => {
    downloadMock.mockResolvedValue(undefined)
    watchBuildMock.mockImplementation(async (_sid: string, onProgress: (s: object) => void) => {
      onProgress({
        state: 'succeeded',
        pilot_ready: true,
        cycle: 'pilot',
        authorship: { artifacts: 19, agent_written: 13, templated: 6 },
      })
    })
    getMock.mockResolvedValue({
      blueprint: LLM_BLUEPRINT,
      blueprint_approved: true,
      generation: { engine: 'runner', product_id: 'vineyard', triggered_by: 'chat_llm' },
    })
    render(<Floor sessionId="sess_dl" goPlatforms={() => {}} />)
    fireEvent.click(await screen.findByRole('button', { name: 'Download platform export (.zip)' }))
    await waitFor(() => expect(downloadMock).toHaveBeenCalledWith('sess_dl'))
  })

  it('replaces FINISHED with TAKEN OVER when a pilot generation event arrives', async () => {
    watchBuildMock
      .mockImplementationOnce(async (_sid: string, onProgress: (s: object) => void) => {
        onProgress({
          state: 'succeeded',
          pilot_ready: false,
          cycle: 'code',
          authorship: { artifacts: 30, agent_written: 11, templated: 19 },
        })
      })
      .mockImplementation(async (_sid: string, onProgress: (s: object) => void) => {
        onProgress({
          state: 'building',
          phases_done: 2,
          phases_total: 5,
          current_phase: { id: 'WRITER', label: 'Platform manufacturer' },
          phase_index: 3,
          phase_total: 5,
          last_event: 'wrote handler tenancy_application_pipeline',
          last_event_age_s: 12,
        })
      })
    getMock.mockResolvedValue({
      blueprint: LLM_BLUEPRINT,
      blueprint_approved: true,
      generation: { engine: 'runner', product_id: 'residential-lettings', triggered_by: 'chat_llm' },
    })
    chatStreamMock.mockImplementation(async (_sid: string, _msg: string, onEvent: (ev: { event: string; data: unknown }) => void) => {
      onEvent({
        event: 'generation',
        data: {
          summary: 'Opening pilot cycle for residential-lettings on the same workspace/hash.',
          triggered_by: 'chat_llm',
          generation: { engine: 'runner', product_id: 'residential-lettings', triggered_by: 'chat_llm' },
        },
      })
    })
    render(<Floor sessionId="sess_pilot" goPlatforms={() => {}} />)
    expect(await screen.findByRole('heading', { name: 'Code-cycle prototype ready' })).toBeInTheDocument()
    fireEvent.change(screen.getByPlaceholderText(/Try:/), {
      target: { value: '(a) continue the existing lettings hub into its pilot cycle' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Send' }))
    expect(await screen.findByRole('heading', { name: 'Coding agent has taken over' })).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'Coding agent finished' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Download platform export (.zip)' })).not.toBeInTheDocument()
    expect(screen.getByText(/Writing your platform/)).toBeInTheDocument()
  })

  it('keeps only one Open Your Platforms CTA after a second generation card', async () => {
    watchBuildMock
      .mockImplementationOnce(async (_sid: string, onProgress: (s: object) => void) => {
        onProgress({
          state: 'succeeded',
          pilot_ready: false,
          cycle: 'code',
          authorship: { artifacts: 30, agent_written: 11, templated: 19 },
        })
      })
      .mockImplementation(async (_sid: string, onProgress: (s: object) => void) => {
        onProgress({
          state: 'building',
          phases_done: 2,
          phases_total: 5,
          current_phase: { id: 'WRITER', label: 'Platform manufacturer' },
          phase_index: 3,
          phase_total: 5,
        })
      })
    getMock.mockResolvedValue({
      blueprint: LLM_BLUEPRINT,
      blueprint_approved: true,
      generation: { engine: 'runner', product_id: 'residential-lettings', triggered_by: 'chat_llm' },
    })
    chatStreamMock.mockImplementation(async (_sid: string, _msg: string, onEvent: (ev: { event: string; data: unknown }) => void) => {
      onEvent({
        event: 'generation',
        data: {
          summary: 'Opening pilot cycle.',
          triggered_by: 'chat_llm',
          generation: { engine: 'runner', triggered_by: 'chat_llm' },
        },
      })
    })
    render(<Floor sessionId="sess_dup" goPlatforms={() => {}} />)
    // Wait for the succeeded snapshot — otherwise coderActive+null build
    // briefly disables the composer (pilot reopen race).
    expect(await screen.findByRole('heading', { name: 'Code-cycle prototype ready' })).toBeInTheDocument()
    expect(screen.getAllByRole('button', { name: 'Open Your Platforms' })).toHaveLength(1)
    fireEvent.change(screen.getByPlaceholderText(/Try:/), {
      target: { value: '(a) continue the existing lettings hub into its pilot cycle' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Send' }))
    await screen.findByText('Opening pilot cycle.')
    expect(screen.getAllByRole('button', { name: 'Open Your Platforms' })).toHaveLength(1)
  })

  it('offers New session on the Floor header', async () => {
    const onNewSession = vi.fn()
    render(
      <Floor sessionId="sess_ui" goPlatforms={() => {}} onNewSession={onNewSession} />,
    )
    const btn = await screen.findByRole('button', { name: 'New session' })
    expect(btn).toBeEnabled()
    fireEvent.click(btn)
    await waitFor(() => expect(onNewSession).toHaveBeenCalledTimes(1))
  })

  it('does not show New session when the control is not provided', async () => {
    render(<Floor sessionId="sess_ui" goPlatforms={() => {}} />)
    expect(await screen.findByRole('heading', { name: 'Factory Floor' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'New session' })).not.toBeInTheDocument()
    expect(screen.queryByTestId('floor-factory-cli-status')).not.toBeInTheDocument()
  })

  it('shows Coding agent stopped — not finished Download — when TESTER is red', async () => {
    watchBuildMock.mockImplementation(async (_sid: string, onProgress: (s: object) => void) => {
      onProgress({
        state: 'failed',
        cycle: 'pilot',
        outcome: 'FAILED_BUDGET_SPENT',
        pilot_ready: false,
        detail:
          'rework budget of 3 exhausted; TESTER gate still failing: PRODUCT (pilot-marked suite): suite is red',
      })
    })
    getMock.mockResolvedValue({
      blueprint: LLM_BLUEPRINT,
      blueprint_approved: true,
      generation: { engine: 'runner', product_id: 'residential-lettings', triggered_by: 'chat_llm' },
    })
    const onNewSession = vi.fn()
    render(
      <Floor sessionId="sess_red" goPlatforms={() => {}} onNewSession={onNewSession} />,
    )
    expect(await screen.findByRole('heading', { name: 'Coding agent stopped' })).toBeInTheDocument()
    expect(screen.getByTestId('floor-failed-pill')).toHaveTextContent('Pilot suite failed')
    expect(screen.getByText(/rework budget of 3 exhausted/)).toBeInTheDocument()
    expect(screen.queryByText(/taken over the floor/i)).not.toBeInTheDocument()
    expect(screen.queryByText('coding agent')).not.toBeInTheDocument()
    expect(screen.queryByText('chat LLM')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Download platform export (.zip)' })).not.toBeInTheDocument()
    const failedExport = screen.getByRole('button', { name: 'Export (.zip) — pilot suite failed' })
    expect(failedExport).toBeDisabled()
    expect(failedExport).toHaveAttribute('disabled')
    expect(failedExport).toHaveClass('ghost')
    expect(screen.queryByRole('heading', { name: 'Coding agent finished' })).not.toBeInTheDocument()
    const startNew = screen.getByRole('button', { name: 'Start a new product' })
    expect(startNew).toBeEnabled()
    fireEvent.click(startNew)
    await waitFor(() => expect(onNewSession).toHaveBeenCalledTimes(1))
  })

  it('names missing Kimi Code CLI credentials from /health on the Floor', async () => {
    getHealthMock.mockResolvedValue({
      factory_code_cli: {
        available: true,
        credentials_file_present: false,
        blocker: 'FACTORY_CODE_CLI_CREDENTIALS_MISSING',
      },
    })
    render(<Floor sessionId="sess_creds" goPlatforms={() => {}} />)
    const banner = await screen.findByTestId('floor-factory-cli-status')
    expect(banner).toHaveTextContent('Kimi Code CLI credentials missing')
    expect(banner).toHaveTextContent('FACTORY_CODE_CLI_CREDENTIALS_MISSING')
    expect(banner).toHaveTextContent('KIMI_CODE_API_KEY')
    expect(banner).toHaveTextContent('config.toml')
    expect(screen.queryByTestId('floor-factory-cli-status')).toHaveTextContent(/Kimi Code CLI credentials/)
  })

  it('names missing Kimi Code CLI default_model from /health on the Floor', async () => {
    getHealthMock.mockResolvedValue({
      factory_code_cli: {
        available: true,
        credentials_file_present: true,
        default_model_configured: false,
        blocker: 'FACTORY_CODE_CLI_NO_MODEL',
      },
    })
    render(<Floor sessionId="sess_no_model" goPlatforms={() => {}} />)
    const banner = await screen.findByTestId('floor-factory-cli-status')
    expect(banner).toHaveTextContent('Kimi Code CLI has no model')
    expect(banner).toHaveTextContent('FACTORY_CODE_CLI_NO_MODEL')
    expect(banner).toHaveTextContent('default_model')
    expect(banner).toHaveTextContent('KIMI_CODE_API_KEY')
    expect(screen.queryByText(/CODING AGENT HAS TAKEN OVER/i)).not.toBeInTheDocument()
  })

  it('does not offer a ready Download when the coding agent stalled', async () => {
    watchBuildMock.mockImplementation(async (_sid: string, onProgress: (s: object) => void) => {
      onProgress({
        state: 'stalled',
        detail: 'no build activity for 31 min — the build process is gone (restart or redeploy); generate again',
        pilot_ready: false,
      })
    })
    getMock.mockResolvedValue({
      blueprint: LLM_BLUEPRINT,
      blueprint_approved: true,
      generation: { engine: 'runner', product_id: 'veterinary-care', triggered_by: 'chat_llm' },
    })
    render(<Floor sessionId="sess_stall" goPlatforms={() => {}} />)
    expect(await screen.findByRole('heading', { name: 'Coding agent stalled' })).toBeInTheDocument()
    expect(screen.getByTestId('floor-stalled-pill')).toHaveTextContent('Build stalled')
    expect(
      screen.queryByRole('button', { name: 'Download platform export (.zip)' }),
    ).not.toBeInTheDocument()
    const exportBtn = screen.getByRole('button', { name: 'Export (.zip) — build stalled' })
    expect(exportBtn).toBeDisabled()
    expect(exportBtn).toHaveClass('ghost')
  })

  it('stops the takeover chrome when the ledger is unreadable', async () => {
    watchBuildMock.mockImplementation(async (_sid: string, onProgress: (s: object) => void) => {
      onProgress({
        state: 'unknown',
        detail: "LEDGER_UNREADABLE: build_ledger.jsonl:4561 is not a readable ledger event: 'seq'",
        pilot_ready: false,
      })
    })
    getMock.mockResolvedValue({
      blueprint: LLM_BLUEPRINT,
      blueprint_approved: true,
      generation: { engine: 'runner', product_id: 'veterinary-care', triggered_by: 'chat_llm' },
    })
    render(<Floor sessionId="sess_ledger" goPlatforms={() => {}} />)
    expect(await screen.findByRole('heading', { name: 'Coding agent stopped' })).toBeInTheDocument()
    expect(screen.queryByText(/Writing your platform/)).not.toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'Coding agent has taken over' })).not.toBeInTheDocument()
    const failedExport = screen.getByRole('button', { name: 'Export (.zip) — pilot suite failed' })
    expect(failedExport).toBeDisabled()
  })

  it('keeps coding chrome after Approve while generation SSE and status poll are pending', async () => {
    let releaseApprove: (() => void) | undefined
    const approveGate = new Promise<void>((resolve) => {
      releaseApprove = resolve
    })
    chatStreamMock.mockImplementation(
      async (_sid: string, message: string, onEvent: (ev: { event: string; data: unknown }) => void) => {
        if (message === 'approve') {
          await approveGate
          onEvent({
            event: 'generation',
            data: {
              summary: 'Build started for residential-lettings.',
              triggered_by: 'chat_llm',
              generation: {
                engine: 'runner',
                product_id: 'residential-lettings',
                triggered_by: 'chat_llm',
              },
            },
          })
          return
        }
        onEvent({
          event: 'blueprint',
          data: {
            summary: 'Drafted from the golden residential-lettings blueprint.',
            blueprint: {
              product_name: 'Residential Lettings Platform',
              vertical: 'residential_lettings',
              summary: 'Factory golden for a residential-lettings platform.',
              drafting_mode: 'golden_lettings',
              capabilities: [
                { id: 'viewing_management', description: 'Record a viewing', strategy_hint: 'COMPOSE' },
              ],
            },
          },
        })
      },
    )
    watchBuildMock.mockImplementation(async () => new Promise(() => {}))
    getMock.mockResolvedValue({
      blueprint: {
        product_name: 'Residential Lettings Platform',
        vertical: 'residential_lettings',
        drafting_mode: 'golden_lettings',
        capabilities: [
          { id: 'viewing_management', description: 'Record a viewing', strategy_hint: 'COMPOSE' },
        ],
      },
      blueprint_approved: false,
    })
    render(<Floor sessionId="sess_hold" goPlatforms={() => {}} />)
    fireEvent.click(await screen.findByRole('button', { name: 'Approve & build' }))
    expect(await screen.findByTestId('floor-coder-takeover')).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Coding agent has taken over' })).toBeInTheDocument()
    expect(screen.queryByText(/Finished —/)).not.toBeInTheDocument()
    expect(screen.queryByText(/Download ready/)).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Download platform export (.zip)' })).not.toBeInTheDocument()
    releaseApprove?.()
    expect(await screen.findByText('coding agent')).toBeInTheDocument()
    expect(screen.getByTestId('floor-coder-takeover')).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'Coding agent finished' })).not.toBeInTheDocument()
  })

  it('does not offer Floor download while the coding agent is still writing', async () => {
    chatStreamMock.mockImplementation(async (_sid: string, message: string, onEvent: (ev: { event: string; data: unknown }) => void) => {
      if (message === 'approve') {
        onEvent({
          event: 'generation',
          data: {
            summary: 'Build started.',
            triggered_by: 'chat_llm',
            generation: { engine: 'runner', product_id: 'vineyard', triggered_by: 'chat_llm' },
          },
        })
        return
      }
      onEvent({
        event: 'blueprint',
        data: { summary: 'Blueprint drafted.', blueprint: LLM_BLUEPRINT },
      })
    })
    render(<Floor sessionId="sess_ui" goPlatforms={() => {}} />)
    fireEvent.change(screen.getByPlaceholderText(/Try:/), {
      target: { value: 'Build me a vineyard management platform' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Send' }))
    await waitFor(() =>
      expect(screen.getByRole('button', { name: 'Approve & build' })).toBeEnabled(),
    )
    fireEvent.click(screen.getByRole('button', { name: 'Approve & build' }))
    expect(await screen.findByRole('heading', { name: 'Coding agent has taken over' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Download platform export (.zip)' })).not.toBeInTheDocument()
  })

  it('does not gold-label Store-green or founding while COLLECTOR is still running', async () => {
    watchBuildMock.mockImplementation(async (_sid: string, onProgress: (s: object) => void) => {
      onProgress({
        state: 'building',
        cycle: 'code',
        pilot_ready: false,
        current_phase: { id: 'COLLECTOR', label: 'Binding surveyor' },
        phase_index: 1,
        phase_total: 5,
        last_event: 'collecting capabilities',
        level_grade: {
          level: 'FOUNDING_CUSTOMER_READY',
          founding_customer_ready: true,
          three_gate: { CODE: 'PASS', PRODUCT: 'PASS', STORE: 'PASS' },
        },
      })
    })
    getMock.mockResolvedValue({
      blueprint: LLM_BLUEPRINT,
      blueprint_approved: true,
      generation: { engine: 'runner', product_id: 'property-management', triggered_by: 'chat_llm' },
    })
    render(<Floor sessionId="sess_northbridge" goPlatforms={() => {}} />)
    expect(await screen.findByRole('heading', { name: 'Coding agent has taken over' })).toBeInTheDocument()
    // Watch snapshot paints one tick after takeover chrome; wait for it
    // the same way the WRITER takeover test waits for "Writing your platform".
    expect(await screen.findByText(/Writing your platform/)).toBeInTheDocument()
    expect(screen.getByText('COLLECTOR 1/5')).toBeInTheDocument()
    expect(screen.queryByText(/Founding-customer-ready/)).not.toBeInTheDocument()
    expect(screen.queryByText(/Store-green/)).not.toBeInTheDocument()
    expect(screen.queryByTestId('floor-pilot-ready-pill')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Download platform export (.zip)' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Download code-cycle prototype (.zip)' })).not.toBeInTheDocument()
  })

  it('clears coder takeover when a new blueprint is drafted after a runner', async () => {
    watchBuildMock.mockImplementation(async (_sid: string, onProgress: (s: object) => void) => {
      onProgress({
        state: 'succeeded',
        pilot_ready: true,
        cycle: 'pilot',
        authorship: { artifacts: 19, agent_written: 13, templated: 6 },
      })
    })
    getMock.mockResolvedValue({
      blueprint: LLM_BLUEPRINT,
      blueprint_approved: true,
      generation: { engine: 'runner', product_id: 'vineyard', triggered_by: 'chat_llm' },
    })
    chatStreamMock.mockImplementation(async (_sid: string, _msg: string, onEvent: (ev: { event: string; data: unknown }) => void) => {
      onEvent({
        event: 'blueprint',
        data: { summary: 'Blueprint drafted: new tasting room.', blueprint: LLM_BLUEPRINT },
      })
    })
    render(<Floor sessionId="sess_redraft" goPlatforms={() => {}} />)
    expect(await screen.findByRole('heading', { name: 'Coding agent finished' })).toBeInTheDocument()
    expect(screen.getByPlaceholderText(/Try:/)).toBeEnabled()
    fireEvent.change(screen.getByPlaceholderText(/Try:/), {
      target: { value: 'build me a tasting room for a family winery' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Send' }))
    expect(await screen.findByText('Vineyard Platform')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Approve & build' })).toBeEnabled()
    expect(screen.queryByRole('status')).not.toBeInTheDocument()
    expect(screen.getByPlaceholderText(/Try:/)).toBeEnabled()
  })

  it('does not wipe a streamed Floor reply when a leftover chain event arrives', async () => {
    chatStreamMock.mockImplementation(async (_sid: string, _msg: string, onEvent: (ev: { event: string; data: unknown }) => void) => {
      onEvent({ event: 'delta', data: 'Drafting a tasting-room platform for the winery. ' })
      onEvent({ event: 'chain', data: { chain: ['audit'] } })
    })
    render(<Floor sessionId="sess_ui" goPlatforms={() => {}} />)
    fireEvent.change(screen.getByPlaceholderText(/Try:/), {
      target: { value: 'build me a tasting room for a family winery' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Send' }))
    expect(await screen.findByText(/Drafting a tasting-room platform/)).toBeInTheDocument()
    expect(screen.queryByText(/That sounds like kit configuration/)).not.toBeInTheDocument()
  })

  it('renders a one-line already-signed-in notice when provided', async () => {
    render(
      <Floor sessionId="sess_ui" goPlatforms={() => {}} notice="Already signed in." />,
    )
    expect(await screen.findByRole('heading', { name: 'Factory Floor' })).toBeInTheDocument()
    expect(screen.getByText('Already signed in.')).toBeInTheDocument()
    expect(screen.getByPlaceholderText(/Try:/)).toBeEnabled()
  })

  it('hard-disables Send when Factory access is paused — typing does not unlock it', async () => {
    render(<Floor sessionId="sess_paused" goPlatforms={() => {}} accessPaused />)
    const send = await screen.findByRole('button', { name: 'Send' })
    expect(send).toBeDisabled()
    const composer = screen.getByPlaceholderText('Factory access is paused')
    expect(composer).toBeDisabled()
    fireEvent.change(composer, { target: { value: 'Invoice Tracker for a small studio' } })
    expect(send).toBeDisabled()
    fireEvent.click(send)
    expect(chatStreamMock).not.toHaveBeenCalled()
    expect(screen.getByText('Factory access is paused.')).toBeInTheDocument()
  })

  it('does not offer Approve & build on a hydrated draft when access is paused', async () => {
    getMock.mockResolvedValue({
      blueprint: LLM_BLUEPRINT,
      blueprint_approved: false,
    })
    render(<Floor sessionId="sess_paused_draft" goPlatforms={() => {}} accessPaused />)
    expect(await screen.findByText('Vineyard Platform')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Approve & build/ })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Send' })).toBeDisabled()
    expect(screen.getByText('Factory access is paused.')).toBeInTheDocument()
  })

  it('streams the coder session log and Pause/Stop call coder-control', async () => {
    getMock.mockResolvedValue({
      blueprint: LLM_BLUEPRINT,
      blueprint_approved: true,
      generation: { engine: 'runner', product_id: 'vineyard', triggered_by: 'chat_llm' },
    })
    render(<Floor sessionId="sess_monitor" goPlatforms={() => {}} />)
    expect(await screen.findByTestId('floor-coder-log')).toBeInTheDocument()
    expect(screen.getByTestId('floor-coder-log')).toHaveTextContent('STEP 0 inventory ok')
    expect(screen.getByTestId('floor-coder-pause')).toBeEnabled()
    expect(screen.getByTestId('floor-coder-stop')).toBeEnabled()
    fireEvent.click(screen.getByTestId('floor-coder-pause'))
    await waitFor(() =>
      expect(coderControlMock).toHaveBeenCalledWith('sess_monitor', 'pause'),
    )
    fireEvent.click(screen.getByTestId('floor-coder-stop'))
    await waitFor(() =>
      expect(coderControlMock).toHaveBeenCalledWith('sess_monitor', 'stop'),
    )
  })
})
