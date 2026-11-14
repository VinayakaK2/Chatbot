/**
 * Terminal Viewer Component for displaying shell execution logs, exit codes, and durations.
 */

import React, { useState } from 'react'
import { IconCheck, IconCopy, IconTerminal } from '../common/Icons'
import { Badge } from '../common/Badge'

export interface TerminalViewerProps {
  command: string
  workingDir?: string
  output?: string
  exitCode?: number
  durationMs?: number
  isTruncated?: boolean
}

export const TerminalViewer: React.FC<TerminalViewerProps> = ({
  command,
  workingDir,
  output,
  exitCode = 0,
  durationMs,
  isTruncated = false,
}) => {
  const [copied, setCopied] = useState(false)

  const handleCopy = async () => {
    try {
      const fullText = `$ ${command}\n${output || ''}`
      await navigator.clipboard.writeText(fullText)
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    } catch (err) {
      console.error('Failed to copy terminal output', err)
    }
  }

  const isSuccess = exitCode === 0

  return (
    <div className="terminal-viewer" role="region" aria-label={`Terminal: ${command}`}>
      <div className="terminal-header">
        <div className="terminal-header-left">
          <IconTerminal size={14} color="var(--text-secondary)" />
          <span className="terminal-title">Terminal</span>
          {workingDir && <span className="terminal-cwd">cwd: {workingDir}</span>}
        </div>
        <div className="terminal-header-right">
          {durationMs !== undefined && (
            <span className="terminal-duration">{durationMs}ms</span>
          )}
          <Badge variant={isSuccess ? 'success' : 'danger'}>
            exit {exitCode}
          </Badge>
          <button
            type="button"
            className="terminal-copy-btn"
            onClick={handleCopy}
            title="Copy command and output"
            aria-label="Copy terminal text"
          >
            {copied ? <IconCheck size={14} color="var(--color-success)" /> : <IconCopy size={14} />}
          </button>
        </div>
      </div>

      <div className="terminal-body">
        <div className="terminal-command-line">
          <span className="terminal-prompt-symbol">$</span>
          <span className="terminal-cmd-text">{command}</span>
        </div>
        {output && <pre className="terminal-output">{output}</pre>}
        {isTruncated && (
          <div className="terminal-truncated-notice">
            [Output truncated — maximum buffer limit reached]
          </div>
        )}
      </div>
    </div>
  )
}
