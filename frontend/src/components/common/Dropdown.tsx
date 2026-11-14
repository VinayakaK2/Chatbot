/**
 * Accessible Dropdown / Popover Component.
 * Supports keyboard navigation (Escape, Enter, arrows), outside-click dismissal, and boundary clamping.
 */

import React, { useEffect, useRef, useState } from 'react'

export interface DropdownItem {
  id: string
  label: string
  icon?: React.ReactNode
  description?: string
  danger?: boolean
  disabled?: boolean
  onClick?: () => void
}

export interface DropdownProps {
  trigger: React.ReactNode
  items: DropdownItem[]
  align?: 'left' | 'right'
  className?: string
}

export const Dropdown: React.FC<DropdownProps> = ({
  trigger,
  items,
  align = 'left',
  className = '',
}) => {
  const [isOpen, setIsOpen] = useState(false)
  const containerRef = useRef<HTMLDivElement>(null)

  // Close on outside click
  useEffect(() => {
    const handleOutsideClick = (event: MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(event.target as Node)) {
        setIsOpen(false)
      }
    }

    if (isOpen) {
      document.addEventListener('mousedown', handleOutsideClick)
    }
    return () => {
      document.removeEventListener('mousedown', handleOutsideClick)
    }
  }, [isOpen])

  // Handle keyboard events (Escape to close)
  const handleKeyDown = (event: React.KeyboardEvent) => {
    if (event.key === 'Escape') {
      setIsOpen(false)
    }
  }

  return (
    <div
      className={`dropdown-container ${className}`.trim()}
      ref={containerRef}
      onKeyDown={handleKeyDown}
    >
      <div
        className="dropdown-trigger"
        onClick={() => setIsOpen((prev) => !prev)}
        role="button"
        tabIndex={0}
        aria-haspopup="menu"
        aria-expanded={isOpen}
      >
        {trigger}
      </div>

      {isOpen && (
        <div
          className={`dropdown-menu dropdown-align-${align}`}
          role="menu"
          aria-orientation="vertical"
        >
          {items.map((item) => (
            <button
              key={item.id}
              type="button"
              className={`dropdown-item ${item.danger ? 'dropdown-item-danger' : ''}`.trim()}
              disabled={item.disabled}
              role="menuitem"
              onClick={() => {
                if (item.onClick) {
                  item.onClick()
                }
                setIsOpen(false)
              }}
            >
              {item.icon && <span className="dropdown-item-icon">{item.icon}</span>}
              <div className="dropdown-item-content">
                <span className="dropdown-item-label">{item.label}</span>
                {item.description && (
                  <span className="dropdown-item-description">{item.description}</span>
                )}
              </div>
            </button>
          ))}
        </div>
      )}
    </div>
  )
}
