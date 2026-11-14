/**
 * Execution Activity Engine Component.
 * Antigravity-style live execution status:
 * - Live in-flight: "Working" (0s) -> "Thinking (1s)" ... "Thinking (4s)" (or "Working (4s)")
 * - Completed: "Worked for 2m >" / "Thought for 4s" collapsible pill button
 */

import React, { useState, useEffect } from 'react'
import type { ToolActivity, VerificationRecord } from '../../types/api'
import { ToolActivityItem } from './ToolActivityItem'
import { VerificationBadge } from './VerificationBadge'
import { IconChevronRight } from '../common/Icons'

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

export const ExecutionActivity: React.FC<ExecutionActivityProps> = ({
  status = 'completed',
  toolActivities = [],
  verification,
  onInspectArtifact,
}) => {
  const [elapsedSeconds, setElapsedSeconds] = useState(0)
  const [isExpanded, setIsExpanded] = useState(false)

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

  // 1. In-progress state: Clean minimal "Working" / "Thinking (Xs)"
  if (isRunning) {
    const hasTools = toolActivities.length > 0
    return (
      <div className="execution-activity-container">
        <div className="activity-working-row" role="status" aria-live="polite">
          <span className="activity-working-label">
            {elapsedSeconds === 0
              ? 'Working'
              : hasTools
              ? `Working (${elapsedSeconds}s)`
              : `Thinking (${elapsedSeconds}s)`}
          </span>
          <span className="activity-working-dots" aria-hidden="true">
            <span className="working-dot" />
            <span className="working-dot" />
            <span className="working-dot" />
          </span>
        </div>

        {/* Live tool activities executing in-flight */}
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

  // 2. Terminal state: "Worked for 2m >" collapsible pill button
  const hasTools = toolActivities.length > 0
  const hasVerification = !!verification

  if (hasTools || hasVerification) {
    const totalToolSec = Math.round(
      toolActivities.reduce((acc, a) => acc + (a.duration_ms || 0), 0) / 1000
    )
    const effectiveSec = elapsedSeconds > 0 ? elapsedSeconds : Math.max(1, totalToolSec)

    return (
      <div className="execution-activity-container">
        <div className="activity-worked-pill-wrapper">
          <button
            type="button"
            className={`activity-worked-pill ${isExpanded ? 'is-expanded' : ''}`}
            onClick={() => setIsExpanded((prev) => !prev)}
            aria-expanded={isExpanded}
            title={isExpanded ? 'Hide activity details' : 'Show activity details'}
          >
            <span className="activity-worked-text">
              {hasTools
                ? `Worked for ${formatDuration(effectiveSec)}`
                : `Verified for ${formatDuration(effectiveSec)}`}
            </span>
            <IconChevronRight
              size={13}
              className={`activity-chevron ${isExpanded ? 'rotate-90' : ''}`}
            />
          </button>

          {isExpanded && (
            <div className="activity-expanded-content">
              {hasTools && (
                <div className="tool-activities-list">
                  {toolActivities.map((act) => (
                    <ToolActivityItem
                      key={act.id}
                      activity={act}
                      onInspectArtifact={onInspectArtifact}
                    />
                  ))}
                </div>
              )}
              {verification && <VerificationBadge verification={verification} />}
            </div>
          )}
        </div>
      </div>
    )
  }

  // Conversational response with elapsed thinking time
  if (elapsedSeconds > 0) {
    return (
      <div className="execution-activity-container">
        <div className="activity-worked-pill-wrapper">
          <span className="activity-thought-label">
            Thought for {formatDuration(elapsedSeconds)}
          </span>
        </div>
      </div>
    )
  }

  return null
}
