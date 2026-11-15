"""Tests for deterministic loop detection and oscillation termination."""

import pytest

from api.schemas.tasks import TaskStatus
from core.agent_loop import AgentExecutionRequest, AgentLoop
from core.loop_detector import LoopDetector
from core.message_models import AgentMessage
from model.contracts import ModelProvider, ModelResponse


def test_consecutive_identical_tool_calls_detected() -> None:
    """Verify loop detector catches identical consecutive tool calls."""
    detector = LoopDetector(max_identical_consecutive=3)

    assert detector.record_call("read_file", {"path": "test.py"}) is None
    assert detector.record_call("read_file", {"path": "test.py"}) is None
    # 3rd identical call triggers detection
    diagnostic = detector.record_call("read_file", {"path": "test.py"})
    assert diagnostic is not None
    assert "Loop detected: tool 'read_file' was invoked 3 consecutive times" in diagnostic
    assert "test.py" in diagnostic


def test_different_arguments_do_not_trigger_consecutive_loop() -> None:
    """Verify distinct arguments do not trigger false positive loop detection."""
    detector = LoopDetector(max_identical_consecutive=3)

    assert detector.record_call("read_file", {"path": "file1.py"}) is None
    assert detector.record_call("read_file", {"path": "file2.py"}) is None
    assert detector.record_call("read_file", {"path": "file3.py"}) is None
    assert detector.detect_loop() is None


def test_oscillating_ping_pong_loop_detected() -> None:
    """Verify loop detector catches alternating A -> B -> A -> B patterns."""
    detector = LoopDetector(max_cycle_repetitions=2)

    # Cycle 1: search -> read
    assert detector.record_call("search_code", {"query": "auth"}) is None
    assert detector.record_call("read_file", {"path": "auth.py"}) is None

    # Cycle 2: search -> read (identical pattern repeated)
    assert detector.record_call("search_code", {"query": "auth"}) is None
    diagnostic = detector.record_call("read_file", {"path": "auth.py"})

    assert diagnostic is not None
    assert "Oscillating loop detected" in diagnostic
    assert "search_code -> read_file" in diagnostic


def test_reset_clears_history() -> None:
    """Verify reset clears tracked invocations."""
    detector = LoopDetector(max_identical_consecutive=2)
    detector.record_call("list_directory", {"path": "."})
    detector.reset()
    # 2nd call after reset is treated as first call
    assert detector.record_call("list_directory", {"path": "."}) is None


class RepeatingToolModel(ModelProvider):
    """Mock model that perpetually requests the same tool call with identical arguments."""

    def __init__(self, tool_name: str = "read_file", path: str = "main.py") -> None:
        self.tool_name = tool_name
        self.path = path
        self.call_count = 0

    async def generate(self, *args, **kwargs) -> ModelResponse:
        self.call_count += 1
        return ModelResponse(
            text="Let me inspect the file again.",
            model="mock-qwen",
            tool_calls=[
                {
                    "id": f"call_{self.call_count}",
                    "function": {
                        "name": self.tool_name,
                        "arguments": {"path": self.path},
                    },
                }
            ],
            finish_reason="tool_calls",
        )

    async def check_connectivity(self) -> bool:
        return True


@pytest.mark.asyncio
async def test_agent_loop_terminates_on_detected_loop(tmp_path) -> None:
    """Verify that AgentLoop terminates execution when a tool failure loop is detected."""
    model = RepeatingToolModel()
    loop = AgentLoop(model_provider=model)

    req = AgentExecutionRequest(
        task_id="loop-test-1",
        prompt="Fix the file",
        max_iterations=10,
        tool_timeout_seconds=5.0,
    )

    result = await loop.run(req)
    assert result.status == TaskStatus.FAILED
    assert result.termination_reason == "loop_detected"
    assert "Loop detected: tool 'read_file' was invoked 3 consecutive times" in result.error
