"""Unit tests for dynamic task limitations and loop detector safety."""

import pytest
from pydantic import ValidationError

from api.schemas.tasks import TaskCreateRequest
from core.agent_loop import AgentExecutionRequest
from core.config import Settings, reset_settings
from core.loop_detector import LoopDetector


class TestDynamicTaskLimits:
    """Verify that task limitations are dynamic and configurable, not artificially choked."""

    def setup_method(self) -> None:
        reset_settings()

    def teardown_method(self) -> None:
        reset_settings()

    def test_task_create_request_accepts_dynamic_limits(self) -> None:
        """Callers can dynamically specify max_iterations, max_tool_calls, and task_timeout_seconds."""
        req = TaskCreateRequest(
            prompt="Build a large full-stack application",
            max_iterations=150,
            max_tool_calls=500,
            task_timeout_seconds=3600.0,
        )
        assert req.max_iterations == 150
        assert req.max_tool_calls == 500
        assert req.task_timeout_seconds == 3600.0

    def test_task_create_request_defaults_to_none_for_dynamic_fields(self) -> None:
        """Dynamic fields default to None so server defaults can apply gracefully."""
        req = TaskCreateRequest(prompt="Simple task")
        assert req.max_iterations is None
        assert req.max_tool_calls is None
        assert req.task_timeout_seconds is None

    def test_agent_execution_request_allows_high_iteration_bounds(self) -> None:
        """AgentExecutionRequest accommodates long-running tasks up to 1000 iterations."""
        req = AgentExecutionRequest(
            task_id="task-dyn-01",
            prompt="Long-running generation",
            max_iterations=500,
            max_tool_calls=1500,
            task_timeout_seconds=7200.0,
        )
        assert req.max_iterations == 500
        assert req.max_tool_calls == 1500
        assert req.task_timeout_seconds == 7200.0

    def test_config_settings_allow_expanded_limits_via_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Server settings accept expanded limits from environment variables up to 1000."""
        monkeypatch.setenv("AGENT_MAX_ITERATIONS", "250")
        monkeypatch.setenv("AGENT_MAX_TOOL_CALLS", "800")
        monkeypatch.setenv("AGENT_TASK_TIMEOUT_SECONDS", "3600.0")

        settings = Settings()
        assert settings.agent_max_iterations == 250
        assert settings.agent_max_tool_calls == 800
        assert settings.agent_task_timeout_seconds == 3600.0

    def test_loop_detector_safeguards_against_true_infinite_loops(self) -> None:
        """LoopDetector hard-stops consecutive identical tool invocations (infinite loop prevention)."""
        detector = LoopDetector(max_identical_consecutive=3)

        # First and second calls are permitted
        assert detector.record_call("write_file", {"path": "test.txt", "content": "same"}) is None
        assert detector.record_call("write_file", {"path": "test.txt", "content": "same"}) is None

        # Third identical call triggers loop detection and stops execution
        loop_msg = detector.record_call("write_file", {"path": "test.txt", "content": "same"})
        assert loop_msg is not None
        assert "Loop detected" in loop_msg
        assert "consecutive times" in loop_msg

    def test_loop_detector_permits_progressive_different_calls(self) -> None:
        """LoopDetector does not stop healthy progressive work on different files."""
        detector = LoopDetector(max_identical_consecutive=3)

        for i in range(50):
            res = detector.record_call("write_file", {"path": f"file_{i}.ts", "content": f"code_{i}"})
            assert res is None, f"Premature loop trigger at step {i}"
