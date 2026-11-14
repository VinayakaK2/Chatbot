"""Deterministic concurrency and atomic state transition tests for Phase 3 Defect F-02."""

import asyncio
from datetime import datetime, timezone
import threading
import time
from typing import Any, Dict, List, Optional
import pytest

from api.schemas.events import RuntimeEvent, RuntimeEventType
from api.schemas.tasks import TaskCreateRequest, TaskResponse, TaskStatus
from core.agent_loop import AgentExecutionRequest, AgentExecutionResult, AgentLoop
from core.config import Settings
from core.task_service import TaskService, set_task_service
from model.contracts import ModelProvider, ModelResponse


class ControllableMockProvider(ModelProvider):
    """Mock provider with execution gates to deterministically control execution timing."""

    def __init__(
        self,
        response_text: str = "Deterministic test completion.",
        before_response_gate: Optional[asyncio.Event] = None,
        after_gate_trigger: Optional[asyncio.Event] = None,
    ) -> None:
        self.response_text = response_text
        self.before_response_gate = before_response_gate
        self.after_gate_trigger = after_gate_trigger
        self.call_count = 0

    async def generate(self, prompt: str, **kwargs: Any) -> ModelResponse:
        self.call_count += 1
        if self.after_gate_trigger:
            self.after_gate_trigger.set()
        if self.before_response_gate:
            await self.before_response_gate.wait()

        return ModelResponse(
            text=self.response_text,
            finish_reason="stop",
            model="mock-qwen-concurrency-test",
            usage={"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20},
            request_id=f"mock-req-{self.call_count}",
        )

    async def check_connectivity(self) -> bool:
        return True


@pytest.fixture
def clean_service() -> TaskService:
    """Fixture providing a fresh TaskService instance."""
    service = TaskService(Settings())
    set_task_service(service)
    return service


# ============================================================================
# GROUP A: Cancellation and Completion Interleavings
# ============================================================================

@pytest.mark.asyncio
async def test_scenario_01_cancellation_immediately_before_completion(clean_service: TaskService) -> None:
    """Scenario 1: Cancellation occurs while the execution loop is in flight right before completion."""
    started_gate = asyncio.Event()
    continue_gate = asyncio.Event()

    provider = ControllableMockProvider(
        response_text="Will be discarded by cancellation.",
        before_response_gate=continue_gate,
        after_gate_trigger=started_gate,
    )
    service = TaskService(Settings(), model_provider=provider)

    # Launch task in background
    task_future = asyncio.create_task(service.submit_task(TaskCreateRequest(prompt="Task 1")))
    await started_gate.wait()

    # Cancel while task is actively executing
    tasks = service._tasks
    assert len(tasks) == 1
    task_id = next(iter(tasks.keys()))

    cancel_resp = service.cancel_task(task_id)
    assert cancel_resp is not None
    assert cancel_resp.status == TaskStatus.CANCELLED

    # Unblock model generation so execution completes
    continue_gate.set()
    final_resp = await task_future

    # Verify task state remains CANCELLED and was NOT overwritten by COMPLETED
    assert final_resp.status == TaskStatus.CANCELLED
    stored = service.get_task(task_id)
    assert stored is not None
    assert stored.status == TaskStatus.CANCELLED

    # Verify terminal event count
    events = service.get_events(task_id)
    terminal_events = [e for e in events if e.event_type == RuntimeEventType.TASK_TERMINAL]
    assert len(terminal_events) == 1
    assert terminal_events[0].payload["status"] == TaskStatus.CANCELLED.value


@pytest.mark.asyncio
async def test_scenario_02_cancellation_while_preparing_final_response(clean_service: TaskService) -> None:
    """Scenario 2: Cancellation arrives during the transition to completion."""
    service = clean_service
    task_id = "test-task-2"
    created_at = datetime.now(timezone.utc).isoformat()

    # Register initial EXECUTING task
    service._tasks[task_id] = TaskResponse(
        task_id=task_id,
        prompt="Prompt 2",
        status=TaskStatus.EXECUTING,
        created_at=created_at,
    )

    # Cancel task
    cancel_resp = service.cancel_task(task_id)
    assert cancel_resp is not None
    assert cancel_resp.status == TaskStatus.CANCELLED

    # Now attempt a late COMPLETED transition
    completed_task = TaskResponse(
        task_id=task_id,
        prompt="Prompt 2",
        status=TaskStatus.COMPLETED,
        response_text="Late response",
        created_at=created_at,
        completed_at=datetime.now(timezone.utc).isoformat(),
    )
    success, stored = service._transition_task_state(task_id, completed_task)

    assert success is False
    assert stored.status == TaskStatus.CANCELLED
    assert service.get_task(task_id).status == TaskStatus.CANCELLED


@pytest.mark.asyncio
async def test_scenario_03_completion_immediately_before_cancellation(clean_service: TaskService) -> None:
    """Scenario 3: Normal completion finishes; subsequent cancellation must not overwrite COMPLETED."""
    provider = ControllableMockProvider(response_text="Immediate success.")
    service = TaskService(Settings(), model_provider=provider)

    task_resp = await service.submit_task(TaskCreateRequest(prompt="Task 3"))
    assert task_resp.status == TaskStatus.COMPLETED

    # Now attempt cancellation on the completed task
    cancel_resp = service.cancel_task(task_resp.task_id)
    assert cancel_resp is not None
    # Must remain COMPLETED, not CANCELLED
    assert cancel_resp.status == TaskStatus.COMPLETED

    stored = service.get_task(task_resp.task_id)
    assert stored.status == TaskStatus.COMPLETED

    # Verify no TASK_CANCELLED event was emitted
    events = service.get_events(task_resp.task_id)
    event_types = [e.event_type for e in events]
    assert RuntimeEventType.TASK_CANCELLED not in event_types


@pytest.mark.asyncio
async def test_scenario_04_concurrent_cancellation_and_completion_barrier(clean_service: TaskService) -> None:
    """Scenario 4: Cancellation and completion compete at the exact same instant using a barrier."""
    service = clean_service
    task_id = "test-task-4"
    created_at = datetime.now(timezone.utc).isoformat()

    service._tasks[task_id] = TaskResponse(
        task_id=task_id,
        prompt="Prompt 4",
        status=TaskStatus.EXECUTING,
        created_at=created_at,
    )

    completed_task = TaskResponse(
        task_id=task_id,
        prompt="Prompt 4",
        status=TaskStatus.COMPLETED,
        response_text="Concurrent result",
        created_at=created_at,
        completed_at=datetime.now(timezone.utc).isoformat(),
    )

    barrier = threading.Barrier(2)
    results: Dict[str, Any] = {}

    def run_completion():
        barrier.wait()
        res = service._transition_task_state(task_id, completed_task)
        results["completion"] = res

    def run_cancellation():
        barrier.wait()
        res = service.cancel_task(task_id)
        results["cancellation"] = res

    t1 = threading.Thread(target=run_completion)
    t2 = threading.Thread(target=run_cancellation)
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    stored = service.get_task(task_id)
    assert stored is not None
    # Exactly one terminal status must win
    assert stored.status in (TaskStatus.COMPLETED, TaskStatus.CANCELLED)

    # Exactly one TASK_TERMINAL event in event log
    events = service.get_events(task_id)
    terminal_events = [e for e in events if e.event_type == RuntimeEventType.TASK_TERMINAL]
    assert len(terminal_events) == 1
    assert terminal_events[0].payload["status"] == stored.status.value


@pytest.mark.asyncio
async def test_scenario_05_repeated_cancellation_requests(clean_service: TaskService) -> None:
    """Scenario 5: Multiple repeated cancellation calls are idempotent with no duplicate events."""
    service = clean_service
    task_id = "test-task-5"
    created_at = datetime.now(timezone.utc).isoformat()

    service._tasks[task_id] = TaskResponse(
        task_id=task_id,
        prompt="Prompt 5",
        status=TaskStatus.EXECUTING,
        created_at=created_at,
    )

    r1 = service.cancel_task(task_id)
    r2 = service.cancel_task(task_id)
    r3 = service.cancel_task(task_id)

    assert r1.status == TaskStatus.CANCELLED
    assert r2.status == TaskStatus.CANCELLED
    assert r3.status == TaskStatus.CANCELLED

    events = service.get_events(task_id)
    cancelled_events = [e for e in events if e.event_type == RuntimeEventType.TASK_CANCELLED]
    terminal_events = [e for e in events if e.event_type == RuntimeEventType.TASK_TERMINAL]

    assert len(cancelled_events) == 1
    assert len(terminal_events) == 1


@pytest.mark.asyncio
async def test_scenario_06_cancellation_of_pending_task(clean_service: TaskService) -> None:
    """Scenario 6: Cancellation of a task in PENDING state transitions to CANCELLED."""
    service = clean_service
    task_id = "test-task-6"
    created_at = datetime.now(timezone.utc).isoformat()

    service._tasks[task_id] = TaskResponse(
        task_id=task_id,
        prompt="Prompt 6",
        status=TaskStatus.PENDING,
        created_at=created_at,
    )

    cancelled = service.cancel_task(task_id)
    assert cancelled is not None
    assert cancelled.status == TaskStatus.CANCELLED
    assert service.get_task(task_id).status == TaskStatus.CANCELLED


@pytest.mark.asyncio
async def test_scenario_07_cancellation_of_already_completed_task(clean_service: TaskService) -> None:
    """Scenario 7: Cancellation on COMPLETED task returns existing task and does not mutate state."""
    service = clean_service
    task_id = "test-task-7"
    created_at = datetime.now(timezone.utc).isoformat()

    service._tasks[task_id] = TaskResponse(
        task_id=task_id,
        prompt="Prompt 7",
        status=TaskStatus.COMPLETED,
        response_text="Done",
        created_at=created_at,
        completed_at=created_at,
    )

    cancelled = service.cancel_task(task_id)
    assert cancelled is not None
    assert cancelled.status == TaskStatus.COMPLETED
    assert service.get_task(task_id).status == TaskStatus.COMPLETED


# ============================================================================
# GROUP B: Other Terminal States
# ============================================================================

@pytest.mark.asyncio
async def test_scenario_08_failure_followed_by_cancellation(clean_service: TaskService) -> None:
    """Scenario 8: A failed task cannot be cancelled; FAILED state is immutable."""
    service = clean_service
    task_id = "test-task-8"
    created_at = datetime.now(timezone.utc).isoformat()

    failed_task = TaskResponse(
        task_id=task_id,
        prompt="Prompt 8",
        status=TaskStatus.FAILED,
        error="Simulated runtime error",
        created_at=created_at,
        completed_at=created_at,
    )
    service._tasks[task_id] = failed_task

    cancelled = service.cancel_task(task_id)
    assert cancelled is not None
    assert cancelled.status == TaskStatus.FAILED
    assert service.get_task(task_id).status == TaskStatus.FAILED


@pytest.mark.asyncio
async def test_scenario_09_cancellation_followed_by_late_failure(clean_service: TaskService) -> None:
    """Scenario 9: If cancelled, a late exception/failure is discarded; task remains CANCELLED."""
    service = clean_service
    task_id = "test-task-9"
    created_at = datetime.now(timezone.utc).isoformat()

    service._tasks[task_id] = TaskResponse(
        task_id=task_id,
        prompt="Prompt 9",
        status=TaskStatus.EXECUTING,
        created_at=created_at,
    )
    service.cancel_task(task_id)
    assert service.get_task(task_id).status == TaskStatus.CANCELLED

    # Late failure attempt
    failed_task = TaskResponse(
        task_id=task_id,
        prompt="Prompt 9",
        status=TaskStatus.FAILED,
        error="Late failure after cancel",
        created_at=created_at,
        completed_at=created_at,
    )
    success, current = service._transition_task_state(task_id, failed_task)
    assert success is False
    assert current.status == TaskStatus.CANCELLED
    assert service.get_task(task_id).status == TaskStatus.CANCELLED


@pytest.mark.asyncio
async def test_scenario_10_limit_reached_followed_by_cancellation(clean_service: TaskService) -> None:
    """Scenario 10: LIMIT_REACHED terminal state cannot be overwritten by cancellation."""
    service = clean_service
    task_id = "test-task-10"
    created_at = datetime.now(timezone.utc).isoformat()

    service._tasks[task_id] = TaskResponse(
        task_id=task_id,
        prompt="Prompt 10",
        status=TaskStatus.LIMIT_REACHED,
        termination_reason="max_iterations",
        created_at=created_at,
        completed_at=created_at,
    )

    cancelled = service.cancel_task(task_id)
    assert cancelled.status == TaskStatus.LIMIT_REACHED
    assert service.get_task(task_id).status == TaskStatus.LIMIT_REACHED


@pytest.mark.asyncio
async def test_scenario_11_late_completion_after_cancellation(clean_service: TaskService) -> None:
    """Scenario 11: Late completion arriving after cancellation is rejected."""
    service = clean_service
    task_id = "test-task-11"
    created_at = datetime.now(timezone.utc).isoformat()

    service._tasks[task_id] = TaskResponse(
        task_id=task_id,
        prompt="Prompt 11",
        status=TaskStatus.EXECUTING,
        created_at=created_at,
    )
    service.cancel_task(task_id)

    late_task = TaskResponse(
        task_id=task_id,
        prompt="Prompt 11",
        status=TaskStatus.COMPLETED,
        response_text="Late completion text",
        created_at=created_at,
        completed_at=created_at,
    )
    success, current = service._transition_task_state(task_id, late_task)
    assert success is False
    assert current.status == TaskStatus.CANCELLED
    assert current.response_text is None


@pytest.mark.asyncio
async def test_scenario_12_repeated_transition_attempts_on_terminal_task(clean_service: TaskService) -> None:
    """Scenario 12: Repeated attempts to change terminal state fail closed."""
    service = clean_service
    task_id = "test-task-12"
    created_at = datetime.now(timezone.utc).isoformat()

    service._tasks[task_id] = TaskResponse(
        task_id=task_id,
        prompt="Prompt 12",
        status=TaskStatus.COMPLETED,
        created_at=created_at,
        completed_at=created_at,
    )

    for target_status in (TaskStatus.EXECUTING, TaskStatus.CANCELLED, TaskStatus.FAILED, TaskStatus.LIMIT_REACHED):
        candidate = TaskResponse(
            task_id=task_id,
            prompt="Prompt 12",
            status=target_status,
            created_at=created_at,
        )
        success, current = service._transition_task_state(task_id, candidate)
        assert success is False
        assert current.status == TaskStatus.COMPLETED


# ============================================================================
# GROUP C: Concurrency and Thread Safety
# ============================================================================

@pytest.mark.asyncio
async def test_scenario_13_two_simultaneous_cancellation_requests(clean_service: TaskService) -> None:
    """Scenario 13: Two cancellation requests from different threads serialize without conflict."""
    service = clean_service
    task_id = "test-task-13"
    created_at = datetime.now(timezone.utc).isoformat()

    service._tasks[task_id] = TaskResponse(
        task_id=task_id,
        prompt="Prompt 13",
        status=TaskStatus.EXECUTING,
        created_at=created_at,
    )

    barrier = threading.Barrier(2)
    results = []

    def cancel_worker():
        barrier.wait()
        res = service.cancel_task(task_id)
        results.append(res)

    t1 = threading.Thread(target=cancel_worker)
    t2 = threading.Thread(target=cancel_worker)
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    assert len(results) == 2
    assert results[0].status == TaskStatus.CANCELLED
    assert results[1].status == TaskStatus.CANCELLED

    # Verify single TASK_TERMINAL event emitted
    events = service.get_events(task_id)
    terminal_events = [e for e in events if e.event_type == RuntimeEventType.TASK_TERMINAL]
    assert len(terminal_events) == 1


@pytest.mark.asyncio
async def test_scenario_14_multiple_independent_tasks_concurrent(clean_service: TaskService) -> None:
    """Scenario 14: Multiple concurrent tasks execute and cancel independently without interference."""
    provider = ControllableMockProvider(response_text="Multi-task answer")
    service = TaskService(Settings(), model_provider=provider)

    task_futures = [
        service.submit_task(TaskCreateRequest(prompt=f"Multi task {i}"))
        for i in range(10)
    ]
    results = await asyncio.gather(*task_futures)
    assert len(results) == 10
    for r in results:
        assert r.status == TaskStatus.COMPLETED
        assert service.get_task(r.task_id).status == TaskStatus.COMPLETED


@pytest.mark.asyncio
async def test_scenario_15_concurrent_status_polling_during_transition(clean_service: TaskService) -> None:
    """Scenario 15: Concurrent get_task calls during transitions always read valid snapshots."""
    service = clean_service
    task_id = "test-task-15"
    created_at = datetime.now(timezone.utc).isoformat()

    service._tasks[task_id] = TaskResponse(
        task_id=task_id,
        prompt="Prompt 15",
        status=TaskStatus.EXECUTING,
        created_at=created_at,
    )

    observed_statuses = set()
    stop_event = threading.Event()

    def poller():
        while not stop_event.is_set():
            t = service.get_task(task_id)
            if t:
                observed_statuses.add(t.status)

    poll_thread = threading.Thread(target=poller)
    poll_thread.start()

    # Perform transition
    service.cancel_task(task_id)
    stop_event.set()
    poll_thread.join()

    # Observed statuses must be subsets of valid lifecycle states
    for st in observed_statuses:
        assert st in (TaskStatus.EXECUTING, TaskStatus.CANCELLED)


@pytest.mark.asyncio
async def test_scenario_16_per_task_lock_does_not_block_unrelated_tasks(clean_service: TaskService) -> None:
    """Scenario 16: Holding lock on Task A does not block operations on Task B."""
    service = clean_service
    task_a = "task-a"
    task_b = "task-b"
    created_at = datetime.now(timezone.utc).isoformat()

    service._tasks[task_a] = TaskResponse(
        task_id=task_a, prompt="Prompt A", status=TaskStatus.EXECUTING, created_at=created_at
    )
    service._tasks[task_b] = TaskResponse(
        task_id=task_b, prompt="Prompt B", status=TaskStatus.EXECUTING, created_at=created_at
    )

    lock_a = service._get_task_lock(task_a)
    task_b_succeeded = False

    # Acquire lock A in thread
    with lock_a:
        # Operations on task B must proceed without blocking
        res_b = service.cancel_task(task_b)
        assert res_b is not None
        assert res_b.status == TaskStatus.CANCELLED
        task_b_succeeded = True

    assert task_b_succeeded is True


# ============================================================================
# GROUP D: Event Consistency and Traceability
# ============================================================================

@pytest.mark.asyncio
async def test_scenario_17_cancellation_no_contradictory_completion_event(clean_service: TaskService) -> None:
    """Scenario 17: Cancelled task produces no subsequent completion event."""
    service = clean_service
    task_id = "test-task-17"
    created_at = datetime.now(timezone.utc).isoformat()

    service._tasks[task_id] = TaskResponse(
        task_id=task_id, prompt="Prompt 17", status=TaskStatus.EXECUTING, created_at=created_at
    )
    service.cancel_task(task_id)

    # Attempt completed transition
    service._transition_task_state(
        task_id,
        TaskResponse(
            task_id=task_id,
            prompt="Prompt 17",
            status=TaskStatus.COMPLETED,
            response_text="Never emitted",
            created_at=created_at,
            completed_at=created_at,
        ),
        terminal_event_payload={"status": "completed"},
    )

    events = service.get_events(task_id)
    terminal_events = [e for e in events if e.event_type == RuntimeEventType.TASK_TERMINAL]
    assert len(terminal_events) == 1
    assert terminal_events[0].payload["status"] == "cancelled"


@pytest.mark.asyncio
async def test_scenario_18_sse_history_consistent_terminal_state(clean_service: TaskService) -> None:
    """Scenario 18: Exactly one terminal event exists in event history."""
    provider = ControllableMockProvider(response_text="Event test")
    service = TaskService(Settings(), model_provider=provider)

    task_resp = await service.submit_task(TaskCreateRequest(prompt="SSE history test"))
    events = service.get_events(task_resp.task_id)

    terminal_events = [e for e in events if e.event_type == RuntimeEventType.TASK_TERMINAL]
    assert len(terminal_events) == 1
    assert terminal_events[0].payload["status"] == task_resp.status.value


@pytest.mark.asyncio
async def test_scenario_19_event_ordering_correctness(clean_service: TaskService) -> None:
    """Scenario 19: Events follow TASK_CREATED -> (intermediate) -> TASK_TERMINAL order."""
    service = clean_service
    task_id = "test-task-19"
    created_at = datetime.now(timezone.utc).isoformat()

    service._record_event(task_id, RuntimeEventType.TASK_CREATED, {"prompt": "Order test"})
    service._tasks[task_id] = TaskResponse(
        task_id=task_id, prompt="Order test", status=TaskStatus.EXECUTING, created_at=created_at
    )
    service.cancel_task(task_id)

    events = service.get_events(task_id)
    types = [e.event_type for e in events]
    assert types == [
        RuntimeEventType.TASK_CREATED,
        RuntimeEventType.TASK_CANCELLED,
        RuntimeEventType.TASK_TERMINAL,
    ]


@pytest.mark.asyncio
async def test_scenario_20_frontend_status_mapping_compatibility(clean_service: TaskService) -> None:
    """Scenario 20: Stored status values strictly match Frontend TypeScript TaskStatus union."""
    valid_frontend_statuses = {
        "pending",
        "executing",
        "completed",
        "failed",
        "cancelled",
        "limit_reached",
    }
    for status_enum in TaskStatus:
        assert status_enum.value in valid_frontend_statuses


# ============================================================================
# GROUP E: Forensic Deep-Dive Tests (F-02-A, F-02-B, F-02-C, F-02-D)
# ============================================================================

@pytest.mark.asyncio
async def test_forensic_01_integration_submit_and_cancel_race(clean_service: TaskService) -> None:
    """F-02-C Integration: Test end-to-end race through public submit_task() and cancel_task() APIs."""
    gate = asyncio.Event()
    provider = ControllableMockProvider(
        response_text="Full integration completion.",
        before_response_gate=gate,
    )
    service = TaskService(Settings(), model_provider=provider)

    # Launch task via public submit_task API
    task_future = asyncio.create_task(service.submit_task(TaskCreateRequest(prompt="Integration race")))

    # Yield control so task starts executing
    await asyncio.sleep(0.01)

    # Inspect active task ID
    tasks = service._tasks
    assert len(tasks) == 1
    task_id = next(iter(tasks.keys()))
    assert service.get_task(task_id).status == TaskStatus.EXECUTING

    # Call public cancel_task() while loop is awaiting model
    cancel_resp = service.cancel_task(task_id)
    assert cancel_resp is not None
    assert cancel_resp.status == TaskStatus.CANCELLED

    # Now release the model gate so agent loop finishes
    gate.set()
    submit_result = await task_future

    # Verify both returned object and stored state are CANCELLED
    assert submit_result.status == TaskStatus.CANCELLED
    assert service.get_task(task_id).status == TaskStatus.CANCELLED

    # Verify event stream has exactly one terminal event
    events = service.get_events(task_id)
    terminals = [e for e in events if e.event_type == RuntimeEventType.TASK_TERMINAL]
    assert len(terminals) == 1
    assert terminals[0].payload["status"] == "cancelled"


@pytest.mark.asyncio
async def test_forensic_02_linearizable_policy_both_orders(clean_service: TaskService) -> None:
    """F-02-C Semantics: Verify linearizable first-to-commit policy under both possible orders."""
    service = clean_service
    created_at = datetime.now(timezone.utc).isoformat()

    # --- ORDER A: Cancellation commits before Completion ---
    task_a = "linear-a"
    service._tasks[task_a] = TaskResponse(
        task_id=task_a, prompt="A", status=TaskStatus.EXECUTING, created_at=created_at
    )
    # Cancel commits first
    c1 = service.cancel_task(task_a)
    assert c1.status == TaskStatus.CANCELLED

    # Late completion attempts commit
    late_complete = TaskResponse(
        task_id=task_a, prompt="A", status=TaskStatus.COMPLETED, created_at=created_at, completed_at=created_at
    )
    applied_a, current_a = service._transition_task_state(task_a, late_complete)
    assert applied_a is False
    assert current_a.status == TaskStatus.CANCELLED
    assert service.get_task(task_a).status == TaskStatus.CANCELLED

    # --- ORDER B: Completion commits before Cancellation ---
    task_b = "linear-b"
    service._tasks[task_b] = TaskResponse(
        task_id=task_b, prompt="B", status=TaskStatus.EXECUTING, created_at=created_at
    )
    # Completion commits first
    early_complete = TaskResponse(
        task_id=task_b, prompt="B", status=TaskStatus.COMPLETED, created_at=created_at, completed_at=created_at
    )
    applied_b, current_b = service._transition_task_state(task_b, early_complete)
    assert applied_b is True
    assert current_b.status == TaskStatus.COMPLETED

    # Late cancellation attempts commit
    c2 = service.cancel_task(task_b)
    assert c2.status == TaskStatus.COMPLETED  # Cannot overwrite completed task
    assert service.get_task(task_b).status == TaskStatus.COMPLETED


@pytest.mark.asyncio
async def test_forensic_03_initial_state_validation_rejects_invalid_states(clean_service: TaskService) -> None:
    """F-02-D Validation: Initial state transition accepts only PENDING, EXECUTING, or FAILED."""
    service = clean_service
    created_at = datetime.now(timezone.utc).isoformat()

    # Valid initial states: PENDING, EXECUTING, FAILED
    for valid_status in (TaskStatus.PENDING, TaskStatus.EXECUTING, TaskStatus.FAILED):
        tid = f"valid-init-{valid_status.value}"
        t = TaskResponse(task_id=tid, prompt="P", status=valid_status, created_at=created_at)
        applied, res = service._transition_task_state(tid, t)
        assert applied is True
        assert res.status == valid_status

    # Invalid initial states for brand-new tasks: COMPLETED, CANCELLED, LIMIT_REACHED
    for invalid_status in (TaskStatus.COMPLETED, TaskStatus.CANCELLED, TaskStatus.LIMIT_REACHED):
        tid = f"invalid-init-{invalid_status.value}"
        t = TaskResponse(task_id=tid, prompt="P", status=invalid_status, created_at=created_at)
        applied, res = service._transition_task_state(tid, t)
        assert applied is False
        assert service.get_task(tid) is None


@pytest.mark.asyncio
async def test_forensic_04_sse_live_queue_delivery(clean_service: TaskService) -> None:
    """F-02-A Live SSE: Test that subscribe_events delivers actual events to an asyncio.Queue."""
    service = clean_service
    task_id = "sse-live-task"
    queue: asyncio.Queue[Optional[RuntimeEvent]] = asyncio.Queue()

    # Subscribe queue before execution
    service.subscribe_events(task_id, queue)

    # Initial executing state
    created_at = datetime.now(timezone.utc).isoformat()
    t = TaskResponse(task_id=task_id, prompt="SSE", status=TaskStatus.EXECUTING, created_at=created_at)
    service._transition_task_state(task_id, t)

    # Cancel task
    service.cancel_task(task_id)

    # Read events off the live queue
    received = []
    while True:
        try:
            ev = await asyncio.wait_for(queue.get(), timeout=0.1)
            if ev is not None:
                received.append(ev)
                if ev.event_type == RuntimeEventType.TASK_TERMINAL:
                    break
        except asyncio.TimeoutError:
            break

    # Must have received TASK_CANCELLED and TASK_TERMINAL
    event_types = [e.event_type for e in received]
    assert RuntimeEventType.TASK_CANCELLED in event_types
    assert RuntimeEventType.TASK_TERMINAL in event_types

    # Unsubscribe
    service.unsubscribe_events(task_id, queue)


@pytest.mark.asyncio
async def test_forensic_05_cross_thread_event_broadcast_to_asyncio_queue(clean_service: TaskService) -> None:
    """F-02-A Thread Safety: Event broadcast from an OS worker thread safely delivers to asyncio.Queue."""
    service = clean_service
    task_id = "cross-thread-task"
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[Optional[RuntimeEvent]] = asyncio.Queue()

    service.subscribe_events(task_id, queue)
    created_at = datetime.now(timezone.utc).isoformat()
    t = TaskResponse(task_id=task_id, prompt="Thread", status=TaskStatus.EXECUTING, created_at=created_at)
    service._transition_task_state(task_id, t)

    # Trigger cancel_task from an independent worker thread (simulating FastAPI threadpool)
    def thread_worker():
        service.cancel_task(task_id)

    worker_thread = threading.Thread(target=thread_worker)
    worker_thread.start()
    worker_thread.join()

    # Read delivered event from queue on the event loop
    ev = await asyncio.wait_for(queue.get(), timeout=1.0)
    assert ev is not None
    assert ev.event_type in (RuntimeEventType.TASK_CANCELLED, RuntimeEventType.TASK_TERMINAL)

    # Clean up
    service.unsubscribe_events(task_id, queue)


@pytest.mark.asyncio
async def test_forensic_06_task_cleanup_lifecycle_and_lock_removal(clean_service: TaskService) -> None:
    """F-02-B Lifecycle: cleanup_task safely removes terminal tasks and locks without leaking."""
    service = clean_service
    task_id = "cleanup-lifecycle-task"
    created_at = datetime.now(timezone.utc).isoformat()

    # 1. Active task cannot be cleaned up
    t = TaskResponse(task_id=task_id, prompt="Clean", status=TaskStatus.EXECUTING, created_at=created_at)
    service._transition_task_state(task_id, t)
    assert service.cleanup_task(task_id) is False
    assert service.get_task(task_id) is not None

    # 2. Transition to terminal state
    service.cancel_task(task_id)
    assert service.get_task(task_id).status == TaskStatus.CANCELLED

    # 3. Terminal task can be safely cleaned up
    assert service.cleanup_task(task_id) is True

    # 4. Confirm all resources and locks were pruned
    assert service.get_task(task_id) is None
    assert task_id not in service._tasks
    assert task_id not in service._events
    assert task_id not in service._task_locks
    assert len(service.get_events(task_id)) == 0


# ============================================================================
# GROUP F: Follow-up Verification (12 Explicit Remediation Interleavings)
# ============================================================================

@pytest.mark.asyncio
async def test_interleaving_01_cleanup_racing_with_state_transition_no_deadlock(clean_service: TaskService) -> None:
    """1. Cleanup racing with state transition cannot deadlock."""
    service = clean_service
    task_id = "interleaving-01"
    created_at = datetime.now(timezone.utc).isoformat()

    # Initialize task in EXECUTING state
    service._tasks[task_id] = TaskResponse(
        task_id=task_id, prompt="Prompt 01", status=TaskStatus.EXECUTING, created_at=created_at
    )
    service._get_task_lock(task_id, create=True)

    completed_task = TaskResponse(
        task_id=task_id, prompt="Prompt 01", status=TaskStatus.COMPLETED, created_at=created_at, completed_at=created_at
    )

    barrier = threading.Barrier(2)
    transition_result: Dict[str, Any] = {}
    cleanup_result: Dict[str, Any] = {}

    def transition_worker():
        barrier.wait()
        res = service._transition_task_state(task_id, completed_task)
        transition_result["applied"] = res[0]

    def cleanup_worker():
        barrier.wait()
        res = service.cleanup_task(task_id)
        cleanup_result["cleaned"] = res

    t1 = threading.Thread(target=transition_worker)
    t2 = threading.Thread(target=cleanup_worker)
    t1.start()
    t2.start()

    # Must complete promptly without deadlock (deadlock would hang or exceed timeout)
    t1.join(timeout=3.0)
    t2.join(timeout=3.0)

    assert not t1.is_alive(), "Deadlock detected in transition_worker"
    assert not t2.is_alive(), "Deadlock detected in cleanup_worker"
    assert "applied" in transition_result
    assert "cleaned" in cleanup_result


@pytest.mark.asyncio
async def test_interleaving_02_cleanup_racing_with_reads_no_split_lock_identities(clean_service: TaskService) -> None:
    """2. Cleanup racing with get_task/get_events cannot create split lock identities."""
    service = clean_service
    task_id = "interleaving-02"
    created_at = datetime.now(timezone.utc).isoformat()

    service._tasks[task_id] = TaskResponse(
        task_id=task_id, prompt="Prompt 02", status=TaskStatus.COMPLETED, created_at=created_at, completed_at=created_at
    )
    service._get_task_lock(task_id, create=True)

    read_results = []
    stop_reads = threading.Event()

    def reader():
        while not stop_reads.is_set():
            t = service.get_task(task_id)
            evs = service.get_events(task_id)
            read_results.append((t, evs))

    t_reader = threading.Thread(target=reader)
    t_reader.start()

    # Execute cleanup
    cleaned = service.cleanup_task(task_id)
    assert cleaned is True

    # Allow a few more reads after cleanup to ensure ghost locks are NOT spawned
    time.sleep(0.02)
    stop_reads.set()
    t_reader.join(timeout=2.0)

    # After cleanup, lock must NOT exist in _task_locks
    assert task_id not in service._task_locks
    assert service.get_task(task_id) is None
    assert service.get_events(task_id) == []
    # Verify reading after cleanup did not resurrect the lock
    assert task_id not in service._task_locks


@pytest.mark.asyncio
async def test_interleaving_03_cleanup_cannot_remove_lock_still_in_use(clean_service: TaskService) -> None:
    """3. Task cleanup cannot remove a lock still in use."""
    service = clean_service
    task_id = "interleaving-03"
    created_at = datetime.now(timezone.utc).isoformat()

    service._tasks[task_id] = TaskResponse(
        task_id=task_id, prompt="Prompt 03", status=TaskStatus.COMPLETED, created_at=created_at, completed_at=created_at
    )
    lock = service._get_task_lock(task_id, create=True)
    assert lock is not None

    holder_entered = threading.Event()
    release_holder = threading.Event()
    cleanup_finished = threading.Event()
    cleanup_success = []

    def lock_holder():
        with lock:
            holder_entered.set()
            release_holder.wait()

    def cleaner():
        # Will block until holder releases lock
        res = service.cleanup_task(task_id)
        cleanup_success.append(res)
        cleanup_finished.set()

    t_holder = threading.Thread(target=lock_holder)
    t_cleaner = threading.Thread(target=cleaner)

    t_holder.start()
    holder_entered.wait()

    # Cleaner attempts to remove while lock is held by t_holder
    t_cleaner.start()
    time.sleep(0.05)

    # Cleaner must still be waiting for lock, not finished
    assert not cleanup_finished.is_set()

    # Now release lock in holder
    release_holder.set()
    t_holder.join(timeout=2.0)
    t_cleaner.join(timeout=2.0)

    assert cleanup_finished.is_set()
    assert cleanup_success == [True]
    assert task_id not in service._task_locks


@pytest.mark.asyncio
async def test_interleaving_04_worker_thread_publishes_while_sse_awaits_get(clean_service: TaskService) -> None:
    """4. Worker thread publishes while SSE coroutine awaits queue.get()."""
    service = clean_service
    task_id = "interleaving-04"
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[Optional[RuntimeEvent]] = asyncio.Queue()

    service.subscribe_events(task_id, queue, loop=loop)
    created_at = datetime.now(timezone.utc).isoformat()
    service._tasks[task_id] = TaskResponse(
        task_id=task_id, prompt="P04", status=TaskStatus.EXECUTING, created_at=created_at
    )
    service._get_task_lock(task_id, create=True)

    # Coroutine actively awaits queue.get()
    async def sse_reader():
        return await queue.get()

    reader_task = asyncio.create_task(sse_reader())
    await asyncio.sleep(0.01)  # Ensure coroutine is awaiting queue.get()

    def worker_publisher():
        service._record_event(task_id, RuntimeEventType.MODEL_REQUEST_STARTED, {"step": 42})

    t_worker = threading.Thread(target=worker_publisher)
    t_worker.start()
    t_worker.join()

    # Awaiting coroutine must receive the event safely
    ev = await asyncio.wait_for(reader_task, timeout=1.0)
    assert ev is not None
    assert ev.event_type == RuntimeEventType.MODEL_REQUEST_STARTED
    assert ev.payload["step"] == 42

    service.unsubscribe_events(task_id, queue)


@pytest.mark.asyncio
async def test_interleaving_05_queue_delivery_with_known_owning_event_loop(clean_service: TaskService) -> None:
    """5. Queue delivery works with a known owning event loop."""
    service = clean_service
    task_id = "interleaving-05"
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[Optional[RuntimeEvent]] = asyncio.Queue()

    # Explicit loop registration
    service.subscribe_events(task_id, queue, loop=loop)

    subs = service._event_subscribers.get(task_id, [])
    assert len(subs) == 1
    assert subs[0].loop is loop

    service._record_event(task_id, RuntimeEventType.TASK_CREATED, {"test": "known_loop"})
    ev = await asyncio.wait_for(queue.get(), timeout=1.0)
    assert ev is not None
    assert ev.payload["test"] == "known_loop"

    service.unsubscribe_events(task_id, queue)


@pytest.mark.asyncio
async def test_interleaving_06_closed_loop_subscriber_handling_explicit_and_safe(clean_service: TaskService) -> None:
    """6. Closed-loop subscriber handling is explicit and safe."""
    service = clean_service
    task_id = "interleaving-06"
    active_loop = asyncio.get_running_loop()

    # Create and close a dummy event loop
    closed_loop = asyncio.new_event_loop()
    closed_queue: asyncio.Queue[Optional[RuntimeEvent]] = asyncio.Queue()
    service.subscribe_events(task_id, closed_queue, loop=closed_loop)
    closed_loop.close()

    # Active subscriber on current loop
    active_queue: asyncio.Queue[Optional[RuntimeEvent]] = asyncio.Queue()
    service.subscribe_events(task_id, active_queue, loop=active_loop)

    assert len(service._event_subscribers[task_id]) == 2

    # Broadcast event must not crash due to the closed loop
    service._record_event(task_id, RuntimeEventType.MODEL_REQUEST_STARTED, {"status": "ok"})

    # Active subscriber receives event
    ev = await asyncio.wait_for(active_queue.get(), timeout=1.0)
    assert ev is not None
    assert ev.payload["status"] == "ok"

    # Dead subscriber was cleanly pruned
    subs = service._event_subscribers[task_id]
    assert len(subs) == 1
    assert subs[0].loop is active_loop

    service.unsubscribe_events(task_id, active_queue)


@pytest.mark.asyncio
async def test_interleaving_07_worker_thread_cancellation_signals_active_asyncio_consumer(clean_service: TaskService) -> None:
    """7. Worker-thread cancellation safely signals actively waiting asyncio cancellation consumer."""
    service = clean_service
    task_id = "interleaving-07"
    loop = asyncio.get_running_loop()

    cancel_token = asyncio.Event()
    service._cancellation_tokens[task_id] = cancel_token
    service._task_loops[task_id] = loop
    created_at = datetime.now(timezone.utc).isoformat()
    service._tasks[task_id] = TaskResponse(
        task_id=task_id, prompt="P07", status=TaskStatus.EXECUTING, created_at=created_at
    )
    service._get_task_lock(task_id, create=True)

    # Coroutine awaits cancellation token
    async def cancel_waiter():
        await cancel_token.wait()
        return True

    waiter_task = asyncio.create_task(cancel_waiter())
    await asyncio.sleep(0.01)

    # Cancel from OS worker thread
    def worker_cancel():
        service.cancel_task(task_id)

    t_worker = threading.Thread(target=worker_cancel)
    t_worker.start()
    t_worker.join()

    # Waiter must be signaled within 1.0s
    res = await asyncio.wait_for(waiter_task, timeout=1.0)
    assert res is True
    assert service.get_task(task_id).status == TaskStatus.CANCELLED


@pytest.mark.asyncio
async def test_interleaving_08_both_completion_first_and_cancellation_first_interleavings(clean_service: TaskService) -> None:
    """8. Both completion-first and cancellation-first interleavings remain correct."""
    service = clean_service
    created_at = datetime.now(timezone.utc).isoformat()

    # Case A: Completion commits first -> cancellation rejected
    tid_a = "interleaving-08-a"
    service._tasks[tid_a] = TaskResponse(
        task_id=tid_a, prompt="A", status=TaskStatus.EXECUTING, created_at=created_at
    )
    comp_a = TaskResponse(
        task_id=tid_a, prompt="A", status=TaskStatus.COMPLETED, response_text="A-done", created_at=created_at, completed_at=created_at
    )
    ok_a, res_a = service._transition_task_state(tid_a, comp_a)
    assert ok_a is True
    assert res_a.status == TaskStatus.COMPLETED

    canc_a = service.cancel_task(tid_a)
    assert canc_a.status == TaskStatus.COMPLETED
    assert service.get_task(tid_a).status == TaskStatus.COMPLETED
    assert service.get_task(tid_a).response_text == "A-done"

    # Case B: Cancellation commits first -> completion rejected
    tid_b = "interleaving-08-b"
    service._tasks[tid_b] = TaskResponse(
        task_id=tid_b, prompt="B", status=TaskStatus.EXECUTING, created_at=created_at
    )
    canc_b = service.cancel_task(tid_b)
    assert canc_b.status == TaskStatus.CANCELLED

    comp_b = TaskResponse(
        task_id=tid_b, prompt="B", status=TaskStatus.COMPLETED, response_text="B-late", created_at=created_at, completed_at=created_at
    )
    ok_b, res_b = service._transition_task_state(tid_b, comp_b)
    assert ok_b is False
    assert res_b.status == TaskStatus.CANCELLED
    assert service.get_task(tid_b).status == TaskStatus.CANCELLED
    assert service.get_task(tid_b).response_text is None


@pytest.mark.asyncio
async def test_interleaving_09_repeated_cancellation_remains_idempotent(clean_service: TaskService) -> None:
    """9. Repeated cancellation remains idempotent."""
    service = clean_service
    task_id = "interleaving-09"
    created_at = datetime.now(timezone.utc).isoformat()

    service._tasks[task_id] = TaskResponse(
        task_id=task_id, prompt="P09", status=TaskStatus.EXECUTING, created_at=created_at
    )

    barrier = threading.Barrier(4)
    results: List[TaskResponse] = []
    lock = threading.Lock()

    def cancel_worker():
        barrier.wait()
        res = service.cancel_task(task_id)
        with lock:
            results.append(res)

    threads = [threading.Thread(target=cancel_worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=2.0)

    assert len(results) == 4
    for r in results:
        assert r.status == TaskStatus.CANCELLED

    events = service.get_events(task_id)
    canc_events = [e for e in events if e.event_type == RuntimeEventType.TASK_CANCELLED]
    term_events = [e for e in events if e.event_type == RuntimeEventType.TASK_TERMINAL]
    assert len(canc_events) == 1
    assert len(term_events) == 1


@pytest.mark.asyncio
async def test_interleaving_10_independent_tasks_remain_concurrent(clean_service: TaskService) -> None:
    """10. Independent tasks remain concurrent."""
    service = clean_service
    task_1 = "interleaving-10-1"
    task_2 = "interleaving-10-2"
    created_at = datetime.now(timezone.utc).isoformat()

    service._tasks[task_1] = TaskResponse(task_id=task_1, prompt="1", status=TaskStatus.EXECUTING, created_at=created_at)
    service._tasks[task_2] = TaskResponse(task_id=task_2, prompt="2", status=TaskStatus.EXECUTING, created_at=created_at)

    lock_1 = service._get_task_lock(task_1, create=True)
    assert lock_1 is not None

    task_2_finished = threading.Event()

    with lock_1:
        # While lock_1 is held, perform operations on task_2 in a separate thread
        def operate_task_2():
            res = service.cancel_task(task_2)
            assert res is not None
            assert res.status == TaskStatus.CANCELLED
            task_2_finished.set()

        t2 = threading.Thread(target=operate_task_2)
        t2.start()
        t2.join(timeout=1.0)

    assert task_2_finished.is_set()


@pytest.mark.asyncio
async def test_interleaving_11_exactly_one_terminal_event_recorded(clean_service: TaskService) -> None:
    """11. Exactly one terminal event is recorded under high concurrency."""
    service = clean_service
    task_id = "interleaving-11"
    created_at = datetime.now(timezone.utc).isoformat()

    service._tasks[task_id] = TaskResponse(
        task_id=task_id, prompt="P11", status=TaskStatus.EXECUTING, created_at=created_at
    )

    barrier = threading.Barrier(3)

    def worker_complete():
        barrier.wait()
        comp = TaskResponse(
            task_id=task_id, prompt="P11", status=TaskStatus.COMPLETED, created_at=created_at, completed_at=created_at
        )
        service._transition_task_state(task_id, comp)

    def worker_cancel():
        barrier.wait()
        service.cancel_task(task_id)

    def worker_fail():
        barrier.wait()
        fail = TaskResponse(
            task_id=task_id, prompt="P11", status=TaskStatus.FAILED, error="fail", created_at=created_at, completed_at=created_at
        )
        service._transition_task_state(task_id, fail)

    threads = [
        threading.Thread(target=worker_complete),
        threading.Thread(target=worker_cancel),
        threading.Thread(target=worker_fail),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=2.0)

    final_task = service.get_task(task_id)
    assert final_task is not None
    assert final_task.status in (TaskStatus.COMPLETED, TaskStatus.CANCELLED, TaskStatus.FAILED)

    events = service.get_events(task_id)
    terminal_events = [e for e in events if e.event_type == RuntimeEventType.TASK_TERMINAL]
    assert len(terminal_events) == 1
    assert terminal_events[0].payload["status"] == final_task.status.value


@pytest.mark.asyncio
async def test_interleaving_12_event_history_and_live_sse_delivery_consistent(clean_service: TaskService) -> None:
    """12. Event history and live SSE delivery remain consistent."""
    service = clean_service
    task_id = "interleaving-12"
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[Optional[RuntimeEvent]] = asyncio.Queue()

    service.subscribe_events(task_id, queue, loop=loop)

    service._record_event(task_id, RuntimeEventType.TASK_CREATED, {"init": 1})
    service._record_event(task_id, RuntimeEventType.MODEL_REQUEST_STARTED, {"step": "thinking"})
    service._record_event(task_id, RuntimeEventType.TOOL_REQUESTED, {"tool": "calculator"})
    service._record_event(task_id, RuntimeEventType.TOOL_COMPLETED, {"tool": "calculator", "result": 4})
    service._record_event(task_id, RuntimeEventType.TASK_TERMINAL, {"status": "completed"})

    # Drain queue
    delivered: List[RuntimeEvent] = []
    while not queue.empty():
        ev = queue.get_nowait()
        if ev is not None:
            delivered.append(ev)

    history = service.get_events(task_id)

    assert len(delivered) == len(history) == 5
    for q_ev, h_ev in zip(delivered, history):
        assert q_ev.event_id == h_ev.event_id
        assert q_ev.event_type == h_ev.event_type
        assert q_ev.payload == h_ev.payload
        assert q_ev.timestamp == h_ev.timestamp

    service.unsubscribe_events(task_id, queue)
