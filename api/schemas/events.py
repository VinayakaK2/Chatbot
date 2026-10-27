"""Event schema definitions for runtime lifecycle observation and streaming."""

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict
from pydantic import BaseModel, Field


class RuntimeEventType(str, Enum):
    """Lifecycle event types emitted by the agent runtime as defined in phases.md."""

    TASK_CREATED = "task_created"
    MODEL_REQUEST_STARTED = "model_request_started"
    TOOL_REQUESTED = "tool_requested"
    PERMISSION_DENIED = "permission_denied"
    APPROVAL_NEEDED = "approval_needed"
    TOOL_COMPLETED = "tool_completed"
    VERIFICATION_COMPLETED = "verification_completed"
    TASK_TERMINAL = "task_terminal"


class RuntimeEvent(BaseModel):
    """Normalized structured event record for auditability and streaming."""

    event_id: str = Field(..., description="Unique event identifier (UUID)")
    task_id: str = Field(..., description="Task to which this event belongs")
    event_type: RuntimeEventType = Field(..., description="Categorical type of the event")
    timestamp: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(),
        description="ISO 8601 UTC timestamp of event generation",
    )
    payload: Dict[str, Any] = Field(
        default_factory=dict,
        description="Event-specific metadata or payload",
    )
