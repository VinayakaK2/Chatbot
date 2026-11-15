/**
 * Task Stream & Execution State Hook.
 * Manages task dispatch, SSE event streaming, event deduplication,
 * approval mediation, and cancellation.
 */

import { useState, useRef, useCallback } from 'react'
import { apiClient } from '../services/api-client'
import type {
  Message,
  RuntimeEvent,
  TaskResponse,
  ToolActivity,
  PendingApproval,
  VerificationRecord,
} from '../types/api'

export function useTaskStream(
  onUpdateMessage: (messageId: string, updater: (msg: Message) => Message) => void,
  onNotify?: (message: string, type: 'info' | 'success' | 'warning' | 'error') => void
) {
  const [activeTaskId, setActiveTaskId] = useState<string | null>(null)
  const [isExecuting, setIsExecuting] = useState(false)
  const [pendingApproval, setPendingApproval] = useState<PendingApproval | null>(null)
  const activeUnsubscribeRef = useRef<(() => void) | null>(null)
  const processedEventIds = useRef<Set<string>>(new Set())
  const isCancelledRef = useRef(false)
  const isStreamingRef = useRef(false)
  const activeTaskIdRef = useRef<string | null>(null)
  const abortControllerRef = useRef<AbortController | null>(null)
  const activeMessageIdRef = useRef<string | null>(null)

  const handleEvent = useCallback(
    (messageId: string, event: RuntimeEvent) => {
      // Deduplicate events
      if (processedEventIds.current.has(event.event_id)) return
      processedEventIds.current.add(event.event_id)

      onUpdateMessage(messageId, (msg) => {
        const payload = event.payload || {}

        switch (event.event_type) {
          case 'task_created':
            return {
              ...msg,
              status: 'pending',
            }

          case 'model_request_started':
            return {
              ...msg,
              status: 'executing',
              model: (payload.model as string) || msg.model,
            }

          case 'tool_requested': {
            const callId = (payload.call_id as string) || `call-${Date.now()}`
            const newActivity: ToolActivity = {
              id: event.event_id,
              call_id: callId,
              tool_name: (payload.tool_name as string) || 'tool',
              operation: (payload.operation as string) || 'Executing tool operation',
              file_path: payload.file_path as string | undefined,
              command: payload.command as string | undefined,
              arguments: payload.arguments as Record<string, unknown> | undefined,
              risk_category: (payload.risk_category as any) || 'read_only',
              permission_decision: (payload.permission_decision as any) || 'allowed',
              status: 'running',
              timestamp: event.timestamp,
            }

            const existing = msg.tool_activities || []
            const filtered = existing.filter((a) => a.call_id !== callId)
            return {
              ...msg,
              tool_activities: [...filtered, newActivity],
            }
          }

          case 'approval_needed': {
            const approval: PendingApproval = {
              call_id: (payload.call_id as string) || `call-${Date.now()}`,
              tool_name: (payload.tool_name as string) || 'tool',
              operation: (payload.operation as string) || 'Sensitive Operation',
              file_path: payload.file_path as string | undefined,
              command: payload.command as string | undefined,
              working_dir: payload.working_dir as string | undefined,
              risk_category: (payload.risk_category as any) || 'workspace_write',
              reason: (payload.reason as string) || 'Security policy requires user confirmation.',
            }
            setPendingApproval(approval)
            return {
              ...msg,
              pending_approval: approval,
            }
          }

          case 'permission_denied': {
            const callId = payload.call_id as string
            const updatedActivities = (msg.tool_activities || []).map((act) => {
              if (act.call_id === callId) {
                return {
                  ...act,
                  permission_decision: 'denied' as const,
                  status: 'denied' as const,
                  error: (payload.reason as string) || 'Denied by policy or user',
                }
              }
              return act
            })
            return {
              ...msg,
              tool_activities: updatedActivities,
              pending_approval: undefined,
            }
          }

          case 'tool_completed': {
            const callId = payload.call_id as string
            const updatedActivities = (msg.tool_activities || []).map((act) => {
              if (act.call_id === callId) {
                return {
                  ...act,
                  status: (payload.success ? 'completed' : 'failed') as any,
                  output: payload.output as string | undefined,
                  error: payload.error as string | undefined,
                  exit_code: payload.exit_code as number | undefined,
                  duration_ms: payload.duration_ms as number | undefined,
                }
              }
              return act
            })
            return {
              ...msg,
              tool_activities: updatedActivities,
            }
          }

          case 'verification_completed': {
            const verificationRecord: VerificationRecord = {
              status: (payload.status as any) || 'inconclusive',
              checks: (payload.checks as any) || [],
              summary: (payload.summary as string) || 'Verification executed',
              timestamp: event.timestamp,
            }
            return {
              ...msg,
              verification: verificationRecord,
            }
          }

          case 'task_terminal': {
            const isCompleted = payload.status === 'completed'
            const termDuration = typeof payload.duration_seconds === 'number' ? payload.duration_seconds : undefined
            return {
              ...msg,
              status: isCompleted ? 'completed' : 'failed',
              error: payload.error as string | undefined,
              usage: (payload.usage as any) || msg.usage,
              duration_seconds: termDuration ?? msg.duration_seconds,
            }
          }

          default:
            return msg
        }
      })
    },
    [onUpdateMessage]
  )

  const executeTask = useCallback(
    async (
      assistantMessageId: string,
      prompt: string,
      modelId: string
    ) => {
      setIsExecuting(true)
      isCancelledRef.current = false
      activeMessageIdRef.current = assistantMessageId
      processedEventIds.current.clear()
      const taskStartTime = Date.now()

      // Generate client-side task ID up front for immediate tracking and cancellation
      const clientTaskId =
        typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function'
          ? crypto.randomUUID()
          : `task-${Date.now()}-${Math.random().toString(36).substring(2, 9)}`

      setActiveTaskId(clientTaskId)
      activeTaskIdRef.current = clientTaskId

      const abortController = new AbortController()
      abortControllerRef.current = abortController

      // Initial setup for message: mark executing so live status and cursor show
      onUpdateMessage(assistantMessageId, (msg) => ({
        ...msg,
        task_id: clientTaskId,
        status: 'executing',
        model: modelId,
        content: '',
        error: undefined,
        usage: undefined,
      }))

      // Step 1: Subscribe to live SSE events immediately for this task ID
      const unsubscribe = apiClient.subscribeToTaskEvents(
        clientTaskId,
        (event) => handleEvent(assistantMessageId, event),
        () => {
          if (!isStreamingRef.current && !isCancelledRef.current) {
            setIsExecuting(false)
            setActiveTaskId(null)
            activeTaskIdRef.current = null
          }
        },
        (err) => {
          console.warn('SSE stream notice:', err.message)
          if (!isStreamingRef.current && !isCancelledRef.current) {
            setIsExecuting(false)
            setActiveTaskId(null)
            activeTaskIdRef.current = null
          }
        }
      )
      activeUnsubscribeRef.current = unsubscribe

      try {
        // Step 2: Submit task through backend API contract with clientTaskId and AbortSignal
        const taskResponse: TaskResponse = await apiClient.submitTask(
          {
            prompt,
            task_id: clientTaskId,
            max_tokens: 16384,
            temperature: 0.2,
          },
          abortController.signal
        )

        // If cancelled while request was in-flight, exit cleanly
        if (isCancelledRef.current) return

        const fullText = taskResponse.response_text || ''

        // Step 3: Stream tokens continuously with natural, readable pacing
        if (fullText && !taskResponse.error) {
          isStreamingRef.current = true
          // Brief natural pause (~600ms) so "Working" state is established comfortably
          await new Promise((resolve) => setTimeout(resolve, 600))
          if (isCancelledRef.current) return

          // Break into word tokens with trailing punctuation/space
          const tokens = fullText.match(/\S+\s*|\s+/g) || [fullText]
          // Calm, comfortable reading cadence (~38ms per token)
          const baseDelayMs = 38

          let accumulated = ''
          for (let i = 0; i < tokens.length; i++) {
            if (isCancelledRef.current) break
            const token = tokens[i]
            accumulated += token
            onUpdateMessage(assistantMessageId, (msg) => ({
              ...msg,
              content: accumulated,
              status: 'executing',
            }))

            // Slight natural cadence pause on punctuation and line breaks
            const hasPunctuation = /[.!?:\n]$/.test(token.trim())
            const pause = hasPunctuation ? baseDelayMs + 45 : baseDelayMs
            await new Promise((resolve) => setTimeout(resolve, pause))
          }
          isStreamingRef.current = false
        }

        // Finalize message status once streaming is complete
        if (!isCancelledRef.current) {
          const clientDurationSec = Math.max(1, Math.round((Date.now() - taskStartTime) / 1000))
          const resolvedDuration =
            typeof taskResponse.duration_seconds === 'number' && taskResponse.duration_seconds > 0
              ? taskResponse.duration_seconds
              : clientDurationSec

          onUpdateMessage(assistantMessageId, (msg) => ({
            ...msg,
            status: taskResponse.status === 'executing' ? 'completed' : taskResponse.status,
            content: fullText || msg.content,
            duration_seconds: msg.duration_seconds ?? resolvedDuration,
          }))
          setIsExecuting(false)
          setActiveTaskId(null)
          activeTaskIdRef.current = null
        }
      } catch (err) {
        if (isCancelledRef.current) {
          // If aborted by user, clean up silently without overriding cancelled status
          setIsExecuting(false)
          setActiveTaskId(null)
          activeTaskIdRef.current = null
          return
        }
        isStreamingRef.current = false
        const errorMsg = err instanceof Error ? err.message : 'Task execution failed.'
        const failDurationSec = Math.max(1, Math.round((Date.now() - taskStartTime) / 1000))
        onUpdateMessage(assistantMessageId, (msg) => ({
          ...msg,
          status: 'failed',
          error: errorMsg,
          duration_seconds: msg.duration_seconds ?? failDurationSec,
        }))
        if (onNotify) {
          onNotify(errorMsg, 'error')
        }
        setIsExecuting(false)
        setActiveTaskId(null)
        activeTaskIdRef.current = null
      }
    },
    [handleEvent, onUpdateMessage, onNotify]
  )

  const cancelActiveTask = useCallback(async () => {
    isCancelledRef.current = true
    isStreamingRef.current = false
    const targetTaskId = activeTaskIdRef.current || activeTaskId
    const targetMsgId = activeMessageIdRef.current

    if (abortControllerRef.current) {
      abortControllerRef.current.abort()
      abortControllerRef.current = null
    }

    if (activeUnsubscribeRef.current) {
      activeUnsubscribeRef.current()
      activeUnsubscribeRef.current = null
    }

    if (targetMsgId) {
      onUpdateMessage(targetMsgId, (msg) => ({
        ...msg,
        status: 'cancelled',
        error: 'Task was cancelled by user.',
      }))
    }

    if (targetTaskId) {
      try {
        await apiClient.cancelTask(targetTaskId)
        if (onNotify) {
          onNotify('Task was cancelled by user.', 'info')
        }
      } catch (err) {
        console.error('Failed to cancel task on backend', err)
      }
    }

    setIsExecuting(false)
    setActiveTaskId(null)
    activeTaskIdRef.current = null
    setPendingApproval(null)
  }, [activeTaskId, onNotify, onUpdateMessage])

  const approveTool = useCallback(
    async (callId: string) => {
      if (!activeTaskId) return
      try {
        await apiClient.approveToolCall(activeTaskId, callId)
        setPendingApproval(null)
        if (onNotify) {
          onNotify('Operation approved.', 'success')
        }
      } catch (err) {
        const message = err instanceof Error ? err.message : 'Approval failed.'
        if (onNotify) {
          onNotify(message, 'error')
        }
      }
    },
    [activeTaskId, onNotify]
  )

  const rejectTool = useCallback(
    async (callId: string, reason?: string) => {
      if (!activeTaskId) return
      try {
        await apiClient.rejectToolCall(activeTaskId, callId, reason)
        setPendingApproval(null)
        if (onNotify) {
          onNotify('Operation rejected.', 'info')
        }
      } catch (err) {
        const message = err instanceof Error ? err.message : 'Rejection failed.'
        if (onNotify) {
          onNotify(message, 'error')
        }
      }
    },
    [activeTaskId, onNotify]
  )

  return {
    activeTaskId,
    isExecuting,
    pendingApproval,
    executeTask,
    cancelActiveTask,
    approveTool,
    rejectTool,
    clearPendingApproval: () => setPendingApproval(null),
  }
}
