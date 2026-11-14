/**
 * Settings Modal Component.
 * Workspace, model parameters, theme, and runtime configuration inspection.
 */

import React, { useState } from 'react'
import type { HealthResponse, ModelReadinessResponse } from '../../types/api'
import { Button } from '../common/Button'
import { Badge } from '../common/Badge'
import { IconCheck, IconRefresh, IconSettings, IconX } from '../common/Icons'

export interface SettingsModalProps {
  isOpen: boolean
  onClose: () => void
  health: HealthResponse | null
  readiness: ModelReadinessResponse | null
  onRefreshHealth?: () => void
}

export const SettingsModal: React.FC<SettingsModalProps> = ({
  isOpen,
  onClose,
  health,
  readiness,
  onRefreshHealth,
}) => {
  const [maxTokens, setMaxTokens] = useState(2048)
  const [temperature, setTemperature] = useState(0.2)
  const [saved, setSaved] = useState(false)

  if (!isOpen) return null

  const handleSave = () => {
    setSaved(true)
    setTimeout(() => {
      setSaved(false)
      onClose()
    }, 800)
  }

  return (
    <div className="modal-backdrop" onClick={onClose} role="dialog" aria-modal="true">
      <div
        className="modal-dialog modal-settings"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="modal-header">
          <div className="modal-title-row">
            <div className="settings-icon-box">
              <IconSettings size={18} color="var(--accent-primary)" />
            </div>
            <div>
              <h2 className="modal-title">Workspace Settings</h2>
              <p className="modal-subtitle">Runtime and Model Configuration</p>
            </div>
          </div>
          <button type="button" className="modal-close-btn" onClick={onClose}>
            <IconX size={16} />
          </button>
        </div>

        <div className="modal-body settings-body">
          {/* Backend Diagnostics Section */}
          <div className="settings-section">
            <div className="settings-section-title">RUNTIME BACKEND STATUS</div>
            <div className="settings-status-grid">
              <div className="settings-status-card">
                <span className="card-label">Backend Service:</span>
                <Badge variant={health?.status === 'healthy' ? 'success' : 'danger'}>
                  {health?.status || 'Offline'}
                </Badge>
              </div>

              <div className="settings-status-card">
                <span className="card-label">Runtime Version:</span>
                <span className="card-val">{health?.version || '0.1.0'}</span>
              </div>

              <div className="settings-status-card">
                <span className="card-label">Model Readiness:</span>
                <Badge variant={readiness?.status === 'ready' ? 'success' : 'warning'}>
                  {readiness?.status || 'Unknown'}
                </Badge>
              </div>

              <div className="settings-status-card">
                <span className="card-label">Configured Model:</span>
                <span className="card-val model-val" title={health?.model_id || 'None'}>
                  {health?.model_id ? health.model_id.split('/').pop() : 'Unconfigured'}
                </span>
              </div>
            </div>

            {onRefreshHealth && (
              <button
                type="button"
                className="settings-refresh-btn"
                onClick={onRefreshHealth}
              >
                <IconRefresh size={14} />
                <span>Probe Upstream Endpoint</span>
              </button>
            )}
          </div>

          {/* Model Inference Parameters */}
          <div className="settings-section">
            <div className="settings-section-title">DEFAULT INFERENCE PARAMETERS</div>
            <div className="settings-field">
              <label htmlFor="temp-range" className="field-label">
                Sampling Temperature: <strong>{temperature}</strong>
              </label>
              <input
                id="temp-range"
                type="range"
                min="0.0"
                max="1.0"
                step="0.05"
                value={temperature}
                onChange={(e) => setTemperature(parseFloat(e.target.value))}
                className="settings-slider"
              />
              <span className="field-hint">Lower values produce deterministic and precise code.</span>
            </div>

            <div className="settings-field">
              <label htmlFor="tokens-input" className="field-label">
                Max Output Tokens:
              </label>
              <input
                id="tokens-input"
                type="number"
                min="256"
                max="8192"
                step="256"
                value={maxTokens}
                onChange={(e) => setMaxTokens(parseInt(e.target.value, 10))}
                className="settings-input"
              />
            </div>
          </div>
        </div>

        <div className="modal-footer">
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button variant="primary" onClick={handleSave}>
            {saved ? (
              <>
                <IconCheck size={14} />
                <span>Saved</span>
              </>
            ) : (
              'Save Preferences'
            )}
          </Button>
        </div>
      </div>
    </div>
  )
}
