"""Data models and entity records for the SQLite persistence layer."""

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, ConfigDict, Field

from api.schemas.tasks import TaskStatus


class MessageRole(str, Enum):
    """Conversation role for persisted transcript records."""

    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class ApprovalStatus(str, Enum):
    """Lifecycle state for persisted approval requests."""

    PENDING = "pending"
    APPROVED = "approved"
    DENIED = "denied"
    EXPIRED = "expired"
    CANCELLED = "cancelled"
    CONSUMED = "consumed"


class SessionStatus(str, Enum):
    """Lifecycle state of an agent session."""

    ACTIVE = "active"
    COMPLETED = "completed"
    ARCHIVED = "archived"


class RecoveryStatus(str, Enum):
    """Crash-recovery classification flags attached to tasks."""

    CLEAN = "clean"
    INTERRUPTED_SAFE = "interrupted_safe"
    NEEDS_REVIEW = "needs_review"
    REPAIRED = "repaired"


class RecoveryClassification(str, Enum):
    """Deterministic classification of interrupted task state on process restart."""

    SAFE_TO_RESUME = "SAFE_TO_RESUME"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    FAILED = "FAILED"
    TERMINAL = "TERMINAL"
    UNKNOWN = "UNKNOWN"


class ExecutionState(str, Enum):
    """Execution lifecycle of an individual tool call."""

    REQUESTED = "requested"
    STARTED = "started"
    COMPLETED = "completed"
    FAILED = "failed"
    INTERRUPTED = "interrupted"


class VerificationStatus(str, Enum):
    """Authoritative outcome status of a verification run (decoupled from task execution status)."""

    NOT_RUN = "not_run"
    PASSED = "passed"
    FAILED = "failed"
    INCONCLUSIVE = "inconclusive"
    BLOCKED = "blocked"


class VerificationType(str, Enum):
    """Categorization of automated verification check."""

    TEST = "test"
    BUILD = "build"
    LINT = "lint"
    TYPECHECK = "typecheck"
    CUSTOM = "custom"


class SessionRecord(BaseModel):
    """Durable record representing an agent session."""

    model_config = ConfigDict(frozen=True)

    id: str = Field(..., description="Unique UUID for the session")
    title: str = Field(..., description="Human-readable title or prompt excerpt")
    workspace_path: str = Field(..., description="Canonical path to workspace root")
    status: SessionStatus = Field(default=SessionStatus.ACTIVE)
    metadata_json: Optional[str] = Field(default=None)
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    @property
    def metadata(self) -> Dict[str, Any]:
        """Parsed dictionary view of metadata_json."""
        import json
        try:
            return json.loads(self.metadata_json) if self.metadata_json else {}
        except Exception:
            return {}


class TaskRecord(BaseModel):
    """Durable record representing an individual task."""

    model_config = ConfigDict(frozen=True)

    id: str = Field(..., description="Unique UUID for the task")
    session_id: Optional[str] = Field(default=None, description="Owning session UUID")
    parent_task_id: Optional[str] = Field(default=None, description="Linked parent task UUID if continuation")
    prompt: str = Field(..., description="Original user prompt")
    status: TaskStatus = Field(..., description="Current state machine status")
    recovery_status: Optional[RecoveryStatus] = Field(default=None)
    response_text: Optional[str] = Field(default=None)
    finish_reason: Optional[str] = Field(default=None)
    model: Optional[str] = Field(default=None)
    usage_json: Optional[str] = Field(default=None)
    error: Optional[str] = Field(default=None)
    verification_status: VerificationStatus = Field(default=VerificationStatus.NOT_RUN, description="Authoritative independent verification result")
    correlation_id: Optional[str] = Field(default=None, description="Root or current trace correlation ID")
    iterations: int = Field(default=0)
    tool_calls_count: int = Field(default=0)
    termination_reason: Optional[str] = Field(default=None)
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    started_at: Optional[str] = Field(default=None)
    completed_at: Optional[str] = Field(default=None)
    version: int = Field(default=1, description="Optimistic locking version counter")


class StateTransitionRecord(BaseModel):
    """Audit log entry for an atomic state machine transition."""

    model_config = ConfigDict(frozen=True)

    id: str = Field(..., description="Unique transition UUID")
    entity_type: str = Field(..., description="Entity type: 'task', 'session', or 'approval'")
    entity_id: str = Field(..., description="Identifier of the mutated entity")
    from_state: str = Field(..., description="Pre-transition state")
    to_state: str = Field(..., description="Post-transition state")
    reason: Optional[str] = Field(default=None, description="Diagnostic explanation")
    actor: str = Field(default="runtime", description="Component initiating transition")
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class TranscriptMessageRecord(BaseModel):
    """Durable conversation message preserved for replay or context assembly."""

    model_config = ConfigDict(frozen=True)

    id: str = Field(..., description="Unique message UUID")
    session_id: Optional[str] = Field(default=None)
    task_id: Optional[str] = Field(default=None)
    sequence_num: int = Field(..., description="Monotonically increasing sequence number")
    role: MessageRole = Field(...)
    content: Optional[str] = Field(default=None)
    name: Optional[str] = Field(default=None)
    tool_call_id: Optional[str] = Field(default=None)
    tool_calls_json: Optional[str] = Field(default=None)
    is_derived: bool = Field(default=False)
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    @property
    def sequence_number(self) -> int:
        """Convenience alias for sequence_num."""
        return self.sequence_num


