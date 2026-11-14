/**
 * Reusable Tool Activity Component.
 * Visualizes individual tool calls, arguments, permission policies, execution state, and output.
 */

import React, { useState } from 'react'
import type { ToolActivity } from '../../types/api'
import { Badge } from '../common/Badge'
import { FileDiffViewer } from './FileDiffViewer'
import { TerminalViewer } from './TerminalViewer'
import {
  IconAlertTriangle,
  IconCheckCircle,
  IconChevronDown,
  IconChevronRight,
  IconFileCode,
  IconGitBranch,
  IconSearch,
  IconShieldAlert,
  IconTerminal,
  IconXCircle,
} from '../common/Icons'

export interface ToolActivityItemProps {
  activity: ToolActivity
  onInspectArtifact?: (activity: ToolActivity) => void
}

function getToolIcon(toolName: string, operation: string) {
  const norm = (toolName + ' ' + operation).toLowerCase()
  if (norm.includes('git')) return <IconGitBranch size={15} color="var(--accent-primary)" />
  if (norm.includes('terminal') || norm.includes('bash') || norm.includes('shell') || norm.includes('exec')) {
    return <IconTerminal size={15} color="var(--color-warning)" />
  }
  if (norm.includes('search') || norm.includes('find') || norm.includes('grep')) {
    return <IconSearch size={15} color="var(--color-info)" />
  }
  return <IconFileCode size={15} color="var(--text-secondary)" />
}

export const ToolActivityItem: React.FC<ToolActivityItemProps> = ({
  activity,
  onInspectArtifact,
}) => {
  const [isExpanded, setIsExpanded] = useState(false)

  // Status mapping
  let statusBadgeVariant: 'neutral' | 'success' | 'warning' | 'danger' | 'info' = 'neutral'
  let StatusIcon = IconCheckCircle

  if (activity.status === 'running') {
    statusBadgeVariant = 'warning'
  } else if (activity.status === 'completed') {
    statusBadgeVariant = 'success'
  } else if (activity.status === 'failed') {
    statusBadgeVariant = 'danger'
    StatusIcon = IconXCircle
  } else if (activity.status === 'denied') {
    statusBadgeVariant = 'danger'
    StatusIcon = IconShieldAlert
  }

  // Permission decision label
  let permVariant: 'neutral' | 'success' | 'warning' | 'danger' = 'neutral'
  let permLabel = 'Allowed'
  if (activity.permission_decision === 'approval_required') {
    permVariant = 'warning'
    permLabel = 'Approval Needed'
  } else if (activity.permission_decision === 'denied') {
    permVariant = 'danger'
    permLabel = 'Policy Denied'
  } else if (activity.permission_decision === 'blocked_by_policy') {
    permVariant = 'danger'
    permLabel = 'Blocked'
  }

  return (
    <div
      className={`tool-activity-card ${activity.status === 'running' ? 'is-running' : ''}`.trim()}
    >
      <div
        className="tool-activity-header"
        onClick={() => setIsExpanded((prev) => !prev)}
        role="button"
        tabIndex={0}
        aria-expanded={isExpanded}
      >
        <div className="tool-activity-left">
          {isExpanded ? <IconChevronDown size={14} /> : <IconChevronRight size={14} />}
          <span className="tool-icon-wrapper">
            {getToolIcon(activity.tool_name, activity.operation)}
          </span>
          <span className="tool-name">{activity.tool_name}</span>
          <span className="tool-operation">{activity.operation}</span>
          {activity.file_path && (
            <span className="tool-file-pill" title={activity.file_path}>
              {activity.file_path.split('/').pop() || activity.file_path}
            </span>
          )}
        </div>

        <div className="tool-activity-right">
          {activity.permission_decision !== 'allowed' && (
            <Badge variant={permVariant} icon={<IconAlertTriangle size={12} />}>
              {permLabel}
            </Badge>
          )}

          <Badge variant={statusBadgeVariant}>
            {activity.status === 'running' ? (
              <span className="tool-spinner" aria-hidden="true" />
            ) : (
              <StatusIcon size={12} />
            )}
            <span style={{ marginLeft: 4 }}>{activity.status}</span>
          </Badge>

          {activity.duration_ms !== undefined && (
            <span className="tool-duration">{activity.duration_ms}ms</span>
          )}
        </div>
      </div>

      {isExpanded && (
        <div className="tool-activity-expanded">
          {activity.command && (
            <TerminalViewer
              command={activity.command}
              output={activity.output}
              exitCode={activity.exit_code ?? (activity.status === 'failed' ? 1 : 0)}
              durationMs={activity.duration_ms}
            />
          )}

          {activity.diff && <FileDiffViewer diff={activity.diff} />}

          {!activity.command && !activity.diff && (
            <>
              {activity.arguments && (
                <div className="tool-arguments-box">
                  <span className="tool-subhead">Arguments:</span>
                  <pre className="tool-json-pre">
                    {JSON.stringify(activity.arguments, null, 2)}
                  </pre>
                </div>
              )}

              {activity.output && (
                <div className="tool-output-box">
                  <span className="tool-subhead">Output:</span>
                  <pre className="tool-output-pre">{activity.output}</pre>
                </div>
              )}
            </>
          )}

          {activity.error && (
            <div className="tool-error-alert" role="alert">
              <strong>Error:</strong> {activity.error}
            </div>
          )}

          {onInspectArtifact && (
            <button
              type="button"
              className="tool-inspect-artifact-btn"
              onClick={() => onInspectArtifact(activity)}
            >
              Open in Preview Panel
            </button>
          )}
        </div>
      )}
    </div>
  )
}
