"""Tests for independent verification execution, decoupled task vs verification status, and bounded output."""

import asyncio
import os
from pathlib import Path
import subprocess
import sys
import pytest
from fastapi.testclient import TestClient

from api.app import create_app
from api.schemas.tasks import TaskCreateRequest, TaskStatus
from core.task_service import TaskService
from core.verification import (
    BoundedStreamCapture,
    DEFAULT_VERIFICATION_PROFILES,
    VerificationOrigin,
    VerificationRunner,
    _terminate_process_tree,
    truncate_output_middle,
    validate_verification_command,
)
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

    # 2. Trigger verification check via API with authenticated operator credentials
    verify_resp = client.post(
        f"/api/tasks/{task_id}/verify",
        json={
            "command": "python -c \"print('VERIFIED')\"",
            "verification_type": "test",
            "timeout_seconds": 10.0,
        },
        headers={"Authorization": "Bearer op-secret-token-3891"},
    )
    assert verify_resp.status_code == 201
    v_data = verify_resp.json()
    assert v_data["task_id"] == task_id
    assert v_data["result"] == "passed"
    assert v_data["exit_code"] == 0
    assert v_data["origin"] == "operator"
    assert v_data["authorized_by"] == "operator_admin"

    # 3. Query verifications list
    list_resp = client.get(f"/api/tasks/{task_id}/verifications")
    assert list_resp.status_code == 200
    records = list_resp.json()
    assert len(records) == 1
    assert records[0]["command"] == "python -c \"print('VERIFIED')\""
    assert records[0]["origin"] == "operator"

    # 4. Query task status: verification_status is reflected
    task_resp = client.get(f"/api/tasks/{task_id}")
    assert task_resp.status_code == 200
    assert task_resp.json()["verification_status"] == "passed"


def test_api_verification_security_authorization_matrix(temp_db: DatabaseManager) -> None:
    """Verify security matrix: unauthenticated (401), non-operator (403), operator (201), profile (201)."""
    from core.task_service import set_task_service
    task_service = TaskService(database_manager=temp_db, auto_run_recovery=False)
    set_task_service(task_service)
    app = create_app()
    client = TestClient(app)

    create_resp = client.post("/api/tasks", json={"prompt": "Task for security matrix check"})
    assert create_resp.status_code == 201
    task_id = create_resp.json()["task_id"]

    # Condition 1: Unauthenticated caller cannot execute custom verification command, even when supplying headers
    unauth_resp = client.post(
        f"/api/tasks/{task_id}/verify",
        json={
            "command": "python -c \"print('ATTACK')\"",
            "origin": "operator",
            "authorized_by": "root_admin",
        },
        headers={"X-Operator-Authorization": "true", "X-Operator-Identity": "fake_admin"},
    )
    assert unauth_resp.status_code == 401
    assert "Authentication required" in unauth_resp.json()["detail"]

    # Condition 2: Authenticated non-operator (role=VIEWER) cannot execute custom command
    viewer_resp = client.post(
        f"/api/tasks/{task_id}/verify",
        json={
            "command": "python -c \"print('VIEWER_CUSTOM')\"",
        },
        headers={"Authorization": "Bearer viewer-token-9912"},
    )
    assert viewer_resp.status_code == 403
    assert "operator role required" in viewer_resp.json()["detail"]

    # Condition 3: Authenticated operator can execute, with provenance tied to the authenticated principal
    op_resp = client.post(
        f"/api/tasks/{task_id}/verify",
        json={
            "command": "python -c \"print('OPERATOR_AUTHORIZED')\"",
            "origin": "spoofed_origin",
            "authorized_by": "spoofed_authorizer",
        },
        headers={"Authorization": "Bearer op-secret-token-3891"},
    )
    assert op_resp.status_code == 201
    op_data = op_resp.json()
    assert op_data["origin"] == "operator"
    # Provenance is strictly bound to the authenticated principal identity, not caller's spoofed fields
    assert op_data["authorized_by"] == "operator_admin"
    assert "OPERATOR_AUTHORIZED" in (op_data["output_excerpt"] or "")

    # Condition 4: Configured profile resolves to trusted executable under intended environment
    task_service.verification_runner.profiles["test_quick"] = f'"{sys.executable}" -c "print(\'QUICK_VERIFIED\')"'
    profile_resp = client.post(
        f"/api/tasks/{task_id}/verify",
        json={
            "profile_name": "test_quick",
            "origin": "operator",
            "authorized_by": "attacker",
        },
    )
    assert profile_resp.status_code == 201
    prof_data = profile_resp.json()
    assert prof_data["origin"] == "configured_profile"
    assert prof_data["authorized_by"] == "system_policy"
    # Command was resolved to sys.executable
    assert sys.executable in prof_data["command"]
    assert "QUICK_VERIFIED" in (prof_data["output_excerpt"] or "")

    # Also verify DEFAULT_VERIFICATION_PROFILES anchors to sys.executable and ignores smoke tests
    default_test_cmd = DEFAULT_VERIFICATION_PROFILES["test"]
    assert sys.executable in default_test_cmd
    assert "-m pytest" in default_test_cmd
    assert "--ignore=tests/test_smoke_live.py" in default_test_cmd


