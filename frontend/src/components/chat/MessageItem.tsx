/**
 * Message Item Component.
 * Implements refined right-aligned user bubble cards and left-aligned assistant responses
 * with live streaming markdown, tool execution activities, and verification results.
 */

import React, { useState, useCallback } from 'react'
import type { Message, ToolActivity } from '../../types/api'
import { MarkdownRenderer } from './MarkdownRenderer'
import { ExecutionActivity } from '../activity/ExecutionActivity'
import {
  IconCheck,
  IconCopy,
  IconFile,
  IconRotateCcw,
  IconXCircle,
} from '../common/Icons'

export interface MessageItemProps {
  message: Message
  onInspectArtifact?: (activity: ToolActivity) => void
  onApproveTool?: (callId: string) => void
  onRejectTool?: (callId: string) => void
}

/**
 * Formats a message timestamp into a compact human-readable relative time (e.g. "17d ago", "5m ago").
 */
function formatMessageTime(isoString: string): string {
  try {
    const date = new Date(isoString)
    const now = new Date()
    const diffMs = now.getTime() - date.getTime()
    if (diffMs < 0) return 'just now'
    const diffMins = Math.floor(diffMs / 60000)
    const diffHours = Math.floor(diffMins / 60)
    const diffDays = Math.floor(diffHours / 24)

    if (diffMins < 1) return 'just now'
    if (diffMins < 60) return `${diffMins}m ago`
    if (diffHours < 24) return `${diffHours}h ago`
    if (diffDays < 30) return `${diffDays}d ago`
    return date.toLocaleDateString([], { month: 'short', day: 'numeric' })
  } catch {
    return ''
  }
}

export const MessageItem: React.FC<MessageItemProps> = ({
  message,
  onInspectArtifact,
  onApproveTool,
  onRejectTool,
}) => {
  const isUser = message.role === 'user'
  const [copied, setCopied] = useState(false)

  // Copy message content to clipboard with temporary feedback
  const handleCopy = useCallback(async () => {
    try {
      if (navigator.clipboard) {
        await navigator.clipboard.writeText(message.content)
        setCopied(true)
        setTimeout(() => setCopied(false), 1600)
      }
    } catch (err) {
      console.warn('Clipboard copy failed:', err)
    }
  }, [message.content])

  // 1. User Message: Right-aligned card bubble with metadata & action buttons
  if (isUser) {
    return (
      <div className="message-row message-user">
        <div className="user-message-card">
          {message.attachments && message.attachments.length > 0 && (
            <div className="user-attachments-list">
              {message.attachments.map((file) => (
                <div key={file.id} className="user-attachment-chip">
                  <IconFile size={13} color="var(--accent-primary)" />
                  <span className="attachment-name">{file.name}</span>
                  <span className="attachment-size">
                    ({Math.round(file.size / 1024)} KB)
                  </span>
                </div>
              ))}
            </div>
          )}

          <div className="user-message-body">
            <MarkdownRenderer content={message.content} />
          </div>
        </div>

        <div className="user-message-meta">
          <span className="user-message-time">
            {formatMessageTime(message.timestamp)}
          </span>
          <button
            type="button"
            className="user-meta-btn"
            onClick={handleCopy}
            title={copied ? 'Copied' : 'Copy prompt'}
            aria-label="Copy prompt"
          >
            {copied ? (
              <IconCheck size={12} color="var(--accent-hover)" />
            ) : (
              <IconCopy size={12} />
            )}
          </button>
          <button
            type="button"
            className="user-meta-btn"
            onClick={handleCopy}
            title="Retry / Edit"
            aria-label="Retry / Edit"
          >
            <IconRotateCcw size={12} />
          </button>
        </div>
      </div>
    )
  }

  // 2. Assistant Message: Left-aligned full-width response with tool activity & verification
  // If completed with no text, no activities, and no error, avoid rendering an empty ghost container
  if (
    !message.content &&
    message.status === 'completed' &&
    !message.error &&
    (!message.tool_activities || message.tool_activities.length === 0)
  ) {
    return null
  }

  return (
    <div className="message-row message-assistant">
      <div className="assistant-message-container">
        {/* Execution Activity (Step 2 - 7) */}
        <ExecutionActivity
          status={message.status}
          model={message.model}
          toolActivities={message.tool_activities}
          verification={message.verification}
          onInspectArtifact={onInspectArtifact}
        />

        {/* Inline Pending Approval Alert */}
        {message.pending_approval && (
          <div className="inline-approval-alert" role="alert">
            <div className="approval-alert-header">
              <strong>Authorization Requested:</strong> {message.pending_approval.operation}
            </div>
            <p className="approval-alert-reason">{message.pending_approval.reason}</p>
            {message.pending_approval.command && (
              <code className="approval-alert-code">{message.pending_approval.command}</code>
            )}
            <div className="approval-alert-actions">
              <button
                type="button"
                className="btn-danger-outline"
                onClick={() => onRejectTool && onRejectTool(message.pending_approval!.call_id)}
              >
                Deny
              </button>
              <button
                type="button"
                className="btn-primary-sm"
                onClick={() => onApproveTool && onApproveTool(message.pending_approval!.call_id)}
              >
                Authorize
              </button>
            </div>
          </div>
        )}

        {/* Markdown Content */}
        {message.content ? (
          <div className="message-body">
            <MarkdownRenderer
              content={message.content}
              isStreaming={message.status === 'executing'}
            />
          </div>
        ) : message.status === 'executing' ? (
          <div className="message-body">
            <span className="streaming-cursor" aria-hidden="true" />
          </div>
        ) : null}

        {/* Error notice if failed */}
        {message.error && (
          <div className="message-error-box" role="alert">
            <IconXCircle size={15} color="var(--color-danger)" />
            <span>{message.error}</span>
          </div>
        )}
      </div>
    </div>
  )
}
