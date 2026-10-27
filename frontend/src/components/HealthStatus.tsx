/**
 * Component displaying system health, model readiness, and endpoint status.
 */

import React from 'react'
import type { HealthResponse, ModelReadinessResponse } from '../types/api'

interface HealthStatusProps {
  health: HealthResponse | null
  readiness: ModelReadinessResponse | null
  isLoading: boolean
  error: string | null
  onRefresh: () => void
}

export const HealthStatus: React.FC<HealthStatusProps> = ({
  health,
  readiness,
  isLoading,
  error,
  onRefresh,
}) => {
  // Determine color scheme for backend health badge
  const getHealthBadgeClass = () => {
    if (error) return 'badge-danger'
    if (!health) return 'badge-neutral'
    return health.status === 'healthy' ? 'badge-success' : 'badge-warning'
  }

  // Determine color scheme for model readiness badge
  const getReadinessBadgeClass = () => {
    if (!readiness) return 'badge-neutral'
    switch (readiness.status) {
      case 'ready':
        return 'badge-success'
      case 'unconfigured':
        return 'badge-neutral'
      case 'unreachable':
      case 'error':
        return 'badge-danger'
      default:
        return 'badge-neutral'
    }
  }

  return (
    <section className="card health-card" aria-label="System Status">
      <div className="card-header">
        <h2 className="card-title">Runtime & Model Status</h2>
        <button
          type="button"
          onClick={onRefresh}
          disabled={isLoading}
          className="btn btn-secondary btn-sm"
          aria-label="Refresh status indicators"
        >
          {isLoading ? 'Checking...' : 'Refresh'}
        </button>
      </div>

      <div className="status-grid">
        <div className="status-item">
          <span className="status-label">Backend Service:</span>
          <span className={`badge ${getHealthBadgeClass()}`}>
            {error ? 'Offline' : health ? health.status.toUpperCase() : 'Connecting...'}
          </span>
          {health && <span className="status-subtext">v{health.version}</span>}
        </div>

        <div className="status-item">
          <span className="status-label">Model Endpoint:</span>
          <span className={`badge ${getReadinessBadgeClass()}`}>
            {readiness ? readiness.status.toUpperCase() : 'Unchecked'}
          </span>
          {health?.model_id && (
            <span className="status-subtext" title={health.model_id}>
              {health.model_id}
            </span>
          )}
        </div>
      </div>

      {readiness?.message && (
        <p className="status-message" role="status">
          {readiness.message}
        </p>
      )}

      {error && (
        <div className="alert alert-danger" role="alert">
          {error}
        </div>
      )}
    </section>
  )
}
