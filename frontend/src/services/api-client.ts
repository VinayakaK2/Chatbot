/**
 * Centralized HTTP and SSE API Client for communicating with the backend runtime.
 * Never connects directly to external model servers or executes unauthorized commands.
 */

import type {
  HealthResponse,
  ModelReadinessResponse,
  TaskCreateRequest,
  TaskResponse,
  RuntimeEvent,
  ApiErrorResponse,
  FileContentResponse,
  GitDiffResponse,
  WorkspaceFileItem,
  WorkspaceInfo,
  BrowseDirectoryResponse,
  WorkspaceCommandResponse,
} from '../types/api'

// Base URL defaults to relative path in dev (handled by Vite reverse proxy) or env override
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
  async submitTask(request: TaskCreateRequest, signal?: AbortSignal): Promise<TaskResponse> {
    try {
      const res = await fetch(`${API_BASE_URL}/api/tasks`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Accept: 'application/json',
        },
        body: JSON.stringify(request),
        signal,
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

  /**
   * Cancel an in-progress or queued task.
   */
  async cancelTask(taskId: string): Promise<TaskResponse> {
    try {
      const res = await fetch(`${API_BASE_URL}/api/tasks/${encodeURIComponent(taskId)}/cancel`, {
        method: 'POST',
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
      throw new Error(`Failed to cancel task with ID: ${taskId}`)
    }
  },

  /**
   * Retrieve all recorded events for a task.
   */
  async getTaskEventHistory(taskId: string): Promise<RuntimeEvent[]> {
    try {
      const res = await fetch(`${API_BASE_URL}/api/tasks/${encodeURIComponent(taskId)}/events/history`, {
        method: 'GET',
        headers: { Accept: 'application/json' },
      })
      const body = await parseResponseBody(res)
      if (!res.ok) {
        throw new Error(extractErrorMessage(res.status, body))
      }
      return body as RuntimeEvent[]
    } catch (err) {
      if (err instanceof Error) {
        throw err
      }
      throw new Error(`Failed to fetch event history for task: ${taskId}`)
    }
  },

  /**
   * Subscribe to live execution events via Server-Sent Events (SSE).
   * Returns a cleanup unsubscribe callback.
   */
  subscribeToTaskEvents(
    taskId: string,
    onEvent: (event: RuntimeEvent) => void,
    onDone?: () => void,
    onError?: (err: Error) => void,
  ): () => void {
    const url = `${API_BASE_URL}/api/tasks/${encodeURIComponent(taskId)}/events`
    const eventSource = new EventSource(url)

    eventSource.onmessage = (e: MessageEvent) => {
      try {
        const parsed = JSON.parse(e.data) as RuntimeEvent
        onEvent(parsed)
      } catch (err) {
        console.error('Failed to parse SSE event data', err)
      }
    }

    eventSource.addEventListener('done', () => {
      eventSource.close()
      if (onDone) {
        onDone()
      }
    })

    eventSource.onerror = () => {
      eventSource.close()
      if (onError) {
        onError(new Error('SSE stream disconnected or encountered an error.'))
      }
    }

    return () => {
      eventSource.close()
    }
  },

  /**
   * Human decision authority: approve an eligible tool execution.
   */
  async approveToolCall(
    taskId: string,
    callId: string,
    reason?: string,
  ): Promise<{ status: string; task_id: string; call_id: string }> {
    try {
      const res = await fetch(`${API_BASE_URL}/api/tasks/${encodeURIComponent(taskId)}/approve`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Accept: 'application/json',
        },
        body: JSON.stringify({ call_id: callId, reason }),
      })
      const body = await parseResponseBody(res)
      if (!res.ok) {
        throw new Error(extractErrorMessage(res.status, body))
      }
      return body as { status: string; task_id: string; call_id: string }
    } catch (err) {
      if (err instanceof Error) {
        throw err
      }
      throw new Error('Approval request failed.')
    }
  },

  /**
   * Human decision authority: reject an eligible tool execution.
   */
  async rejectToolCall(
    taskId: string,
    callId: string,
    reason?: string,
  ): Promise<{ status: string; task_id: string; call_id: string }> {
    try {
      const res = await fetch(`${API_BASE_URL}/api/tasks/${encodeURIComponent(taskId)}/reject`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Accept: 'application/json',
        },
        body: JSON.stringify({ call_id: callId, reason }),
      })
      const body = await parseResponseBody(res)
      if (!res.ok) {
        throw new Error(extractErrorMessage(res.status, body))
      }
      return body as { status: string; task_id: string; call_id: string }
    } catch (err) {
      if (err instanceof Error) {
        throw err
      }
      throw new Error('Rejection request failed.')
    }
  },

  /**
   * Fetch bounded workspace file content and language detection for preview.
   */
  async getFileContent(path: string): Promise<FileContentResponse> {
    try {
      const res = await fetch(`${API_BASE_URL}/api/files/content?path=${encodeURIComponent(path)}`, {
        method: 'GET',
        headers: { Accept: 'application/json' },
      })
      const body = await parseResponseBody(res)
      if (!res.ok) {
        throw new Error(extractErrorMessage(res.status, body))
      }
      return body as FileContentResponse
    } catch (err) {
      if (err instanceof Error) {
        throw err
      }
      throw new Error(`Failed to load file content for '${path}'.`)
    }
  },

  /**
   * Retrieve structured git diff and repository file change metrics.
   */
  async getGitDiff(): Promise<GitDiffResponse> {
    try {
      const res = await fetch(`${API_BASE_URL}/api/files/diff`, {
        method: 'GET',
        headers: { Accept: 'application/json' },
      })
      const body = await parseResponseBody(res)
      if (!res.ok) {
        throw new Error(extractErrorMessage(res.status, body))
      }
      return body as GitDiffResponse
    } catch (err) {
      if (err instanceof Error) {
        throw err
      }
      throw new Error('Failed to retrieve git diff.')
    }
  },

  /**
   * List available markdown reports and workspace documents for quick tab opening.
   */
  async getAvailableDocuments(allFiles = false, search?: string): Promise<WorkspaceFileItem[]> {
    try {
      const queryParams = new URLSearchParams()
      if (allFiles) queryParams.set('all_files', 'true')
      if (search) queryParams.set('search', search)
      const queryString = queryParams.toString() ? `?${queryParams.toString()}` : ''
      const res = await fetch(`${API_BASE_URL}/api/files/list${queryString}`, {
        method: 'GET',
        headers: { Accept: 'application/json' },
      })
      const body = await parseResponseBody(res)
      if (!res.ok) {
        throw new Error(extractErrorMessage(res.status, body))
      }
      return body as WorkspaceFileItem[]
    } catch (err) {
      if (err instanceof Error) {
        throw err
      }
      return []
    }
  },

  /**
   * Execute an interactive local PowerShell terminal command in the workspace.
   */
  async executeCommand(
    command: string,
    workingDir?: string,
    timeoutSeconds?: number
  ): Promise<WorkspaceCommandResponse> {
    try {
      const res = await fetch(`${API_BASE_URL}/api/workspace/execute-command`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Accept: 'application/json',
        },
        body: JSON.stringify({
          command,
          working_dir: workingDir,
          timeout_seconds: timeoutSeconds,
        }),
      })
      const body = await parseResponseBody(res)
      if (!res.ok) {
        throw new Error(extractErrorMessage(res.status, body))
      }
      return body as WorkspaceCommandResponse
    } catch (err) {
      if (err instanceof Error) {
        throw err
      }
      throw new Error(`Failed to execute command: ${command}`)
    }
  },

  /**
   * Retrieve active Phase 4 workspace configuration, limits, and registered tools.
   */
  async getWorkspaceInfo(): Promise<WorkspaceInfo> {
    try {
      const res = await fetch(`${API_BASE_URL}/api/workspace`, {
        method: 'GET',
        headers: { Accept: 'application/json' },
      })
      const body = await parseResponseBody(res)
      if (!res.ok) {
        throw new Error(extractErrorMessage(res.status, body))
      }
      return body as WorkspaceInfo
    } catch (err) {
      if (err instanceof Error) {
        throw err
      }
      throw new Error('Failed to retrieve workspace configuration.')
    }
  },

  /**
   * Atomically switch or select active workspace root folder.
   */
  async selectWorkspace(path: string): Promise<WorkspaceInfo> {
    try {
      const res = await fetch(`${API_BASE_URL}/api/workspace/select`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Accept: 'application/json',
        },
        body: JSON.stringify({ path }),
      })
      const body = await parseResponseBody(res)
      if (!res.ok) {
        throw new Error(extractErrorMessage(res.status, body))
      }
      return body as WorkspaceInfo
    } catch (err) {
      if (err instanceof Error) {
        throw err
      }
      throw new Error(`Failed to switch workspace to '${path}'.`)
    }
  },

  /**
   * Browse subdirectories for folder selection dialog.
   */
  async browseDirectories(basePath?: string): Promise<BrowseDirectoryResponse> {
    try {
      const url = basePath
        ? `${API_BASE_URL}/api/workspace/directories?base_path=${encodeURIComponent(basePath)}`
        : `${API_BASE_URL}/api/workspace/directories`
      const res = await fetch(url, {
        method: 'GET',
        headers: { Accept: 'application/json' },
      })
      const body = await parseResponseBody(res)
      if (!res.ok) {
        throw new Error(extractErrorMessage(res.status, body))
      }
      return body as BrowseDirectoryResponse
    } catch (err) {
      if (err instanceof Error) {
        throw err
      }
      throw new Error('Failed to browse directories.')
    }
  },

  /**
   * Retrieve recent workspaces list.
   */
  async getRecentWorkspaces(): Promise<string[]> {
    try {
      const res = await fetch(`${API_BASE_URL}/api/workspace/recent`, {
        method: 'GET',
        headers: { Accept: 'application/json' },
      })
      const body = await parseResponseBody(res)
      if (!res.ok) {
        throw new Error(extractErrorMessage(res.status, body))
      }
      return body as string[]
    } catch (err) {
      if (err instanceof Error) {
        throw err
      }
      return []
    }
  },
}
