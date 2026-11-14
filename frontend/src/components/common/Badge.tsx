/**
 * Semantic Badge Component for risk categories, statuses, and tags.
 */

import React from 'react'

export interface BadgeProps {
  children: React.ReactNode
  variant?: 'neutral' | 'success' | 'warning' | 'danger' | 'accent' | 'info'
  size?: 'sm' | 'md'
  className?: string
  icon?: React.ReactNode
}

export const Badge: React.FC<BadgeProps> = ({
  children,
  variant = 'neutral',
  size = 'sm',
  className = '',
  icon,
}) => {
  return (
    <span className={`badge badge-${variant} badge-${size} ${className}`.trim()}>
      {icon && <span className="badge-icon">{icon}</span>}
      <span className="badge-content">{children}</span>
    </span>
  )
}
