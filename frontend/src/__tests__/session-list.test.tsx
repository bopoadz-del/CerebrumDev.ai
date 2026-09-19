import { describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import { SessionList } from '../App'

// Owner: "new session deletes the ongoing one, the sessions should be saved on
// the left panel, not disappear". They were always kept server-side; the rail
// simply never rendered them.
describe('SessionList', () => {
  const items = [
    { session_id: 'sess_new0000000000', title: '', stage: 'empty' as const },
    { session_id: 'sess_bakery00000000', title: 'Bakery Chain Operations', stage: 'build' as const },
    { session_id: 'sess_vet00000000000', title: 'i run a vet clinic', stage: 'talking' as const },
  ]

  it('lists every session by a title a person can recognise', () => {
    render(<SessionList items={items} currentId="sess_new0000000000" onOpen={() => {}} />)
    expect(screen.getByRole('region', { name: 'Your sessions' })).toBeInTheDocument()
    expect(screen.getByText('Bakery Chain Operations')).toBeInTheDocument()
    expect(screen.getByText('i run a vet clinic')).toBeInTheDocument()
    expect(screen.getByText('New session')).toBeInTheDocument()
  })

  it('opens the session that was left behind', () => {
    const onOpen = vi.fn()
    render(<SessionList items={items} currentId="sess_new0000000000" onOpen={onOpen} />)
    fireEvent.click(screen.getByText('Bakery Chain Operations'))
    expect(onOpen).toHaveBeenCalledWith('sess_bakery00000000')
  })

  it('marks the session you are in, and only that one', () => {
    render(<SessionList items={items} currentId="sess_bakery00000000" onOpen={() => {}} />)
    const current = screen.getAllByRole('button').filter((b) => b.getAttribute('aria-current') === 'page')
    expect(current).toHaveLength(1)
    expect(current[0]).toHaveTextContent('Bakery Chain Operations')
  })

  it('shows the stage so a running build is findable', () => {
    render(<SessionList items={items} currentId={null} onOpen={() => {}} />)
    expect(screen.getByText('build')).toBeInTheDocument()
    expect(screen.getByText('talking')).toBeInTheDocument()
  })

  it('renders nothing when there are no sessions', () => {
    const { container } = render(<SessionList items={[]} currentId={null} onOpen={() => {}} />)
    expect(container).toBeEmptyDOMElement()
  })
})
