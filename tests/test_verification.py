"""Tests for independent verification execution, decoupled task vs verification status, and bounded output."""

import asyncio
from pathlib import Path
import pytest
from fastapi.testclient import TestClient

from api.app import create_app
from api.schemas.tasks import TaskCreateRequest, TaskStatus
from core.task_service import TaskService
from core.verification import VerificationRunner, truncate_output_middle
from storage.database import DatabaseManager
from storage.migrations import MigrationRunner
from storage.models import TaskRecord, VerificationRecord, VerificationStatus, VerificationType
from storage.repositories import TaskRepository, VerificationRepository


@pytest.fixture
def temp_db(tmp_path: Path) -> DatabaseManager:
    """Provide a fresh on-disk SQLite DatabaseManager instance with migrations applied."""
    db_file = tmp_path / "test_verification.db"
    db_url = f"sqlite:///{db_file.as_posix()}"
    manager = DatabaseManager(database_url=db_url)
    runner = MigrationRunner(manager)
    runner.run_migrations()
    yield manager
    manager.close()


@pytest.mark.asyncio
async def test_verification_runner_success(temp_db: DatabaseManager) -> None:
    """Verify that a successful test command (exit code 0) yields VerificationStatus.PASSED."""
    task_repo = TaskRepository(temp_db)
    task = task_repo.create_task(prompt="Run unit tests")
    repo = VerificationRepository(temp_db)
    runner = VerificationRunner(verification_repo=repo, default_timeout_seconds=5.0)

    record = await runner.run_verification(
        task_id=task.id,
        command="python -c \"print('ALL TESTS PASSED')\"",
        verification_type=VerificationType.TEST,
    )

    assert record.exit_code == 0
    assert record.result == VerificationStatus.PASSED
    assert "ALL TESTS PASSED" in (record.output_excerpt or "")
    assert record.duration_ms is not None and record.duration_ms > 0

    # Verify persisted in database
    persisted = repo.get(record.id)
    assert persisted is not None
    assert persisted.result == VerificationStatus.PASSED
    assert persisted.exit_code == 0


@pytest.mark.asyncio
async def test_verification_runner_failure(temp_db: DatabaseManager) -> None:
    """Verify that a failing test command (non-zero exit code) yields VerificationStatus.FAILED."""
    task_repo = TaskRepository(temp_db)
    task = task_repo.create_task(prompt="Run failing test")
    repo = VerificationRepository(temp_db)
    runner = VerificationRunner(verification_repo=repo, default_timeout_seconds=5.0)

    record = await runner.run_verification(
        task_id=task.id,
        command="python -c \"import sys; sys.stderr.write('AssertionError: expected 1 got 2\\n'); sys.exit(2)\"",
        verification_type=VerificationType.TEST,
    )

    assert record.exit_code == 2
    assert record.result == VerificationStatus.FAILED
    assert "AssertionError: expected 1 got 2" in (record.output_excerpt or "")

    persisted = repo.get(record.id)
    assert persisted is not None
    assert persisted.result == VerificationStatus.FAILED


@pytest.mark.asyncio
async def test_verification_runner_timeout(temp_db: DatabaseManager) -> None:
    """Verify that a test command exceeding timeout limit terminates and yields FAILED."""
    task_repo = TaskRepository(temp_db)
    task = task_repo.create_task(prompt="Run slow test")
    repo = VerificationRepository(temp_db)
    runner = VerificationRunner(verification_repo=repo, default_timeout_seconds=1.0)

    record = await runner.run_verification(
        task_id=task.id,
        command="python -c \"import time; time.sleep(10)\"",
        verification_type=VerificationType.TEST,
        timeout_seconds=0.5,
    )

    assert record.exit_code == -1
    assert record.result == VerificationStatus.FAILED
    assert "exceeded timeout limit" in (record.output_excerpt or "")


def test_verification_output_middle_truncation() -> None:
    """Verify that outputs exceeding 64KB are middle-truncated without losing head and tail evidence."""
    # Generate 100KB of output
    large_output = "START_LINE\n" + ("x" * 1000 + "\n") * 100 + "END_LINE\n"
    assert len(large_output.encode("utf-8")) > 65536

    truncated = truncate_output_middle(large_output, max_bytes=65536)
    assert len(truncated.encode("utf-8")) < 70000
    assert "START_LINE" in truncated
    assert "END_LINE" in truncated
    assert "... [VERIFICATION OUTPUT TRUNCATED:" in truncated


def test_decoupled_task_status_vs_verification_status(temp_db: DatabaseManager) -> None:
    """Verify Work Item 8: TaskStatus (execution lifecycle) and VerificationStatus (test evidence) remain strictly decoupled."""
    task_repo = TaskRepository(temp_db)
    v_repo = VerificationRepository(temp_db)

    # 1. Create a completed task
    task = task_repo.create_task(prompt="Implement feature X")
    task_repo.transition_state(task.id, TaskStatus.EXECUTING)
    task_repo.transition_state(
        task.id,
        TaskStatus.COMPLETED,
        response_text="I have implemented feature X and all tests pass!",
    )

    # Initial state: Task is COMPLETED, but verification is NOT_RUN
    loaded = task_repo.get(task.id)
    assert loaded.status == TaskStatus.COMPLETED
    assert loaded.verification_status == VerificationStatus.NOT_RUN

    # 2. Record a FAILED verification run
    v_repo.record_verification(
        task_id=task.id,
        verification_type=VerificationType.TEST,
        command="pytest",
        result=VerificationStatus.FAILED,
        exit_code=1,
        output_excerpt="FAILED test_x",
    )

    # The task remains COMPLETED (model finished reasoning), but verification_status is FAILED
    loaded_after_test = task_repo.get(task.id)
    assert loaded_after_test.status == TaskStatus.COMPLETED
    assert loaded_after_test.verification_status == VerificationStatus.FAILED

    # 3. Model claims cannot self-certify: even though model said "all tests pass!", verification is FAILED
    assert "all tests pass!" in loaded_after_test.response_text
    assert loaded_after_test.verification_status == VerificationStatus.FAILED


def test_api_verification_routes(temp_db: DatabaseManager) -> None:
    """Verify REST API endpoints for triggering and retrieving verification records."""
    from core.task_service import set_task_service
    task_service = TaskService(database_manager=temp_db, auto_run_recovery=False)
    set_task_service(task_service)
    app = create_app()

    client = TestClient(app)

    # 1. Submit a task
    create_resp = client.post("/api/tasks", json={"prompt": "Write hello world"})
    assert create_resp.status_code == 201
    task_id = create_resp.json()["task_id"]

    # 2. Trigger verification check via API
    verify_resp = client.post(
        f"/api/tasks/{task_id}/verify",
        json={
            "command": "python -c \"print('VERIFIED')\"",
            "verification_type": "test",
            "timeout_seconds": 10.0,
        },
    )
    assert verify_resp.status_code == 201
    v_data = verify_resp.json()
    assert v_data["task_id"] == task_id
    assert v_data["result"] == "passed"
    assert v_data["exit_code"] == 0

    # 3. Query verifications list
    list_resp = client.get(f"/api/tasks/{task_id}/verifications")
    assert list_resp.status_code == 200
    records = list_resp.json()
    assert len(records) == 1
    assert records[0]["command"] == "python -c \"print('VERIFIED')\""

    # 4. Query task status: verification_status is reflected
    task_resp = client.get(f"/api/tasks/{task_id}")
    assert task_resp.status_code == 200
    assert task_resp.json()["verification_status"] == "passed"
