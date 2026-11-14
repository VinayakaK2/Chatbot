/**
 * ConversationView — Centered content column, auto-scroll, 800px max-width.
 * The conversation is the center of the application.
 */

import React, { useEffect, useRef } from 'react'
import type { Message, ToolActivity } from '../../types/api'
import { MessageItem } from './MessageItem'

export interface ConversationViewProps {
  messages: Message[]
  isExecuting?: boolean
  onInspectArtifact?: (activity: ToolActivity) => void
  onApproveTool?: (callId: string) => void
  onRejectTool?: (callId: string) => void
}

export const ConversationView: React.FC<ConversationViewProps> = ({
  messages,
  isExecuting = false,
  onInspectArtifact,
  onApproveTool,
  onRejectTool,
}) => {
  const scrollEndRef = useRef<HTMLDivElement>(null)

  // Auto-scroll to bottom smoothly on message change or streaming
  useEffect(() => {
    scrollEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, isExecuting])

  return (
    <div className="conversation-scroll-area" role="log" aria-label="Conversation history">
      <div className="conversation-content-column">
        {messages.map((msg) => (
          <MessageItem
            key={msg.id}
            message={msg}
            onInspectArtifact={onInspectArtifact}
            onApproveTool={onApproveTool}
            onRejectTool={onRejectTool}
          />
        ))}
        {/* Bottom padding so last message is not behind the composer */}
        <div style={{ height: '32px' }} />
        <div ref={scrollEndRef} />
      </div>
    </div>
  )
}
