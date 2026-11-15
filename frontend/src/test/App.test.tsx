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
    getFileContent: vi.fn().mockResolvedValue({
      path: 'core/agent_loop.py',
      name: 'agent_loop.py',
      content: 'def main(): pass',
      language: 'python',
      is_markdown: false,
      size_bytes: 16,
    }),
    getGitDiff: vi.fn().mockResolvedValue({
      files_changed_count: 0,
      insertions: 0,
      deletions: 0,
      files: [],
    }),
    getAvailableDocuments: vi.fn().mockResolvedValue([]),
    getCurrentWorkspace: vi.fn().mockResolvedValue(null),
    listWorkspaces: vi.fn().mockResolvedValue([]),
    selectWorkspace: vi.fn().mockResolvedValue({ status: 'ok', root_path: 'h:/chatbot' }),
  },
}))

describe('App Component', () => {
  it('renders application shell with sidebar and no-project empty state initially', async () => {
    await act(async () => {
      render(<App />)
    })

    const brandTitles = screen.getAllByText('Agent Runtime')
    expect(brandTitles.length).toBeGreaterThan(0)
    expect(screen.getByRole('navigation', { name: /Main sidebar/i })).toBeDefined()
    expect(screen.getByRole('main')).toBeDefined()
    // When no project is open, hero states "Select a Project Folder"
    expect(screen.getByText('Select a Project Folder')).toBeDefined()
    const openBtns = screen.getAllByRole('button', { name: /Open Project Folder/i })
    expect(openBtns.length).toBeGreaterThan(0)
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

  it('toggles sidebar collapse with Ctrl+B smoothly without unmounting', async () => {
    let containerRef: HTMLElement
    await act(async () => {
      const { container } = render(<App />)
      containerRef = container
    })

    const sidebar = containerRef!.querySelector('.sidebar-container')
    expect(sidebar).not.toBeNull()
    expect(sidebar?.classList.contains('is-collapsed')).toBe(false)

    // Press Ctrl+B to collapse
    await act(async () => {
      fireEvent.keyDown(window, { key: 'b', ctrlKey: true })
    })
    expect(sidebar?.classList.contains('is-collapsed')).toBe(true)

    // Press Ctrl+B to expand
    await act(async () => {
      fireEvent.keyDown(window, { key: 'b', ctrlKey: true })
    })
    expect(sidebar?.classList.contains('is-collapsed')).toBe(false)
  })
})
