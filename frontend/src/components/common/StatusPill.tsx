/**
 * Status Pill Component for backend health & model readiness indication.
 */

import React from 'react'
import type { HealthResponse, ModelReadinessResponse } from '../../types/api'

export interface StatusPillProps {
  health: HealthResponse | null
  readiness: ModelReadinessResponse | null
  isLoading?: boolean
  onClick?: () => void
}

export const StatusPill: React.FC<StatusPillProps> = ({
  health,
  readiness,
  isLoading = false,
  onClick,
}) => {
  const isHealthy = health?.status === 'healthy'
  const isModelReady = readiness?.status === 'ready'

  let dotColor = 'var(--text-muted)'
  let statusText = 'Checking...'

  if (isLoading) {
    statusText = 'Checking...'
    dotColor = 'var(--color-warning)'
  } else if (!health) {
    statusText = 'Offline'
    dotColor = 'var(--color-danger)'
  } else if (isHealthy && isModelReady) {
    statusText = readiness?.model_name ? readiness.model_name.split('/').pop() || 'Model Ready' : 'Ready'
    dotColor = 'var(--color-success)'
  } else if (isHealthy && !isModelReady) {
    statusText = readiness?.status === 'unconfigured' ? 'Model Offline' : 'Model Degraded'
    dotColor = 'var(--color-warning)'
  } else {
    statusText = 'Backend Error'
    dotColor = 'var(--color-danger)'
  }

  return (
    <button
      type="button"
      className="status-pill"
      onClick={onClick}
      title={`Backend: ${health?.status || 'unknown'} | Model: ${readiness?.status || 'unknown'}`}
      aria-label={`System status: ${statusText}`}
    >
      <span className="status-dot" style={{ backgroundColor: dotColor }} />
      <span className="status-text">{statusText}</span>
    </button>
  )
}
