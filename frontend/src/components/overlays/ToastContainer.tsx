/**
 * Toast Notification Container.
 * Renders stacked, unobtrusive feedback notifications with auto-dismissal.
 */

import React from 'react'
import type { Toast } from '../../hooks/useToast'
import {
  IconAlertTriangle,
  IconCheckCircle,
  IconSparkles,
  IconX,
  IconXCircle,
} from '../common/Icons'

export interface ToastContainerProps {
  toasts: Toast[]
  onRemove: (id: string) => void
}

export const ToastContainer: React.FC<ToastContainerProps> = ({ toasts, onRemove }) => {
  if (toasts.length === 0) return null

  return (
    <div className="toast-container" role="region" aria-live="polite" aria-label="Notifications">
      {toasts.map((toast) => {
        let IconComponent = IconSparkles
        if (toast.type === 'success') IconComponent = IconCheckCircle
        if (toast.type === 'warning') IconComponent = IconAlertTriangle
        if (toast.type === 'error') IconComponent = IconXCircle

        return (
          <div key={toast.id} className={`toast-card toast-${toast.type}`}>
            <span className="toast-icon">
              <IconComponent size={16} />
            </span>
            <span className="toast-message">{toast.message}</span>
            <button
              type="button"
              className="toast-close-btn"
              onClick={() => onRemove(toast.id)}
              aria-label="Dismiss notification"
            >
              <IconX size={13} />
            </button>
          </div>
        )
      })}
    </div>
  )
}
