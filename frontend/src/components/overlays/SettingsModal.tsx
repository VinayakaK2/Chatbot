/**
 * Settings Modal Component.
 * Workspace, model parameters, theme, and runtime configuration inspection.
 * Enhanced with Phase 4 Workspace Filesystem Bounds and Tool Registry Status.
 */

import React, { useState } from 'react'
import type { HealthResponse, ModelReadinessResponse, WorkspaceInfo } from '../../types/api'
import { Button } from '../common/Button'
import { Badge } from '../common/Badge'
import {
  IconCheck,
  IconFolder,
  IconFolderPlus,
  IconRefresh,
  IconSettings,
  IconX,
} from '../common/Icons'

export interface SettingsModalProps {
  isOpen: boolean
  onClose: () => void
  health: HealthResponse | null
  readiness: ModelReadinessResponse | null
  currentWorkspace?: WorkspaceInfo | null
  onRefreshHealth?: () => void
  onOpenWorkspaceSelector?: () => void
}

export const SettingsModal: React.FC<SettingsModalProps> = ({
  isOpen,
  onClose,
  health,
  readiness,
  currentWorkspace,
  onRefreshHealth,
  onOpenWorkspaceSelector,
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
              <p className="modal-subtitle">Phase 4 Filesystem Confinement & Model Configuration</p>
            </div>
          </div>
          <button type="button" className="modal-close-btn" onClick={onClose} aria-label="Close settings">
            <IconX size={16} />
          </button>
        </div>

        <div className="modal-body settings-body">
          {/* Phase 4 Workspace Confinement Section */}
          <div className="settings-section">
            <div className="settings-section-title">PHASE 4 WORKSPACE & CONFINEMENT</div>
            <div className="settings-workspace-card">
              <div className="settings-workspace-row">
                <div className="settings-workspace-info">
                  <div className="settings-workspace-name">
                    <IconFolder size={16} color="var(--accent-primary)" />
                    <span>{currentWorkspace?.name || 'Active Project Root'}</span>
                    <Badge variant="success">Confinement Active</Badge>
                  </div>
                  <div className="settings-workspace-path" title={currentWorkspace?.root_path}>
                    {currentWorkspace?.root_path || 'No workspace selected'}
                  </div>
                </div>
                {onOpenWorkspaceSelector && (
                  <Button
                    variant="secondary"
                    size="sm"
                    onClick={() => {
                      onClose()
                      onOpenWorkspaceSelector()
                    }}
                    leftIcon={<IconFolderPlus size={14} />}
                  >
                    Change Folder
                  </Button>
                )}
              </div>

              {/* Operational Limits Matrix */}
              <div className="settings-limits-grid">
                <div className="limit-item">
                  <span className="limit-label">Max File Size:</span>
                  <span className="limit-val">
                    {Math.round((currentWorkspace?.limits?.max_file_size_bytes || 10485760) / (1024 * 1024))} MB
                  </span>
                </div>
                <div className="limit-item">
                  <span className="limit-label">Max Read Payload:</span>
                  <span className="limit-val">
                    {Math.round((currentWorkspace?.limits?.max_read_bytes || 262144) / 1024)} KB
                  </span>
                </div>
                <div className="limit-item">
                  <span className="limit-label">Directory Limit:</span>
                  <span className="limit-val">
                    {currentWorkspace?.limits?.max_directory_entries || 1000} entries
                  </span>
                </div>
                <div className="limit-item">
                  <span className="limit-label">Max Search Results:</span>
                  <span className="limit-val">
                    {currentWorkspace?.limits?.max_search_results || 200} matches
                  </span>
                </div>
                <div className="limit-item">
                  <span className="limit-label">Max Write Payload:</span>
                  <span className="limit-val">
                    {Math.round((currentWorkspace?.limits?.max_write_bytes || 5242880) / (1024 * 1024))} MB
                  </span>
                </div>
                <div className="limit-item">
                  <span className="limit-label">Max Edit Payload:</span>
                  <span className="limit-val">
                    {Math.round((currentWorkspace?.limits?.max_edit_bytes || 1048576) / (1024 * 1024))} MB
                  </span>
                </div>
              </div>

              {/* Registered Phase 4 Tools */}
              {currentWorkspace?.tools && currentWorkspace.tools.length > 0 && (
                <div className="settings-tools-list">
                  <span className="tools-list-title">Registered Phase 4 Workspace Tools:</span>
                  <div className="tools-badges-wrap">
                    {currentWorkspace.tools.map((tool) => (
                      <span key={tool.name} title={tool.description}>
                        <Badge
                          variant={tool.risk === 'workspace_write' ? 'warning' : 'neutral'}
                        >
                          {tool.name}
                        </Badge>
                      </span>
                    ))}
                  </div>
                </div>
              )}
            </div>
          </div>

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
