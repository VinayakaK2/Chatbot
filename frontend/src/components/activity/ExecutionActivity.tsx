/**
 * Execution Activity Engine Component.
 * Implements the Antigravity 3-Level Disclosure Tree:
 * - Level 0 (Collapsed): "Worked for 8m >" (Image 1)
 * - Level 1 (Top Expanded): "Worked for 8m ⌵" -> "Explored files, ran commands, edited files >" (Image 2)
 * - Level 2 (Summary Expanded): "Explored files, ran commands, edited files ⌵" -> Tool list (Image 3)
 * - Level 3 (Tool Expanded): Inline black terminal card with command & output (Image 4)
 * - 100% transparent backgrounds for all tree disclosure rows.
 */

import React, { useState, useEffect } from 'react'
import type { ToolActivity, VerificationRecord } from '../../types/api'
import { ToolActivityItem } from './ToolActivityItem'
import { VerificationBadge } from './VerificationBadge'
import { IconChevronDown, IconChevronRight } from '../common/Icons'

export interface ExecutionActivityProps {
  status?: 'sending' | 'pending' | 'executing' | 'completed' | 'failed' | 'cancelled' | 'limit_reached'
  model?: string
  toolActivities?: ToolActivity[]
  verification?: VerificationRecord
  onInspectArtifact?: (activity: ToolActivity) => void
}

function formatDuration(sec: number): string {
  if (sec < 60) return `${Math.max(1, sec)}s`
  const mins = Math.floor(sec / 60)
  const remSec = sec % 60
  return remSec > 0 ? `${mins}m ${remSec}s` : `${mins}m`
}

/**
 * Computes dynamic Antigravity summary label based on tools run:
 * e.g., "Explored files, ran commands, edited files"
 */
function getToolSummaryLabel(activities: ToolActivity[]): string {
  const hasExplore = activities.some(
    (a) =>
      a.tool_name?.includes('read') ||
      a.tool_name?.includes('list') ||
      a.tool_name?.includes('search') ||
      a.operation?.toLowerCase().includes('read') ||
      a.operation?.toLowerCase().includes('analyz')
  )
  const hasCommand = activities.some(
    (a) =>
      a.tool_name?.includes('command') ||
      a.tool_name?.includes('shell') ||
      a.tool_name?.includes('git') ||
      !!a.command ||
      a.operation?.toLowerCase().includes('run')
  )
  const hasEdit = activities.some(
    (a) =>
      a.tool_name?.includes('write') ||
      a.tool_name?.includes('edit') ||
      !!a.diff ||
      a.operation?.toLowerCase().includes('edit') ||
      a.operation?.toLowerCase().includes('write')
  )

  const parts: string[] = []
  if (hasExplore) parts.push('Explored files')
  if (hasCommand) parts.push(parts.length > 0 ? 'ran commands' : 'Ran commands')
  if (hasEdit) parts.push(parts.length > 0 ? 'edited files' : 'Edited files')

  return parts.length > 0 ? parts.join(', ') : 'Explored files, ran commands, edited files'
}

export const ExecutionActivity: React.FC<ExecutionActivityProps> = ({
  status = 'completed',
  toolActivities = [],
  verification,
  onInspectArtifact,
}) => {
  const [elapsedSeconds, setElapsedSeconds] = useState(0)
  // Level 1: "Worked for Xm" accordion state
  const [isTopExpanded, setIsTopExpanded] = useState(false)
  // Level 2: "Explored files, ran commands, edited files" accordion state
  const [isSummaryExpanded, setIsSummaryExpanded] = useState(false)

  const isRunning = status === 'sending' || status === 'pending' || status === 'executing'

  // Live timer while task is actively running
  useEffect(() => {
    if (!isRunning) return

    const startTime = Date.now()
    const interval = setInterval(() => {
      const diff = Math.max(1, Math.floor((Date.now() - startTime) / 1000))
      setElapsedSeconds(diff)
    }, 1000)

    return () => clearInterval(interval)
  }, [isRunning])

  // In-progress state: Minimal "Working" indicator
  if (isRunning) {
    const hasTools = toolActivities.length > 0
    return (
      <div className="execution-activity-container antigravity-activity-container">
        <div className="activity-working-row" role="status" aria-live="polite">
          <span className="activity-working-label">
            {elapsedSeconds === 0 ? 'Working' : `Working (${elapsedSeconds}s)`}
          </span>
          <span className="activity-working-dots" aria-hidden="true">
            <span className="working-dot" />
            <span className="working-dot" />
            <span className="working-dot" />
          </span>
        </div>

        {hasTools && (
          <div className="tool-activities-list in-flight">
            {toolActivities.map((act) => (
              <ToolActivityItem
                key={act.id}
                activity={act}
                onInspectArtifact={onInspectArtifact}
              />
            ))}
          </div>
        )}
      </div>
    )
  }

  // Terminal state: 3-Level Disclosure Tree
  const hasTools = toolActivities.length > 0
  const hasVerification = !!verification

  if (hasTools || hasVerification) {
    const totalToolSec = Math.round(
      toolActivities.reduce((acc, a) => acc + (a.duration_ms || 0), 0) / 1000
    )
    const effectiveSec = elapsedSeconds > 0 ? elapsedSeconds : Math.max(1, totalToolSec)
    const summaryLabel = getToolSummaryLabel(toolActivities)

    return (
      <div className="execution-activity-container antigravity-activity-container">
        <div className="antigravity-disclosure-tree">
          {/* Level 0: Top disclosure row — "Worked for 8m >" or "Worked for 8m ⌵" */}
          <button
            type="button"
            className="antigravity-disclosure-row level-0"
            onClick={() => setIsTopExpanded((prev) => !prev)}
            aria-expanded={isTopExpanded}
            title={isTopExpanded ? 'Collapse activity' : 'Expand activity'}
          >
            <span className="disclosure-text">
              {hasTools
                ? `Worked for ${formatDuration(effectiveSec)}`
                : `Verified for ${formatDuration(effectiveSec)}`}
            </span>
            {isTopExpanded ? (
              <IconChevronDown size={13} className="disclosure-chevron" />
            ) : (
              <IconChevronRight size={13} className="disclosure-chevron" />
            )}
          </button>

          {/* Level 1: Underneath "Worked for 8m ⌵" */}
          {isTopExpanded && (
            <div className="antigravity-tree-children level-1-children">
              {hasTools && (
                <>
                  {/* Level 1 Summary button: "Explored files, ran commands, edited files >" / "⌵" */}
                  <button
                    type="button"
                    className="antigravity-disclosure-row level-1"
                    onClick={() => setIsSummaryExpanded((prev) => !prev)}
                    aria-expanded={isSummaryExpanded}
                    title={isSummaryExpanded ? 'Collapse operations' : 'Expand operations'}
                  >
                    <span className="disclosure-text">{summaryLabel}</span>
                    {isSummaryExpanded ? (
                      <IconChevronDown size={13} className="disclosure-chevron" />
                    ) : (
                      <IconChevronRight size={13} className="disclosure-chevron" />
                    )}
                  </button>

                  {/* Level 2: Individual Tool list (Image 3) */}
                  {isSummaryExpanded && (
                    <div className="antigravity-tree-children level-2-children">
                      {toolActivities.map((act) => (
                        <ToolActivityItem
                          key={act.id}
                          activity={act}
                          onInspectArtifact={onInspectArtifact}
                        />
                      ))}
                    </div>
                  )}
                </>
              )}

              {/* Verification details if attached */}
              {verification && <VerificationBadge verification={verification} />}
            </div>
          )}
        </div>
      </div>
    )
  }

  return null
}
