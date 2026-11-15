/**
 * Accessible Dropdown / Popover Component.
 * Supports React Portal for unclipped floating menus, outside-click dismissal,
 * keyboard navigation (Escape, Enter), and boundary clamping.
 * Supports both declarative item list and custom children markup.
 */

import React, { useEffect, useRef, useState, useCallback } from 'react'
import { createPortal } from 'react-dom'

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
  items?: DropdownItem[]
  children?: React.ReactNode
  isOpen?: boolean
  onToggle?: () => void
  align?: 'left' | 'right' | 'right-outside'
  className?: string
  usePortal?: boolean
}

export const Dropdown: React.FC<DropdownProps> = ({
  trigger,
  items,
  children,
  isOpen: controlledIsOpen,
  onToggle,
  align = 'right-outside',
  className = '',
  usePortal = true,
}) => {
  const [internalIsOpen, setInternalIsOpen] = useState(false)
  const isDropdownOpen = controlledIsOpen !== undefined ? controlledIsOpen : internalIsOpen

  const [coords, setCoords] = useState<{ top: number; left: number }>({ top: 0, left: 0 })
  const containerRef = useRef<HTMLDivElement>(null)
  const triggerRef = useRef<HTMLDivElement>(null)
  const menuRef = useRef<HTMLDivElement>(null)

  // Calculate coordinates when opening
  const updatePosition = useCallback(() => {
    const el = triggerRef.current || containerRef.current
    if (!el) return
    const rect = el.getBoundingClientRect()
    const menuWidth = 175
    const itemCount = items?.length || 4
    const menuHeight = itemCount * 36 + 12

    let top = rect.top - 2
    let left = rect.right + 6 // Default to right-outside (where "More options" opens)

    if (align === 'right') {
      left = rect.right - menuWidth
      top = rect.bottom + 6
      if (top + menuHeight > window.innerHeight - 8) {
        top = Math.max(8, rect.top - menuHeight - 6)
      }
    } else if (align === 'left') {
      left = rect.left
      top = rect.bottom + 6
      if (top + menuHeight > window.innerHeight - 8) {
        top = Math.max(8, rect.top - menuHeight - 6)
      }
    } else {
      // align === 'right-outside'
      if (left + menuWidth > window.innerWidth - 8) {
        left = Math.max(8, rect.left - menuWidth - 6)
      }
      if (top + menuHeight > window.innerHeight - 8) {
        top = Math.max(8, window.innerHeight - menuHeight - 8)
      }
    }

    if (left < 8) left = 8
    if (top < 8) top = 8

    setCoords({ top, left })
  }, [align, items])

  const closeDropdown = useCallback(() => {
    if (controlledIsOpen !== undefined) {
      if (onToggle && controlledIsOpen) onToggle()
    } else {
      setInternalIsOpen(false)
    }
  }, [controlledIsOpen, onToggle])

  const handleToggle = (e: React.MouseEvent) => {
    e.stopPropagation()
    if (!isDropdownOpen) {
      updatePosition()
      if (controlledIsOpen !== undefined) {
        if (onToggle) onToggle()
      } else {
        setInternalIsOpen(true)
      }
    } else {
      closeDropdown()
    }
  }

  // Update position if dropdown is opened externally
  useEffect(() => {
    if (isDropdownOpen) {
      updatePosition()
    }
  }, [isDropdownOpen, updatePosition])

  // Close on outside click or scroll
  useEffect(() => {
    const handleOutsideClick = (event: MouseEvent) => {
      const target = event.target as Node
      if (
        containerRef.current &&
        !containerRef.current.contains(target) &&
        menuRef.current &&
        !menuRef.current.contains(target)
      ) {
        closeDropdown()
      }
    }

    const handleScroll = (event: Event) => {
      // Don't close if scrolling inside the dropdown menu itself
      if (menuRef.current && menuRef.current.contains(event.target as Node)) {
        return
      }
      closeDropdown()
    }

    if (isDropdownOpen) {
      document.addEventListener('mousedown', handleOutsideClick)
      window.addEventListener('scroll', handleScroll, true)
      window.addEventListener('resize', handleScroll)
    }
    return () => {
      document.removeEventListener('mousedown', handleOutsideClick)
      window.removeEventListener('scroll', handleScroll, true)
      window.removeEventListener('resize', handleScroll)
    }
  }, [isDropdownOpen, closeDropdown])

  // Handle keyboard events (Escape to close)
  const handleKeyDown = (event: React.KeyboardEvent) => {
    if (event.key === 'Escape') {
      closeDropdown()
    }
  }

  const menuContent = (
    <div
      className={`dropdown-menu dropdown-portal-menu ${className}`.trim()}
      ref={menuRef}
      style={{
        position: 'fixed',
        top: `${coords.top}px`,
        left: `${coords.left}px`,
        zIndex: 9999,
      }}
      role="menu"
      aria-orientation="vertical"
      onClick={(e) => e.stopPropagation()}
    >
      {items && items.length > 0
        ? items.map((item) => (
            <button
              key={item.id}
              type="button"
              className={`dropdown-item ${item.danger ? 'dropdown-item-danger is-danger' : ''}`.trim()}
              disabled={item.disabled}
              role="menuitem"
              onClick={(e) => {
                e.stopPropagation()
                if (item.onClick) {
                  item.onClick()
                }
                closeDropdown()
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
          ))
        : children}
    </div>
  )

  return (
    <div
      className={`dropdown-container ${className}`.trim()}
      ref={containerRef}
      onKeyDown={handleKeyDown}
    >
      <div
        ref={triggerRef}
        className="dropdown-trigger"
        onClick={handleToggle}
        role="button"
        tabIndex={0}
        aria-haspopup="true"
        aria-expanded={isDropdownOpen}
      >
        {trigger}
      </div>

      {isDropdownOpen && (usePortal ? createPortal(menuContent, document.body) : menuContent)}
    </div>
  )
}
