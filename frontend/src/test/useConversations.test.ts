import { describe, it, expect, beforeEach } from 'vitest'
import { renderHook, act } from '@testing-library/react'
import { useConversations } from '../hooks/useConversations'

describe('useConversations Hook', () => {
  beforeEach(() => {
    localStorage.clear()
  })

  it('initializes with a default project and conversation', () => {
    const { result } = renderHook(() => useConversations())

    expect(result.current.projects.length).toBeGreaterThan(0)
    expect(result.current.conversations.length).toBeGreaterThan(0)
    expect(result.current.activeConversationId).toBeDefined()
  })

  it('creates and activates a new conversation', () => {
    const { result } = renderHook(() => useConversations())

    act(() => {
      result.current.createConversation()
    })

    expect(result.current.conversations.length).toBe(2)
    expect(result.current.activeConversationId).toBe(result.current.conversations[0].id)
  })

  it('pins and unpins a conversation', () => {
    const { result } = renderHook(() => useConversations())
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
})
