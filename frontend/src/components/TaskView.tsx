/**
 * Component rendering task execution state, model outputs, token metrics, and errors.
 */

import React from 'react'
import type { TaskResponse } from '../types/api'

interface TaskViewProps {
  task: TaskResponse | null
  onClear: () => void
}

export const TaskView: React.FC<TaskViewProps> = ({ task, onClear }) => {
  if (!task) {
    return (
      <section className="card empty-state" aria-label="Task Results">
        <p className="empty-text">No active task. Submit a prompt above to begin.</p>
      </section>
    )
  }

  // Determine badge styling based on execution status
  const getStatusBadgeClass = () => {
    switch (task.status) {
      case 'completed':
        return 'badge-success'
      case 'executing':
      case 'pending':
        return 'badge-warning'
      case 'failed':
        return 'badge-danger'
      default:
        return 'badge-neutral'
    }
  }

  return (
    <section className="card task-view" aria-label="Task Execution Details">
      <div className="card-header">
        <div className="task-header-meta">
          <h2 className="card-title">Task Result</h2>
          <span className={`badge ${getStatusBadgeClass()}`}>
            {task.status.toUpperCase()}
          </span>
        </div>
        <button
          type="button"
          onClick={onClear}
          className="btn btn-secondary btn-sm"
          aria-label="Clear current task results"
        >
          Clear
        </button>
      </div>

      <div className="task-meta-grid">
        <div className="meta-item">
          <span className="meta-label">Task ID:</span>
          <code className="meta-value">{task.task_id}</code>
        </div>
        {task.model && (
          <div className="meta-item">
            <span className="meta-label">Model:</span>
            <span className="meta-value">{task.model}</span>
          </div>
        )}
        {task.finish_reason && (
          <div className="meta-item">
            <span className="meta-label">Finish Reason:</span>
            <span className="meta-value">{task.finish_reason}</span>
          </div>
        )}
      </div>

      {task.status === 'executing' && (
        <div className="loading-container" role="status" aria-live="polite">
          <div className="spinner" aria-hidden="true" />
          <p>Sending request to model and awaiting completion...</p>
        </div>
      )}

      {task.response_text && (
        <div className="response-container">
          <h3 className="section-subtitle">Normalized Model Response:</h3>
          <div className="response-box">
            <pre className="response-text">{task.response_text}</pre>
          </div>
        </div>
      )}

      {task.usage && (
        <div className="usage-section">
          <span className="usage-title">Token Usage:</span>
          <div className="usage-tags">
            {task.usage.prompt_tokens !== undefined && (
              <span className="usage-tag">Prompt: {task.usage.prompt_tokens}</span>
            )}
            {task.usage.completion_tokens !== undefined && (
              <span className="usage-tag">Completion: {task.usage.completion_tokens}</span>
            )}
            {task.usage.total_tokens !== undefined && (
              <span className="usage-tag">Total: {task.usage.total_tokens}</span>
            )}
          </div>
        </div>
      )}

      {task.error && (
        <div className="alert alert-danger" role="alert">
          <strong>Task Execution Error:</strong> {task.error}
        </div>
      )}
    </section>
  )
}
