"""Tests for Phase 8 Cancellation and Graceful Shutdown."""

import asyncio
from datetime import datetime, timezone
from pathlib import Path
import pytest

from api.schemas.tasks import TaskCreateRequest, TaskStatus, TaskResponse
from core.task_service import TaskService
from storage.database import DatabaseManager
from storage.migrations import MigrationRunner
from storage.models import TaskRecord


@pytest.fixture
def temp_db(tmp_path: Path) -> DatabaseManager:
    """Provide a fresh on-disk SQLite DatabaseManager instance with migrations applied."""
    db_file = tmp_path / "test_cancel.db"
    db_url = f"sqlite:///{db_file.as_posix()}"
    manager = DatabaseManager(database_url=db_url)
    runner = MigrationRunner(manager)
    runner.run_migrations()
    yield manager
    manager.close()


@pytest.mark.asyncio
async def test_task_cancellation_lifecycle(temp_db: DatabaseManager) -> None:
    """Verify that cancelling an executing task transitions it to CANCELLED and marks cancellation token."""
    task_service = TaskService(database_manager=temp_db, auto_run_recovery=False)

    # 1. Create a task in EXECUTING state
    task_id = "t-cancel-test-1"
    now_iso = datetime.now(timezone.utc).isoformat()
    init_task = TaskResponse(
        task_id=task_id,
        prompt="Long running calculation",
        status=TaskStatus.EXECUTING,
        created_at=now_iso,
    )
    task_service._transition_task_state(task_id=task_id, target_task=init_task)

    # Set up cancellation token
    cancel_event = asyncio.Event()
    task_service._cancellation_tokens[task_id] = cancel_event

    # 2. Cancel the task
    res = task_service.cancel_task(task_id=task_id, reason="User clicked stop")
    assert res is not None
    assert res.status == TaskStatus.CANCELLED
    assert cancel_event.is_set()

    # Verify task state is CANCELLED in memory and persisted in DB
    cancelled_task = task_service.get_task(task_id)
    assert cancelled_task is not None
    assert cancelled_task.status == TaskStatus.CANCELLED
    assert cancelled_task.termination_reason == "User clicked stop"

    persisted = task_service.task_repo.get(task_id)
    assert persisted is not None
    assert persisted.status == TaskStatus.CANCELLED


def test_late_cancellation_rejected_after_completion(temp_db: DatabaseManager) -> None:
    """Verify linearizable policy: once a task commits COMPLETED, subsequent cancellation requests are rejected."""
    task_service = TaskService(database_manager=temp_db, auto_run_recovery=False)

    task_id = "t-cancel-test-2"
    now_iso = datetime.now(timezone.utc).isoformat()
    # 1. Start as PENDING
    pending_task = TaskResponse(
        task_id=task_id,
        prompt="Quick task",
        status=TaskStatus.PENDING,
        created_at=now_iso,
    )
    task_service._transition_task_state(task_id=task_id, target_task=pending_task)

    # 2. Transition to EXECUTING
    exec_task = TaskResponse(
        task_id=task_id,
        prompt="Quick task",
        status=TaskStatus.EXECUTING,
        created_at=now_iso,
    )
    task_service._transition_task_state(task_id=task_id, target_task=exec_task)

    # 3. Complete task
    completed_task = TaskResponse(
        task_id=task_id,
        prompt="Quick task",
        status=TaskStatus.COMPLETED,
        response_text="Done!",
        created_at=now_iso,
        completed_at=now_iso,
    )
    task_service._transition_task_state(task_id=task_id, target_task=completed_task)

    # Attempt late cancellation
    res = task_service.cancel_task(task_id=task_id, reason="Late cancel")
    assert res is not None
    assert res.status == TaskStatus.COMPLETED

    task = task_service.get_task(task_id)
    assert task.status == TaskStatus.COMPLETED


@pytest.mark.asyncio
async def test_graceful_shutdown(temp_db: DatabaseManager) -> None:
    """Verify that graceful_shutdown cancels all active tasks without database corruption."""
    task_service = TaskService(database_manager=temp_db, auto_run_recovery=False)

    # Spawn 3 executing tasks with cancellation tokens
    now_iso = datetime.now(timezone.utc).isoformat()
    for i in range(3):
        tid = f"t-shutdown-{i}"
        task = TaskResponse(
            task_id=tid,
            prompt=f"Task {i}",
            status=TaskStatus.EXECUTING,
            created_at=now_iso,
        )
        task_service._transition_task_state(task_id=tid, target_task=task)
        token = asyncio.Event()
        task_service._cancellation_tokens[tid] = token

    # Execute graceful shutdown
    task_service.graceful_shutdown(timeout_seconds=2.0)

    # Verify all cancellation tokens are set
    for i in range(3):
        tid = f"t-shutdown-{i}"
        token = task_service._cancellation_tokens[tid]
        assert token.is_set()

    # Verify database integrity after shutdown
    conn = temp_db.get_connection()
    cursor = conn.cursor()
    cursor.execute("PRAGMA integrity_check;")
    rows = cursor.fetchall()
    assert rows[0][0] == "ok"
