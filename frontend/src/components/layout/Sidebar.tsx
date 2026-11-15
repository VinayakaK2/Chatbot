/**
 * Desktop AI Application Sidebar Component.
 * Strict project-scoped information architecture:
 * - Brand: "Agent Runtime" only (no subtitles or breadcrumbs)
 * - New Conversation
 * - Projects section with folder selection & project-scoped nested conversations
 * - User Profile & Settings at bottom
 * - Zero global recent conversations
 * - Smooth continuous collapse/expand transition (no abrupt display:none)
 */

import React, { useState, useMemo, useRef, useEffect } from 'react'
import type { Conversation, Project, HealthResponse, ModelReadinessResponse, WorkspaceInfo } from '../../types/api'
import {
  IconArchive,
  IconBot,
  IconCheck,
  IconChevronDown,
  IconChevronRight,
  IconEdit,
  IconFilter,
  IconFolder,
  IconFolderPlus,
  IconMoreVertical,
  IconPin,
  IconPlus,
  IconSearch,
  IconSettings,
  IconSidebar,
  IconTrash,
  IconX,
} from '../common/Icons'
import { Dropdown } from '../common/Dropdown'

export interface SidebarProps {
  conversations: Conversation[]
  projects: Project[]
  selectedProjectId?: string | null
  activeConversationId: string | null
  runningConversationIds?: string[]
  onSelectProject?: (projectId: string) => void
  onDeleteProject?: (projectId: string) => void
  onSelectConversation: (id: string) => void
  onNewConversation: (projectId?: string) => void
  onRenameConversation?: (id: string, newTitle: string) => void
  onArchiveConversation?: (id: string) => void
  onDeleteConversation: (id: string) => void
  onTogglePinConversation: (id: string) => void
  onOpenSettings: () => void
  isCollapsed: boolean
  onToggleCollapse: () => void
  health?: HealthResponse | null
  readiness?: ModelReadinessResponse | null
  isHealthLoading?: boolean
  onRefreshHealth?: () => void
  currentWorkspace?: WorkspaceInfo | null
  onOpenWorkspaceSelector?: () => void
}

function formatCompactTime(isoString?: string): string {
  if (!isoString) return ''
  try {
    const date = new Date(isoString)
    const now = new Date()
    const diffMs = now.getTime() - date.getTime()
    if (diffMs < 0) return 'now'
    const diffMins = Math.floor(diffMs / 60000)
    const diffHours = Math.floor(diffMins / 60)
    const diffDays = Math.floor(diffHours / 24)

    if (diffMins < 1) return 'now'
    if (diffMins < 60) return `${diffMins}m`
    if (diffHours < 24) return `${diffHours}h`
    if (diffDays < 30) return `${diffDays}d`
    return `${Math.floor(diffDays / 30)}mo`
  } catch {
    return ''
  }
}

interface ConversationRowProps {
  chat: Conversation
  isActive: boolean
  isRunning: boolean
  isUnread: boolean
  onSelect: (id: string) => void
  onTogglePin: (id: string) => void
  onRename?: (id: string, newTitle: string) => void
  onArchive?: (id: string) => void
  onDelete: (id: string) => void
}

