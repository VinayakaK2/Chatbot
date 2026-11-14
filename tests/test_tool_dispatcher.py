"""Tests for ToolDispatcher validation, permission enforcement, handler execution, and errors."""

import asyncio
import json
import time
import pytest
from pydantic import BaseModel, Field

from core.tool_contracts import (
    ToolDefinition,
    ToolResultEnvelope,
    ToolRiskCategory,
)
from core.tool_dispatcher import (
    DefaultPermissionPolicy,
    ToolDispatcher,
)
from core.tool_registry import ToolRegistry


# Test input models
class AddInput(BaseModel):
    a: int = Field(..., description="First integer")
    b: int = Field(..., description="Second integer")


class EchoInput(BaseModel):
    message: str = Field(..., description="Text message to echo")


def sync_add_handler(a: int, b: int) -> int:
    """Synchronous add handler."""
    return a + b


async def async_add_handler(a: int, b: int) -> int:
    """Asynchronous add handler."""
    await asyncio.sleep(0.01)
    return a + b


def slow_handler(message: str) -> str:
    """Synchronous slow handler for timeout testing."""
    time.sleep(0.5)
    return message


async def slow_async_handler(message: str) -> str:
    """Asynchronous slow handler for timeout testing."""
    await asyncio.sleep(0.5)
    return message


def exploding_handler(message: str) -> str:
    """Handler that raises an unexpected exception."""
    raise RuntimeError("Database connection exploded!")


@pytest.fixture
def test_registry() -> ToolRegistry:
    """Provide an isolated tool registry populated with test tools."""
    registry = ToolRegistry()

    # 1. Sync addition tool (READ_ONLY)
    tool_sync = ToolDefinition.from_pydantic(
        name="sync_add",
        description="Add two numbers synchronously.",
        parameters_model=AddInput,
        risk_category=ToolRiskCategory.READ_ONLY,
        handler=sync_add_handler,
    )
    registry.register(tool_sync)

    # 2. Async addition tool (SYSTEM_PROBE)
    tool_async = ToolDefinition.from_pydantic(
        name="async_add",
        description="Add two numbers asynchronously.",
        parameters_model=AddInput,
        risk_category=ToolRiskCategory.SYSTEM_PROBE,
        handler=async_add_handler,
    )
    registry.register(tool_async)

    # 3. Slow tool for timeout tests
    tool_slow = ToolDefinition.from_pydantic(
        name="slow_tool",
        description="A tool that takes time.",
        parameters_model=EchoInput,
        risk_category=ToolRiskCategory.READ_ONLY,
        handler=slow_async_handler,
    )
    registry.register(tool_slow)

    # 4. Exploding tool
    tool_boom = ToolDefinition.from_pydantic(
        name="exploding_tool",
        description="A tool that fails.",
        parameters_model=EchoInput,
        risk_category=ToolRiskCategory.READ_ONLY,
        handler=exploding_handler,
    )
    registry.register(tool_boom)

    # 5. Dangerous command tool (COMMAND_EXECUTION)
    tool_cmd = ToolDefinition.from_pydantic(
        name="run_bash",
        description="Execute a bash command.",
        parameters_model=EchoInput,
        risk_category=ToolRiskCategory.COMMAND_EXECUTION,
        handler=lambda message: f"Executed: {message}",
    )
    registry.register(tool_cmd)

    # 6. Tool with no handler
    tool_nohandler = ToolDefinition.from_pydantic(
        name="no_handler_tool",
        description="A tool without an implementation.",
        parameters_model=EchoInput,
        risk_category=ToolRiskCategory.READ_ONLY,
        handler=None,
    )
    registry.register(tool_nohandler)

    return registry


