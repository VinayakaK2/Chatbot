import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { Sidebar } from '../components/layout/Sidebar'
import { MessageItem } from '../components/chat/MessageItem'
import type { Conversation, Message, Project } from '../types/api'

describe('ConversationRow & Status Indicators', () => {
  const mockProjects: Project[] = [
    {
      id: 'p-1',
      name: 'chatbot',
      path: 'h:/chatbot',
      created_at: new Date().toISOString(),
    },
  ]

  const mockConversations: Conversation[] = [
    {
      id: 'c-1',
      title: 'Active Task Investigation',
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
      project_id: 'p-1',
      pinned: false,
      archived: false,
      model: 'Qwen/Qwen2.5-Coder-14B-Instruct-AWQ',
      messages: [],
      is_running: true,
    },
    {
      id: 'c-2',
      title: 'Completed Unread Chat',
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
      project_id: 'p-1',
      pinned: true,
      archived: false,
      model: 'Qwen/Qwen2.5-Coder-14B-Instruct-AWQ',
      messages: [],
      unread: true,
    },
    {
      id: 'c-3',
      title: 'Clean Read Conversation',
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
      project_id: 'p-1',
      pinned: false,
      archived: false,
      model: 'Qwen/Qwen2.5-Coder-14B-Instruct-AWQ',
      messages: [],
    },
  ]

  it('renders status indicators for running and unread conversations nested under selected project', () => {
    const { container } = render(
      <Sidebar
        conversations={mockConversations}
        projects={mockProjects}
        selectedProjectId="p-1"
        activeConversationId="c-1"
        onSelectConversation={vi.fn()}
        onNewConversation={vi.fn()}
        onDeleteConversation={vi.fn()}
        onTogglePinConversation={vi.fn()}
        onOpenSettings={vi.fn()}
        isCollapsed={false}
        onToggleCollapse={vi.fn()}
      />
    )

    // Running task spinner
    const spinner = container.querySelector('.chat-spinner')
    expect(spinner).not.toBeNull()
    expect(spinner?.getAttribute('title')).toBe('Task running')

    // Unread dot
    const unreadDot = container.querySelector('.chat-unread-dot')
    expect(unreadDot).not.toBeNull()
    expect(unreadDot?.getAttribute('title')).toBe('Unread updates')
  })

  it('reveals pin and dropdown actions on conversation row', async () => {
    const onTogglePin = vi.fn()
    const onRename = vi.fn()
    const onDelete = vi.fn()

    render(
      <Sidebar
        conversations={mockConversations}
        projects={mockProjects}
        selectedProjectId="p-1"
        activeConversationId="c-3"
        onSelectConversation={vi.fn()}
        onNewConversation={vi.fn()}
        onRenameConversation={onRename}
        onDeleteConversation={onDelete}
        onTogglePinConversation={onTogglePin}
        onOpenSettings={vi.fn()}
        isCollapsed={false}
        onToggleCollapse={vi.fn()}
      />
    )

    const pinButtons = screen.getAllByRole('button', { name: /Pin conversation|Unpin conversation/i })
    expect(pinButtons.length).toBeGreaterThan(0)

    // Click pin button
    fireEvent.click(pinButtons[0])
    expect(onTogglePin).toHaveBeenCalled()

    // More options dropdown
    const moreButtons = screen.getAllByRole('button', { name: /More options/i })
    expect(moreButtons.length).toBeGreaterThan(0)
    fireEvent.click(moreButtons[0])

    expect(screen.getByRole('menuitem', { name: /Rename/i })).toBeDefined()
    expect(screen.getByRole('menuitem', { name: /Delete/i })).toBeDefined()
  })

  it('renders collapse control in sidebar header without X icon', () => {
    const onToggleCollapse = vi.fn()
    render(
      <Sidebar
        conversations={mockConversations}
        projects={mockProjects}
        selectedProjectId="p-1"
        activeConversationId="c-1"
        onSelectConversation={vi.fn()}
        onNewConversation={vi.fn()}
        onDeleteConversation={vi.fn()}
        onTogglePinConversation={vi.fn()}
        onOpenSettings={vi.fn()}
        isCollapsed={false}
        onToggleCollapse={onToggleCollapse}
      />
    )

    const collapseBtn = screen.getByRole('button', { name: /Collapse sidebar/i })
    expect(collapseBtn).toBeDefined()
    fireEvent.click(collapseBtn)
    expect(onToggleCollapse).toHaveBeenCalledTimes(1)
  })

  it('sidebar remains mounted when collapsed with smooth class and aria-hidden', () => {
    const { container } = render(
      <Sidebar
        conversations={mockConversations}
        projects={mockProjects}
        selectedProjectId="p-1"
        activeConversationId="c-1"
        onSelectConversation={vi.fn()}
        onNewConversation={vi.fn()}
        onDeleteConversation={vi.fn()}
        onTogglePinConversation={vi.fn()}
        onOpenSettings={vi.fn()}
        isCollapsed={true}
        onToggleCollapse={vi.fn()}
      />
    )

    const aside = container.querySelector('.sidebar-container')
    expect(aside).not.toBeNull()
    expect(aside?.classList.contains('is-collapsed')).toBe(true)
    expect(aside?.getAttribute('aria-hidden')).toBe('true')
  })

  it('renders search button in sidebar header next to collapse and toggles search field', () => {
    render(
      <Sidebar
        conversations={mockConversations}
        projects={mockProjects}
        selectedProjectId="p-1"
        activeConversationId="c-1"
        onSelectConversation={vi.fn()}
        onNewConversation={vi.fn()}
        onDeleteConversation={vi.fn()}
        onTogglePinConversation={vi.fn()}
        onOpenSettings={vi.fn()}
        isCollapsed={false}
        onToggleCollapse={vi.fn()}
      />
    )

    const searchBtn = screen.getByRole('button', { name: /Search conversations/i })
    expect(searchBtn).toBeDefined()

    // Initially search box input is hidden
    expect(screen.queryByPlaceholderText(/Search conversations/i)).toBeNull()

    // Click search icon in header
    fireEvent.click(searchBtn)

    // Now search input appears
    expect(screen.getByPlaceholderText(/Search conversations/i)).toBeDefined()
  })

  it('renders clean empty state when no project is open and removes Automations/Customizations', () => {
    const onOpen = vi.fn()
    render(
      <Sidebar
        conversations={[]}
        projects={[]}
        activeConversationId={null}
        onSelectConversation={vi.fn()}
        onNewConversation={vi.fn()}
        onDeleteConversation={vi.fn()}
        onTogglePinConversation={vi.fn()}
        onOpenSettings={vi.fn()}
        onOpenWorkspaceSelector={onOpen}
        isCollapsed={false}
        onToggleCollapse={vi.fn()}
      />
    )

    // Header displays ONLY "Agent Runtime"
    expect(screen.getByText('Agent Runtime')).toBeDefined()

    // Empty state message and button
    expect(screen.getByText('No project folder open')).toBeDefined()
    expect(screen.getByText('Select Folder')).toBeDefined()

    // Clicking Select Folder calls onOpenWorkspaceSelector
    fireEvent.click(screen.getByText('Select Folder'))
    expect(onOpen).toHaveBeenCalledTimes(1)

    // Automations, Customizations, and global Recent Conversations must NOT exist
    expect(screen.queryByText('Automations')).toBeNull()
    expect(screen.queryByText('Customizations')).toBeNull()
    expect(screen.queryByText('Recent Conversations')).toBeNull()
  })

  it('opens filter and sort dropdown and allows switching sort order', () => {
    render(
      <Sidebar
        conversations={mockConversations}
        projects={mockProjects}
        selectedProjectId="p-1"
        activeConversationId="c-1"
        onSelectConversation={vi.fn()}
        onNewConversation={vi.fn()}
        onDeleteConversation={vi.fn()}
        onTogglePinConversation={vi.fn()}
        onOpenSettings={vi.fn()}
        isCollapsed={false}
        onToggleCollapse={vi.fn()}
      />
    )

    const filterBtn = screen.getByRole('button', { name: /Filter conversations/i })
    expect(filterBtn).toBeDefined()

    // Click filter button
    fireEvent.click(filterBtn)

    // Dropdown items appear
    expect(screen.getByText('Last Updated')).toBeDefined()
    expect(screen.getByText('Sort by Name')).toBeDefined()
    expect(screen.getByText('Date Created')).toBeDefined()
    expect(screen.getByText('Search Text...')).toBeDefined()

    // Click Sort by Name
    const sortByNameBtn = screen.getByText('Sort by Name')
    fireEvent.click(sortByNameBtn)

    // Menu closes after selection
    expect(screen.queryByText('Date Created')).toBeNull()
  })
})

