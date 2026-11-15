/**
 * Comprehensive API and Runtime Contract Type Definitions.
 * Aligned with backend Pydantic schemas, 2604.14228v3.pdf 5-layer architecture, and phases.md.
 */

// ==============================================================================
// Health and Readiness Schemas
// ==============================================================================

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

// ==============================================================================
// Task and Execution Schemas
// ==============================================================================

export type TaskStatusType = 'pending' | 'executing' | 'completed' | 'failed' | 'cancelled' | 'limit_reached'

export interface TaskCreateRequest {
  prompt: string
  task_id?: string
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
  iterations?: number | null
  tool_calls_count?: number | null
  termination_reason?: string | null
  created_at: string
  completed_at: string | null
  duration_seconds?: number | null
}

// ==============================================================================
// Runtime Events
// ==============================================================================

export type RuntimeEventType =
  | 'task_created'
  | 'model_request_started'
  | 'model_response_received'
  | 'tool_requested'
  | 'tool_validated'
  | 'tool_rejected'
  | 'permission_denied'
  | 'approval_needed'
  | 'tool_execution_started'
  | 'tool_completed'
  | 'tool_execution_failed'
  | 'verification_completed'
  | 'task_cancelled'
  | 'task_limit_reached'
  | 'task_terminal'

export interface RuntimeEvent {
  event_id: string
  task_id: string
  event_type: RuntimeEventType
  timestamp: string
  payload: Record<string, unknown>
}

// ==============================================================================
// Tool Activity, Safety & Verification
// ==============================================================================

export type ToolRiskCategory =
  | 'read_only'
  | 'workspace_write'
  | 'command_execution'
  | 'destructive'
  | 'system_probe'

export type PermissionDecision =
  | 'allowed'
  | 'approval_required'
  | 'denied'
  | 'blocked_by_policy'

export interface DiffLine {
  type: 'add' | 'delete' | 'context'
  content: string
  old_line_no?: number
  new_line_no?: number
}

export interface DiffChunk {
  old_start: number
  old_lines: number
  new_start: number
  new_lines: number
  lines: DiffLine[]
}

export interface FileDiff {
  file_path: string
  old_content?: string
  new_content?: string
  added_lines: number
  removed_lines: number
  chunks: DiffChunk[]
}

export interface ToolActivity {
  id: string
  call_id: string
  tool_name: string
  operation: string
  file_path?: string
  command?: string
  arguments?: Record<string, unknown>
  risk_category: ToolRiskCategory
  permission_decision: PermissionDecision
  status: 'pending' | 'running' | 'completed' | 'failed' | 'denied'
  output?: string
  error?: string
  exit_code?: number
  duration_ms?: number
  timestamp: string
  diff?: FileDiff
}

export type VerificationStatus =
  | 'not_run'
  | 'running'
  | 'passed'
  | 'failed'
  | 'inconclusive'
  | 'blocked'

export interface VerificationCheck {
  name: string
  command: string
  status: VerificationStatus
  output?: string
  exit_code?: number
  duration_ms?: number
}

export interface VerificationRecord {
  status: VerificationStatus
  checks: VerificationCheck[]
  summary: string
  timestamp: string
}

export interface ArtifactRecord {
  id: string
  title: string
  type: 'code' | 'markdown' | 'diff' | 'terminal' | 'document'
  language?: string
  content: string
  file_path?: string
  timestamp: string
}

export interface FileContentResponse {
  path: string
  name: string
  content: string
  size_bytes: number
  language: string
  is_markdown: boolean
}

export interface FileDiffItem {
  path: string
  status: string
  insertions: number
  deletions: number
  patch: string
}

export interface GitDiffResponse {
  files_changed_count: number
  insertions: number
  deletions: number
  files: FileDiffItem[]
  raw_diff: string
}

export interface WorkspaceFileItem {
  path: string
  name: string
  is_dir: boolean
  is_markdown: boolean
  size_bytes: number
}

export interface PendingApproval {
  call_id: string
  tool_name: string
  operation: string
  file_path?: string
  command?: string
  risk_category: ToolRiskCategory
  reason: string
  working_dir?: string
}

// ==============================================================================
// Conversation & Project Hierarchy
// ==============================================================================

export interface AttachedFile {
  id: string
  name: string
  size: number
  type: string
  content?: string
}

export interface Message {
  id: string
  role: 'user' | 'assistant' | 'system'
  content: string
  timestamp: string
  task_id?: string
  status?: 'sending' | 'pending' | 'executing' | 'completed' | 'failed' | 'cancelled' | 'limit_reached'
  model?: string
  usage?: TokenUsage
  tool_activities?: ToolActivity[]
  verification?: VerificationRecord
  artifacts?: ArtifactRecord[]
  pending_approval?: PendingApproval
  attachments?: AttachedFile[]
  duration_seconds?: number
  thinking?: string
  error?: string
}

export interface Project {
  id: string
  name: string
  path?: string
  description?: string
  color?: string
  created_at: string
}

export interface Conversation {
  id: string
  title: string
  created_at: string
  updated_at: string
  project_id?: string
  pinned?: boolean
  archived?: boolean
  is_running?: boolean
  unread?: boolean
  model: string
  messages: Message[]
}

// ==============================================================================
// Phase 4 Workspace Schemas
// ==============================================================================

export interface WorkspaceLimits {
  max_file_size_bytes: number
  max_read_bytes: number
  max_directory_entries: number
  max_search_results: number
  max_write_bytes: number
  max_edit_bytes: number
}

export interface WorkspaceToolInfo {
  name: string
  risk: string
  description: string
}

export interface WorkspaceInfo {
  root_path: string
  name: string
  exists: boolean
  is_git_repo: boolean
  current_branch?: string
  total_files: number
  limits: WorkspaceLimits
  tools: WorkspaceToolInfo[]
}

export interface DirectoryItem {
  name: string
  path: string
  is_dir: boolean
}

export interface BrowseDirectoryResponse {
  current_path: string
  parent_path?: string | null
  directories: DirectoryItem[]
}

export interface WorkspaceCommandResponse {
  command: string
  working_dir: string
  exit_code: number
  stdout: string
  stderr: string
  duration_ms: number
  truncated: boolean
  timed_out: boolean
}

export interface WorkspaceFileItem {
  path: string
  name: string
  is_dir: boolean
  is_markdown: boolean
  size_bytes: number
}


// ==============================================================================
// API Error Schemas
// ==============================================================================

export interface ApiValidationErrorDetail {
  field: string
  message: string
}

export interface ApiErrorResponse {
  error: string
  message: string
  details?: ApiValidationErrorDetail[]
}