const ConversationRow: React.FC<ConversationRowProps> = ({
  chat,
  isActive,
  isRunning,
  isUnread,
  onSelect,
  onTogglePin,
  onRename,
  onArchive,
  onDelete,
}) => {
  const isSimulatedHover =
    typeof window !== 'undefined' &&
    window.location.search.includes('hover=2') &&
    chat.id === 'conv-demo-2'

  const [isEditing, setIsEditing] = useState(false)
  const [editTitle, setEditTitle] = useState(chat.title)
  const [dropdownOpen, setDropdownOpen] = useState(false)
  const inputRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    if (isEditing) {
      inputRef.current?.focus()
      inputRef.current?.select()
    }
  }, [isEditing])

  const handleSaveRename = () => {
    const trimmed = editTitle.trim()
    if (trimmed && trimmed !== chat.title && onRename) {
      onRename(chat.id, trimmed)
    } else {
      setEditTitle(chat.title)
    }
    setIsEditing(false)
  }

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter') {
      handleSaveRename()
    } else if (e.key === 'Escape') {
      setEditTitle(chat.title)
      setIsEditing(false)
    }
  }

  return (
    <div
      className={`sidebar-chat-item ${isActive ? 'is-active' : ''} ${
        isSimulatedHover ? 'is-hovered' : ''
      }`}
      onClick={() => {
        if (!isEditing) onSelect(chat.id)
      }}
      role="button"
      tabIndex={0}
      aria-label={`Conversation: ${chat.title}${chat.pinned ? ' (Pinned)' : ''}${
        isRunning ? ' (Running)' : ''
      }`}
      aria-current={isActive ? 'true' : undefined}
      onKeyDown={(e) => {
        if (e.key === 'Enter' || e.key === ' ') {
          if (!isEditing) onSelect(chat.id)
        }
      }}
    >
      {/* Leading Pin indicator badge for pinned chats */}
      {chat.pinned && (
        <span className="chat-pinned-badge" title="Pinned conversation" aria-hidden="true">
          <IconPin size={11} color="var(--text-muted)" />
        </span>
      )}

      {/* Main Conversation Title */}
      <div className="chat-item-title-wrap">
        {isEditing ? (
          <input
            ref={inputRef}
            type="text"
            className="chat-item-rename-input"
            value={editTitle}
            onChange={(e) => setEditTitle(e.target.value)}
            onBlur={handleSaveRename}
            onKeyDown={handleKeyDown}
            onClick={(e) => e.stopPropagation()}
            aria-label="Rename conversation"
          />
        ) : (
          <span className="chat-item-title" title={chat.title}>
            {chat.title}
          </span>
        )}
      </div>

      {/* Trailing area: Actions (hover/focus) vs Status Indicator (normal) */}
      <div className="chat-item-trailing">
        {/* Status Indicator or Relative Timestamp */}
        <div className="chat-status-indicator" aria-hidden="true">
          {isRunning ? (
            <span className="chat-spinner" title="Task running" />
          ) : isUnread ? (
            <span className="chat-unread-dot" title="Unread updates" />
          ) : (
            <span className="chat-item-time">{formatCompactTime(chat.updated_at || chat.created_at)}</span>
          )}
        </div>

        {/* Action Controls (Revealed on hover / focus) */}
        <div
          className="chat-item-actions"
          onClick={(e) => e.stopPropagation()}
          onKeyDown={(e) => e.stopPropagation()}
        >
          {/* Quick Pin Toggle Button */}
          <button
            type="button"
            className={`chat-action-btn ${chat.pinned ? 'is-pinned' : ''}`}
            onClick={(e) => {
              e.stopPropagation()
              onTogglePin(chat.id)
            }}
            title={chat.pinned ? 'Unpin conversation' : 'Pin conversation'}
            aria-label={chat.pinned ? 'Unpin conversation' : 'Pin conversation'}
          >
            <IconPin size={12} color={chat.pinned ? 'var(--text-primary)' : 'currentColor'} />
          </button>

          {/* Context Menu Dropdown */}
          <Dropdown
            isOpen={dropdownOpen}
            onToggle={() => setDropdownOpen((prev) => !prev)}
            trigger={
              <button
                type="button"
                className="chat-action-btn"
                title="More options"
                aria-label="More options"
                aria-expanded={dropdownOpen}
              >
                <IconMoreVertical size={13} />
              </button>
            }
          >
            {onRename && (
              <button
                type="button"
                className="dropdown-item"
                onClick={() => {
                  setDropdownOpen(false)
                  setIsEditing(true)
                }}
                role="menuitem"
              >
                <IconEdit size={13} />
                <span>Rename</span>
              </button>
            )}

            <button
              type="button"
              className="dropdown-item"
              onClick={() => {
                setDropdownOpen(false)
                onTogglePin(chat.id)
              }}
              role="menuitem"
            >
              <IconPin size={13} />
              <span>{chat.pinned ? 'Unpin' : 'Pin'}</span>
            </button>

            {onArchive && (
              <button
                type="button"
                className="dropdown-item"
                onClick={() => {
                  setDropdownOpen(false)
                  onArchive(chat.id)
                }}
                role="menuitem"
              >
                <IconArchive size={13} />
                <span>{chat.archived ? 'Unarchive' : 'Archive'}</span>
              </button>
            )}

            <div className="dropdown-divider" />

            <button
              type="button"
              className="dropdown-item danger"
              onClick={() => {
                setDropdownOpen(false)
                onDelete(chat.id)
              }}
              role="menuitem"
            >
              <IconTrash size={13} />
              <span>Delete</span>
            </button>
          </Dropdown>
        </div>
      </div>
    </div>
  )
}

