/**
 * Collapsible & Resizable Right Preview Panel.
 * Inspects generated artifacts, code diffs, markdown documents, and verification reports.
 */

import React, { useState } from 'react'
import type { ArtifactRecord, FileDiff, VerificationRecord } from '../../types/api'
import { CodeBlock } from '../chat/CodeBlock'
import { MarkdownRenderer } from '../chat/MarkdownRenderer'
import { FileDiffViewer } from '../activity/FileDiffViewer'
import { VerificationBadge } from '../activity/VerificationBadge'
import {
  IconCheck,
  IconCopy,
  IconFileCode,
  IconLayers,
  IconTerminal,
  IconX,
} from '../common/Icons'

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
  const [copied, setCopied] = useState(false)
  const [activeTab, setActiveTab] = useState<'artifact' | 'diff' | 'terminal' | 'verification'>(
    artifact ? 'artifact' : diff ? 'diff' : terminalOutput ? 'terminal' : 'verification'
  )

  // Ensure active tab automatically aligns when diff, artifact, or terminalOutput changes
  React.useEffect(() => {
    if (diff) setActiveTab('diff')
    else if (artifact) setActiveTab('artifact')
    else if (terminalOutput) setActiveTab('terminal')
    else if (verification) setActiveTab('verification')
  }, [diff, artifact, terminalOutput, verification])

  if (!isOpen) return null

  const handleCopyContent = async (text: string) => {
    try {
      await navigator.clipboard.writeText(text)
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    } catch (err) {
      console.error('Failed to copy', err)
    }
  }

  return (
    <aside className="preview-panel-container" role="complementary" aria-label="Inspection preview panel">
      {/* Panel Header */}
      <div className="preview-panel-header">
        <div className="preview-tabs-row">
          {artifact && (
            <button
              type="button"
              className={`preview-tab-btn ${activeTab === 'artifact' ? 'is-active' : ''}`}
              onClick={() => setActiveTab('artifact')}
            >
              <IconFileCode size={14} />
              <span>{artifact.title}</span>
            </button>
          )}

          {diff && (
            <button
              type="button"
              className={`preview-tab-btn ${activeTab === 'diff' ? 'is-active' : ''}`}
              onClick={() => setActiveTab('diff')}
            >
              <IconLayers size={14} />
              <span>Diff</span>
            </button>
          )}

          {terminalOutput && (
            <button
              type="button"
              className={`preview-tab-btn ${activeTab === 'terminal' ? 'is-active' : ''}`}
              onClick={() => setActiveTab('terminal')}
            >
              <IconTerminal size={14} />
              <span>Terminal</span>
            </button>
          )}

          {verification && (
            <button
              type="button"
              className={`preview-tab-btn ${activeTab === 'verification' ? 'is-active' : ''}`}
              onClick={() => setActiveTab('verification')}
            >
              <span>Verification</span>
            </button>
          )}
        </div>

        <div className="preview-header-actions">
          {artifact && activeTab === 'artifact' && (
            <button
              type="button"
              className="preview-action-btn"
              onClick={() => handleCopyContent(artifact.content)}
              title="Copy content"
              aria-label="Copy artifact content"
            >
              {copied ? <IconCheck size={14} color="var(--color-success)" /> : <IconCopy size={14} />}
            </button>
          )}

          <button
            type="button"
            className="preview-close-btn"
            onClick={onClose}
            title="Close preview panel"
            aria-label="Close preview"
          >
            <IconX size={16} />
          </button>
        </div>
      </div>

      {/* Panel Body */}
      <div className="preview-panel-body">
        {activeTab === 'artifact' && artifact && (
          <div className="artifact-view">
            {artifact.type === 'code' ? (
              <CodeBlock code={artifact.content} language={artifact.language || 'text'} />
            ) : (
              <MarkdownRenderer content={artifact.content} />
            )}
          </div>
        )}

        {activeTab === 'diff' && diff && <FileDiffViewer diff={diff} initiallyExpanded={true} />}

        {activeTab === 'terminal' && terminalOutput && (
          <div className="preview-terminal-view">
            <div className="terminal-cmd-bar">$ {terminalOutput.command}</div>
            <pre className="terminal-output-pre">{terminalOutput.output}</pre>
          </div>
        )}

        {activeTab === 'verification' && verification && (
          <div className="preview-verification-view">
            <VerificationBadge verification={verification} />
          </div>
        )}
      </div>
    </aside>
  )
}
