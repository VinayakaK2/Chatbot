/**
 * Phase 4 Workspace Selector & Confinement Manager Modal.
 * Enables inspecting active workspace root, browsing local folders,
 * switching active project directory, and reviewing Phase 4 filesystem limits.
 */

import React, { useState, useEffect, useCallback } from 'react'
import type { WorkspaceInfo, DirectoryItem } from '../../types/api'
import { apiClient } from '../../services/api-client'
import { Button } from '../common/Button'
import { Badge } from '../common/Badge'
import {
  IconCheck,
  IconCopy,
  IconFolder,
  IconFolderPlus,
  IconGitBranch,
  IconShieldAlert,
  IconX,
} from '../common/Icons'

export interface WorkspaceSelectorModalProps {
  isOpen: boolean
  onClose: () => void
  currentWorkspace: WorkspaceInfo | null
  onWorkspaceSelected: (newWorkspace: WorkspaceInfo) => void
}

export const WorkspaceSelectorModal: React.FC<WorkspaceSelectorModalProps> = ({
  isOpen,
  onClose,
  currentWorkspace,
  onWorkspaceSelected,
}) => {
  const [targetPath, setTargetPath] = useState('')
  const [browsedPath, setBrowsedPath] = useState('')
  const [parentPath, setParentPath] = useState<string | null>(null)
  const [subdirectories, setSubdirectories] = useState<DirectoryItem[]>([])
  const [recentWorkspaces, setRecentWorkspaces] = useState<string[]>([])
  const [isLoading, setIsLoading] = useState(false)
  const [isBrowsing, setIsBrowsing] = useState(false)
  const [errorMessage, setErrorMessage] = useState<string | null>(null)
  const [copied, setCopied] = useState(false)

  // Initialize form path when modal opens or currentWorkspace changes
  useEffect(() => {
    if (currentWorkspace?.root_path) {
      setTargetPath(currentWorkspace.root_path)
      setBrowsedPath(currentWorkspace.root_path)
    }
  }, [currentWorkspace])

  // Fetch recent workspaces and initial subdirectories on modal open
  const loadDirectoryData = useCallback(async (dirPath?: string) => {
    setIsBrowsing(true)
    try {
      const [browseRes, recentRes] = await Promise.all([
        apiClient.browseDirectories(dirPath),
        apiClient.getRecentWorkspaces(),
      ])
      setBrowsedPath(browseRes.current_path)
      setParentPath(browseRes.parent_path || null)
      setSubdirectories(browseRes.directories || [])
      setRecentWorkspaces(recentRes || [])
      setErrorMessage(null)
    } catch (err) {
      if (err instanceof Error) {
        setErrorMessage(err.message)
      }
    } finally {
      setIsBrowsing(false)
    }
  }, [])

  useEffect(() => {
    if (isOpen) {
      loadDirectoryData(currentWorkspace?.root_path)
    }
  }, [isOpen, currentWorkspace, loadDirectoryData])

  if (!isOpen) return null

  // Copy canonical workspace path to clipboard
  const handleCopyPath = () => {
    if (!currentWorkspace?.root_path) return
    navigator.clipboard.writeText(currentWorkspace.root_path)
    setCopied(true)
    setTimeout(() => setCopied(false), 1500)
  }

  // Handle switching the workspace root
  const handleSwitchWorkspace = async (pathToSwitch?: string) => {
    const selected = (pathToSwitch || targetPath).trim()
    if (!selected) {
      setErrorMessage('Please enter or select a valid workspace directory path.')
      return
    }

    setIsLoading(true)
    setErrorMessage(null)
    try {
      const updated = await apiClient.selectWorkspace(selected)
      onWorkspaceSelected(updated)
      onClose()
    } catch (err) {
      if (err instanceof Error) {
        setErrorMessage(err.message)
      } else {
        setErrorMessage('Failed to select workspace folder.')
      }
    } finally {
      setIsLoading(false)
    }
  }

  // Navigate directory browser into a child folder
  const handleNavigateSubdir = (dir: DirectoryItem) => {
    setTargetPath(dir.path)
    loadDirectoryData(dir.path)
  }

  // Navigate directory browser up to parent
  const handleNavigateUp = () => {
    if (parentPath) {
      setTargetPath(parentPath)
      loadDirectoryData(parentPath)
    }
  }

  return (
    <div className="modal-backdrop" onClick={onClose} role="dialog" aria-modal="true">
      <div
        className="modal-dialog modal-workspace-selector"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="modal-header">
          <div className="modal-title-row">
            <div className="workspace-icon-box">
              <IconFolderPlus size={18} color="var(--accent-primary)" />
            </div>
            <div>
              <h2 className="modal-title">Phase 4 Workspace Management</h2>
              <p className="modal-subtitle">Configure Canonical Confinement Root and Filesystem Scope</p>
            </div>
          </div>
          <button
            type="button"
            className="modal-close-btn"
            onClick={onClose}
            aria-label="Close modal"
          >
            <IconX size={16} />
          </button>
        </div>

        {/* Modal Body */}
        <div className="modal-body workspace-modal-body">
          {/* Active Workspace Banner Card */}
          <div className="workspace-active-card">
            <div className="workspace-active-header">
              <div className="workspace-active-title-group">
                <IconFolder size={18} color="var(--accent-primary)" />
                <span className="workspace-active-name">
                  {currentWorkspace?.name || 'Current Workspace'}
                </span>
                <Badge variant="success">Confinement Active</Badge>
                {currentWorkspace?.is_git_repo && currentWorkspace.current_branch && (
                  <Badge variant="neutral" icon={<IconGitBranch size={12} />}>
                    {currentWorkspace.current_branch}
                  </Badge>
                )}
              </div>
              <button
                type="button"
                className="workspace-copy-btn"
                onClick={handleCopyPath}
                title="Copy canonical path"
              >
                {copied ? <IconCheck size={13} color="var(--color-success)" /> : <IconCopy size={13} />}
                <span>{copied ? 'Copied' : 'Copy Path'}</span>
              </button>
            </div>

            <div className="workspace-canonical-path" title={currentWorkspace?.root_path}>
              {currentWorkspace?.root_path || 'No active workspace selected'}
            </div>

            <div className="workspace-active-metrics">
              <span className="metric-item">
                <strong>{currentWorkspace?.total_files ?? 0}</strong> files indexed
              </span>
              <span className="metric-sep">•</span>
              <span className="metric-item">
                Max Read: <strong>{Math.round((currentWorkspace?.limits?.max_read_bytes || 262144) / 1024)} KB</strong>
              </span>
              <span className="metric-sep">•</span>
              <span className="metric-item">
                Max Write: <strong>{Math.round((currentWorkspace?.limits?.max_write_bytes || 5242880) / (1024 * 1024))} MB</strong>
              </span>
            </div>
          </div>

          {/* Error Banner */}
          {errorMessage && (
            <div className="workspace-error-banner" role="alert">
              <IconShieldAlert size={16} color="var(--color-danger)" />
              <span>{errorMessage}</span>
            </div>
          )}

          {/* Folder Selection Input & Browse Bar */}
          <div className="workspace-field-group">
            <label htmlFor="workspace-path-input" className="workspace-field-label">
              Switch Workspace Root Directory:
            </label>
            <div className="workspace-input-row">
              <input
                id="workspace-path-input"
                type="text"
                value={targetPath}
                onChange={(e) => setTargetPath(e.target.value)}
                placeholder="e.g. H:\chatbot or /path/to/project"
                className="workspace-path-input"
                aria-label="Workspace folder path"
              />
              <Button
                variant="primary"
                onClick={() => handleSwitchWorkspace()}
                disabled={isLoading || !targetPath.trim()}
              >
                {isLoading ? 'Switching...' : 'Switch Folder'}
              </Button>
            </div>
            <span className="workspace-field-hint">
              Paths are resolved canonically. Traversal attempts outside this root are rejected by Phase 4 security policy.
            </span>
          </div>

          {/* Directory Navigation Browser */}
          <div className="workspace-browser-section">
            <div className="browser-header">
              <span className="browser-header-title">Browse Folders:</span>
              <span className="browser-current-path" title={browsedPath}>
                {browsedPath}
              </span>
              {parentPath && (
                <button
                  type="button"
                  className="browser-nav-up-btn"
                  onClick={handleNavigateUp}
                  title={`Navigate up to ${parentPath}`}
                >
                  ↑ Parent Folder
                </button>
              )}
            </div>

            <div className="browser-directories-grid">
              {isBrowsing ? (
                <div className="browser-loading">Scanning directories...</div>
              ) : subdirectories.length > 0 ? (
                subdirectories.map((dir) => (
                  <button
                    key={dir.path}
                    type="button"
                    className="browser-dir-chip"
                    onClick={() => handleNavigateSubdir(dir)}
                    title={`Browse ${dir.name}`}
                  >
                    <IconFolder size={13} color="var(--accent-primary)" />
                    <span className="dir-name">{dir.name}</span>
                  </button>
                ))
              ) : (
                <div className="browser-empty">No child folders found in this directory.</div>
              )}
            </div>
          </div>

          {/* Recent Workspaces Quick Selection */}
          {recentWorkspaces.length > 0 && (
            <div className="workspace-recent-section">
              <div className="workspace-section-subtitle">RECENT WORKSPACES</div>
              <div className="workspace-recent-list">
                {recentWorkspaces.map((path) => (
                  <button
                    key={path}
                    type="button"
                    className={`workspace-recent-chip ${path === currentWorkspace?.root_path ? 'is-active' : ''}`}
                    onClick={() => {
                      setTargetPath(path)
                      handleSwitchWorkspace(path)
                    }}
                    title={path}
                  >
                    <IconFolder size={13} />
                    <span className="recent-path">{path.split(/[\\/]/).pop() || path}</span>
                    <span className="recent-full-path">({path})</span>
                  </button>
                ))}
              </div>
            </div>
          )}

          {/* Phase 4 Operational Invariants & Security Guarantees */}
          <div className="workspace-security-guarantees">
            <div className="guarantees-title">Phase 4 Security & Concurrency Invariants:</div>
            <div className="guarantees-grid">
              <div className="guarantee-badge">
                <span className="dot success" />
                <span>Canonical Path Resolution</span>
              </div>
              <div className="guarantee-badge">
                <span className="dot success" />
                <span>Windows Device Name Traps (CON, NUL, PRN)</span>
              </div>
              <div className="guarantee-badge">
                <span className="dot success" />
                <span>Alternate Data Streams Prohibited</span>
              </div>
              <div className="guarantee-badge">
                <span className="dot success" />
                <span>Atomic Write Replacement (os.replace)</span>
              </div>
            </div>
          </div>
        </div>

        {/* Modal Footer */}
        <div className="modal-footer">
          <Button variant="ghost" onClick={onClose}>
            Close
          </Button>
          <Button
            variant="primary"
            onClick={() => handleSwitchWorkspace()}
            disabled={isLoading || !targetPath.trim() || targetPath === currentWorkspace?.root_path}
          >
            {isLoading ? 'Switching...' : 'Confirm Workspace Root'}
          </Button>
        </div>
      </div>
    </div>
  )
}
