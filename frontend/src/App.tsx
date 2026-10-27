/**
 * Main application component orchestrating GUI layout and runtime API communication.
 */

import React, { useEffect, useState } from 'react'
import { apiClient } from './services/api-client'
import { HealthStatus } from './components/HealthStatus'
import { TaskForm } from './components/TaskForm'
import { TaskView } from './components/TaskView'
import type {
  HealthResponse,
  ModelReadinessResponse,
  TaskCreateRequest,
  TaskResponse,
} from './types/api'

export const App: React.FC = () => {
  const [health, setHealth] = useState<HealthResponse | null>(null)
  const [readiness, setReadiness] = useState<ModelReadinessResponse | null>(null)
  const [isHealthLoading, setIsHealthLoading] = useState<boolean>(false)
  const [healthError, setHealthError] = useState<string | null>(null)

  const [currentTask, setCurrentTask] = useState<TaskResponse | null>(null)
  const [isSubmitting, setIsSubmitting] = useState<boolean>(false)
  const [appError, setAppError] = useState<string | null>(null)

  // Fetch backend health and model readiness
  const refreshSystemStatus = async () => {
    setIsHealthLoading(true)
    setHealthError(null)
    try {
      const [healthData, readinessData] = await Promise.all([
        apiClient.getHealth(),
        apiClient.getReadiness(),
      ])
      setHealth(healthData)
      setReadiness(readinessData)
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to reach runtime backend.'
      setHealthError(message)
    } finally {
      setIsHealthLoading(false)
    }
  }

  // Initial status probe on mount
  useEffect(() => {
    refreshSystemStatus()
  }, [])

  // Handle task prompt submission from the GUI form
  const handleTaskSubmit = async (request: TaskCreateRequest) => {
    setIsSubmitting(true)
    setAppError(null)

    // Set optimistic executing state
    const optimisticTask: TaskResponse = {
      task_id: 'submitting...',
      prompt: request.prompt,
      status: 'executing',
      response_text: null,
      finish_reason: null,
      model: health?.model_id || null,
      usage: null,
      error: null,
      created_at: new Date().toISOString(),
      completed_at: null,
    }
    setCurrentTask(optimisticTask)

    try {
      const response = await apiClient.submitTask(request)
      setCurrentTask(response)
    } catch (err) {
      const errorMessage = err instanceof Error ? err.message : 'Task submission failed.'
      setAppError(errorMessage)
      setCurrentTask({
        ...optimisticTask,
        status: 'failed',
        error: errorMessage,
      })
    } finally {
      setIsSubmitting(false)
    }
  }

  // Clear current active task result
  const handleClearTask = () => {
    setCurrentTask(null)
    setAppError(null)
  }

  return (
    <div className="app-container">
      <header className="app-header">
        <div className="header-content">
          <h1 className="app-title">Agent Runtime GUI</h1>
          <p className="app-subtitle">
            Autonomous Coding Agent Control Surface — Phase 0 &amp; Phase 1
          </p>
        </div>
      </header>

      <main className="main-content">
        {appError && (
          <div className="alert alert-danger global-alert" role="alert">
            <strong>System Error:</strong> {appError}
          </div>
        )}

        <HealthStatus
          health={health}
          readiness={readiness}
          isLoading={isHealthLoading}
          error={healthError}
          onRefresh={refreshSystemStatus}
        />

        <TaskForm
          onSubmit={handleTaskSubmit}
          isSubmitting={isSubmitting}
        />

        <TaskView
          task={currentTask}
          onClear={handleClearTask}
        />
      </main>

      <footer className="app-footer">
        <p>Coding Agent Runtime — Qwen2.5-Coder-14B-Instruct via vLLM OpenAI-Compatible Backend</p>
      </footer>
    </div>
  )
}

export default App
