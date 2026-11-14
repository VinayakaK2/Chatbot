"""Comprehensive test suite for AgentLoop execution, turn control, budgets, events, and recovery."""

import asyncio
from typing import Any, Dict, List
import pytest
from pydantic import BaseModel, Field

from api.schemas.events import RuntimeEvent, RuntimeEventType
from api.schemas.tasks import TaskStatus
from core.agent_loop import AgentExecutionRequest, AgentLoop
from core.message_models import AgentMessageRole, validate_conversation_integrity
from core.tool_contracts import (
    ToolDefinition,
    ToolRiskCategory,
)
from core.tool_dispatcher import DefaultPermissionPolicy, ToolDispatcher
from core.tool_registry import ToolRegistry
from model.contracts import (
    ModelAuthError,
    ModelConnectionError,
    ModelRateLimitError,
    ModelResponse,
    ModelTimeoutError,
)
from tests.fakes import FakeModelProvider


# Input models for test tools
class MathInput(BaseModel):
    x: int = Field(..., description="First number")
    y: int = Field(..., description="Second number")


class EchoInput(BaseModel):
    text: str = Field(..., description="Text to echo")


def multiply_handler(x: int, y: int) -> int:
    """Synchronous multiplication."""
    return x * y


async def async_echo_handler(text: str) -> str:
    """Asynchronous echo."""
    await asyncio.sleep(0.01)
    return f"echo: {text}"


def failing_handler(text: str) -> str:
    """Handler that raises an exception."""
    raise ValueError("Calculation failed due to division by zero.")


@pytest.fixture
def agent_test_registry() -> ToolRegistry:
    """Provide a tool registry populated with standard test tools."""
    registry = ToolRegistry()

    tool_mult = ToolDefinition.from_pydantic(
        name="multiply",
        description="Multiply two integers.",
        parameters_model=MathInput,
        risk_category=ToolRiskCategory.READ_ONLY,
        handler=multiply_handler,
    )
    registry.register(tool_mult)

    tool_echo = ToolDefinition.from_pydantic(
        name="echo",
        description="Echo string asynchronously.",
        parameters_model=EchoInput,
        risk_category=ToolRiskCategory.READ_ONLY,
        handler=async_echo_handler,
    )
    registry.register(tool_echo)

    tool_fail = ToolDefinition.from_pydantic(
        name="failing_tool",
        description="Always fails.",
        parameters_model=EchoInput,
        risk_category=ToolRiskCategory.READ_ONLY,
        handler=failing_handler,
    )
    registry.register(tool_fail)

    tool_dangerous = ToolDefinition.from_pydantic(
        name="delete_database",
        description="Destructive action.",
        parameters_model=EchoInput,
        risk_category=ToolRiskCategory.DESTRUCTIVE,
        handler=lambda text: "Deleted",
    )
    registry.register(tool_dangerous)

    return registry


