"""Tests for Phase 8 Observability: Correlation IDs, Structured Lifecycle Events, and Secret Redaction."""

import asyncio
from datetime import datetime, timezone
from pathlib import Path
import pytest

from core.observability import (
    EventType,
    StructuredEventEmitter,
    get_correlation_id,
    correlation_context,
    bound_and_scrub_payload,
)
from storage.database import DatabaseManager
from storage.migrations import MigrationRunner
from storage.models import TaskRecord, TaskStatus, VerificationStatus
from storage.repositories import (
    RuntimeEventRepository,
    TaskRepository,
    ToolExecutionRepository,
    ModelCallRepository,
)


@pytest.fixture
def temp_db(tmp_path: Path) -> DatabaseManager:
    """Provide a fresh on-disk SQLite DatabaseManager instance with migrations applied."""
    db_file = tmp_path / "test_observability.db"
    db_url = f"sqlite:///{db_file.as_posix()}"
    manager = DatabaseManager(database_url=db_url)
    runner = MigrationRunner(manager)
    runner.run_migrations()
    yield manager
    manager.close()


def test_correlation_id_contextvars() -> None:
    """Verify that correlation ID defaults to a UUID4 string and propagates in contextvars."""
    cid1 = get_correlation_id()
    assert cid1 is not None
    assert len(cid1) == 36

    # Test explicit override via correlation_context
    custom_cid = "custom-trace-uuid-1234"
    with correlation_context(correlation_id=custom_cid):
        assert get_correlation_id() == custom_cid

    # Post-context returns back
    assert get_correlation_id() != custom_cid


def test_payload_middle_truncation() -> None:
    """Verify that payloads exceeding 64KB are safely middle-truncated."""
    large_str = "HEADER_INFO\n" + ("y" * 1000 + "\n") * 100 + "FOOTER_INFO\n"
    payload = {"key": large_str}
    scrubbed = bound_and_scrub_payload(payload)
    assert len(scrubbed.encode("utf-8")) < 70000
    assert "HEADER_INFO" in scrubbed
    assert "FOOTER_INFO" in scrubbed
    assert "... [PAYLOAD TRUNCATED:" in scrubbed


def test_event_emitter_secret_redaction_and_persistence(temp_db: DatabaseManager) -> None:
    """Verify that StructuredEventEmitter redacts sensitive API keys and persists events to SQLite."""
    event_repo = RuntimeEventRepository(temp_db, extra_secrets=["secret-credential-value"])
    emitter = StructuredEventEmitter(event_repo=event_repo, extra_secrets=["secret-credential-value"])

    task_repo = TaskRepository(temp_db)
    task = task_repo.create_task(prompt="Process sensitive data")

    raw_payload = {
        "api_key": "Bearer sk-proj-1234567890abcdef1234567890abcdef",
        "custom_token": "secret-credential-value",
        "nested": {
            "info": "This contains secret-credential-value in nested text",
        },
        "safe_key": "normal_public_value",
    }

    event = emitter.emit(
        event_type=EventType.TOOL_DISPATCH_STARTED,
        task_id=task.id,
        correlation_id="corr-obs-123",
        payload=raw_payload,
    )

    assert event.id is not None
    assert event.correlation_id == "corr-obs-123"

    # Verify event payload_json has redacted values
    assert "secret-credential-value" not in event.payload_json
    assert "[REDACTED" in event.payload_json

    # Verify persisted in database
    persisted = event_repo.list_for_task(task.id)
    assert len(persisted) == 1
    p_event = persisted[0]
    assert p_event.event_type == EventType.TOOL_DISPATCH_STARTED
    assert p_event.correlation_id == "corr-obs-123"
    assert "secret-credential-value" not in p_event.payload_json


@pytest.mark.asyncio
async def test_event_emitter_async_subscribers(temp_db: DatabaseManager) -> None:
    """Verify that async subscribers receive broadcasted structured events in real time."""
    event_repo = RuntimeEventRepository(temp_db)
    emitter = StructuredEventEmitter(event_repo=event_repo)

    queue: asyncio.Queue = asyncio.Queue()

    def callback(ev):
        queue.put_nowait(ev)

    emitter.add_listener(callback)

    emitter.emit(
        event_type=EventType.TASK_SUBMITTED,
        task_id="t-sub-1",
        payload={"note": "first"},
    )
    emitter.emit(
        event_type=EventType.TASK_COMPLETED,
        task_id="t-sub-1",
        payload={"note": "second"},
    )

    ev1 = await asyncio.wait_for(queue.get(), timeout=2.0)
    ev2 = await asyncio.wait_for(queue.get(), timeout=2.0)

    assert ev1.event_type == EventType.TASK_SUBMITTED
    assert '"note": "first"' in ev1.payload_json
    assert ev2.event_type == EventType.TASK_COMPLETED
    assert '"note": "second"' in ev2.payload_json

    # Unsubscribe test
    emitter.remove_listener(callback)
    emitter.emit(
        event_type=EventType.TASK_FAILED,
        task_id="t-sub-1",
        payload={"note": "third"},
    )
    assert queue.empty()


def test_correlation_id_persistence_across_tables(temp_db: DatabaseManager) -> None:
    """Verify that correlation_id is faithfully preserved across tasks, tool_executions, and model_calls."""
    task_repo = TaskRepository(temp_db)
    tool_repo = ToolExecutionRepository(temp_db)
    model_repo = ModelCallRepository(temp_db)

    test_cid = "corr-system-trace-456"

    # 1. Task record
    task = TaskRecord(
        id="t-corr-1",
        prompt="Test prompt",
        status=TaskStatus.EXECUTING,
        correlation_id=test_cid,
        created_at=datetime.now(timezone.utc).isoformat(),
    )
    task_repo.create(task)
    loaded_task = task_repo.get("t-corr-1")
    assert loaded_task is not None
    assert loaded_task.correlation_id == test_cid

    # 2. Tool execution record
    tool_rec = tool_repo.record_started(
        task_id="t-corr-1",
        call_id="call-123",
        tool_name="read_file",
        arguments={"path": "test.txt"},
        correlation_id=test_cid,
    )
    assert tool_rec.correlation_id == test_cid
    loaded_tool = tool_repo.get_by_call_id(tool_rec.call_id)
    assert loaded_tool is not None
    assert loaded_tool.correlation_id == test_cid

    # 3. Model call record
    model_rec = model_repo.record_call(
        task_id="t-corr-1",
        model_name="mock-qwen",
        prompt_tokens=10,
        completion_tokens=20,
        total_tokens=30,
        duration_ms=150.0,
        request_id="req-987",
        correlation_id=test_cid,
        retry_count=1,
    )
    assert model_rec.correlation_id == test_cid
    assert model_rec.request_id == "req-987"
    assert model_rec.retry_count == 1
    calls = model_repo.list_for_task(task.id)
    assert len(calls) == 1
    loaded_model = calls[0]
    assert loaded_model.correlation_id == test_cid
    assert loaded_model.request_id == "req-987"