describe('MessageItem Right/Left Alignment', () => {
  it('renders user message right-aligned with card bubble and copy button', () => {
    const userMsg: Message = {
      id: 'm-1',
      role: 'user',
      content: 'Can you inspect the tests?',
      timestamp: new Date().toISOString(),
      status: 'completed',
    }

    const { container } = render(<MessageItem message={userMsg} />)
    const userRow = container.querySelector('.message-row.message-user')
    expect(userRow).not.toBeNull()
    expect(container.querySelector('.user-message-card')).not.toBeNull()
    expect(screen.getByText('Can you inspect the tests?')).toBeDefined()
    expect(screen.getByRole('button', { name: /Copy prompt/i })).toBeDefined()
  })

  it('renders assistant message left-aligned full width', () => {
    const assistantMsg: Message = {
      id: 'm-2',
      role: 'assistant',
      content: 'All tests passed cleanly.',
      timestamp: new Date().toISOString(),
      status: 'completed',
      model: 'Qwen2.5-Coder-14B',
    }

    const { container } = render(<MessageItem message={assistantMsg} />)
    const botRow = container.querySelector('.message-row.message-assistant')
    expect(botRow).not.toBeNull()
    expect(container.querySelector('.assistant-message-container')).not.toBeNull()
    expect(screen.getByText('All tests passed cleanly.')).toBeDefined()
  })
})