@pytest.mark.asyncio
class TestAgentLoopExecution:
    """Unit and integration tests for AgentLoop execution scenarios."""

    async def test_scenario_01_final_answer_no_tools(self, agent_test_registry: ToolRegistry) -> None:
        """Model returns a direct final answer without proposing any tool calls."""
        fake_model = FakeModelProvider()
        fake_model.queue_text_response("2 + 2 is 4.")

        loop = AgentLoop(model_provider=fake_model, registry=agent_test_registry)
        req = AgentExecutionRequest(task_id="t-01", prompt="What is 2+2?")
        res = await loop.run(req)

        assert res.status == TaskStatus.COMPLETED
        assert res.final_response == "2 + 2 is 4."
        assert res.iterations == 1
        assert res.tool_calls_count == 0
        assert res.termination_reason == "completed"
        assert len(res.history) == 2  # user prompt, assistant response
        validate_conversation_integrity(res.history)

    async def test_scenario_02_single_tool_call_and_completion(
        self, agent_test_registry: ToolRegistry
    ) -> None:
        """Model requests one tool call, receives result, and returns final answer."""
        fake_model = FakeModelProvider()
        # Turn 1: Propose multiply tool
        fake_model.queue_tool_call_response(
            tool_name="multiply",
            arguments={"x": 6, "y": 7},
            call_id="call_mult_1",
        )
        # Turn 2: Final response using tool output
        fake_model.queue_text_response("6 multiplied by 7 is 42.")

        loop = AgentLoop(model_provider=fake_model, registry=agent_test_registry)
        req = AgentExecutionRequest(task_id="t-02", prompt="Calculate 6 * 7")
        res = await loop.run(req)

        assert res.status == TaskStatus.COMPLETED
        assert res.final_response == "6 multiplied by 7 is 42."
        assert res.iterations == 2
        assert res.tool_calls_count == 1
        assert res.termination_reason == "completed"

        # Verify message sequence: User -> Assistant(tool_calls) -> Tool(result) -> Assistant(final)
        assert len(res.history) == 4
        assert res.history[0].role == AgentMessageRole.USER
        assert res.history[1].role == AgentMessageRole.ASSISTANT
        assert res.history[1].tool_calls[0].id == "call_mult_1"
        assert res.history[2].role == AgentMessageRole.TOOL
        assert res.history[2].tool_call_id == "call_mult_1"
        assert '"output":42' in (res.history[2].content or "")
        assert res.history[3].role == AgentMessageRole.ASSISTANT
        validate_conversation_integrity(res.history)

    async def test_scenario_03_multiple_tool_calls_batch(
        self, agent_test_registry: ToolRegistry
    ) -> None:
        """Model requests multiple tool calls in one turn; all are executed sequentially in order."""
        fake_model = FakeModelProvider()
        # Turn 1: Propose two tool calls
        fake_model.queue_multi_tool_call_response(
            calls=[
                {"id": "call_1", "name": "multiply", "arguments": {"x": 3, "y": 4}},
                {"id": "call_2", "name": "echo", "arguments": {"text": "hello"}},
            ]
        )
        # Turn 2: Final response
        fake_model.queue_text_response("Done: 12 and echo: hello.")

        loop = AgentLoop(model_provider=fake_model, registry=agent_test_registry)
        req = AgentExecutionRequest(task_id="t-03", prompt="Calculate 3*4 and echo hello")
        res = await loop.run(req)

        assert res.status == TaskStatus.COMPLETED
        assert res.iterations == 2
        assert res.tool_calls_count == 2
        assert len(res.history) == 5  # user -> assistant(2 calls) -> tool 1 -> tool 2 -> assistant
        assert res.history[2].tool_call_id == "call_1"
        assert res.history[3].tool_call_id == "call_2"
        validate_conversation_integrity(res.history)

    async def test_scenario_04_unknown_tool_recovers(
        self, agent_test_registry: ToolRegistry
    ) -> None:
        """Model requests unregistered tool; error envelope is returned; model recovers."""
        fake_model = FakeModelProvider()
        # Turn 1: Propose unknown tool
        fake_model.queue_tool_call_response(
            tool_name="nonexistent_weather",
            arguments={"city": "Tokyo"},
            call_id="call_unk",
        )
        # Turn 2: Model sees tool_not_found error and apologizes
        fake_model.queue_text_response("Sorry, weather tool is unavailable.")

        loop = AgentLoop(model_provider=fake_model, registry=agent_test_registry)
        req = AgentExecutionRequest(task_id="t-04", prompt="Weather in Tokyo")
        res = await loop.run(req)

        assert res.status == TaskStatus.COMPLETED
        assert res.iterations == 2
        assert res.tool_calls_count == 1
        assert "tool_not_found" in (res.history[2].content or "")
        validate_conversation_integrity(res.history)

    async def test_scenario_05_invalid_arguments_recovers(
        self, agent_test_registry: ToolRegistry
    ) -> None:
        """Model supplies invalid argument types; validation_error returned; model fixes call."""
        fake_model = FakeModelProvider()
        # Turn 1: Invalid argument (string instead of int)
        fake_model.queue_tool_call_response(
            tool_name="multiply",
            arguments='{"x": "ten", "y": 5}',
            call_id="call_invalid",
        )
        # Turn 2: Corrected arguments
        fake_model.queue_tool_call_response(
            tool_name="multiply",
            arguments={"x": 10, "y": 5},
            call_id="call_valid",
        )
        # Turn 3: Final answer
        fake_model.queue_text_response("Result is 50.")

        loop = AgentLoop(model_provider=fake_model, registry=agent_test_registry)
        req = AgentExecutionRequest(task_id="t-05", prompt="Multiply 10 by 5")
        res = await loop.run(req)

        assert res.status == TaskStatus.COMPLETED
        assert res.iterations == 3
        assert res.tool_calls_count == 2
        assert "validation_error" in (res.history[2].content or "")
        assert '"output":50' in (res.history[4].content or "")
        validate_conversation_integrity(res.history)

    async def test_scenario_06_malformed_json_arguments(
        self, agent_test_registry: ToolRegistry
    ) -> None:
        """Model returns broken JSON string; validation_error returned safely."""
        fake_model = FakeModelProvider()
        fake_model.queue_tool_call_response(
            tool_name="multiply",
            arguments="broken json { x: 1",
            call_id="call_broken_json",
        )
        fake_model.queue_text_response("Fixed: answer is 0.")

        loop = AgentLoop(model_provider=fake_model, registry=agent_test_registry)
        req = AgentExecutionRequest(task_id="t-06", prompt="Calculate broken")
        res = await loop.run(req)

        assert res.status == TaskStatus.COMPLETED
        assert "malformed json" in (res.history[2].content or "").lower()
        validate_conversation_integrity(res.history)

    async def test_scenario_07_empty_model_response_fails(
        self, agent_test_registry: ToolRegistry
    ) -> None:
        """Model returning empty text and zero tool calls terminates as FAILED."""
        fake_model = FakeModelProvider()
        fake_model.queue_text_response("")

        loop = AgentLoop(model_provider=fake_model, registry=agent_test_registry)
        req = AgentExecutionRequest(task_id="t-07", prompt="Give me text")
        res = await loop.run(req)

        assert res.status == TaskStatus.FAILED
        assert res.termination_reason == "empty_response"
        assert "empty completion" in (res.error or "").lower()

    async def test_scenario_08_max_iterations_limit(
        self, agent_test_registry: ToolRegistry
    ) -> None:
        """Infinite tool-call loop stops at configured max_iterations ceiling."""
        fake_model = FakeModelProvider()
        # Queue 10 tool call responses
        for i in range(10):
            fake_model.queue_tool_call_response(
                tool_name="echo",
                arguments={"text": f"loop_{i}"},
                call_id=f"call_loop_{i}",
            )

        loop = AgentLoop(model_provider=fake_model, registry=agent_test_registry)
        req = AgentExecutionRequest(task_id="t-08", prompt="Loop forever", max_iterations=3)
        res = await loop.run(req)

        assert res.status == TaskStatus.LIMIT_REACHED
        assert res.termination_reason == "max_iterations"
        assert res.iterations == 3
        assert "maximum allowed iterations" in (res.error or "").lower()

    async def test_scenario_09_max_tool_calls_budget(
        self, agent_test_registry: ToolRegistry
    ) -> None:
        """Batch of tool calls exceeding max_tool_calls budget terminates as LIMIT_REACHED."""
        fake_model = FakeModelProvider()
        fake_model.queue_multi_tool_call_response(
            calls=[
                {"id": "c1", "name": "echo", "arguments": {"text": "1"}},
                {"id": "c2", "name": "echo", "arguments": {"text": "2"}},
                {"id": "c3", "name": "echo", "arguments": {"text": "3"}},
            ]
        )

        loop = AgentLoop(model_provider=fake_model, registry=agent_test_registry)
        req = AgentExecutionRequest(task_id="t-09", prompt="3 calls", max_tool_calls=2)
        res = await loop.run(req)

        assert res.status == TaskStatus.LIMIT_REACHED
        assert res.termination_reason == "max_tool_calls"
        assert "maximum tool calls budget" in (res.error or "").lower()

    async def test_scenario_10_task_wall_clock_timeout(
        self, agent_test_registry: ToolRegistry
    ) -> None:
        """Slow model or task exceeding task_timeout_seconds terminates as LIMIT_REACHED."""
        fake_model = FakeModelProvider(delay_seconds=0.15)
        fake_model.queue_tool_call_response(tool_name="echo", arguments={"text": "hi"})
        fake_model.queue_text_response("Done")

        loop = AgentLoop(model_provider=fake_model, registry=agent_test_registry)
        req = AgentExecutionRequest(task_id="t-10", prompt="Timeout test", task_timeout_seconds=0.08)
        res = await loop.run(req)

        assert res.status == TaskStatus.LIMIT_REACHED
        assert res.termination_reason == "task_timeout"

    async def test_scenario_11_user_cancellation_before_iteration(
        self, agent_test_registry: ToolRegistry
    ) -> None:
        """Signaling cancellation token causes clean termination with CANCELLED status."""
        fake_model = FakeModelProvider()
        fake_model.queue_text_response("Never seen.")

        cancel_token = asyncio.Event()
        cancel_token.set()  # Cancel immediately

        loop = AgentLoop(model_provider=fake_model, registry=agent_test_registry)
        req = AgentExecutionRequest(
            task_id="t-11",
            prompt="Cancel me",
            cancellation_token=cancel_token,
        )
        res = await loop.run(req)

        assert res.status == TaskStatus.CANCELLED
        assert res.termination_reason == "cancelled"
        assert res.iterations == 0
        assert len(fake_model.calls) == 0

    async def test_scenario_12_model_provider_error_handling(
        self, agent_test_registry: ToolRegistry
    ) -> None:
        """Model connection or auth errors terminate cleanly with FAILED status."""
        fake_model = FakeModelProvider()
        fake_model.queue_exception(ModelConnectionError("Connection to vLLM failed."))

        loop = AgentLoop(model_provider=fake_model, registry=agent_test_registry)
        req = AgentExecutionRequest(task_id="t-12", prompt="Fail connection")
        res = await loop.run(req)

        assert res.status == TaskStatus.FAILED
        assert res.termination_reason == "model_error"
        assert "connection to vllm failed" in (res.error or "").lower()

    async def test_scenario_13_tool_execution_exception_recovers(
        self, agent_test_registry: ToolRegistry
    ) -> None:
        """Tool handler raising exception returns execution_error envelope; model finishes."""
        fake_model = FakeModelProvider()
        fake_model.queue_tool_call_response(
            tool_name="failing_tool",
            arguments={"text": "crash"},
            call_id="call_fail_1",
        )
        fake_model.queue_text_response("I noticed the calculation failed with division by zero.")

        loop = AgentLoop(model_provider=fake_model, registry=agent_test_registry)
        req = AgentExecutionRequest(task_id="t-13", prompt="Calculate failing")
        res = await loop.run(req)

        assert res.status == TaskStatus.COMPLETED
        assert res.iterations == 2
        assert "execution_error" in (res.history[2].content or "")
        validate_conversation_integrity(res.history)

    async def test_scenario_14_permission_denial_recovers(
        self, agent_test_registry: ToolRegistry
    ) -> None:
        """Tool denied by permission policy returns permission_denied envelope; model explains."""
        fake_model = FakeModelProvider()
        fake_model.queue_tool_call_response(
            tool_name="delete_database",
            arguments={"text": "drop tables"},
            call_id="call_perm_1",
        )
        fake_model.queue_text_response("I am not permitted to delete the database.")

        loop = AgentLoop(model_provider=fake_model, registry=agent_test_registry)
        req = AgentExecutionRequest(task_id="t-14", prompt="Delete DB")
        res = await loop.run(req)

        assert res.status == TaskStatus.COMPLETED
        assert "permission_denied" in (res.history[2].content or "")
        validate_conversation_integrity(res.history)

    async def test_scenario_15_runtime_events_streaming(
        self, agent_test_registry: ToolRegistry
    ) -> None:
        """Verify all fine-grained lifecycle events are emitted in strict sequence."""
        fake_model = FakeModelProvider()
        fake_model.queue_tool_call_response(
            tool_name="multiply",
            arguments={"x": 5, "y": 5},
            call_id="c_evt",
        )
        fake_model.queue_text_response("5 * 5 is 25.")

        recorded_events: List[RuntimeEvent] = []

        loop = AgentLoop(model_provider=fake_model, registry=agent_test_registry)
        req = AgentExecutionRequest(
            task_id="t-15",
            prompt="Event sequence test",
            event_sink=lambda ev: recorded_events.append(ev),
        )
        res = await loop.run(req)

        assert res.status == TaskStatus.COMPLETED
        event_types = [e.event_type for e in recorded_events]

        assert RuntimeEventType.MODEL_REQUEST_STARTED in event_types
        assert RuntimeEventType.MODEL_RESPONSE_RECEIVED in event_types
        assert RuntimeEventType.TOOL_REQUESTED in event_types
        assert RuntimeEventType.TOOL_EXECUTION_STARTED in event_types
        assert RuntimeEventType.TOOL_COMPLETED in event_types

        # Verify event ordering
        req_start_idx = event_types.index(RuntimeEventType.MODEL_REQUEST_STARTED)
        resp_recv_idx = event_types.index(RuntimeEventType.MODEL_RESPONSE_RECEIVED)
        tool_req_idx = event_types.index(RuntimeEventType.TOOL_REQUESTED)
        tool_exec_idx = event_types.index(RuntimeEventType.TOOL_EXECUTION_STARTED)
        tool_comp_idx = event_types.index(RuntimeEventType.TOOL_COMPLETED)

        assert req_start_idx < resp_recv_idx < tool_req_idx < tool_exec_idx < tool_comp_idx

    async def test_scenario_16_token_usage_accumulation(
        self, agent_test_registry: ToolRegistry
    ) -> None:
        """Token metrics accumulate correctly across multi-turn iterations."""
        fake_model = FakeModelProvider()
        fake_model.queue_tool_call_response(
            tool_name="multiply",
            arguments={"x": 2, "y": 3},
        )
        fake_model.queue_text_response("Result is 6.")

        loop = AgentLoop(model_provider=fake_model, registry=agent_test_registry)
        req = AgentExecutionRequest(task_id="t-16", prompt="Accumulate usage")
        res = await loop.run(req)

        assert res.usage is not None
        assert res.usage["total_tokens"] > 0
        assert res.usage["prompt_tokens"] > 0
        assert res.usage["completion_tokens"] > 0

    async def test_scenario_17_system_prompt_and_history_preserved(
        self, agent_test_registry: ToolRegistry
    ) -> None:
        """Initial system prompt and prior messages are preserved at the head of conversation."""
        fake_model = FakeModelProvider()
        fake_model.queue_text_response("Understood.")

        loop = AgentLoop(model_provider=fake_model, registry=agent_test_registry)
        req = AgentExecutionRequest(
            task_id="t-17",
            prompt="Follow system directive",
            system_prompt="You are a strict code review agent.",
        )
        res = await loop.run(req)

        assert len(res.history) == 3
        assert res.history[0].role == AgentMessageRole.SYSTEM
        assert res.history[0].content == "You are a strict code review agent."
        assert res.history[1].role == AgentMessageRole.USER
        assert res.history[2].role == AgentMessageRole.ASSISTANT

    async def test_scenario_18_sanitize_greeting_response(
        self, agent_test_registry: ToolRegistry
    ) -> None:
        """Greeting prompts sanitize robotic boot headers into polite human replies."""
        fake_model = FakeModelProvider()
        fake_model.queue_text_response(
            "Session Initialized\nReady to assist. Please provide your repository details."
        )

        loop = AgentLoop(model_provider=fake_model, registry=agent_test_registry)
        req = AgentExecutionRequest(
            task_id="t-18",
            prompt="hi",
        )
        res = await loop.run(req)

        assert res.status == TaskStatus.COMPLETED
        assert res.final_response == "Hello! How can I help you with your code or project today?"
        assert res.history[-1].content == "Hello! How can I help you with your code or project today?"
