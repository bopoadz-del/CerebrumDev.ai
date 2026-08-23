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
    downloadMock.mockReset()
    buildStatusMock.mockResolvedValue({ ok: true, build: { state: 'not_started' } })
  })

  it('empty state when nothing has been generated', async () => {
    getMock.mockResolvedValue({ blueprint: { product_name: 'Draft', vertical: 'winery' } })
    render(<Platforms sessionId="sess_ui" />)
    expect(await screen.findByText('No platform built yet')).toBeInTheDocument()
    expect(awaitBuildMock).not.toHaveBeenCalled()
  })

  it('auto-polls and shows the coding agent at work', async () => {
    getMock.mockResolvedValue({
      generation: GENERATION,
      blueprint: { product_name: 'Vineyard Platform', vertical: 'winery' },
    })
    awaitBuildMock.mockImplementation(async (_sid: string, onProgress?: (s: object) => void) => {
      const building = {
        state: 'building',
        phases_done: 2,
        phases_total: 5,
        activity: 'WRITER handler 2/4',
        completed: ['cloner'],
      }
      onProgress?.(building)
      return building
    })
    render(<Platforms sessionId="sess_ui" />)
    expect(await screen.findByText('vineyard')).toBeInTheDocument()
    expect(screen.getByText('runner')).toBeInTheDocument()
    expect(await screen.findByText(/Coding agent at work — 2\/5 phases \(last: WRITER handler 2\/4\)/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Building…' })).toBeDisabled()
  })

  it('refresh re-fetches product + build-status while the runner is at 2/5', async () => {
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
    buildStatusMock
      .mockResolvedValueOnce({ ok: true, build: atTwo })
      .mockResolvedValueOnce({ ok: true, build: atThree })
    // Background poll stays in-flight; Refresh must not wait on it.
    awaitBuildMock.mockImplementation(() => new Promise(() => {}))

    render(<Platforms sessionId="sess_ui" />)
    expect(await screen.findByText('runner')).toBeInTheDocument()
    expect(await screen.findByText(/Coding agent at work — 2\/5 phases \(last: WRITER handler 2\/4\)/)).toBeInTheDocument()
    expect(getMock).toHaveBeenCalledTimes(1)
    expect(buildStatusMock).toHaveBeenCalledTimes(1)

    fireEvent.click(screen.getByRole('button', { name: 'Refresh' }))

    await waitFor(() => {
      expect(getMock).toHaveBeenCalledTimes(2)
      expect(buildStatusMock).toHaveBeenCalledTimes(2)
    })
    expect(getMock).toHaveBeenNthCalledWith(2, 'sess_ui')
    expect(buildStatusMock).toHaveBeenNthCalledWith(2, 'sess_ui')
    expect(await screen.findByText(/Coding agent at work — 3\/5 phases \(last: TESTER gate\)/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Building…' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Refresh' })).toBeEnabled()
    expect(generateMock).not.toHaveBeenCalled()
  })

  it('names how many artifacts the coding agent wrote', async () => {
    getMock.mockResolvedValue({
      generation: GENERATION,
      blueprint: { product_name: 'Vineyard Platform', vertical: 'winery' },
    })
    awaitBuildMock.mockImplementation(async (_sid: string, onProgress?: (s: object) => void) => {
      const done = {
        state: 'succeeded',
        authorship: { artifacts: 10, agent_written: 6, templated: 4 },
      }
      onProgress?.(done)
      return done
    })
    render(<Platforms sessionId="sess_ui" />)
    expect(await screen.findByText('Coding agent wrote 6 of 10 artifacts.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Download platform export (.zip)' })).toBeEnabled()
  })

  it('says so when the coding agent wrote nothing', async () => {
    getMock.mockResolvedValue({ generation: GENERATION, blueprint: { product_name: 'Vineyard Platform' } })
    awaitBuildMock.mockResolvedValue({
      state: 'succeeded',
      authorship: {
        artifacts: 8,
        agent_written: 0,
        templated: 8,
        coder_failures: { audit: 'Factory architect requires KIMI_API_KEY' },
      },
    })
    render(<Platforms sessionId="sess_ui" />)
    expect(
      await screen.findByText(/Coding agent wrote 0 artifacts — this platform is templated/),
    ).toBeInTheDocument()
    expect(screen.getByText(/Coder skip: Factory architect requires KIMI_API_KEY/)).toBeInTheDocument()
  })

  it('downloads only after the build succeeds', async () => {
    getMock.mockResolvedValue({ generation: GENERATION, blueprint: { product_name: 'Vineyard Platform' } })
    awaitBuildMock.mockResolvedValue({
      state: 'succeeded',
      authorship: { artifacts: 3, agent_written: 3, templated: 0 },
    })
    downloadMock.mockResolvedValue(undefined)
    render(<Platforms sessionId="sess_ui" />)
    fireEvent.click(await screen.findByRole('button', { name: 'Download platform export (.zip)' }))
    await waitFor(() => expect(downloadMock).toHaveBeenCalledWith('sess_ui'))
  })

  it('does not download a failed coding-agent build', async () => {
    getMock.mockResolvedValue({ generation: GENERATION, blueprint: { product_name: 'Vineyard Platform' } })
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
