"""Tests for multi-turn message models, tool-call tracking records, and OpenAI wire conversion."""

import json
import pytest
from pydantic import ValidationError

from core.message_models import (
    AgentMessage,
    AgentMessageRole,
    ToolCallRecord,
    ToolCallStatus,
    validate_conversation_integrity,
)
from core.tool_contracts import ToolResultEnvelope


class TestToolCallRecord:
    """Tests for ToolCallRecord model and lifecycle transitions."""

    def test_tool_call_record_creation(self) -> None:
        """Verify initialization of ToolCallRecord with required attributes."""
        tc = ToolCallRecord(
            id="call_001",
            name="calculator",
            raw_arguments='{"a": 10, "b": 20}',
        )
        assert tc.id == "call_001"
        assert tc.name == "calculator"
        assert tc.raw_arguments == '{"a": 10, "b": 20}'
        assert tc.parsed_arguments is None
        assert tc.status == ToolCallStatus.REQUESTED
        assert tc.result is None

    def test_tool_call_record_immutability(self) -> None:
        """Verify ToolCallRecord is frozen and rejects in-place attribute mutations."""
        tc = ToolCallRecord(
            id="call_001",
            name="calculator",
            raw_arguments="{}",
        )
        with pytest.raises(ValidationError):
            tc.name = "other"  # type: ignore

    def test_tool_call_record_with_status_transition(self) -> None:
        """Verify with_status returns a fresh copy with updated attributes."""
        tc = ToolCallRecord(
            id="call_001",
            name="calculator",
            raw_arguments='{"a": 5}',
        )
        env = ToolResultEnvelope.create_success(tool_name="calculator", output=25, call_id="call_001")
        updated = tc.with_status(
            status=ToolCallStatus.EXECUTED,
            parsed_arguments={"a": 5},
            result=env,
        )
        assert updated is not tc
        assert updated.id == "call_001"
        assert updated.status == ToolCallStatus.EXECUTED
        assert updated.parsed_arguments == {"a": 5}
        assert updated.result == env
        # Original remains requested
        assert tc.status == ToolCallStatus.REQUESTED


class TestAgentMessage:
    """Tests for AgentMessage constructors and OpenAI wire formatting."""

    def test_create_system_message(self) -> None:
        """Verify system message construction."""
        msg = AgentMessage.create_system("You are a helpful assistant.")
        assert msg.role == AgentMessageRole.SYSTEM
        assert msg.content == "You are a helpful assistant."
        wire = msg.to_openai_dict()
        assert wire == {"role": "system", "content": "You are a helpful assistant."}

    def test_create_user_message(self) -> None:
        """Verify user prompt message construction."""
        msg = AgentMessage.create_user("Hello, world!")
        assert msg.role == AgentMessageRole.USER
        assert msg.content == "Hello, world!"
        wire = msg.to_openai_dict()
        assert wire == {"role": "user", "content": "Hello, world!"}

    def test_create_assistant_text_message(self) -> None:
        """Verify text-only assistant response message construction."""
        msg = AgentMessage.create_assistant(content="Here is the answer.")
        assert msg.role == AgentMessageRole.ASSISTANT
        assert msg.content == "Here is the answer."
        assert msg.tool_calls is None
        wire = msg.to_openai_dict()
        assert wire == {"role": "assistant", "content": "Here is the answer."}

    def test_create_assistant_with_tool_calls(self) -> None:
        """Verify assistant message containing proposed tool calls."""
        tc = ToolCallRecord(
            id="call_456",
            name="search_files",
            raw_arguments='{"pattern": "*.py"}',
        )
        msg = AgentMessage.create_assistant(content="", tool_calls=[tc])
        assert msg.role == AgentMessageRole.ASSISTANT
        assert msg.tool_calls is not None
        assert len(msg.tool_calls) == 1

        wire = msg.to_openai_dict()
        assert wire["role"] == "assistant"
        assert wire["content"] is None
        assert "tool_calls" in wire
        assert len(wire["tool_calls"]) == 1
        assert wire["tool_calls"][0] == {
            "id": "call_456",
            "type": "function",
            "function": {
                "name": "search_files",
                "arguments": '{"pattern": "*.py"}',
            },
        }

    def test_create_tool_result_message(self) -> None:
        """Verify tool result message construction with serialized envelope."""
        env = ToolResultEnvelope.create_success(
            tool_name="search_files",
            output=["main.py", "test.py"],
            call_id="call_456",
        )
        msg = AgentMessage.create_tool_result(
            tool_call_id="call_456",
            tool_name="search_files",
            result=env,
        )
        assert msg.role == AgentMessageRole.TOOL
        assert msg.tool_call_id == "call_456"
        assert msg.name == "search_files"

        parsed_content = json.loads(msg.content or "{}")
        assert parsed_content["success"] is True
        assert parsed_content["output"] == ["main.py", "test.py"]

        wire = msg.to_openai_dict()
        assert wire["role"] == "tool"
        assert wire["tool_call_id"] == "call_456"
        assert wire["name"] == "search_files"
        assert "main.py" in wire["content"]


