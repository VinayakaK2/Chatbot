/**
 * Desktop AI Application Sidebar Component.
 * Supports project folders, nested conversations, search filter, pinning,
 * context actions (rename, delete), and smooth collapse.
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
  activeConversationId: string | null
  runningConversationIds?: string[]
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

  return (
    <div
      className={`sidebar-chat-item ${isActive ? 'is-active' : ''} ${isSimulatedHover ? 'is-hovered' : ''}`.trim()}
      onClick={() => onSelect(chat.id)}
      role="button"
      tabIndex={0}
      onKeyDown={(e) => {
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault()
          onSelect(chat.id)
        }
      }}
      aria-current={isActive ? 'page' : undefined}
    >
      <span className="chat-item-title" title={chat.title}>
        {chat.title}
      </span>

      {/* Trailing area: Actions (hover/focus) vs Status Indicator (normal) */}
      <div className="chat-item-trailing">
        {/* Status Indicator or Relative Timestamp (Shown in normal state when mouse NOT hovering) */}
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
        <div className="chat-item-actions" onClick={(e) => e.stopPropagation()}>
          {/* Quick Pin / Unpin button */}
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
            <IconPin size={14} />
          </button>

          {/* Three-dot More Options dropdown */}
          <Dropdown
            trigger={
              <button
                type="button"
                className="chat-action-btn"
                title="More options"
                aria-label="More options"
              >
                <IconMoreVertical size={14} />
              </button>
            }
            items={[
              {
                id: 'rename',
                label: 'Rename',
                icon: <IconEdit size={13} />,
                onClick: () => {
                  const newTitle = window.prompt('Rename conversation:', chat.title)
                  if (newTitle && onRename) onRename(chat.id, newTitle)
                },
              },
              {
                id: 'pin',
                label: chat.pinned ? 'Unpin conversation' : 'Pin to top',
                icon: <IconPin size={13} />,
                onClick: () => onTogglePin(chat.id),
              },
              {
                id: 'archive',
                label: chat.archived ? 'Unarchive' : 'Archive',
                icon: <IconArchive size={13} />,
                onClick: () => onArchive?.(chat.id),
              },
              {
                id: 'delete',
                label: 'Delete conversation',
                icon: <IconTrash size={13} />,
                danger: true,
                onClick: () => {
                  if (window.confirm(`Delete conversation "${chat.title}"?`)) {
                    onDelete(chat.id)
                  }
                },
              },
            ]}
            align="right-outside"
            usePortal={true}
          />
        </div>
      </div>
    </div>
  )
}

export const Sidebar: React.FC<SidebarProps> = ({
  conversations,
  projects,
  activeConversationId,
  runningConversationIds = [],
  onSelectConversation,
  onNewConversation,
  onRenameConversation,
  onArchiveConversation,
  onDeleteConversation,
  onTogglePinConversation,
  onOpenSettings,
  isCollapsed,
  onToggleCollapse,
  currentWorkspace,
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

  const [expandedProjects, setExpandedProjects] = useState<Record<string, boolean>>({
    default: true,
    'workspace-root-project': true,
  })

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
      [projectId]: !prev[projectId],
    }))
  }

  // Display projects: explicitly filter out 'Agent Runtime Core' / 'proj-default'
  const displayProjects = useMemo(() => {
    const valid = projects.filter(
      (p) => p.name !== 'Agent Runtime Core' && p.id !== 'proj-default'
    )
    if (valid.length > 0) return valid
    if (currentWorkspace?.name) {
      return [
        {
          id: 'workspace-root-project',
          name: currentWorkspace.name,
          created_at: new Date().toISOString(),
        },
      ]
    }
    return []
  }, [projects, currentWorkspace])

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

  const pinnedConversations = useMemo(
    () => filteredConversations.filter((c) => c.pinned && !c.archived),
    [filteredConversations]
  )
  const unpinnedConversations = useMemo(
    () => filteredConversations.filter((c) => !c.pinned && !c.archived),
    [filteredConversations]
  )

  if (isCollapsed) {
    return null
  }

  return (
    <aside className="sidebar-container" role="navigation" aria-label="Main sidebar">
      {/* Brand Header */}
      <div className="sidebar-header">
        <div className="sidebar-brand">
          <div className="brand-icon-box">
            <IconBot size={18} color="var(--accent-primary)" />
          </div>
          <div className="brand-text">
            <span className="brand-title">Agent Runtime</span>
            <button
              type="button"
              className="sidebar-workspace-pill"
              onClick={onOpenWorkspaceSelector}
              title={`Active Workspace: ${currentWorkspace?.root_path || 'Select folder'}`}
              aria-label="Open Workspace Selector"
            >
              <IconFolder size={11} color="var(--accent-primary)" />
              <span className="sidebar-workspace-name">{currentWorkspace?.name || 'Workspace'}</span>
            </button>
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

      {/* New Conversation Button */}
      <div className="sidebar-action-bar">
        <button
          type="button"
          className="sidebar-new-chat-btn"
          onClick={() => onNewConversation()}
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

      {/* Scrollable Conversation History & Projects */}
      <div className="sidebar-scroll-area">
        {/* Projects Folders Section */}
        {displayProjects.length > 0 && (
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
                            // Ignore localStorage write failures
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
                            // Ignore localStorage write failures
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
                            // Ignore localStorage write failures
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
                    title="Open workspace folder"
                    aria-label="Open workspace folder"
                  >
                    <IconFolderPlus size={13} />
                  </button>
                )}
              </div>
            </div>
            {displayProjects.map((proj) => {
              const isExpanded = !!expandedProjects[proj.id]
              const projectChats = filteredConversations.filter(
                (c) =>
                  c.project_id === proj.id ||
                  (proj.id === 'workspace-root-project' &&
                    (!c.project_id || c.project_id === 'proj-default' || c.project_id === 'workspace-root-project'))
              )

              return (
                <div key={proj.id} className="project-group">
                  <div
                    className="project-row"
                    onClick={() => toggleProject(proj.id)}
                    role="button"
                    tabIndex={0}
                    aria-expanded={isExpanded}
                  >
                    {isExpanded ? (
                      <IconChevronDown size={14} />
                    ) : (
                      <IconChevronRight size={14} />
                    )}
                    <IconFolder size={14} color="var(--color-file-text, #60a5fa)" />
                    <span className="project-name project-name-blue">{proj.name}</span>
                    <span className="project-count">{projectChats.length}</span>
                  </div>

                  {isExpanded && projectChats.length > 0 && (
                    <div className="project-nested-chats">
                      {projectChats.map((chat) => (
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
                      ))}
                    </div>
                  )}
                </div>
              )
            })}
          </div>
        )}

        {/* Pinned Section */}
        {pinnedConversations.length > 0 && (
          <div className="sidebar-section">
            <div className="sidebar-section-title">
              <IconPin size={11} />
              <span>PINNED</span>
            </div>
            {pinnedConversations.map((chat) => (
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
            ))}
          </div>
        )}

        {/* Recent Conversations Section */}
        <div className="sidebar-section">
          <div className="sidebar-section-title">RECENT CONVERSATIONS</div>
          {unpinnedConversations.length === 0 ? (
            <div className="sidebar-empty-state">No conversations found</div>
          ) : (
            unpinnedConversations.map((chat) => (
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
      </div>

      {/* Fixed Bottom Settings & Profile Area */}
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
    </aside>
  )
}
