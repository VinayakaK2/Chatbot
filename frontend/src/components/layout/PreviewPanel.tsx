/**
 * Antigravity File & Diff Viewer Adapter.
 * Bridges legacy PreviewPanel interface with modern multi-tab FileViewerPanel.
 */

import React from 'react'
import type { ArtifactRecord, FileDiff, VerificationRecord } from '../../types/api'
import { FileViewerPanel, type FileViewerTab } from './FileViewerPanel'

export interface PreviewPanelProps {
  isOpen: boolean
  onClose: () => void
  artifact?: ArtifactRecord | null
  diff?: FileDiff | null
  verification?: VerificationRecord | null
  terminalOutput?: { command: string; output: string } | null
}

export const PreviewPanel: React.FC<PreviewPanelProps> = ({
  isOpen,
  onClose,
  artifact,
  diff,
  verification,
  terminalOutput,
}) => {
  const tabs: FileViewerTab[] = []

  if (artifact) {
    tabs.push({
      id: artifact.id || 'artifact-tab',
      title: artifact.title || 'Document',
      path: artifact.file_path || artifact.title || 'document.md',
      type: artifact.type === 'code' ? 'code' : 'markdown',
      language: artifact.language || 'markdown',
      content: artifact.content,
    })
  }

  if (diff) {
    tabs.push({
      id: 'diff-tab',
      title: diff.file_path ? diff.file_path.split('/').pop() || 'Diff' : 'Diff',
      path: diff.file_path || 'workspace.diff',
      type: 'diff',
      content: diff.patch || '',
      diff: diff,
    })
  }

  if (terminalOutput) {
    tabs.push({
      id: 'terminal-tab',
      title: 'Terminal',
      path: 'terminal.log',
      type: 'terminal',
      content: terminalOutput.output,
      terminalOutput: terminalOutput,
    })
  }

  if (verification) {
    tabs.push({
      id: 'verification-tab',
      title: 'Verification',
      path: 'verification.json',
      type: 'verification',
      content: verification.summary,
      verification: verification,
    })
  }

  // If no specific artifact or diff passed, default to phase-4-test-evidence.md report
  if (tabs.length === 0) {
    tabs.push({
      id: 'phase-4-test-evidence',
      title: 'phase-4-test-evidence.md',
      path: 'docs/reports/phase-4-test-evidence.md',
      type: 'markdown',
      language: 'markdown',
      content: '# Loading evidence report...',
    })
  }

  return (
    <FileViewerPanel
      isOpen={isOpen}
      onClose={onClose}
      initialTabs={tabs}
      activeTabId={tabs[0]?.id}
    />
  )
}
