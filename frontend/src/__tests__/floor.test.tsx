/**
 * Factory Floor contract: the architect LLM drafts, the user approves,
 * the coding agent manufactures the platform. Pin the SSE cards the
 * live client actually renders — a template fallback must not look like
 * a working architect, and a runner build must name the coding agent.
 */
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { Floor } from '../App'

const chatStreamMock = vi.fn()

vi.mock('../api/factory', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/factory')>()
  return {
    ...actual,
    chatStream: (...args: unknown[]) => chatStreamMock(...args),
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
              'Build started for vineyard: the coding agent is writing 2 capability(ies) against the real block contracts.',
            generation: { engine: 'runner', product_id: 'vineyard' },
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
    expect(screen.getByText(/coding agent is writing/)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Open Your Platforms' }))
    expect(goPlatforms).toHaveBeenCalled()
    expect(chatStreamMock).toHaveBeenCalledWith('sess_ui', 'approve', expect.any(Function))
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
