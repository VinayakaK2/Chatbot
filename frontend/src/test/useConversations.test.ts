import { describe, it, expect, beforeEach } from 'vitest'
import { renderHook, act } from '@testing-library/react'
import { useConversations } from '../hooks/useConversations'

describe('useConversations Hook', () => {
  beforeEach(() => {
    localStorage.clear()
  })

  it('initializes with empty projects and no active conversation when no project folder is open', () => {
    const { result } = renderHook(() => useConversations())

    expect(Array.isArray(result.current.projects)).toBe(true)
    expect(result.current.projects.length).toBe(0)
    expect(result.current.conversations.length).toBe(0)
    expect(result.current.selectedProjectId).toBeNull()
    expect(result.current.activeConversationId).toBeNull()
    expect(result.current.activeConversation).toBeNull()
  })

  it('creates a project, auto-selects it, and creates an initial scoped conversation', () => {
    const { result } = renderHook(() => useConversations())

    act(() => {
      result.current.createProject('chatbot', 'h:/chatbot')
    })

    expect(result.current.projects.length).toBe(1)
    const project = result.current.projects[0]
    expect(project.name).toBe('chatbot')
    expect(result.current.selectedProjectId).toBe(project.id)
    expect(result.current.conversations.length).toBe(1)
    expect(result.current.conversations[0].project_id).toBe(project.id)
    expect(result.current.activeConversationId).toBe(result.current.conversations[0].id)
  })

  it('creates and activates a new conversation strictly within selected project', () => {
    const { result } = renderHook(() => useConversations())

    act(() => {
      result.current.createProject('chatbot', 'h:/chatbot')
    })

    const initialCount = result.current.conversations.length

    act(() => {
      result.current.createConversation()
    })

    expect(result.current.conversations.length).toBe(initialCount + 1)
    const newChat = result.current.conversations[0]
    expect(newChat.project_id).toBe(result.current.selectedProjectId)
    expect(result.current.activeConversationId).toBe(newChat.id)
  })

  it('pins and unpins a conversation', () => {
    const { result } = renderHook(() => useConversations())

    act(() => {
      result.current.createProject('chatbot', 'h:/chatbot')
    })

    const activeId = result.current.activeConversationId!
    expect(result.current.conversations.find((c) => c.id === activeId)?.pinned).toBe(false)

    act(() => {
      result.current.togglePinConversation(activeId)
    })

    expect(result.current.conversations.find((c) => c.id === activeId)?.pinned).toBe(true)

    act(() => {
      result.current.togglePinConversation(activeId)
    })

    expect(result.current.conversations.find((c) => c.id === activeId)?.pinned).toBe(false)
  })

  it('adds and updates messages in active conversation', () => {
    const { result } = renderHook(() => useConversations())

    act(() => {
      result.current.createProject('chatbot', 'h:/chatbot')
    })

    act(() => {
      result.current.addMessageToActive({
        id: 'msg-test-1',
        role: 'user',
        content: 'Investigate pytest results',
        timestamp: new Date().toISOString(),
      })
    })

    expect(
      result.current.activeConversation?.messages.some((m) => m.id === 'msg-test-1')
    ).toBe(true)

    act(() => {
      result.current.updateMessageInActive('msg-test-1', (m) => ({
        ...m,
        content: 'Updated content',
      }))
    })

    expect(
      result.current.activeConversation?.messages.find((m) => m.id === 'msg-test-1')?.content
    ).toBe('Updated content')
  })

  it('switches projects and filters active conversations with zero cross-project leakage', () => {
    const { result } = renderHook(() => useConversations())

    act(() => {
      result.current.createProject('Project A', 'h:/project-a')
    })
    const projAId = result.current.selectedProjectId!
    const chatAId = result.current.activeConversationId!

    act(() => {
      result.current.createProject('Project B', 'h:/project-b')
    })
    const projBId = result.current.selectedProjectId!
    const chatBId = result.current.activeConversationId!

    expect(projAId).not.toBe(projBId)
    expect(chatAId).not.toBe(chatBId)

    // Switch back to Project A
    act(() => {
      result.current.selectProject(projAId)
    })

    expect(result.current.selectedProjectId).toBe(projAId)
    expect(result.current.activeConversationId).toBe(chatAId)

    // Deleting Project B removes its conversations
    act(() => {
      result.current.deleteProject(projBId)
    })

    expect(result.current.projects.some((p) => p.id === projBId)).toBe(false)
    expect(result.current.conversations.some((c) => c.project_id === projBId)).toBe(false)
  })
})