@pytest.mark.asyncio
class TestToolDispatcher:
    """Test suite for ToolDispatcher execution, validation, and safety."""

    async def test_dispatch_sync_handler_success(self, test_registry: ToolRegistry) -> None:
        """Verify synchronous handler executes in thread pool and returns success envelope."""
        dispatcher = ToolDispatcher(registry=test_registry)
        result = await dispatcher.dispatch(
            tool_name="sync_add",
            arguments='{"a": 10, "b": 32}',
            call_id="call_001",
        )
        assert result.success is True
        assert result.output == 42
        assert result.call_id == "call_001"
        assert result.tool_name == "sync_add"
        assert result.error is None
        assert "duration_ms" in result.metadata

    async def test_dispatch_async_handler_success(self, test_registry: ToolRegistry) -> None:
        """Verify native async coroutine handler executes and returns success envelope."""
        dispatcher = ToolDispatcher(registry=test_registry)
        result = await dispatcher.dispatch(
            tool_name="async_add",
            arguments={"a": 100, "b": 200},
            call_id="call_002",
        )
        assert result.success is True
        assert result.output == 300
        assert result.call_id == "call_002"

    async def test_dispatch_unregistered_tool(self, test_registry: ToolRegistry) -> None:
        """Querying unregistered tool returns structured tool_not_found error."""
        dispatcher = ToolDispatcher(registry=test_registry)
        result = await dispatcher.dispatch(
            tool_name="nonexistent_tool",
            arguments="{}",
            call_id="call_missing",
        )
        assert result.success is False
        assert result.error_category == "tool_not_found"
        assert "not registered" in (result.error or "").lower()

    async def test_dispatch_missing_handler(self, test_registry: ToolRegistry) -> None:
        """Tool registered with handler=None returns missing_handler error."""
        dispatcher = ToolDispatcher(registry=test_registry)
        result = await dispatcher.dispatch(
            tool_name="no_handler_tool",
            arguments={"message": "hello"},
            call_id="call_nohandler",
        )
        assert result.success is False
        assert result.error_category == "missing_handler"
        assert "no registered execution handler" in (result.error or "").lower()

    async def test_dispatch_malformed_json_arguments(self, test_registry: ToolRegistry) -> None:
        """Malformed JSON string argument returns validation_error without crashing."""
        dispatcher = ToolDispatcher(registry=test_registry)
        result = await dispatcher.dispatch(
            tool_name="sync_add",
            arguments="{invalid json, a: 10",
            call_id="call_bad_json",
        )
        assert result.success is False
        assert result.error_category == "validation_error"
        assert "malformed json" in (result.error or "").lower()

    async def test_dispatch_non_dict_json_payload(self, test_registry: ToolRegistry) -> None:
        """Non-dictionary JSON values (arrays, primitives) return validation_error."""
        dispatcher = ToolDispatcher(registry=test_registry)
        result = await dispatcher.dispatch(
            tool_name="sync_add",
            arguments="[1, 2, 3]",
            call_id="call_array",
        )
        assert result.success is False
        assert result.error_category == "validation_error"
        assert "expected json object" in (result.error or "").lower()

    async def test_dispatch_schema_type_mismatch(self, test_registry: ToolRegistry) -> None:
        """Schema type violations return validation_error with field details."""
        dispatcher = ToolDispatcher(registry=test_registry)
        result = await dispatcher.dispatch(
            tool_name="sync_add",
            arguments='{"a": "not_an_int", "b": 10}',
            call_id="call_type_err",
        )
        assert result.success is False
        assert result.error_category == "validation_error"
        assert "validation failed" in (result.error or "").lower()

    async def test_dispatch_extra_forbidden_arguments(self, test_registry: ToolRegistry) -> None:
        """Unexpected extra fields in strict tool definitions return validation_error."""
        dispatcher = ToolDispatcher(registry=test_registry)
        result = await dispatcher.dispatch(
            tool_name="sync_add",
            arguments='{"a": 1, "b": 2, "unexpected_field": "injected"}',
            call_id="call_extra",
        )
        assert result.success is False
        assert result.error_category == "validation_error"

    async def test_dispatch_handler_exception_captured(self, test_registry: ToolRegistry) -> None:
        """Unhandled exceptions in tool handlers are caught and converted to error envelope."""
        dispatcher = ToolDispatcher(registry=test_registry)
        result = await dispatcher.dispatch(
            tool_name="exploding_tool",
            arguments='{"message": "boom"}',
            call_id="call_boom",
        )
        assert result.success is False
        assert result.error_category == "execution_error"
        assert "Database connection exploded" in (result.error or "")

    async def test_dispatch_timeout_enforcement(self, test_registry: ToolRegistry) -> None:
        """Tool exceeding configured timeout is interrupted and returns timeout error."""
        dispatcher = ToolDispatcher(registry=test_registry, default_timeout_seconds=0.05)
        result = await dispatcher.dispatch(
            tool_name="slow_tool",
            arguments='{"message": "slow"}',
            call_id="call_timeout",
            timeout_seconds=0.05,
        )
        assert result.success is False
        assert result.error_category == "timeout"
        assert "timed out after 0.05" in (result.error or "").lower()

    async def test_dispatch_permission_denied_default(self, test_registry: ToolRegistry) -> None:
        """COMMAND_EXECUTION tools are denied by default under least-privilege policy."""
        dispatcher = ToolDispatcher(registry=test_registry)
        result = await dispatcher.dispatch(
            tool_name="run_bash",
            arguments='{"message": "ls -la"}',
            call_id="call_perm_denied",
        )
        assert result.success is False
        assert result.error_category == "permission_denied"
        assert "permission denied" in (result.error or "").lower()

    async def test_dispatch_permission_explicit_allowlist(self, test_registry: ToolRegistry) -> None:
        """Tool in explicit allowlist executes successfully even if category is not default-allowed."""
        policy = DefaultPermissionPolicy(allowed_tool_names={"run_bash"})
        dispatcher = ToolDispatcher(registry=test_registry, permission_policy=policy)
        result = await dispatcher.dispatch(
            tool_name="run_bash",
            arguments='{"message": "echo hello"}',
            call_id="call_perm_allowed",
        )
        assert result.success is True
        assert result.output == "Executed: echo hello"

    async def test_dispatch_permission_explicit_denylist_wins(self, test_registry: ToolRegistry) -> None:
        """Tool in explicit denylist is blocked even if category is normally allowed."""
        policy = DefaultPermissionPolicy(denied_tool_names={"sync_add"})
        dispatcher = ToolDispatcher(registry=test_registry, permission_policy=policy)
        result = await dispatcher.dispatch(
            tool_name="sync_add",
            arguments='{"a": 1, "b": 1}',
            call_id="call_deny_wins",
        )
        assert result.success is False
        assert result.error_category == "permission_denied"

    async def test_dispatch_output_truncation_protection(self, test_registry: ToolRegistry) -> None:
        """Oversized tool outputs are truncated to prevent context window blowout."""
        # Register a tool that returns a massive string
        big_str = "x" * 100_000
        tool_big = ToolDefinition.from_pydantic(
            name="big_output_tool",
            description="Returns large data.",
            parameters_model=EchoInput,
            risk_category=ToolRiskCategory.READ_ONLY,
            handler=lambda message: big_str,
        )
        test_registry.register(tool_big)

        dispatcher = ToolDispatcher(registry=test_registry, max_output_chars=500)
        result = await dispatcher.dispatch(
            tool_name="big_output_tool",
            arguments='{"message": "give me big"}',
        )
        assert result.success is True
        assert isinstance(result.output, str)
        assert len(result.output) < 1000
        assert "Output truncated to 500 chars" in result.output
        assert result.metadata.get("truncated") is True
