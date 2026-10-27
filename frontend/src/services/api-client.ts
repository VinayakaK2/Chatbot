/**
 * Centralized HTTP API client for communicating with the backend runtime.
 * Never connects directly to external model servers or exposes credentials.
 */

import type {
  HealthResponse,
  ModelReadinessResponse,
  TaskCreateRequest,
  TaskResponse,
  ApiErrorResponse,
} from '../types/api'

// Base URL defaults to relative path in dev (handled by Vite proxy) or env override
const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || ''

// Helper to safely parse JSON or return raw text on unexpected formats
async function parseResponseBody(response: Response): Promise<unknown> {
  const contentType = response.headers.get('content-type') || ''
  if (contentType.includes('application/json')) {
    try {
      return await response.json()
    } catch {
      return null
    }
  }
  return await response.text()
}

// Convert HTTP failures into user-actionable error messages
function extractErrorMessage(status: number, body: unknown): string {
  if (body && typeof body === 'object') {
    const errorObj = body as Partial<ApiErrorResponse> & { detail?: string }
    if (errorObj.message) {
      if (Array.isArray(errorObj.details) && errorObj.details.length > 0) {
        const detailsStr = errorObj.details.map((d) => `${d.field}: ${d.message}`).join(', ')
        return `${errorObj.message} (${detailsStr})`
      }
      return errorObj.message
    }
    if (errorObj.detail) {
      return errorObj.detail
    }
  }
  return `Server returned error status ${status}`
}

export const apiClient = {
  /**
   * Fetch backend process liveness and configuration state.
   */
  async getHealth(): Promise<HealthResponse> {
    try {
      const res = await fetch(`${API_BASE_URL}/api/health`, {
        method: 'GET',
        headers: { Accept: 'application/json' },
      })
      const body = await parseResponseBody(res)
      if (!res.ok) {
        throw new Error(extractErrorMessage(res.status, body))
      }
      return body as HealthResponse
    } catch (err) {
      if (err instanceof Error) {
        throw err
      }
      throw new Error('Unable to connect to backend server. Is it running?')
    }
  },

  /**
   * Probe upstream model readiness through the backend API.
   */
  async getReadiness(): Promise<ModelReadinessResponse> {
    try {
      const res = await fetch(`${API_BASE_URL}/api/readiness`, {
        method: 'GET',
        headers: { Accept: 'application/json' },
      })
      const body = await parseResponseBody(res)
      if (!res.ok) {
        throw new Error(extractErrorMessage(res.status, body))
      }
      return body as ModelReadinessResponse
    } catch (err) {
      if (err instanceof Error) {
        throw err
      }
      throw new Error('Model readiness probe failed due to a network connection error.')
    }
  },

  /**
   * Submit a user prompt to the backend task execution pipeline.
   */
  async submitTask(request: TaskCreateRequest): Promise<TaskResponse> {
    try {
      const res = await fetch(`${API_BASE_URL}/api/tasks`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Accept: 'application/json',
        },
        body: JSON.stringify(request),
      })
      const body = await parseResponseBody(res)
      if (!res.ok) {
        throw new Error(extractErrorMessage(res.status, body))
      }
      return body as TaskResponse
    } catch (err) {
      if (err instanceof Error) {
        throw err
      }
      throw new Error('Task submission failed due to an unexpected error.')
    }
  },

  /**
   * Retrieve current status and results of a task by its UUID.
   */
  async getTask(taskId: string): Promise<TaskResponse> {
    try {
      const res = await fetch(`${API_BASE_URL}/api/tasks/${encodeURIComponent(taskId)}`, {
        method: 'GET',
        headers: { Accept: 'application/json' },
      })
      const body = await parseResponseBody(res)
      if (!res.ok) {
        throw new Error(extractErrorMessage(res.status, body))
      }
      return body as TaskResponse
    } catch (err) {
      if (err instanceof Error) {
        throw err
      }
      throw new Error(`Failed to retrieve task with ID: ${taskId}`)
    }
  },
}
