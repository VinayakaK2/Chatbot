/**
 * Type definitions for API contracts matching the backend Pydantic schemas.
 */

export type HealthStatusType = 'healthy' | 'degraded' | 'unhealthy'

export interface HealthResponse {
  status: HealthStatusType
  version: string
  timestamp: string
  model_configured: boolean
  model_id: string | null
}

export type ReadinessStatusType = 'ready' | 'unconfigured' | 'unreachable' | 'error'

export interface ModelReadinessResponse {
  status: ReadinessStatusType
  model_name: string | null
  endpoint: string | null
  message: string
  checked_at: string
}

export type TaskStatusType = 'pending' | 'executing' | 'completed' | 'failed'

export interface TaskCreateRequest {
  prompt: string
  max_tokens?: number
  temperature?: number
}

export interface TokenUsage {
  prompt_tokens?: number
  completion_tokens?: number
  total_tokens?: number
  [key: string]: number | undefined
}

export interface TaskResponse {
  task_id: string
  prompt: string
  status: TaskStatusType
  response_text: string | null
  finish_reason: string | null
  model: string | null
  usage: TokenUsage | null
  error: string | null
  created_at: string
  completed_at: string | null
}

export interface ApiValidationErrorDetail {
  field: string
  message: string
}

export interface ApiErrorResponse {
  error: string
  message: string
  details?: ApiValidationErrorDetail[]
}
