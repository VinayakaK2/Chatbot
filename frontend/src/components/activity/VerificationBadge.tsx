/**
 * Verification Status Component.
 * Enforces strict separation of task completion vs actual runner verification evidence.
 * A completed task without executed verification checks NEVER shows verified success.
 */

import React, { useState } from 'react'
import type { VerificationRecord } from '../../types/api'
import { Badge } from '../common/Badge'
import {
  IconCheckCircle,
  IconChevronDown,
  IconChevronRight,
  IconClock,
  IconShieldAlert,
  IconXCircle,
} from '../common/Icons'

export interface VerificationBadgeProps {
  verification?: VerificationRecord
}

export const VerificationBadge: React.FC<VerificationBadgeProps> = ({ verification }) => {
  const [isExpanded, setIsExpanded] = useState(false)

  if (!verification || verification.status === 'not_run') {
    return (
      <div className="verification-pill-container not-run">
        <Badge variant="neutral" icon={<IconClock size={12} />}>
          Verification Not Executed
        </Badge>
      </div>
    )
  }

  const { status, checks, summary } = verification
  let badgeVariant: 'success' | 'danger' | 'warning' | 'neutral' = 'neutral'
  let StatusIcon = IconClock
  let statusLabel = 'Inconclusive'

  if (status === 'passed') {
    badgeVariant = 'success'
    StatusIcon = IconCheckCircle
    statusLabel = 'Verified Passed'
  } else if (status === 'failed') {
    badgeVariant = 'danger'
    StatusIcon = IconXCircle
    statusLabel = 'Verification Failed'
  } else if (status === 'running') {
    badgeVariant = 'warning'
    StatusIcon = IconClock
    statusLabel = 'Running Verification...'
  } else if (status === 'blocked') {
    badgeVariant = 'warning'
    StatusIcon = IconShieldAlert
    statusLabel = 'Verification Blocked'
  }

  return (
    <div className="verification-box">
      <div
        className="verification-header"
        onClick={() => setIsExpanded((prev) => !prev)}
        role="button"
        tabIndex={0}
        aria-expanded={isExpanded}
      >
        <div className="verification-header-left">
          {isExpanded ? <IconChevronDown size={14} /> : <IconChevronRight size={14} />}
          <Badge variant={badgeVariant} icon={<StatusIcon size={13} />}>
            {statusLabel}
          </Badge>
          <span className="verification-summary">{summary}</span>
        </div>
        <span className="verification-check-count">{checks.length} checks</span>
      </div>

      {isExpanded && checks.length > 0 && (
        <div className="verification-checks-list">
          {checks.map((check, idx) => {
            const checkSuccess = check.status === 'passed'
            return (
              <div key={idx} className="verification-check-item">
                <div className="check-item-header">
                  <div className="check-item-left">
                    {checkSuccess ? (
                      <IconCheckCircle size={14} color="var(--color-success)" />
                    ) : (
                      <IconXCircle size={14} color="var(--color-danger)" />
                    )}
                    <span className="check-name">{check.name}</span>
                    <span className="check-command">({check.command})</span>
                  </div>
                  {check.duration_ms !== undefined && (
                    <span className="check-duration">{check.duration_ms}ms</span>
                  )}
                </div>
                {check.output && (
                  <pre className="check-output">{check.output}</pre>
                )}
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}
