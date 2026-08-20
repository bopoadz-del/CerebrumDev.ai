/**
 * Factory Floor contract: the architect LLM drafts, the user approves
 * the feature list, the coding agent takes over and manufactures it.
 */
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { Floor } from '../App'

const chatStreamMock = vi.fn()
const awaitBuildMock = vi.fn()

vi.mock('../api/factory', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/factory')>()
  return {
    ...actual,
    chatStream: (...args: unknown[]) => chatStreamMock(...args),
    awaitBuild: (...args: unknown[]) => awaitBuildMock(...args),
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
    awaitBuildMock.mockImplementation(async (_sid: string, onProgress?: (s: object) => void) => {
      const status = {
        state: 'building',
        activity: 'writing handlers',
        phases_done: 2,
        phases_total: 5,
      }
      onProgress?.(status)
      return status
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
    expect(screen.getByText('WRITER')).toBeInTheDocument()
    expect(screen.getByText('TESTER')).toBeInTheDocument()
    expect(await screen.findByText(/Writing your platform/)).toBeInTheDocument()
    expect(screen.getByPlaceholderText(/coding agent has taken over/i)).toBeDisabled()
    fireEvent.click(screen.getByRole('button', { name: 'Open Your Platforms' }))
    expect(goPlatforms).toHaveBeenCalled()
    expect(chatStreamMock).toHaveBeenCalledWith('sess_ui', 'approve', expect.any(Function))
    expect(awaitBuildMock).toHaveBeenCalled()
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
})
