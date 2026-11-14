"""Multi-turn message models, tool-call tracking records, and OpenAI wire conversion helpers."""

import json
from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, ConfigDict, Field

from core.tool_contracts import ToolResultEnvelope


class ToolCallStatus(str, Enum):
    """Lifecycle status of a tool call record within an agent turn."""

    REQUESTED = "requested"
    VALIDATED = "validated"
    REJECTED = "rejected"
    EXECUTED = "executed"
    FAILED = "failed"


class ToolCallRecord(BaseModel):
    """Detailed record of a single tool invocation requested by the model."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(..., description="Provider-assigned tool call identifier (e.g. 'call_123')")
    name: str = Field(..., description="Target tool identifier requested by the model")
    raw_arguments: str = Field(..., description="Raw argument payload string as emitted by the model")
    parsed_arguments: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Parsed argument dictionary if JSON parsing succeeded",
    )
    status: ToolCallStatus = Field(
        default=ToolCallStatus.REQUESTED,
        description="Current execution lifecycle status of this tool call",
    )
    result: Optional[ToolResultEnvelope] = Field(
        default=None,
        description="Result envelope produced by tool execution",
    )

    def with_status(
        self,
        status: ToolCallStatus,
        parsed_arguments: Optional[Dict[str, Any]] = None,
        result: Optional[ToolResultEnvelope] = None,
    ) -> "ToolCallRecord":
        """Return a new ToolCallRecord with updated status and execution attributes."""
        return ToolCallRecord(
            id=self.id,
            name=self.name,
            raw_arguments=self.raw_arguments,
            parsed_arguments=parsed_arguments if parsed_arguments is not None else self.parsed_arguments,
            status=status,
            result=result if result is not None else self.result,
        )


class AgentMessageRole(str, Enum):
    """Authoritative role taxonomy for multi-turn agent conversations."""

    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class AgentMessage(BaseModel):
    """Unified message record for agent conversation history and wire serialization."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    role: AgentMessageRole = Field(..., description="Message author role")
    content: Optional[str] = Field(
        default="",
        description="Textual content of the message; may be empty for tool-call proposals",
    )
    name: Optional[str] = Field(
        default=None,
        description="Optional author or tool name",
    )
    tool_call_id: Optional[str] = Field(
        default=None,
        description="Correlated tool call ID when role is 'tool'",
    )
    tool_calls: Optional[List[ToolCallRecord]] = Field(
        default=None,
        description="List of tool calls proposed by assistant messages",
    )

    @classmethod
    def create_system(cls, content: str) -> "AgentMessage":
        """Construct a system instruction message."""
        return cls(role=AgentMessageRole.SYSTEM, content=content)

    @classmethod
    def create_user(cls, content: str) -> "AgentMessage":
        """Construct a user prompt message."""
        return cls(role=AgentMessageRole.USER, content=content)

    @classmethod
    def create_assistant(
        cls,
        content: Optional[str] = None,
        tool_calls: Optional[List[ToolCallRecord]] = None,
    ) -> "AgentMessage":
        """Construct an assistant response message, optionally containing tool calls."""
        return cls(
            role=AgentMessageRole.ASSISTANT,
            content=content or "",
            tool_calls=tool_calls if tool_calls else None,
        )

    @classmethod
    def create_tool_result(
        cls,
        tool_call_id: str,
        tool_name: str,
        result: ToolResultEnvelope,
    ) -> "AgentMessage":
        """Construct a tool result message containing the serialized ToolResultEnvelope."""
        serialized_content = json.dumps(result.model_dump(), separators=(",", ":"))
        return cls(
            role=AgentMessageRole.TOOL,
            content=serialized_content,
            name=tool_name,
            tool_call_id=tool_call_id,
        )

    def to_openai_dict(self) -> Dict[str, Any]:
        """Convert message into the standard OpenAI chat completions wire format."""
        payload: Dict[str, Any] = {"role": self.role.value}

        if self.role == AgentMessageRole.ASSISTANT and self.tool_calls:
            payload["content"] = self.content if self.content else None
            payload["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.name,
                        "arguments": tc.raw_arguments,
                    },
                }
                for tc in self.tool_calls
            ]
        elif self.role == AgentMessageRole.TOOL:
            payload["content"] = self.content or ""
            payload["tool_call_id"] = self.tool_call_id or ""
            if self.name:
                payload["name"] = self.name
        else:
            payload["content"] = self.content or ""
            if self.name:
                payload["name"] = self.name

        return payload


def validate_conversation_integrity(messages: List[AgentMessage]) -> None:
    """Validate that multi-turn history adheres to message sequence and tool pairing rules.

    Raises ValueError if an assistant tool call lacks a matching tool result or if
    a tool message has no preceding matching tool call ID.
    """
    pending_tool_calls: Dict[str, str] = {}  # tool_call_id -> tool_name

    for idx, msg in enumerate(messages):
        if msg.role == AgentMessageRole.ASSISTANT and msg.tool_calls:
            # Check for unresolved previous tool calls
            if pending_tool_calls:
                raise ValueError(
                    f"Message at index {idx} contains new tool calls while previous calls "
                    f"{list(pending_tool_calls.keys())} remain unresolved."
                )
            for tc in msg.tool_calls:
                if tc.id in pending_tool_calls:
                    raise ValueError(f"Duplicate tool call ID '{tc.id}' in assistant message at index {idx}.")
                pending_tool_calls[tc.id] = tc.name

        elif msg.role == AgentMessageRole.TOOL:
            if not msg.tool_call_id:
                raise ValueError(f"Tool message at index {idx} is missing required 'tool_call_id'.")
            if msg.tool_call_id not in pending_tool_calls:
                raise ValueError(
                    f"Tool message at index {idx} references unknown or unrequested "
                    f"tool_call_id '{msg.tool_call_id}'."
                )
            del pending_tool_calls[msg.tool_call_id]

    if pending_tool_calls:
        raise ValueError(
            f"Conversation history has {len(pending_tool_calls)} unresolved tool calls: "
            f"{list(pending_tool_calls.keys())}."
        )
