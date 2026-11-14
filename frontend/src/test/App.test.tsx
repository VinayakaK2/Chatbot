import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent, act } from '@testing-library/react'
import App from '../App'

// Mock apiClient methods
vi.mock('../services/api-client', () => ({
  apiClient: {
    getHealth: vi.fn().mockResolvedValue({
      status: 'healthy',
      version: '0.1.0',
      model_id: 'Qwen/Qwen2.5-Coder-14B-Instruct-AWQ',
      active_tasks: 0,
    }),
    getReadiness: vi.fn().mockResolvedValue({
      ready: true,
      model_id: 'Qwen/Qwen2.5-Coder-14B-Instruct-AWQ',
      endpoint: 'http://127.0.0.1:8000',
    }),
    submitTask: vi.fn().mockResolvedValue({
      task_id: 'task-mock-123',
      prompt: 'Hello agent',
      status: 'executing',
      response_text: null,
      finish_reason: null,
      model: 'Qwen/Qwen2.5-Coder-14B-Instruct-AWQ',
      usage: null,
      error: null,
      created_at: new Date().toISOString(),
      completed_at: null,
    }),
    subscribeToTaskEvents: vi.fn().mockReturnValue(() => {}),
    cancelTask: vi.fn().mockResolvedValue({ status: 'cancelled' }),
    approveToolCall: vi.fn().mockResolvedValue({ status: 'approved' }),
    rejectToolCall: vi.fn().mockResolvedValue({ status: 'rejected' }),
  },
}))

describe('App Component', () => {
  it('renders application shell with sidebar, workspace, and composer', async () => {
    await act(async () => {
      render(<App />)
    })

    const brandTitles = screen.getAllByText('Agent Runtime')
    expect(brandTitles.length).toBeGreaterThan(0)
    expect(screen.getByRole('navigation', { name: /Main sidebar/i })).toBeDefined()
    expect(screen.getByRole('main')).toBeDefined()
    expect(screen.getByRole('textbox', { name: /Task prompt input/i })).toBeDefined()
    expect(screen.getByRole('button', { name: /Command Palette/i })).toBeDefined()
  })

  it('toggles command palette with button click and closes on Escape', async () => {
    await act(async () => {
      render(<App />)
    })

    const cmdBtn = screen.getByRole('button', { name: /Command Palette/i })
    await act(async () => {
      fireEvent.click(cmdBtn)
    })

    expect(screen.getByPlaceholderText(/Type a command or search/i)).toBeDefined()

    // Press Escape to dismiss
    await act(async () => {
      fireEvent.keyDown(window, { key: 'Escape' })
    })
    expect(screen.queryByPlaceholderText(/Type a command or search/i)).toBeNull()
  })

  it('toggles sidebar collapse with Ctrl+B', async () => {
    let containerRef: HTMLElement
    await act(async () => {
      const { container } = render(<App />)
      containerRef = container
    })

    expect(containerRef!.querySelector('.sidebar-container')).toBeDefined()

    // Press Ctrl+B to collapse
    await act(async () => {
      fireEvent.keyDown(window, { key: 'b', ctrlKey: true })
    })
    expect(containerRef!.querySelector('.sidebar-container')).toBeNull()

    // Press Ctrl+B to expand
    await act(async () => {
      fireEvent.keyDown(window, { key: 'b', ctrlKey: true })
    })
    expect(containerRef!.querySelector('.sidebar-container')).toBeDefined()
  })
})
