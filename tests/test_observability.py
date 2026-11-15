"""Tests for Phase 8 Observability: Correlation IDs, Structured Lifecycle Events, and Secret Redaction."""

import asyncio
from datetime import datetime, timezone
from pathlib import Path
import pytest

from api.schemas.tasks import TaskCreateRequest
from core.observability import (
    EventType,
    StructuredEventEmitter,
    get_correlation_id,
    correlation_context,
    bound_and_scrub_payload,
    sanitize_correlation_id,
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
from tools.security import redact_secrets, redact_secrets_obj


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


def test_correlation_id_validation_strict() -> None:
    """Verify strict validation of correlation IDs against log injection, length, and malformed inputs."""
    # 1. Valid IDs
    valid_req = TaskCreateRequest(prompt="Run test", correlation_id="trace_abc-123.456")
    assert valid_req.correlation_id == "trace_abc-123.456"

    # None and empty strings default safely
    empty_req = TaskCreateRequest(prompt="Run test", correlation_id="   ")
    assert empty_req.correlation_id is None

    # 2. Overlong ID rejected
    with pytest.raises(ValueError, match="must not exceed 128 characters"):
        TaskCreateRequest(prompt="Run test", correlation_id="a" * 129)

    # 3. Control and newline characters rejected
    for invalid in ["bad\nid", "bad\rid", "bad\x00id", "id; DROP TABLE", "id with space"]:
        with pytest.raises(ValueError):
            TaskCreateRequest(prompt="Run test", correlation_id=invalid)

    # 4. Context sanitize fallback
    safe = sanitize_correlation_id("bad\nid")
    assert safe != "bad\nid"
    assert len(safe) == 36  # Regenerated UUID4


def test_secret_redaction_patterns_and_nested_structures() -> None:
    """Verify F8-03: defense-in-depth sanitization of recognized keys, tokens, URLs, and nested structures."""
    nested_data = {
        "aws_key": "AKIAIOSFODNN7EXAMPLE",
        "jwt_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIiwibmFtZSI6IkpvaG4gRG9lIiwiaWF0IjoxNTE2MjM5MDIyfQ.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c",
        "db_url": "https://service_user:SuperSecretPass123@db.internal.corp:5432/main",
        "nested_dict": {
            "key_block": "-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAKCAQEA0Y...\n-----END RSA PRIVATE KEY-----",
            "bearer": "Bearer sk-proj-1234567890abcdef1234567890abcdef",
        },
        "safe_list": ["ordinary_item", 12345, True],
    }

    scrubbed = redact_secrets_obj(nested_data)

    # AWS Key redacted
    assert "AKIAIOSFODNN7EXAMPLE" not in str(scrubbed)
    assert "[REDACTED]" in scrubbed["aws_key"]

    # JWT redacted
    assert "eyJhbGciOi" not in str(scrubbed)
    assert "[REDACTED]" in scrubbed["jwt_token"]

    # URL password redacted
    assert "SuperSecretPass123" not in scrubbed["db_url"]
    assert "service_user:[REDACTED_PASSWORD]@db.internal.corp" in scrubbed["db_url"]

    # Private key redacted
    assert "MIIEowIBAAKCAQEA0Y" not in scrubbed["nested_dict"]["key_block"]
    assert "[REDACTED]" in scrubbed["nested_dict"]["key_block"]

    # Bearer redacted
    assert "sk-proj-1234567890" not in scrubbed["nested_dict"]["bearer"]
    assert "Bearer [REDACTED_TOKEN]" in scrubbed["nested_dict"]["bearer"]

    # Safe primitives preserved
    assert scrubbed["safe_list"] == ["ordinary_item", 12345, True]


def test_event_repository_pagination(temp_db: DatabaseManager) -> None:
    """Verify limit and offset pagination on RuntimeEventRepository."""
    task_repo = TaskRepository(temp_db)
    task = task_repo.create_task(prompt="Pagination test task")
    task_id = task.id

    event_repo = RuntimeEventRepository(temp_db)
    emitter = StructuredEventEmitter(event_repo=event_repo)

    # Emit 10 events
    for i in range(10):
        emitter.emit(
            event_type=EventType.TOOL_DISPATCH_STARTED,
            task_id=task_id,
            correlation_id="corr-page-1",
            payload={"index": i},
        )

    # Page 1: limit 3, offset 0
    p1 = event_repo.list_for_task(task_id, limit=3, offset=0)
    assert len(p1) == 3
    assert '"index": 0' in p1[0].payload_json
    assert '"index": 2' in p1[2].payload_json

    # Page 2: limit 3, offset 3
    p2 = event_repo.list_for_task(task_id, limit=3, offset=3)
    assert len(p2) == 3
    assert '"index": 3' in p2[0].payload_json
    assert '"index": 5' in p2[2].payload_json

    # Out of bounds offset
    p_empty = event_repo.list_for_task(task_id, limit=3, offset=20)
    assert len(p_empty) == 0

