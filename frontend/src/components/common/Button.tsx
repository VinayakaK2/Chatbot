/**
 * Reusable Button Component following Dark Cement design tokens.
 * Features rounded rectangular geometry, smooth hover transitions, and keyboard focus states.
 */

import React from 'react'

export interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: 'primary' | 'secondary' | 'ghost' | 'danger'
  size?: 'sm' | 'md' | 'lg' | 'icon'
  isLoading?: boolean
  leftIcon?: React.ReactNode
  rightIcon?: React.ReactNode
}

export const Button: React.FC<ButtonProps> = ({
  children,
  variant = 'secondary',
  size = 'md',
  isLoading = false,
  leftIcon,
  rightIcon,
  disabled,
  className = '',
  ...props
}) => {
  const baseClasses = 'btn'
  const variantClass = `btn-${variant}`
  const sizeClass = `btn-${size}`
  const stateClass = disabled || isLoading ? 'btn-disabled' : ''

  return (
    <button
      className={`${baseClasses} ${variantClass} ${sizeClass} ${stateClass} ${className}`.trim()}
      disabled={disabled || isLoading}
      {...props}
    >
      {isLoading ? (
        <span className="btn-spinner" aria-hidden="true" />
      ) : (
        leftIcon && <span className="btn-icon left-icon">{leftIcon}</span>
      )}
      {children && <span className="btn-label">{children}</span>}
      {!isLoading && rightIcon && <span className="btn-icon right-icon">{rightIcon}</span>}
    </button>
  )
}
