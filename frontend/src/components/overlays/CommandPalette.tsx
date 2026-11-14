/**
 * Command Palette Component (Ctrl+K / Cmd+K).
 * Searchable keyboard command interface for rapid navigation and actions.
 */

import React, { useEffect, useRef, useState } from 'react'
import {
  IconEye,
  IconPlus,
  IconRefresh,
  IconSearch,
  IconSettings,
  IconSidebar,
  IconTrash,
} from '../common/Icons'

export interface CommandItem {
  id: string
  label: string
  shortcut?: string
  icon: React.ReactNode
  action: () => void
}

export interface CommandPaletteProps {
  isOpen: boolean
  onClose: () => void
  onNewConversation: () => void
  onToggleSidebar: () => void
  onTogglePreview: () => void
  onOpenSettings: () => void
  onClearActiveChat: () => void
  onRefreshHealth: () => void
}

export const CommandPalette: React.FC<CommandPaletteProps> = ({
  isOpen,
  onClose,
  onNewConversation,
  onToggleSidebar,
  onTogglePreview,
  onOpenSettings,
  onClearActiveChat,
  onRefreshHealth,
}) => {
  const [query, setQuery] = useState('')
  const [selectedIndex, setSelectedIndex] = useState(0)
  const inputRef = useRef<HTMLInputElement>(null)

  const commands: CommandItem[] = [
    {
      id: 'new-chat',
      label: 'New Conversation',
      shortcut: 'Ctrl+N',
      icon: <IconPlus size={16} />,
      action: onNewConversation,
    },
    {
      id: 'toggle-sidebar',
      label: 'Toggle Sidebar',
      shortcut: 'Ctrl+B',
      icon: <IconSidebar size={16} />,
      action: onToggleSidebar,
    },
    {
      id: 'toggle-preview',
      label: 'Toggle Right Preview Panel',
      shortcut: 'Ctrl+P',
      icon: <IconEye size={16} />,
      action: onTogglePreview,
    },
    {
      id: 'open-settings',
      label: 'Open Workspace Settings',
      shortcut: 'Ctrl+,',
      icon: <IconSettings size={16} />,
      action: onOpenSettings,
    },
    {
      id: 'refresh-health',
      label: 'Probe System & Model Readiness',
      shortcut: 'Ctrl+R',
      icon: <IconRefresh size={16} />,
      action: onRefreshHealth,
    },
    {
      id: 'clear-chat',
      label: 'Clear Current Conversation Messages',
      shortcut: 'Ctrl+L',
      icon: <IconTrash size={16} />,
      action: onClearActiveChat,
    },
  ]

  const filtered = commands.filter((c) =>
    c.label.toLowerCase().includes(query.toLowerCase())
  )

  useEffect(() => {
    if (isOpen) {
      setQuery('')
      setSelectedIndex(0)
      setTimeout(() => inputRef.current?.focus(), 50)
    }
  }, [isOpen])

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Escape') {
      onClose()
    } else if (e.key === 'ArrowDown') {
      e.preventDefault()
      setSelectedIndex((prev) => (prev + 1) % (filtered.length || 1))
    } else if (e.key === 'ArrowUp') {
      e.preventDefault()
      setSelectedIndex((prev) => (prev - 1 + filtered.length) % (filtered.length || 1))
    } else if (e.key === 'Enter') {
      e.preventDefault()
      if (filtered[selectedIndex]) {
        filtered[selectedIndex].action()
        onClose()
      }
    }
  }

  if (!isOpen) return null

  return (
    <div className="modal-backdrop" onClick={onClose} role="dialog" aria-modal="true">
      <div
        className="modal-dialog modal-palette"
        onClick={(e) => e.stopPropagation()}
        onKeyDown={handleKeyDown}
      >
        <div className="palette-input-row">
          <IconSearch size={16} color="var(--text-muted)" />
          <input
            ref={inputRef}
            type="text"
            value={query}
            onChange={(e) => {
              setQuery(e.target.value)
              setSelectedIndex(0)
            }}
            placeholder="Type a command or search actions..."
            className="palette-input"
            aria-label="Search actions"
          />
          <kbd className="palette-kbd">ESC</kbd>
        </div>

        <div className="palette-results-list" role="menu">
          {filtered.length === 0 ? (
            <div className="palette-empty">No matching commands found</div>
          ) : (
            filtered.map((cmd, idx) => (
              <div
                key={cmd.id}
                className={`palette-item ${idx === selectedIndex ? 'is-selected' : ''}`}
                onClick={() => {
                  cmd.action()
                  onClose()
                }}
                onMouseEnter={() => setSelectedIndex(idx)}
                role="menuitem"
                tabIndex={0}
              >
                <div className="palette-item-left">
                  <span className="palette-icon">{cmd.icon}</span>
                  <span className="palette-label">{cmd.label}</span>
                </div>
                {cmd.shortcut && <kbd className="palette-item-shortcut">{cmd.shortcut}</kbd>}
              </div>
            ))
          )}
        </div>
      </div>
    </div>
  )
}