def test_verification_rejects_trivial_and_unsafe_commands() -> None:
    """Verify F8-01 trust boundary: self-certification and injection commands are rejected."""
    # 1. Trivial pass commands
    for trivial in ["echo 'tests passed'", "echo passed", "true", "exit 0", "printf 'ok'", ":"]:
        with pytest.raises(ValueError, match="Trivial self-certification commands"):
            validate_verification_command(trivial)

    # 2. Unquoted shell chaining
    for chaining in ["pytest && echo done", "pytest || true", "pytest ; rm -rf /", "pytest | grep ok"]:
        with pytest.raises(ValueError, match="Shell chaining"):
            validate_verification_command(chaining, allow_shell_chaining=False)

    # 3. Valid commands succeed
    validate_verification_command("pytest tests/ -v")
    validate_verification_command("python -m unittest discover")


@pytest.mark.asyncio
async def test_verification_profile_resolution(temp_db: DatabaseManager) -> None:
    """Verify pre-approved profile resolution and provenance tracking."""
    task_repo = TaskRepository(temp_db)
    task = task_repo.create_task(prompt="Run profile verification")
    repo = VerificationRepository(temp_db)
    runner = VerificationRunner(
        verification_repo=repo,
        profiles={"custom_check": "python -c \"print('CUSTOM_PROFILE_OK')\""},
    )

    record = await runner.run_verification(
        task_id=task.id,
        profile_name="custom_check",
        origin=VerificationOrigin.CONFIGURED_PROFILE,
        authorized_by="system_policy",
    )

    assert record.result == VerificationStatus.PASSED
    assert record.origin == "configured_profile"
    assert record.profile_name == "custom_check"
    assert record.authorized_by == "system_policy"
    assert "CUSTOM_PROFILE_OK" in (record.output_excerpt or "")

    # Unknown profile rejected
    with pytest.raises(ValueError, match="Unknown verification profile"):
        await runner.run_verification(
            task_id=task.id,
            profile_name="non_existent_profile",
        )