class ToolExecutionRecord(BaseModel):
    """Durable execution record for an individual tool invocation."""

    model_config = ConfigDict(frozen=True)

    id: str = Field(..., description="Unique execution UUID")
    task_id: str = Field(..., description="Associated task UUID")
    call_id: str = Field(..., description="Model-generated tool call identifier")
    tool_name: str = Field(..., description="Registered name of the tool")
    arguments_json: str = Field(..., description="Sanitized and secret-scrubbed JSON arguments")
    execution_state: ExecutionState = Field(default=ExecutionState.STARTED)
    is_mutation: bool = Field(default=False, description="Whether tool performs state-modifying side effects")
    idempotent: bool = Field(default=False, description="Whether repeated execution is safe and idempotent")
    exit_code: Optional[int] = Field(default=None)
    result_output: Optional[str] = Field(default=None, description="Sanitized and secret-scrubbed output")
    error: Optional[str] = Field(default=None)
    error_category: Optional[str] = Field(default=None)
    duration_ms: Optional[float] = Field(default=None)
    correlation_id: Optional[str] = Field(default=None, description="Trace correlation ID")
    retry_count: int = Field(default=0, description="Attempt sequence number for transient retries")
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    started_at: Optional[str] = Field(default=None)
    completed_at: Optional[str] = Field(default=None)

    @property
    def result_json(self) -> Optional[str]:
        """Convenience alias for result_output."""
        return self.result_output


class ModelCallRecord(BaseModel):
    """Telemetry and audit record for an upstream model completion request."""

    model_config = ConfigDict(frozen=True)

    id: str = Field(..., description="Unique model call UUID")
    task_id: str = Field(..., description="Associated task UUID")
    request_id: Optional[str] = Field(default=None, description="Upstream provider request ID")
    correlation_id: Optional[str] = Field(default=None, description="Trace correlation ID")
    iteration: int = Field(...)
    model: str = Field(...)
    prompt_tokens: int = Field(default=0)
    completion_tokens: int = Field(default=0)
    total_tokens: int = Field(default=0)
    finish_reason: Optional[str] = Field(default=None)
    latency_ms: Optional[float] = Field(default=None)
    retry_count: int = Field(default=0, description="Attempt sequence number for transient retries")
    failure_category: Optional[str] = Field(default=None, description="Classified error category if failed")
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    @property
    def model_name(self) -> str:
        """Convenience alias for model."""
        return self.model

    @property
    def duration_ms(self) -> Optional[float]:
        """Convenience alias for latency_ms."""
        return self.latency_ms


class ApprovalRecord(BaseModel):
    """Durable tracking record for a human decision authority request."""

    model_config = ConfigDict(frozen=True)

    id: str = Field(..., description="Unique approval request UUID")
    task_id: str = Field(..., description="Associated task UUID")
    call_id: str = Field(..., description="Tool call identifier")
    tool_name: str = Field(...)
    risk_category: str = Field(...)
    action_hash: str = Field(..., description="SHA-256 cryptographic binding")
    status: ApprovalStatus = Field(default=ApprovalStatus.PENDING)
    reason: str = Field(...)
    decision_reason: Optional[str] = Field(default=None)
    requested_at: str = Field(...)
    expires_at: str = Field(...)
    decided_at: Optional[str] = Field(default=None)

    @property
    def token_consumed(self) -> bool:
        """Whether this approval token has already been consumed."""
        return self.status == ApprovalStatus.CONSUMED

    @property
    def resolution_reason(self) -> Optional[str]:
        """Alias for decision_reason."""
        return self.decision_reason

    @property
    def resolved_by(self) -> Optional[str]:
        """Alias for decision_reason."""
        return self.decision_reason


class RecoverySummary(BaseModel):
    """Outcome report produced by the startup reconciliation engine."""

    interrupted_tasks_found: int = Field(default=0)
    safe_to_resume_count: int = Field(default=0)
    needs_review_count: int = Field(default=0)
    failed_count: int = Field(default=0)
    stale_approvals_invalidated: int = Field(default=0)
    continuation_task_ids: List[str] = Field(default_factory=list)
    details: List[Dict[str, Any]] = Field(default_factory=list)


class VerificationRecord(BaseModel):
    """Authoritative durable record of an automated verification command execution."""

    model_config = ConfigDict(frozen=True)

    id: str = Field(..., description="Unique verification record UUID")
    task_id: str = Field(..., description="Associated task UUID")
    verification_type: VerificationType = Field(..., description="Categorization of verification check")
    command: str = Field(..., description="Executed command line string")
    exit_code: Optional[int] = Field(default=None, description="Process termination exit code")
    result: VerificationStatus = Field(default=VerificationStatus.NOT_RUN, description="Classified outcome")
    output_excerpt: Optional[str] = Field(default=None, description="Sanitized and bounded stdout/stderr capture")
    duration_ms: Optional[float] = Field(default=None, description="Execution duration in milliseconds")
    correlation_id: Optional[str] = Field(default=None, description="Associated trace correlation ID")
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class PersistedEventRecord(BaseModel):
    """Structured runtime lifecycle event persisted for audit and observability."""

    model_config = ConfigDict(frozen=True)

    id: str = Field(..., description="Unique runtime event UUID")
    event_type: str = Field(..., description="Dot-delimited lifecycle event name")
    task_id: Optional[str] = Field(default=None, description="Associated task UUID if applicable")
    session_id: Optional[str] = Field(default=None, description="Associated session UUID if applicable")
    correlation_id: Optional[str] = Field(default=None, description="Trace correlation ID")
    parent_event_id: Optional[str] = Field(default=None, description="Parent event UUID for nested causal chains")
    payload_json: Optional[str] = Field(default=None, description="JSON-serialized event payload")
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    @property
    def payload(self) -> Dict[str, Any]:
        """Parsed dictionary view of payload_json."""
        import json
        try:
            return json.loads(self.payload_json) if self.payload_json else {}
        except Exception:
            return {}