export const Sidebar: React.FC<SidebarProps> = ({
  conversations,
  projects,
  selectedProjectId,
  activeConversationId,
  runningConversationIds = [],
  onSelectProject,
  onDeleteProject: _onDeleteProject,
  onSelectConversation,
  onNewConversation,
  onRenameConversation,
  onArchiveConversation,
  onDeleteConversation,
  onTogglePinConversation,
  onOpenSettings,
  isCollapsed,
  onToggleCollapse,
  onOpenWorkspaceSelector,
}) => {
  const [searchQuery, setSearchQuery] = useState('')
  const [isSearchOpen, setIsSearchOpen] = useState(false)
  const [sortBy, setSortBy] = useState<'updated' | 'name' | 'created'>(() => {
    try {
      const saved = localStorage.getItem('agent_conversations_sort_by')
      if (saved === 'name' || saved === 'created' || saved === 'updated') {
        return saved
      }
    } catch {
      // Ignore localStorage read failures
    }
    return 'updated'
  })
  const [isFilterMenuOpen, setIsFilterMenuOpen] = useState(false)
  const filterMenuRef = useRef<HTMLDivElement>(null)

  // Expand all projects by default
  const [expandedProjects, setExpandedProjects] = useState<Record<string, boolean>>({})

  // Close filter dropdown on outside click
  useEffect(() => {
    if (!isFilterMenuOpen) return
    const handleClickOutside = (e: MouseEvent) => {
      if (filterMenuRef.current && !filterMenuRef.current.contains(e.target as Node)) {
        setIsFilterMenuOpen(false)
      }
    }
    document.addEventListener('mousedown', handleClickOutside)
    return () => document.removeEventListener('mousedown', handleClickOutside)
  }, [isFilterMenuOpen])

  const toggleProject = (projectId: string) => {
    setExpandedProjects((prev) => ({
      ...prev,
      [projectId]: !(prev[projectId] ?? true),
    }))
  }

  // Sort conversations dynamically based on user selection
  const sortedConversations = useMemo(() => {
    return [...conversations].sort((a, b) => {
      if (sortBy === 'name') {
        return (a.title || '').localeCompare(b.title || '')
      }
      if (sortBy === 'created') {
        const tA = new Date(a.created_at).getTime()
        const tB = new Date(b.created_at).getTime()
        return tB - tA
      }
      // Default: 'updated'
      const tA = new Date(a.updated_at || a.created_at).getTime()
      const tB = new Date(b.updated_at || b.created_at).getTime()
      return tB - tA
    })
  }, [conversations, sortBy])

  // Filter conversations by search query
  const filteredConversations = useMemo(() => {
    if (!searchQuery.trim()) return sortedConversations
    const q = searchQuery.toLowerCase()
    return sortedConversations.filter(
      (c) =>
        c.title.toLowerCase().includes(q) ||
        c.messages.some((m) => m.content.toLowerCase().includes(q))
    )
  }, [sortedConversations, searchQuery])

  return (
    <aside
      className={`sidebar-container ${isCollapsed ? 'is-collapsed' : ''}`}
      role="navigation"
      aria-label="Main sidebar"
      aria-hidden={isCollapsed}
    >
      <div className="sidebar-content-wrapper">
        {/* 1. Brand Header: "Agent Runtime" ONLY (no subtitle/breadcrumb) */}
        <div className="sidebar-header">
          <div className="sidebar-brand">
            <div className="brand-icon-box">
              <IconBot size={18} color="var(--accent-primary)" />
            </div>
            <div className="brand-text">
              <span className="brand-title">Agent Runtime</span>
            </div>
          </div>

          <div className="sidebar-header-actions">
            {/* Search conversations toggle icon */}
            <button
              type="button"
              className={`sidebar-header-btn ${isSearchOpen ? 'is-active' : ''}`}
              onClick={() => {
                setIsSearchOpen((prev) => !prev)
                if (isSearchOpen) {
                  setSearchQuery('')
                }
              }}
              title="Search conversations"
              aria-label="Search conversations"
            >
              <IconSearch size={15} />
            </button>

            {/* Collapse sidebar button */}
            <button
              type="button"
              className="sidebar-collapse-btn"
              onClick={onToggleCollapse}
              title="Collapse sidebar (Ctrl+B)"
              aria-label="Collapse sidebar"
            >
              <IconSidebar size={16} />
            </button>
          </div>
        </div>

        {/* 2. New Conversation Button (Capsule Shape) */}
        <div className="sidebar-action-bar">
          <button
            type="button"
            className="sidebar-new-chat-btn"
            onClick={() => {
              if (!selectedProjectId && projects.length === 0) {
                if (onOpenWorkspaceSelector) onOpenWorkspaceSelector()
              } else {
                onNewConversation(selectedProjectId || projects[0]?.id)
              }
            }}
            title={
              !selectedProjectId && projects.length === 0
                ? 'Open a project folder to start a conversation'
                : 'Start a new conversation in current project'
            }
          >
            <IconPlus size={15} />
            <span>New Conversation</span>
          </button>
        </div>

        {/* Search Field (Only shown when search icon is clicked) */}
        {isSearchOpen && (
          <div className="sidebar-search-box is-active">
            <IconSearch size={14} color="var(--text-muted)" />
            <input
              type="text"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              placeholder="Search conversations..."
              className="sidebar-search-input"
              aria-label="Search conversation history"
              autoFocus
            />
            {searchQuery && (
              <button
                type="button"
                className="sidebar-search-clear"
                onClick={() => setSearchQuery('')}
                aria-label="Clear search"
              >
                <IconX size={12} />
              </button>
            )}
          </div>
        )}

        {/* 3. Scrollable Project Hierarchy (No global recent conversations) */}
        <div className="sidebar-scroll-area">
          <div className="sidebar-section">
            <div className="sidebar-section-header-row">
              <span className="sidebar-section-title">Projects</span>
              <div className="sidebar-section-actions">
                <div className="sidebar-filter-dropdown-wrap" ref={filterMenuRef}>
                  <button
                    type="button"
                    className={`sidebar-section-icon-btn ${isFilterMenuOpen ? 'is-active' : ''}`}
                    onClick={() => setIsFilterMenuOpen((prev) => !prev)}
                    title="Filter and sort conversations"
                    aria-label="Filter conversations"
                    aria-expanded={isFilterMenuOpen}
                  >
                    <IconFilter size={13} />
                  </button>

                  {isFilterMenuOpen && (
                    <div className="sidebar-filter-menu" role="menu" aria-label="Sort and filter options">
                      <button
                        type="button"
                        className={`sidebar-filter-item ${sortBy === 'updated' ? 'is-active' : ''}`}
                        onClick={() => {
                          setSortBy('updated')
                          try {
                            localStorage.setItem('agent_conversations_sort_by', 'updated')
                          } catch {
                            // Ignore
                          }
                          setIsFilterMenuOpen(false)
                        }}
                        role="menuitem"
                      >
                        <span>Last Updated</span>
                        {sortBy === 'updated' && <IconCheck size={12} color="var(--color-file-text, #60a5fa)" />}
                      </button>

                      <button
                        type="button"
                        className={`sidebar-filter-item ${sortBy === 'name' ? 'is-active' : ''}`}
                        onClick={() => {
                          setSortBy('name')
                          try {
                            localStorage.setItem('agent_conversations_sort_by', 'name')
                          } catch {
                            // Ignore
                          }
                          setIsFilterMenuOpen(false)
                        }}
                        role="menuitem"
                      >
                        <span>Sort by Name</span>
                        {sortBy === 'name' && <IconCheck size={12} color="var(--color-file-text, #60a5fa)" />}
                      </button>

                      <button
                        type="button"
                        className={`sidebar-filter-item ${sortBy === 'created' ? 'is-active' : ''}`}
                        onClick={() => {
                          setSortBy('created')
                          try {
                            localStorage.setItem('agent_conversations_sort_by', 'created')
                          } catch {
                            // Ignore
                          }
                          setIsFilterMenuOpen(false)
                        }}
                        role="menuitem"
                      >
                        <span>Date Created</span>
                        {sortBy === 'created' && <IconCheck size={12} color="var(--color-file-text, #60a5fa)" />}
                      </button>

                      <div className="sidebar-filter-divider" />

                      <button
                        type="button"
                        className="sidebar-filter-item"
                        onClick={() => {
                          setIsSearchOpen(true)
                          setIsFilterMenuOpen(false)
                        }}
                        role="menuitem"
                      >
                        <span style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
                          <IconSearch size={12} />
                          <span>Search Text...</span>
                        </span>
                      </button>
                    </div>
                  )}
                </div>

                {onOpenWorkspaceSelector && (
                  <button
                    type="button"
                    className="sidebar-section-icon-btn"
                    onClick={onOpenWorkspaceSelector}
                    title="Open or add project folder"
                    aria-label="Open project folder"
                  >
                    <IconFolderPlus size={13} />
                  </button>
                )}
              </div>
            </div>

            {/* Empty State when no project is open */}
            {projects.length === 0 ? (
              <div className="sidebar-projects-empty">
                <div className="empty-project-icon" aria-hidden="true">
                  <IconFolder size={18} color="var(--text-muted)" />
                </div>
                <span className="empty-project-title">No project folder open</span>
                <span className="empty-project-desc">Select a folder to start working with the agent.</span>
                {onOpenWorkspaceSelector && (
                  <button
                    type="button"
                    className="sidebar-empty-open-btn"
                    onClick={onOpenWorkspaceSelector}
                  >
                    <IconPlus size={12} />
                    <span>Select Folder</span>
                  </button>
                )}
              </div>
            ) : (
              /* Projects List & Nested Conversations */
              projects.map((proj) => {
                const isSelected = proj.id === selectedProjectId
                const isExpanded = expandedProjects[proj.id] ?? true
                const projectChats = filteredConversations.filter(
                  (c) => c.project_id === proj.id
                )

                return (
                  <div key={proj.id} className={`project-group ${isSelected ? 'is-selected-project' : ''}`}>
                    <div
                      className={`project-row ${isSelected ? 'is-active-project' : ''}`}
                      onClick={() => {
                        if (onSelectProject) onSelectProject(proj.id)
                        toggleProject(proj.id)
                      }}
                      role="button"
                      tabIndex={0}
                      aria-expanded={isExpanded}
                      title={`Project: ${proj.name} (${proj.path || 'Workspace'})`}
                    >
                      <span className="project-chevron-wrap">
                        {isExpanded ? (
                          <IconChevronDown size={14} />
                        ) : (
                          <IconChevronRight size={14} />
                        )}
                      </span>
                      <IconFolder size={14} color="var(--color-file-text, #60a5fa)" />
                      <span className="project-name project-name-blue">{proj.name}</span>
                      <span className="project-count">{projectChats.length}</span>
                    </div>

                    {isExpanded && (
                      <div className="project-nested-chats">
                        {projectChats.length === 0 ? (
                          <div className="project-empty-chats">
                            <span>No conversations yet</span>
                            <button
                              type="button"
                              className="project-new-chat-link"
                              onClick={() => onNewConversation(proj.id)}
                            >
                              + New chat
                            </button>
                          </div>
                        ) : (
                          projectChats.map((chat) => (
                            <ConversationRow
                              key={chat.id}
                              chat={chat}
                              isActive={chat.id === activeConversationId}
                              isRunning={!!chat.is_running || runningConversationIds.includes(chat.id)}
                              isUnread={!!chat.unread && chat.id !== activeConversationId}
                              onSelect={onSelectConversation}
                              onTogglePin={onTogglePinConversation}
                              onRename={onRenameConversation}
                              onArchive={onArchiveConversation}
                              onDelete={onDeleteConversation}
                            />
                          ))
                        )}
                      </div>
                    )}
                  </div>
                )
              })
            )}
          </div>
        </div>

        {/* 4. Fixed Bottom Settings & Profile Area */}
        <div className="sidebar-footer">
          <div className="sidebar-footer-row">
            <div className="sidebar-user-profile" title="Signed in as Vinayaka K">
              <div className="user-avatar">
                <span className="user-initials">VK</span>
              </div>
              <span className="user-name">Vinayaka K</span>
            </div>

            <button
              type="button"
              className="sidebar-settings-btn"
              onClick={onOpenSettings}
              title="Open workspace settings (Ctrl+,)"
              aria-label="Workspace settings"
            >
              <IconSettings size={16} />
            </button>
          </div>
        </div>
      </div>
    </aside>
  )
}
