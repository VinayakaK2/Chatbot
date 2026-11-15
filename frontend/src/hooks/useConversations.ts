/**
 * Conversations and Projects State Management Hook.
 * Enforces strict project ownership: every conversation belongs to exactly one project.
 * Supports zero-project startup, dynamic folder selection, and clean switching.
 */

import { useState, useEffect, useCallback, useMemo } from 'react'
import type { Conversation, Message, Project } from '../types/api'

const STORAGE_KEY_CONVERSATIONS = 'agent_runtime_conversations_v2'
const STORAGE_KEY_PROJECTS = 'agent_runtime_projects_v2'
const STORAGE_KEY_SELECTED_PROJECT = 'agent_runtime_selected_project_id_v2'

function createInitialConversation(projectId: string): Conversation {
  const id = `conv-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`
  return {
    id,
    title: 'New Conversation',
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
    project_id: projectId,
    pinned: false,
    archived: false,
    model: 'Qwen/Qwen2.5-Coder-14B-Instruct-AWQ',
    messages: [],
  }
}

export function useConversations() {
  // 1. Projects state — strictly empty by default; no hardcoded projects
  const [projects, setProjects] = useState<Project[]>(() => {
    try {
      const stored = localStorage.getItem(STORAGE_KEY_PROJECTS)
      if (stored) {
        const parsed = JSON.parse(stored) as Project[]
        // Sanitize: remove any legacy mock projects
        return parsed.filter(
          (p) => p.name !== 'Agent Runtime Core' && p.id !== 'proj-default'
        )
      }
    } catch (e) {
      console.warn('Could not read projects from localStorage', e)
    }
    return []
  })

  // 2. Currently selected project ID — validated against existing projects
  const [selectedProjectId, setSelectedProjectId] = useState<string | null>(() => {
    try {
      const saved = localStorage.getItem(STORAGE_KEY_SELECTED_PROJECT)
      if (saved) return saved
    } catch {
      // Ignore localStorage access failures
    }
    return null
  })

  // 3. Conversations state — every conversation must have a project_id
  const [conversations, setConversations] = useState<Conversation[]>(() => {
    try {
      const stored = localStorage.getItem(STORAGE_KEY_CONVERSATIONS)
      if (stored) {
        const parsed = JSON.parse(stored) as Conversation[]
        return parsed.filter(
          (c) => c.project_id && c.project_id !== 'proj-default'
        )
      }
    } catch (e) {
      console.warn('Could not read conversations from localStorage', e)
    }
    return []
  })

  // 4. Active conversation ID within the selected project
  const [activeConversationId, setActiveConversationId] = useState<string | null>(null)

  // Validate selected project against projects list
  useEffect(() => {
    if (projects.length === 0) {
      if (selectedProjectId !== null) {
        setSelectedProjectId(null)
      }
      return
    }
    if (selectedProjectId && !projects.some((p) => p.id === selectedProjectId)) {
      // If current selection is invalid, select the first project
      setSelectedProjectId(projects[0].id)
    }
  }, [projects, selectedProjectId])

  // Sync projects to localStorage
  useEffect(() => {
    try {
      localStorage.setItem(STORAGE_KEY_PROJECTS, JSON.stringify(projects))
    } catch (e) {
      console.warn('Could not write projects to localStorage', e)
    }
  }, [projects])

  // Sync selected project ID to localStorage
  useEffect(() => {
    try {
      if (selectedProjectId) {
        localStorage.setItem(STORAGE_KEY_SELECTED_PROJECT, selectedProjectId)
      } else {
        localStorage.removeItem(STORAGE_KEY_SELECTED_PROJECT)
      }
    } catch (e) {
      console.warn('Could not write selected project ID to localStorage', e)
    }
  }, [selectedProjectId])

  // Sync conversations to localStorage
  useEffect(() => {
    try {
      localStorage.setItem(STORAGE_KEY_CONVERSATIONS, JSON.stringify(conversations))
    } catch (e) {
      console.warn('Could not write conversations to localStorage', e)
    }
  }, [conversations])

  // Active conversation scoped strictly to selected project
  const activeConversation = useMemo(() => {
    if (!selectedProjectId) return null
    const projectChats = conversations.filter((c) => c.project_id === selectedProjectId)
    if (projectChats.length === 0) return null
    return (
      projectChats.find((c) => c.id === activeConversationId) ||
      projectChats[0] ||
      null
    )
  }, [conversations, activeConversationId, selectedProjectId])

  // Automatically keep activeConversationId in sync with activeConversation
  useEffect(() => {
    if (activeConversation && activeConversation.id !== activeConversationId) {
      setActiveConversationId(activeConversation.id)
    } else if (!activeConversation && activeConversationId !== null) {
      setActiveConversationId(null)
    }
  }, [activeConversation, activeConversationId])

  // Switch active project
  const selectProject = useCallback(
    (projectId: string | null) => {
      setSelectedProjectId(projectId)
      if (projectId) {
        const chats = conversations.filter((c) => c.project_id === projectId)
        setActiveConversationId(chats[0]?.id || null)
      } else {
        setActiveConversationId(null)
      }
    },
    [conversations]
  )

  // Create or register a project folder
  const createProject = useCallback(
    (name: string, path?: string, description?: string): Project => {
      const cleanName = name.trim() || 'Project'
      const cleanPath = (path || '').trim()

      // Check if project with same path already exists
      const existing = projects.find(
        (p) => (cleanPath && p.path === cleanPath) || p.name.toLowerCase() === cleanName.toLowerCase()
      )
      if (existing) {
        setSelectedProjectId(existing.id)
        return existing
      }

      const newProj: Project = {
        id: `proj-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`,
        name: cleanName,
        path: cleanPath,
        description,
        created_at: new Date().toISOString(),
      }

      setProjects((prev) => [...prev, newProj])
      setSelectedProjectId(newProj.id)

      // Initialize first conversation for this project
      const firstChat = createInitialConversation(newProj.id)
      setConversations((prev) => [firstChat, ...prev])
      setActiveConversationId(firstChat.id)

      return newProj
    },
    [projects]
  )

  // Delete project and all its associated conversations
  const deleteProject = useCallback(
    (projectId: string) => {
      setProjects((prev) => prev.filter((p) => p.id !== projectId))
      setConversations((prev) => prev.filter((c) => c.project_id !== projectId))
      if (selectedProjectId === projectId) {
        setSelectedProjectId(null)
        setActiveConversationId(null)
      }
    },
    [selectedProjectId]
  )

  // Create a new conversation strictly scoped to a project
  const createConversation = useCallback(
    (projectId?: string): Conversation | null => {
      const targetProjectId = projectId || selectedProjectId
      if (!targetProjectId) {
        // Prevent orphaned conversations without a project
        return null
      }
      const newConv = createInitialConversation(targetProjectId)
      setConversations((prev) => [newConv, ...prev])
      setActiveConversationId(newConv.id)
      return newConv
    },
    [selectedProjectId]
  )

  // Select active conversation (and auto-switch active project if cross-project)
  const selectConversation = useCallback(
    (id: string) => {
      const targetChat = conversations.find((c) => c.id === id)
      if (targetChat && targetChat.project_id && targetChat.project_id !== selectedProjectId) {
        setSelectedProjectId(targetChat.project_id)
      }
      setActiveConversationId(id)
      setConversations((prev) =>
        prev.map((c) => (c.id === id ? { ...c, unread: false } : c))
      )
    },
    [conversations, selectedProjectId]
  )

  // Rename conversation
  const renameConversation = useCallback((id: string, newTitle: string) => {
    const trimmed = newTitle.trim()
    if (!trimmed) return
    setConversations((prev) =>
      prev.map((c) =>
        c.id === id ? { ...c, title: trimmed, updated_at: new Date().toISOString() } : c
      )
    )
  }, [])

  // Archive conversation
  const archiveConversation = useCallback((id: string) => {
    setConversations((prev) =>
      prev.map((c) =>
        c.id === id ? { ...c, archived: !c.archived, updated_at: new Date().toISOString() } : c
      )
    )
  }, [])

  // Set running status
  const setConversationRunning = useCallback((id: string, isRunning: boolean) => {
    setConversations((prev) =>
      prev.map((c) => (c.id === id ? { ...c, is_running: isRunning } : c))
    )
  }, [])

  // Set unread flag
  const setConversationUnread = useCallback((id: string, unread: boolean) => {
    setConversations((prev) =>
      prev.map((c) => (c.id === id ? { ...c, unread } : c))
    )
  }, [])

  // Delete conversation
  const deleteConversation = useCallback(
    (id: string) => {
      const deleted = conversations.find((c) => c.id === id)
      const targetProjId = deleted?.project_id || selectedProjectId

      setConversations((prev) => prev.filter((c) => c.id !== id))

      setActiveConversationId((prevId) => {
        if (prevId === id) {
          const remainingInProject = conversations.filter(
            (c) => c.id !== id && c.project_id === targetProjId
          )
          return remainingInProject[0]?.id || null
        }
        return prevId
      })
    },
    [conversations, selectedProjectId]
  )

  // Toggle pin
  const togglePinConversation = useCallback((id: string) => {
    setConversations((prev) =>
      prev.map((c) => (c.id === id ? { ...c, pinned: !c.pinned } : c))
    )
  }, [])

  // Add message to active conversation
  const addMessageToActive = useCallback(
    (message: Message) => {
      if (!activeConversationId) return
      setConversations((prev) =>
        prev.map((conv) => {
          if (conv.id === activeConversationId) {
            if (conv.messages.some((m) => m.id === message.id)) {
              return conv
            }
            let newTitle = conv.title
            if (
              message.role === 'user' &&
              (conv.title === 'New Workspace Task' || conv.title === 'New Conversation')
            ) {
              newTitle =
                message.content.slice(0, 36).trim() +
                (message.content.length > 36 ? '...' : '')
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
    },
    [activeConversationId]
  )

  // Update message
  const updateMessageInActive = useCallback(
    (messageId: string, updater: (msg: Message) => Message) => {
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
    },
    [activeConversationId]
  )

  // Clear messages in active conversation
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

  return {
    conversations,
    setConversations,
    projects,
    selectedProjectId,
    selectProject,
    createProject,
    deleteProject,
    activeConversation,
    activeConversationId,
    createConversation,
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
