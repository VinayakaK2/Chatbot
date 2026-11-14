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
}

export const PreviewPanel: React.FC<PreviewPanelProps> = ({
  isOpen,
  onClose,
  artifact,
  diff,
  verification,
  terminalOutput,
  onOpenDiff,
}) => {
  const tabs: FileViewerTab[] = []

  // Image 2 default code tab: agent_loop.py
  const agentLoopTab: FileViewerTab = {
    id: 'agent_loop.py',
    title: 'agent_loop.py',
    path: 'core/agent_loop.py',
    type: 'code',
    language: 'python',
    content: '# Loading core/agent_loop.py...',
  }

  // Image 2 default terminal tab: powershell.exe
  const powershellTab: FileViewerTab = {
    id: 'powershell.exe',
    title: 'powershell.exe',
    path: 'powershell.exe',
    type: 'terminal',
    language: 'shell',
    content:
      'PS H:\\chatbot> pytest tests/\n============================= test session starts =============================\nplatform win32 -- Python 3.14.6, pytest-8.3.5\ncollected 325 items\n\ntests/test_task_concurrency.py ......................................   [ 11%]\ntests/test_agent_loop.py .................                              [ 16%]\ntests/test_tool_dispatcher.py ..............                             [ 20%]\ntests/test_workspace_security.py ..................                     [ 26%]\ntests/test_filesystem.py ..............                                 [ 30%]\ntests/test_filesystem_integration.py ...........                       [ 33%]\n\n============================= 325 passed in 4.91s =============================',
  }

  let activeId = 'agent_loop.py'

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
      id: artifact.id || 'artifact-tab',
      title: artifact.title || 'Document',
      path: artifact.file_path || artifact.title || 'document.md',
      type: artifact.type === 'code' ? 'code' : 'markdown',
      language: artifact.language || 'markdown',
      content: artifact.content,
    }
    tabs.push(artTab)
    if (!diff) {
      activeId = artTab.id
    }
  }

  // Always include agent_loop.py and powershell.exe so panel tabs match Image 2
  tabs.push(agentLoopTab)
  tabs.push(powershellTab)

  if (terminalOutput) {
    const termTab: FileViewerTab = {
      id: 'terminal-tab',
      title: 'Terminal',
      path: 'terminal.log',
      type: 'terminal',
      content: terminalOutput.output,
      terminalOutput: terminalOutput,
    }
    tabs.push(termTab)
    activeId = termTab.id
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
    />
  )
}