class TestConversationIntegrity:
    """Tests for validate_conversation_integrity multi-turn pairing checks."""

    def test_valid_text_conversation(self) -> None:
        """Text-only conversation passes validation."""
        history = [
            AgentMessage.create_system("Be concise."),
            AgentMessage.create_user("What is 2+2?"),
            AgentMessage.create_assistant(content="4"),
        ]
        validate_conversation_integrity(history)  # Should not raise

    def test_valid_single_tool_cycle(self) -> None:
        """Single tool call correctly matched with a tool result passes validation."""
        tc = ToolCallRecord(id="c1", name="add", raw_arguments='{"a": 2, "b": 2}')
        env = ToolResultEnvelope.create_success(tool_name="add", output=4, call_id="c1")
        history = [
            AgentMessage.create_user("What is 2+2?"),
            AgentMessage.create_assistant(tool_calls=[tc]),
            AgentMessage.create_tool_result(tool_call_id="c1", tool_name="add", result=env),
            AgentMessage.create_assistant(content="The sum is 4."),
        ]
        validate_conversation_integrity(history)  # Should not raise

    def test_valid_multi_tool_batch(self) -> None:
        """Batch of multiple tool calls with matching results passes validation."""
        tc1 = ToolCallRecord(id="c1", name="get_user", raw_arguments='{"id": 1}')
        tc2 = ToolCallRecord(id="c2", name="get_user", raw_arguments='{"id": 2}')
        env1 = ToolResultEnvelope.create_success(tool_name="get_user", output="Alice", call_id="c1")
        env2 = ToolResultEnvelope.create_success(tool_name="get_user", output="Bob", call_id="c2")

        history = [
            AgentMessage.create_user("Get users 1 and 2"),
            AgentMessage.create_assistant(tool_calls=[tc1, tc2]),
            AgentMessage.create_tool_result(tool_call_id="c1", tool_name="get_user", result=env1),
            AgentMessage.create_tool_result(tool_call_id="c2", tool_name="get_user", result=env2),
            AgentMessage.create_assistant(content="Users are Alice and Bob."),
        ]
        validate_conversation_integrity(history)  # Should not raise

    def test_unresolved_tool_call_detected(self) -> None:
        """Assistant proposes tool call but no tool result is provided."""
        tc = ToolCallRecord(id="c1", name="add", raw_arguments="{}")
        history = [
            AgentMessage.create_user("Calc"),
            AgentMessage.create_assistant(tool_calls=[tc]),
        ]
        with pytest.raises(ValueError, match="unresolved tool calls"):
            validate_conversation_integrity(history)

    def test_interleaved_tool_call_without_resolving_first(self) -> None:
        """Assistant sends second tool call before resolving first."""
        tc1 = ToolCallRecord(id="c1", name="t1", raw_arguments="{}")
        tc2 = ToolCallRecord(id="c2", name="t2", raw_arguments="{}")
        history = [
            AgentMessage.create_assistant(tool_calls=[tc1]),
            AgentMessage.create_assistant(tool_calls=[tc2]),
        ]
        with pytest.raises(ValueError, match="remain unresolved"):
            validate_conversation_integrity(history)

    def test_orphaned_tool_result_detected(self) -> None:
        """Tool message referencing unrequested call ID raises ValueError."""
        env = ToolResultEnvelope.create_success(tool_name="add", output=4, call_id="ghost_call")
        history = [
            AgentMessage.create_user("Calc"),
            AgentMessage.create_tool_result(tool_call_id="ghost_call", tool_name="add", result=env),
        ]
        with pytest.raises(ValueError, match="unknown or unrequested"):
            validate_conversation_integrity(history)

    def test_duplicate_tool_call_id_rejected(self) -> None:
        """Assistant proposing duplicate tool call IDs in the same message raises ValueError."""
        tc1 = ToolCallRecord(id="dup_id", name="t1", raw_arguments="{}")
        tc2 = ToolCallRecord(id="dup_id", name="t2", raw_arguments="{}")
        history = [
            AgentMessage.create_assistant(tool_calls=[tc1, tc2]),
        ]
        with pytest.raises(ValueError, match="Duplicate tool call ID"):
            validate_conversation_integrity(history)
