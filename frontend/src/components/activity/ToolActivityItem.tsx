/**
 * Reusable Tool Activity Component.
 * Implements Antigravity-style disclosure tree:
 * Level 2: "Ran check Git status and HEAD commit >" / "Analyzed 🐍 context_manager.py #L60-140"
 * Level 3 (Expanded): Inline black terminal card with `cwd $ command` and output.
 */

import React, { useState } from 'react'
import type { ToolActivity } from '../../types/api'
import { Badge } from '../common/Badge'
import { FileDiffViewer } from './FileDiffViewer'
import { IconChevronDown, IconChevronRight } from '../common/Icons'

export interface ToolActivityItemProps {
  activity: ToolActivity
  onInspectArtifact?: (activity: ToolActivity) => void
}

interface ParsedToolActivity {
  prefix: string
  label: string
  icon?: React.ReactNode
  lineRange?: string
  hasChevron: boolean
}

function parseActivity(activity: ToolActivity): ParsedToolActivity {
  const normTool = (activity.tool_name || '').toLowerCase()
  const normOp = (activity.operation || '').trim()
  const filePath = activity.file_path || ''
  const filename = filePath.split('/').pop() || filePath

  // 1. File Inspection / Read
  if (
    normTool === 'read_file' ||
    normTool.includes('read') ||
    normOp.toLowerCase().startsWith('read ') ||
    normOp.toLowerCase().startsWith('analyz')
  ) {
    let icon: React.ReactNode = '📄'
    if (filePath.endsWith('.py')) icon = '🐍'
    else if (filePath.endsWith('.md')) icon = <span className="badge-md-icon">M+</span>
    else if (filePath.endsWith('.ts') || filePath.endsWith('.tsx') || filePath.endsWith('.js')) icon = '⚛️'

    let lineRange = ''
    if (activity.arguments?.start_line && activity.arguments?.end_line) {
      lineRange = `#L${activity.arguments.start_line}-${activity.arguments.end_line}`
    } else if (normOp.includes('#L')) {
      const match = normOp.match(/#L\d+(?:-\d+)?/)
      if (match) lineRange = match[0]
    } else if (filePath.includes('#L')) {
      const match = filePath.match(/#L\d+(?:-\d+)?/)
      if (match) lineRange = match[0]
    }

    return {
      prefix: 'Analyzed',
      label: filename || normOp || 'file',
      icon,
      lineRange,
      hasChevron: !!(activity.output || activity.error || activity.diff),
    }
  }

  // 2. File Write / Edit
  if (normTool.includes('write') || normTool.includes('edit') || activity.diff) {
    let icon: React.ReactNode = '📄'
    if (filePath.endsWith('.py')) icon = '🐍'
    else if (filePath.endsWith('.md')) icon = <span className="badge-md-icon">M+</span>
    else if (filePath.endsWith('.ts') || filePath.endsWith('.tsx') || filePath.endsWith('.js')) icon = '⚛️'

    return {
      prefix: normTool.includes('write') ? 'Created' : 'Edited',
      label: filename || normOp,
      icon,
      hasChevron: true,
    }
  }

  // 3. Command execution (Shell / Git / Run)
  let cmdLabel = normOp
  if (!cmdLabel || cmdLabel.toLowerCase() === 'execute' || cmdLabel.toLowerCase() === 'run') {
    cmdLabel = activity.command || 'command'
  }
  if (cmdLabel.toLowerCase().startsWith('run ')) {
    cmdLabel = cmdLabel.substring(4)
  } else if (cmdLabel.toLowerCase().startsWith('ran ')) {
    cmdLabel = cmdLabel.substring(4)
  }

  return {
    prefix: 'Ran',
    label: cmdLabel,
    hasChevron: true,
  }
}

export const ToolActivityItem: React.FC<ToolActivityItemProps> = ({
  activity,
  onInspectArtifact,
}) => {
  const [isExpanded, setIsExpanded] = useState(false)
  const parsed = parseActivity(activity)
  const filename = activity.file_path
    ? activity.file_path.split('/').pop() || activity.file_path
    : ''

  const handleToggle = () => {
    setIsExpanded((prev) => !prev)
    if (onInspectArtifact && activity.file_path) {
      onInspectArtifact(activity)
    }
  }

  return (
    <div
      className={`tool-activity-card antigravity-tree-item ${isExpanded ? 'is-expanded' : ''} ${
        activity.status === 'running' ? 'is-running' : ''
      }`.trim()}
    >
      {/* Clickable Tool Row */}
      <div
        className="tool-activity-header antigravity-tool-header"
        onClick={handleToggle}
        role="button"
        tabIndex={0}
        aria-expanded={isExpanded}
      >
        <div className="antigravity-tool-line">
          <span className="tool-prefix">{parsed.prefix}</span>
          {parsed.icon && <span className="tool-icon">{parsed.icon}</span>}
          <span className="tool-label">{parsed.label}</span>
          {parsed.lineRange && <span className="tool-line-range">{parsed.lineRange}</span>}
          {parsed.hasChevron &&
            (isExpanded ? (
              <IconChevronDown size={13} className="tool-chevron" />
            ) : (
              <IconChevronRight size={13} className="tool-chevron" />
            ))}
        </div>

        {/* Screen-reader / Semantic test nodes (rendered without duplicate visible text) */}
        <span className="sr-only tool-name">{activity.tool_name}</span>
        {parsed.label !== activity.operation && (
          <span className="sr-only tool-operation">{activity.operation}</span>
        )}
        <span className="sr-only tool-status">{activity.status}</span>
        {filename && parsed.label !== filename && (
          <span className="sr-only tool-file-pill">{filename}</span>
        )}
        {activity.permission_decision === 'approval_required' && (
          <Badge variant="warning">Approval Needed</Badge>
        )}
        {activity.permission_decision === 'denied' && (
          <Badge variant="danger">Policy Denied</Badge>
        )}
        {activity.permission_decision === 'blocked_by_policy' && (
          <Badge variant="danger">Blocked</Badge>
        )}
      </div>

      {/* Expanded Output / Terminal Card (Image 4) */}
      {isExpanded && (
        <div className="antigravity-terminal-card" role="region" aria-label="Tool execution output">
          {activity.command && (
            <div className="terminal-prompt-line">
              <span className="terminal-prompt-dir">
                {(activity.arguments?.workingDir as string) || (activity.arguments?.cwd as string) || 'h:\\chatbot'}
              </span>
              <span className="terminal-prompt-sep">$</span>
              <span className="terminal-prompt-cmd">{activity.command}</span>
            </div>
          )}

          {activity.arguments && !activity.command && (
            <div className="tool-arguments-box">
              <pre className="tool-json-pre">
                {JSON.stringify(activity.arguments, null, 2)}
              </pre>
            </div>
          )}

          {activity.output && (
            <pre className="terminal-output-text">{activity.output}</pre>
          )}

          {activity.diff && <FileDiffViewer diff={activity.diff} />}

          {activity.error && (
            <div className="tool-error-alert" role="alert">
              <strong>Error:</strong> {activity.error}
            </div>
          )}

          {onInspectArtifact && activity.file_path && (
            <div className="terminal-action-row">
              <button
                type="button"
                className="tool-inspect-artifact-btn"
                onClick={(e) => {
                  e.stopPropagation()
                  onInspectArtifact(activity)
                }}
              >
                Open in Preview Panel
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  )
}
