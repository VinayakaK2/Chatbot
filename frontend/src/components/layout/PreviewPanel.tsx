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
  onOpenDiff?: () => void
  workspaceName?: string
  workspaceRoot?: string
}

export const PreviewPanel: React.FC<PreviewPanelProps> = ({
  isOpen,
  onClose,
  artifact,
  diff,
  verification,
  terminalOutput,
  onOpenDiff,
  workspaceName,
  workspaceRoot,
}) => {
  const tabs: FileViewerTab[] = []
  let activeId = 'powershell.exe'

  if (diff) {
    const diffTab: FileViewerTab = {
      id: 'diff-tab',
      title: diff.file_path ? diff.file_path.split('/').pop() || 'Diff' : 'workspace.diff',
      path: diff.file_path || 'workspace.diff',
      type: 'diff',
      content: (diff as any).patch || diff.new_content || '',
      diff: diff,
    }
    tabs.push(diffTab)
    activeId = diffTab.id
  }

  if (artifact) {
    const artTab: FileViewerTab = {
      id: artifact.id || `file-${artifact.file_path || artifact.title || 'doc'}`,
      title: artifact.title || (artifact.file_path ? artifact.file_path.split('/').pop() || 'File' : 'Document'),
      path: artifact.file_path || artifact.title || 'document.md',
      type: artifact.type === 'code' ? 'code' : 'markdown',
      language: artifact.language || (artifact.type === 'code' ? 'text' : 'markdown'),
      content: artifact.content,
    }
    tabs.push(artTab)
    if (!diff) {
      activeId = artTab.id
    }
  }

  if (terminalOutput) {
    const termTab: FileViewerTab = {
      id: 'powershell.exe',
      title: 'powershell.exe',
      path: 'powershell.exe',
      type: 'terminal',
      language: 'shell',
      content: terminalOutput.output,
      terminalOutput: terminalOutput,
    }
    tabs.push(termTab)
    activeId = termTab.id
  } else {
    // Ensure default terminal tab is present
    tabs.push({
      id: 'powershell.exe',
      title: 'powershell.exe',
      path: 'powershell.exe',
      type: 'terminal',
      language: 'shell',
      content: '',
    })
  }

  if (verification) {
    const verifTab: FileViewerTab = {
      id: 'verification-tab',
      title: 'Verification',
      path: 'verification.json',
      type: 'verification',
      content: verification.summary,
      verification: verification,
    }
    tabs.push(verifTab)
    activeId = verifTab.id
  }

  return (
    <FileViewerPanel
      isOpen={isOpen}
      onClose={onClose}
      initialTabs={tabs}
      activeTabId={activeId}
      onOpenDiff={onOpenDiff}
      workspaceName={workspaceName}
      workspaceRoot={workspaceRoot}
    />
  )
}
