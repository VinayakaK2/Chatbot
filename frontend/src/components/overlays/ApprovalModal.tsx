/**
 * Human Decision Authority: Approval Modal.
 * Implements Section 10E & 5.1 of 2604.14228v3.pdf.
 * Clearly surfaces exact operation, working directory, risk category, and policy reason.
 */

import React, { useEffect, useRef } from 'react'
import type { PendingApproval } from '../../types/api'
import { Badge } from '../common/Badge'
import { Button } from '../common/Button'
import { IconAlertTriangle, IconShieldAlert, IconX } from '../common/Icons'

export interface ApprovalModalProps {
  approval: PendingApproval | null
  onApprove: (callId: string) => void
  onReject: (callId: string, reason?: string) => void
  onClose: () => void
}

export const ApprovalModal: React.FC<ApprovalModalProps> = ({
  approval,
  onApprove,
  onReject,
  onClose,
}) => {
  const modalRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && approval) {
        onReject(approval.call_id, 'Dismissed by user via Escape')
      }
    }
    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [approval, onReject])

  if (!approval) return null

  let riskVariant: 'warning' | 'danger' | 'info' = 'warning'
  if (approval.risk_category === 'destructive' || approval.risk_category === 'command_execution') {
    riskVariant = 'danger'
  }

  return (
    <div className="modal-backdrop" role="dialog" aria-modal="true" aria-labelledby="approval-title">
      <div className="modal-dialog modal-approval" ref={modalRef}>
        <div className="modal-header">
          <div className="modal-title-row">
            <div className="approval-icon-box">
              <IconShieldAlert size={20} color="var(--color-warning)" />
            </div>
            <div>
              <h2 id="approval-title" className="modal-title">
                Permission Approval Required
              </h2>
              <p className="modal-subtitle">
                The agent proposes an operation requiring human authorization.
              </p>
            </div>
          </div>
          <button
            type="button"
            className="modal-close-btn"
            onClick={onClose}
            aria-label="Close dialog"
          >
            <IconX size={16} />
          </button>
        </div>

        <div className="modal-body">
          <div className="approval-details-grid">
            <div className="approval-detail-row">
              <span className="detail-label">Tool:</span>
              <span className="detail-value tool-badge">{approval.tool_name}</span>
            </div>

            <div className="approval-detail-row">
              <span className="detail-label">Operation:</span>
              <span className="detail-value">{approval.operation}</span>
            </div>

            <div className="approval-detail-row">
              <span className="detail-label">Risk Category:</span>
              <Badge variant={riskVariant} icon={<IconAlertTriangle size={12} />}>
                {approval.risk_category}
              </Badge>
            </div>

            {approval.working_dir && (
              <div className="approval-detail-row">
                <span className="detail-label">Working Directory:</span>
                <code className="detail-code">{approval.working_dir}</code>
              </div>
            )}

            {approval.file_path && (
              <div className="approval-detail-row">
                <span className="detail-label">Target File:</span>
                <code className="detail-code">{approval.file_path}</code>
              </div>
            )}

            {approval.command && (
              <div className="approval-detail-row">
                <span className="detail-label">Command:</span>
                <code className="detail-code command-code">{approval.command}</code>
              </div>
            )}

            <div className="approval-policy-reason">
              <span className="reason-title">Policy Assessment:</span>
              <p className="reason-text">{approval.reason}</p>
            </div>
          </div>
        </div>

        <div className="modal-footer">
          <Button
            variant="ghost"
            onClick={() => onReject(approval.call_id, 'Rejected by operator')}
          >
            Reject Operation
          </Button>
          <Button
            variant="primary"
            onClick={() => onApprove(approval.call_id)}
          >
            Authorize &amp; Execute
          </Button>
        </div>
      </div>
    </div>
  )
}
