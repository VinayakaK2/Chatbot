/**
 * Desktop AI Application Sidebar Component.
 * Supports project folders, nested conversations, search filter, pinning,
 * context actions (rename, delete), and smooth collapse.
 */

import React, { useState } from 'react'
import type { Conversation, Project, HealthResponse, ModelReadinessResponse } from '../../types/api'
import {
  IconArchive,
  IconBot,
  IconChevronDown,
  IconChevronRight,
  IconEdit,
  IconFolder,
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
  return (
    <div
      className={`sidebar-chat-item ${isActive ? 'is-active' : ''}`}
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
        {/* Status Indicator (Shown in normal state when mouse NOT hovering) */}
        <div className="chat-status-indicator" aria-hidden="true">
          {isRunning ? (
            <span className="chat-spinner" title="Task running" />
          ) : isUnread ? (
            <span className="chat-unread-dot" title="Unread updates" />
          ) : null}
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
            <IconPin size={13} />
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
                <IconMoreVertical size={13} />
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
            align="right"
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
}) => {
  const [searchQuery, setSearchQuery] = useState('')
  const [expandedProjects, setExpandedProjects] = useState<Record<string, boolean>>({
    default: true,
  })

  const toggleProject = (projectId: string) => {
    setExpandedProjects((prev) => ({
      ...prev,
      [projectId]: !prev[projectId],
    }))
  }

  // Filter conversations by search query
  const filteredConversations = conversations.filter((c) =>
    c.title.toLowerCase().includes(searchQuery.toLowerCase())
  )

  const pinnedConversations = filteredConversations.filter((c) => c.pinned && !c.archived)
  const unpinnedConversations = filteredConversations.filter((c) => !c.pinned && !c.archived)

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
            <span className="brand-subtitle">v0.1.0 • Workspace</span>
          </div>
        </div>
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

      {/* New Conversation Button */}
      <div className="sidebar-action-bar">
        <button
          type="button"
          className="sidebar-new-chat-btn"
          onClick={() => onNewConversation()}
        >
          <IconPlus size={16} />
          <span>New Conversation</span>
        </button>
      </div>

      {/* Search Field */}
      <div className="sidebar-search-box">
        <IconSearch size={14} color="var(--text-muted)" />
        <input
          type="text"
          value={searchQuery}
          onChange={(e) => setSearchQuery(e.target.value)}
          placeholder="Search conversations..."
          className="sidebar-search-input"
          aria-label="Search conversation history"
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

      {/* Scrollable Conversation History & Projects */}
      <div className="sidebar-scroll-area">
        {/* Projects Folders Section */}
        {projects.length > 0 && (
          <div className="sidebar-section">
            <div className="sidebar-section-title">PROJECTS</div>
            {projects.map((proj) => {
              const isExpanded = !!expandedProjects[proj.id]
              const projectChats = filteredConversations.filter(
                (c) => c.project_id === proj.id
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
                    <IconFolder size={14} color="var(--accent-primary)" />
                    <span className="project-name">{proj.name}</span>
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
