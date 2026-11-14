/**
 * Global Toast Notification State and Hook.
 */

import { useState, useCallback } from 'react'

export interface Toast {
  id: string
  message: string
  type: 'info' | 'success' | 'warning' | 'error'
  durationMs?: number
}

export function useToast() {
  const [toasts, setToasts] = useState<Toast[]>([])

  const addToast = useCallback(
    (message: string, type: 'info' | 'success' | 'warning' | 'error' = 'info', durationMs = 3500) => {
      const id = `${Date.now()}-${Math.random()}`
      const newToast: Toast = { id, message, type, durationMs }
      setToasts((prev) => [...prev, newToast])

      setTimeout(() => {
        setToasts((prev) => prev.filter((t) => t.id !== id))
      }, durationMs)
    },
    []
  )

  const removeToast = useCallback((id: string) => {
    setToasts((prev) => prev.filter((t) => t.id !== id))
  }, [])

  return { toasts, addToast, removeToast }
}
