/**
 * Conversations and Projects State Management Hook.
 * Manages conversation list, project hierarchies, active messages, and local persistence.
 */

import { useState, useEffect, useCallback } from 'react'
import type { Conversation, Message, Project } from '../types/api'

const STORAGE_KEY_CONVERSATIONS = 'agent_runtime_conversations_v1'
const STORAGE_KEY_PROJECTS = 'agent_runtime_projects_v1'

const DEFAULT_PROJECTS: Project[] = [
  {
    id: 'proj-default',
    name: 'Agent Runtime Core',
    description: 'Autonomous Coding Agent Workspace',
    created_at: new Date().toISOString(),
  },
]

function createInitialConversation(projectId?: string): Conversation {
  const id = `conv-${Date.now()}`
  return {
    id,
    title: 'New Conversation',
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
    project_id: projectId || 'proj-default',
    pinned: false,
    archived: false,
    model: 'Qwen/Qwen2.5-Coder-14B-Instruct-AWQ',
    messages: [],
  }
}

export function useConversations() {
  const [projects, setProjects] = useState<Project[]>(() => {
    try {
      const stored = localStorage.getItem(STORAGE_KEY_PROJECTS)
      if (stored) {
        return JSON.parse(stored) as Project[]
      }
    } catch (e) {
      console.warn('Could not read projects from localStorage', e)
    }
    return DEFAULT_PROJECTS
  })

  const [conversations, setConversations] = useState<Conversation[]>(() => {
    try {
      const stored = localStorage.getItem(STORAGE_KEY_CONVERSATIONS)
      if (stored) {
        const parsed = JSON.parse(stored) as Conversation[]
        if (parsed.length > 0) return parsed
      }
    } catch (e) {
      console.warn('Could not read conversations from localStorage', e)
    }
    return [createInitialConversation()]
  })

  const [activeConversationId, setActiveConversationId] = useState<string | null>(() => {
    return conversations[0]?.id || null
  })

  // Sync to localStorage
  useEffect(() => {
    try {
      localStorage.setItem(STORAGE_KEY_CONVERSATIONS, JSON.stringify(conversations))
    } catch (e) {
      console.warn('Could not write conversations to localStorage', e)
    }
  }, [conversations])

  useEffect(() => {
    try {
      localStorage.setItem(STORAGE_KEY_PROJECTS, JSON.stringify(projects))
    } catch (e) {
      console.warn('Could not write projects to localStorage', e)
    }
  }, [projects])

  const activeConversation = conversations.find((c) => c.id === activeConversationId) || conversations[0] || null

  const createConversation = useCallback((projectId?: string): Conversation => {
    const newConv = createInitialConversation(projectId)
    setConversations((prev) => [newConv, ...prev])
    setActiveConversationId(newConv.id)
    return newConv
  }, [])

  const selectConversation = useCallback((id: string) => {
    setActiveConversationId(id)
    setConversations((prev) =>
      prev.map((c) => (c.id === id ? { ...c, unread: false } : c))
    )
  }, [])

  const renameConversation = useCallback((id: string, newTitle: string) => {
    const trimmed = newTitle.trim()
    if (!trimmed) return
    setConversations((prev) =>
      prev.map((c) => (c.id === id ? { ...c, title: trimmed, updated_at: new Date().toISOString() } : c))
    )
  }, [])

  const archiveConversation = useCallback((id: string) => {
    setConversations((prev) =>
      prev.map((c) => (c.id === id ? { ...c, archived: !c.archived, updated_at: new Date().toISOString() } : c))
    )
  }, [])

  const setConversationRunning = useCallback((id: string, isRunning: boolean) => {
    setConversations((prev) =>
      prev.map((c) => (c.id === id ? { ...c, is_running: isRunning } : c))
    )
  }, [])

  const setConversationUnread = useCallback((id: string, unread: boolean) => {
    setConversations((prev) =>
      prev.map((c) => (c.id === id ? { ...c, unread } : c))
    )
  }, [])

  const deleteConversation = useCallback((id: string) => {
    setConversations((prev) => {
      const filtered = prev.filter((c) => c.id !== id)
      if (filtered.length === 0) {
        const fallback = createInitialConversation()
        setActiveConversationId(fallback.id)
        return [fallback]
      }
      return filtered
    })
    setActiveConversationId((prevId) => {
      if (prevId === id) {
        const remaining = conversations.filter((c) => c.id !== id)
        return remaining[0]?.id || null
      }
      return prevId
    })
  }, [conversations])

  const togglePinConversation = useCallback((id: string) => {
    setConversations((prev) =>
      prev.map((c) => (c.id === id ? { ...c, pinned: !c.pinned } : c))
    )
  }, [])

  const addMessageToActive = useCallback((message: Message) => {
    if (!activeConversationId) return
    setConversations((prev) =>
      prev.map((conv) => {
        if (conv.id === activeConversationId) {
          // Prevent duplicate message insertion
          if (conv.messages.some((m) => m.id === message.id)) {
            return conv
          }
          // If first user message, generate title from prompt
          let newTitle = conv.title
          if (message.role === 'user' && (conv.title === 'New Workspace Task' || conv.title === 'New Conversation')) {
            newTitle = message.content.slice(0, 36).trim() + (message.content.length > 36 ? '...' : '')
          }
          return {
            ...conv,
            title: newTitle,
            updated_at: new Date().toISOString(),
            messages: [...conv.messages, message],
          }
        }
        return conv
      })
    )
  }, [activeConversationId])

  const updateMessageInActive = useCallback((messageId: string, updater: (msg: Message) => Message) => {
    if (!activeConversationId) return
    setConversations((prev) =>
      prev.map((conv) => {
        if (conv.id === activeConversationId) {
          return {
            ...conv,
            updated_at: new Date().toISOString(),
            messages: conv.messages.map((m) => (m.id === messageId ? updater(m) : m)),
          }
        }
        return conv
      })
    )
  }, [activeConversationId])

  const clearActiveConversation = useCallback(() => {
    if (!activeConversationId) return
    setConversations((prev) =>
      prev.map((conv) => {
        if (conv.id === activeConversationId) {
          return {
            ...conv,
            messages: [],
            updated_at: new Date().toISOString(),
          }
        }
        return conv
      })
    )
  }, [activeConversationId])

  const createProject = useCallback((name: string, description?: string) => {
    const newProj: Project = {
      id: `proj-${Date.now()}`,
      name,
      description,
      created_at: new Date().toISOString(),
    }
    setProjects((prev) => [...prev, newProj])
    return newProj
  }, [])

  return {
    conversations,
    projects,
    activeConversation,
    activeConversationId,
    createConversation,
    createProject,
    selectConversation,
    renameConversation,
    archiveConversation,
    setConversationRunning,
    setConversationUnread,
    deleteConversation,
    togglePinConversation,
    addMessageToActive,
    updateMessageInActive,
    clearActiveConversation,
  }
}