@pytest.mark.asyncio
async def test_verification_bounded_stream_capture_large_output(temp_db: DatabaseManager) -> None:
    """Verify F8-02: BoundedStreamCapture limits memory at capture time for multi-megabyte streams."""
    # 1. Direct stream test with 10MB of data
    capture = BoundedStreamCapture(max_bytes=65536)
    chunk = b"A" * 4096
    total_written = 0

    class MockStream:
        def __init__(self, chunks_count: int) -> None:
            self.remaining = chunks_count

        async def read(self, n: int) -> bytes:
            if self.remaining <= 0:
                return b""
            self.remaining -= 1
            return chunk

    mock_stream = MockStream(chunks_count=2500)  # 2500 * 4KB = 10MB
    await capture.consume_stream(mock_stream)

    assert capture.total_bytes == 2500 * 4096
    assert len(capture.head_buffer) == capture.half_bytes
    assert len(capture.tail_buffer) == capture.half_bytes
    # Capture-time memory in buffers is capped strictly within max_bytes
    assert (len(capture.head_buffer) + len(capture.tail_buffer)) <= 65536
    bounded_text = capture.get_bounded_text()
    assert "... [STREAMED OUTPUT TRUNCATED: 10240000 bytes streamed" in bounded_text

    # 2. Subprocess execution with 2MB stdout
    task_repo = TaskRepository(temp_db)
    task = task_repo.create_task(prompt="Stream massive output")
    repo = VerificationRepository(temp_db)
    runner = VerificationRunner(verification_repo=repo, default_timeout_seconds=15.0)

    record = await runner.run_verification(
        task_id=task.id,
        command="python -c \"import sys; [sys.stdout.write(f'LINE {i}\\n') for i in range(15000)]\"",
        origin=VerificationOrigin.OPERATOR,
        authorized_by="operator",
    )

    assert record.result == VerificationStatus.PASSED
    assert record.exit_code == 0
    assert "LINE 0" in (record.output_excerpt or "")
    assert "LINE 14999" in (record.output_excerpt or "")
    assert "[STREAMED OUTPUT TRUNCATED:" in (record.output_excerpt or "")
    assert len((record.output_excerpt or "").encode("utf-8")) < 75000


@pytest.mark.asyncio
async def test_verification_cancellation(temp_db: DatabaseManager) -> None:
    """Verify cooperative cancellation terminates subprocess promptly and records FAILED."""
    task_repo = TaskRepository(temp_db)
    task = task_repo.create_task(prompt="Long running test to cancel")
    repo = VerificationRepository(temp_db)
    runner = VerificationRunner(verification_repo=repo, default_timeout_seconds=30.0)

    token = asyncio.Event()

    # Schedule cancellation 0.5s after start
    loop = asyncio.get_running_loop()
    loop.call_later(0.5, token.set)

    record = await runner.run_verification(
        task_id=task.id,
        command="python -c \"import time; time.sleep(20)\"",
        origin=VerificationOrigin.OPERATOR,
        cancellation_token=token,
    )

    assert record.result == VerificationStatus.FAILED
    assert record.exit_code == -1
    assert "cancelled by user" in (record.output_excerpt or "")


@pytest.mark.asyncio
async def test_verification_process_tree_termination(temp_db: DatabaseManager, tmp_path: Path) -> None:
    """Verify F8-04: multi-level process tree (child and grandchild) is completely killed on timeout."""
    pid_file = tmp_path / "grandchild.pid"
    script_file = tmp_path / "spawner.py"

    # Spawner script: spawns a grandchild process running a 60s sleep, writes grandchild PID, then sleeps 60s
    script_content = f"""
import subprocess, sys, time
gc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
with open(r"{pid_file.as_posix()}", "w") as f:
    f.write(str(gc.pid))
    f.flush()
time.sleep(60)
"""
    script_file.write_text(script_content, encoding="utf-8")

    task_repo = TaskRepository(temp_db)
    task = task_repo.create_task(prompt="Process tree test")
    repo = VerificationRepository(temp_db)
    runner = VerificationRunner(verification_repo=repo, default_timeout_seconds=1.0)

    record = await runner.run_verification(
        task_id=task.id,
        command=f'python "{script_file.as_posix()}"',
        origin=VerificationOrigin.OPERATOR,
        timeout_seconds=1.5,
    )

    assert record.result == VerificationStatus.FAILED
    assert record.exit_code == -1
    assert "exceeded timeout limit" in (record.output_excerpt or "")

    # Grandchild PID must have been recorded
    assert pid_file.exists(), "Grandchild PID file was not created"
    gc_pid = int(pid_file.read_text().strip())

    # Wait 0.5s for OS to clean up terminated process
    await asyncio.sleep(0.5)

    # Verify grandchild PID is no longer running
    if os.name == "nt":
        res = subprocess.run(["tasklist", "/FI", f"PID eq {gc_pid}"], capture_output=True, text=True)
        assert str(gc_pid) not in res.stdout, f"Grandchild process PID {gc_pid} is still alive!"
    else:
        try:
            os.kill(gc_pid, 0)
            pytest.fail(f"Grandchild process PID {gc_pid} is still alive!")
        except OSError:
            pass  # Terminated cleanly

