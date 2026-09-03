/**
 * Your Platforms contract: a runner build is a background coding-agent job.
 * The page must show that work, name who wrote the artifacts, and refuse
 * to download a failed or in-flight tree.
 */
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { Platforms } from '../App'

const getMock = vi.fn()
const buildStatusMock = vi.fn()
const generateMock = vi.fn()
const awaitBuildMock = vi.fn()
const watchBuildMock = vi.fn()
const downloadMock = vi.fn()

vi.mock('../api/factory', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/factory')>()
  return {
    ...actual,
    product: {
      ...actual.product,
      get: (...args: unknown[]) => getMock(...args),
      buildStatus: (...args: unknown[]) => buildStatusMock(...args),
      generate: (...args: unknown[]) => generateMock(...args),
    },
    awaitBuild: (...args: unknown[]) => awaitBuildMock(...args),
    watchBuildStatus: (...args: unknown[]) => watchBuildMock(...args),
    downloadProductPackage: (...args: unknown[]) => downloadMock(...args),
  }
})

const GENERATION = {
  product_id: 'vineyard',
  engine: 'runner',
  inputs_hash: 'abc123',
  output_dir: '/tmp/vineyard',
}

describe('Your Platforms — coding-agent build', () => {
  beforeEach(() => {
    getMock.mockReset()
    buildStatusMock.mockReset()
    generateMock.mockReset()
    awaitBuildMock.mockReset()
    watchBuildMock.mockReset()
    downloadMock.mockReset()
    buildStatusMock.mockResolvedValue({ ok: true, build: { state: 'not_started' } })
    watchBuildMock.mockImplementation(async () => {})
  })

  it('shows a loading skeleton — never empty-state — while the first fetch is in flight', async () => {
    let resolveGet: (v: object) => void = () => {}
    getMock.mockImplementation(
      () =>
        new Promise((resolve) => {
          resolveGet = resolve as (v: object) => void
        }),
    )
    render(<Platforms sessionId="sess_ui" />)
    expect(screen.getByTestId('loading-skeleton')).toBeInTheDocument()
    expect(screen.queryByText('No platform built yet')).not.toBeInTheDocument()
    resolveGet({ blueprint: { product_name: 'Draft', vertical: 'winery' } })
    expect(await screen.findByText('No platform built yet')).toBeInTheDocument()
    expect(screen.queryByTestId('loading-skeleton')).not.toBeInTheDocument()
    expect(watchBuildMock).not.toHaveBeenCalled()
  })

  it('empty state when nothing has been generated', async () => {
    getMock.mockResolvedValue({ blueprint: { product_name: 'Draft', vertical: 'winery' } })
    render(<Platforms sessionId="sess_ui" />)
    expect(await screen.findByText('No platform built yet')).toBeInTheDocument()
    expect(watchBuildMock).not.toHaveBeenCalled()
  })

  it('auto-polls and shows the coding agent at work', async () => {
    getMock.mockResolvedValue({
      generation: GENERATION,
      blueprint: { product_name: 'Vineyard Platform', vertical: 'winery' },
    })
    watchBuildMock.mockImplementation(async (_sid: string, onProgress: (s: object) => void) => {
      const building = {
        state: 'building',
        phases_done: 2,
        phases_total: 5,
        current_phase: { id: 'WRITER', label: 'Platform manufacturer' },
        phase_index: 3,
        phase_total: 5,
        activity: 'wrote handler inventory_management',
        last_event: 'wrote handler inventory_management',
        phase_progress: { done: 2, total: 4, fraction: 0.5, stage: 'handlers' },
        last_event_age_s: 20,
        stale: false,
        completed: ['COLLECTOR', 'CLONER'],
      }
      onProgress(building)
    })
    render(<Platforms sessionId="sess_ui" />)
    expect(await screen.findByText('vineyard')).toBeInTheDocument()
    expect(screen.getByText('runner')).toBeInTheDocument()
    expect(await screen.findByText(/Coding agent at work — WRITER 3\/5/)).toBeInTheDocument()
    expect(screen.getByText(/2\/4 handlers/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Building…' })).toBeDisabled()
  })

  it('refresh shows Refreshing… then re-fetches product + build-status', async () => {
    getMock.mockResolvedValue({
      generation: GENERATION,
      blueprint: { product_name: 'Dealership Platform', vertical: 'auto' },
    })
    const atTwo = {
      state: 'building',
      phases_done: 2,
      phases_total: 5,
      activity: 'WRITER handler 2/4',
      completed: ['cloner'],
    }
    const atThree = {
      state: 'building',
      phases_done: 3,
      phases_total: 5,
      activity: 'TESTER gate',
      completed: ['cloner', 'writer'],
    }
    // Refresh is the only buildStatus caller; watcher seeds the first snapshot.
    buildStatusMock.mockResolvedValueOnce({ ok: true, build: atThree })
    watchBuildMock.mockImplementation(async (_sid: string, onProgress: (s: object) => void) => {
      onProgress(atTwo)
      return new Promise(() => {})
    })

    render(<Platforms sessionId="sess_ui" />)
    expect(await screen.findByText('runner')).toBeInTheDocument()
    expect(
      await screen.findByText(/Coding agent at work — 3\/5 · last: WRITER handler 2\/4 · still working/),
    ).toBeInTheDocument()
    expect(getMock).toHaveBeenCalledTimes(1)
    expect(buildStatusMock).not.toHaveBeenCalled()

    fireEvent.click(screen.getByRole('button', { name: 'Refresh' }))
    expect(screen.getByRole('button', { name: 'Refreshing…' })).toBeDisabled()

    await waitFor(() => {
      expect(getMock).toHaveBeenCalledTimes(2)
      expect(buildStatusMock).toHaveBeenCalledTimes(1)
    })
    expect(getMock).toHaveBeenNthCalledWith(2, 'sess_ui')
    expect(buildStatusMock).toHaveBeenNthCalledWith(1, 'sess_ui')
    expect(
      await screen.findByText(/Coding agent at work — 4\/5 · last: TESTER gate · still working/),
    ).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Building…' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Refresh' })).toBeEnabled()
    expect(generateMock).not.toHaveBeenCalled()
  })

  it('names how many artifacts the coding agent wrote', async () => {
    getMock.mockResolvedValue({
      generation: GENERATION,
      blueprint: { product_name: 'Vineyard Platform', vertical: 'winery' },
    })
    watchBuildMock.mockImplementation(async (_sid: string, onProgress: (s: object) => void) => {
      onProgress({
        state: 'succeeded',
        authorship: { artifacts: 10, agent_written: 6, templated: 4 },
      })
    })
    render(<Platforms sessionId="sess_ui" />)
    expect(await screen.findByText('Finished — 6 artifacts; 4 templated')).toBeInTheDocument()
    expect(screen.queryByText(/6 of 10/)).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Download platform export (.zip)' })).toBeEnabled()
  })

  it('says so when the coding agent wrote nothing', async () => {
    getMock.mockResolvedValue({ generation: GENERATION, blueprint: { product_name: 'Vineyard Platform' } })
    watchBuildMock.mockImplementation(async (_sid: string, onProgress: (s: object) => void) => {
      onProgress({
        state: 'succeeded',
        authorship: {
          artifacts: 8,
          agent_written: 0,
          templated: 8,
          coder_failures: { audit: 'Factory architect requires KIMI_API_KEY' },
        },
      })
    })
    render(<Platforms sessionId="sess_ui" />)
    expect(
      await screen.findByText(/Coding agent wrote 0 artifacts — this platform is templated/),
    ).toBeInTheDocument()
    expect(screen.getByText(/Coder skip: Factory architect requires KIMI_API_KEY/)).toBeInTheDocument()
  })

  it('downloads only after the build succeeds', async () => {
    getMock.mockResolvedValue({ generation: GENERATION, blueprint: { product_name: 'Vineyard Platform' } })
    watchBuildMock.mockImplementation(async (_sid: string, onProgress: (s: object) => void) => {
      onProgress({
        state: 'succeeded',
        authorship: { artifacts: 3, agent_written: 3, templated: 0 },
      })
    })
    downloadMock.mockResolvedValue(undefined)
    render(<Platforms sessionId="sess_ui" />)
    fireEvent.click(await screen.findByRole('button', { name: 'Download platform export (.zip)' }))
    await waitFor(() => expect(downloadMock).toHaveBeenCalledWith('sess_ui'))
  })

  it('does not download a failed coding-agent build', async () => {
    getMock.mockResolvedValue({ generation: GENERATION, blueprint: { product_name: 'Vineyard Platform' } })
    watchBuildMock.mockImplementation(async (_sid: string, onProgress: (s: object) => void) => {
      onProgress({
        state: 'failed',
        detail: 'TESTER gate red',
      })
    })
    awaitBuildMock.mockResolvedValue({
      state: 'failed',
      detail: 'TESTER gate red',
    })
    render(<Platforms sessionId="sess_ui" />)
    fireEvent.click(await screen.findByRole('button', { name: 'Download platform export (.zip)' }))
    expect(await screen.findByText(/will not be shipped: TESTER gate red/)).toBeInTheDocument()
    expect(downloadMock).not.toHaveBeenCalled()
  })
})
