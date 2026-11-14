"""Tests for Phase 7 process restart, crash boundary classification, and session recovery.

Mandatory verification requirements:
- Uses real on-disk temporary SQLite database files (never :memory: for restart tests).
- Verifies deterministic crash boundary classification:
  - Interrupted with mutating tool -> NEEDS_REVIEW (task FAILED, zero blind replay)
  - Interrupted with read-only tool / no tools -> SAFE_TO_RESUME (interrupted_safe)
- Verifies bulk invalidation of pending approvals across process restarts.
- Verifies complete session context reconstruction from persistent SQLite storage.
- Verifies terminal task immutability across recovery passes.
"""

from pathlib import Path
import pytest
from typing import Generator

from api.schemas.tasks import TaskStatus
from core.recovery import StartupRecoveryEngine
from core.session_manager import SessionManager
from storage.database import DatabaseManager
from storage.migrations import MigrationRunner
from storage.models import (
    ApprovalStatus,
    ExecutionState,
    RecoveryClassification,
    RecoveryStatus,
    SessionRecord,
    SessionStatus,
    TaskRecord,
)
from storage.repositories import (
    ApprovalRepository,
    SessionRepository,
    TaskRepository,
    ToolExecutionRepository,
    TranscriptRepository,
)


@pytest.fixture
def disk_db_path(tmp_path: Path) -> Path:
    """Return a path to a temporary on-disk SQLite database file."""
    return tmp_path / "recovery_test.db"


@pytest.fixture
def disk_db(disk_db_path: Path) -> Generator[DatabaseManager, None, None]:
    """Provide a real on-disk DatabaseManager with migrations applied."""
    manager = DatabaseManager(db_path=str(disk_db_path), enable_wal=True)
    MigrationRunner.apply_all(manager.get_connection())
    yield manager
    manager.close()


def test_recovery_clean_startup_no_interrupted_tasks(disk_db: DatabaseManager) -> None:
    """Test 1: Clean restart when no tasks were in EXECUTING state at shutdown."""
    session_repo = SessionRepository(disk_db)
    task_repo = TaskRepository(disk_db)

    session = session_repo.create_session(title="Clean Session")
    # One completed task, one pending task
    t1 = task_repo.create_task(prompt="Completed work", session_id=session.id)
    task_repo.transition_task_state(t1.id, to_status=TaskStatus.EXECUTING, expected_version=1)
    task_repo.transition_task_state(t1.id, to_status=TaskStatus.COMPLETED, expected_version=2)

    t2 = task_repo.create_task(prompt="Pending work", session_id=session.id)

    recovery = StartupRecoveryEngine(disk_db)
    summary = recovery.reconcile()

    assert summary.interrupted_tasks_found == 0
    assert summary.safe_to_resume_count == 0
    assert summary.needs_review_count == 0
    assert summary.stale_approvals_invalidated == 0

    # Verify task states remain untouched
    assert task_repo.get(t1.id).status == TaskStatus.COMPLETED
    assert task_repo.get(t2.id).status == TaskStatus.PENDING


def test_recovery_interrupted_executing_task_no_tools_safe_to_resume(
    disk_db_path: Path,
) -> None:
    """Test 2: Process crash while task is EXECUTING with no in-flight tools -> SAFE_TO_RESUME."""
    # Process 1: Start task and transition to EXECUTING
    db1 = DatabaseManager(db_path=str(disk_db_path), enable_wal=True)
    MigrationRunner.apply_all(db1.get_connection())
    session_repo1 = SessionRepository(db1)
    task_repo1 = TaskRepository(db1)

    session = session_repo1.create_session(title="Crash Session")
    task = task_repo1.create_task(prompt="Analyze architecture", session_id=session.id)
    task_repo1.transition_task_state(task.id, to_status=TaskStatus.EXECUTING, expected_version=1)
    db1.close()  # Simulate sudden process crash / shutdown

    # Process 2: New application instance boots from same disk database
    db2 = DatabaseManager(db_path=str(disk_db_path), enable_wal=True)
    recovery2 = StartupRecoveryEngine(db2)
    summary = recovery2.reconcile()

    assert summary.interrupted_tasks_found == 1
    assert summary.safe_to_resume_count == 1
    assert summary.needs_review_count == 0
    assert summary.details[0]["classification"] == RecoveryClassification.SAFE_TO_RESUME.value

    # Verify task was transitioned to FAILED with recovery_status INTERRUPTED_SAFE
    task_repo2 = TaskRepository(db2)
    reconciled = task_repo2.get(task.id)
    assert reconciled.status == TaskStatus.FAILED
    assert reconciled.recovery_status == RecoveryStatus.INTERRUPTED_SAFE
    assert reconciled.termination_reason == "interrupted_safe"
    db2.close()


def test_recovery_interrupted_executing_task_readonly_tool_safe_to_resume(
    disk_db_path: Path,
) -> None:
    """Test 3: Process crash during read-only tool execution -> SAFE_TO_RESUME."""
    db1 = DatabaseManager(db_path=str(disk_db_path), enable_wal=True)
    MigrationRunner.apply_all(db1.get_connection())
    session_repo = SessionRepository(db1)
    task_repo = TaskRepository(db1)
    tool_repo = ToolExecutionRepository(db1)

    session = session_repo.create_session(title="Readonly Crash")
    task = task_repo.create_task(prompt="Read project config", session_id=session.id)
    task_repo.transition_task_state(task.id, to_status=TaskStatus.EXECUTING, expected_version=1)

    # In-flight read-only tool: read_file (is_mutation=False, idempotent=True)
    tool_repo.record_started(
        task_id=task.id,
        call_id="call-read-01",
        tool_name="read_file",
        arguments={"path": "README.md"},
        is_mutation=False,
        idempotent=True,
    )
    db1.close()  # Crash

    # Boot Process 2
    db2 = DatabaseManager(db_path=str(disk_db_path), enable_wal=True)
    recovery = StartupRecoveryEngine(db2)
    summary = recovery.reconcile()

    assert summary.interrupted_tasks_found == 1
    assert summary.safe_to_resume_count == 1
    assert summary.needs_review_count == 0

    task_repo2 = TaskRepository(db2)
    reconciled = task_repo2.get(task.id)
    assert reconciled.recovery_status == RecoveryStatus.INTERRUPTED_SAFE

    # Confirm in-flight tool record was marked INTERRUPTED
    tool_repo2 = ToolExecutionRepository(db2)
    tool_rec = tool_repo2.get_by_call_id("call-read-01")
    assert tool_rec.execution_state == ExecutionState.INTERRUPTED
    db2.close()


def test_recovery_interrupted_executing_task_mutating_tool_needs_review_no_blind_replay(
    disk_db_path: Path,
) -> None:
    """Test 4: Process crash during mutating tool call (write_file) -> NEEDS_REVIEW. Zero blind replay."""
    db1 = DatabaseManager(db_path=str(disk_db_path), enable_wal=True)
    MigrationRunner.apply_all(db1.get_connection())
    session_repo = SessionRepository(db1)
    task_repo = TaskRepository(db1)
    tool_repo = ToolExecutionRepository(db1)

    session = session_repo.create_session(title="Mutation Crash")
    task = task_repo.create_task(prompt="Write production config", session_id=session.id)
    task_repo.transition_task_state(task.id, to_status=TaskStatus.EXECUTING, expected_version=1)

    # In-flight mutating tool: write_file (is_mutation=True, idempotent=False)
    tool_repo.record_started(
        task_id=task.id,
        call_id="call-write-01",
        tool_name="write_file",
        arguments={"path": "config.json", "content": "{\"env\": \"prod\"}"},
        is_mutation=True,
        idempotent=False,
    )
    db1.close()  # Crash during file write

    # Boot Process 2
    db2 = DatabaseManager(db_path=str(disk_db_path), enable_wal=True)
    recovery = StartupRecoveryEngine(db2)
    summary = recovery.reconcile()

    assert summary.interrupted_tasks_found == 1
    assert summary.safe_to_resume_count == 0
    assert summary.needs_review_count == 1
    assert summary.details[0]["classification"] == RecoveryClassification.NEEDS_REVIEW.value
    assert "write_file" in summary.details[0]["tools"]

    task_repo2 = TaskRepository(db2)
    reconciled = task_repo2.get(task.id)
    assert reconciled.status == TaskStatus.FAILED
    assert reconciled.recovery_status == RecoveryStatus.NEEDS_REVIEW
    assert "write_file" in reconciled.termination_reason
    assert "Manual inspection required" in reconciled.error

    # Verify tool was marked INTERRUPTED
    tool_repo2 = ToolExecutionRepository(db2)
    tool_rec = tool_repo2.get_by_call_id("call-write-01")
    assert tool_rec.execution_state == ExecutionState.INTERRUPTED
    db2.close()


def test_recovery_interrupted_executing_task_shell_command_needs_review(
    disk_db_path: Path,
) -> None:
    """Test 5: Process crash during execute_command -> NEEDS_REVIEW."""
    db1 = DatabaseManager(db_path=str(disk_db_path), enable_wal=True)
    MigrationRunner.apply_all(db1.get_connection())
    session_repo = SessionRepository(db1)
    task_repo = TaskRepository(db1)
    tool_repo = ToolExecutionRepository(db1)

    session = session_repo.create_session(title="Shell Crash")
    task = task_repo.create_task(prompt="Deploy build", session_id=session.id)
    task_repo.transition_task_state(task.id, to_status=TaskStatus.EXECUTING, expected_version=1)

    tool_repo.record_started(
        task_id=task.id,
        call_id="call-cmd-01",
        tool_name="execute_command",
        arguments={"command": "npm run build"},
        is_mutation=True,
        idempotent=False,
    )
    db1.close()

    # Boot Process 2
    db2 = DatabaseManager(db_path=str(disk_db_path), enable_wal=True)
    recovery = StartupRecoveryEngine(db2)
    summary = recovery.reconcile()

    assert summary.needs_review_count == 1
    task_repo2 = TaskRepository(db2)
    reconciled = task_repo2.get(task.id)
    assert reconciled.recovery_status == RecoveryStatus.NEEDS_REVIEW
    db2.close()


def test_recovery_pending_approvals_invalidated_across_restart(
    disk_db_path: Path,
) -> None:
    """Test 6: All pending approvals are marked EXPIRED on startup. Zero grants survive restart."""
    db1 = DatabaseManager(db_path=str(disk_db_path), enable_wal=True)
    MigrationRunner.apply_all(db1.get_connection())
    session_repo = SessionRepository(db1)
    task_repo = TaskRepository(db1)
    approval_repo = ApprovalRepository(db1)

    session = session_repo.create_session(title="Approval Test")
    task = task_repo.create_task(prompt="Dangerous operation", session_id=session.id)

    # Create pending approval request
    app_rec = approval_repo.create_request(
        task_id=task.id,
        action="execute_command",
        action_summary="rm -rf build/",
        call_id="call-appr-01",
        token_hash="hash_secure_token",
    )
    assert app_rec.status == ApprovalStatus.PENDING
    db1.close()  # Restart

    # Boot Process 2
    db2 = DatabaseManager(db_path=str(disk_db_path), enable_wal=True)
    recovery = StartupRecoveryEngine(db2)
    summary = recovery.reconcile()

    assert summary.stale_approvals_invalidated == 1

    approval_repo2 = ApprovalRepository(db2)
    expired_app = approval_repo2.get_by_id(app_rec.id)
    assert expired_app.status == ApprovalStatus.EXPIRED
    assert "process_restart" in expired_app.resolution_reason

    # Single-use token consumption must be rejected
    consumed = approval_repo2.consume_token(app_rec.id)
    assert consumed is False
    db2.close()


def test_recovery_reconstruct_session_context_across_process_restart(
    disk_db_path: Path,
) -> None:
    """Test 7: Full session context, task history, and transcript reconstructed across restart."""
    db1 = DatabaseManager(db_path=str(disk_db_path), enable_wal=True)
    MigrationRunner.apply_all(db1.get_connection())
    session_mgr1 = SessionManager(db1)
    transcript_repo1 = TranscriptRepository(db1)

    session = session_mgr1.create_session(
        title="Historical Session",
        workspace_path="/workspace/project",
        metadata={"author": "engineer"},
    )
    t1 = session_mgr1.task_repo.create_task(prompt="Task 1", session_id=session.id)
    t2 = session_mgr1.task_repo.create_task(prompt="Task 2", session_id=session.id)

    transcript_repo1.append_message(
        session_id=session.id,
        task_id=t1.id,
        role="user",
        content="First question",
    )
    transcript_repo1.append_message(
        session_id=session.id,
        task_id=t1.id,
        role="assistant",
        content="First answer",
    )
    transcript_repo1.append_message(
        session_id=session.id,
        task_id=t2.id,
        role="user",
        content="Second question",
    )
    db1.close()  # Terminate process

    # Boot Process 2
    db2 = DatabaseManager(db_path=str(disk_db_path), enable_wal=True)
    session_mgr2 = SessionManager(db2)

    context = session_mgr2.load_session_context(session.id)
    assert context is not None
    assert context["session"]["id"] == session.id
    assert context["session"]["title"] == "Historical Session"
    assert len(context["tasks"]) == 2
    assert [t["id"] for t in context["tasks"]] == [t1.id, t2.id]
    assert len(context["transcript"]) == 3
    assert [m["sequence_num"] for m in context["transcript"]] == [1, 2, 3]
    assert context["transcript"][0]["content"] == "First question"
    assert context["transcript"][1]["content"] == "First answer"
    assert context["transcript"][2]["content"] == "Second question"
    db2.close()


def test_recovery_terminal_tasks_immutable_across_recovery(
    disk_db_path: Path,
) -> None:
    """Test 8: Terminal tasks (COMPLETED, CANCELLED, FAILED) are never modified by recovery."""
    db1 = DatabaseManager(db_path=str(disk_db_path), enable_wal=True)
    MigrationRunner.apply_all(db1.get_connection())
    session_repo = SessionRepository(db1)
    task_repo = TaskRepository(db1)

    session = session_repo.create_session(title="Terminal Session")
    t_comp = task_repo.create_task(prompt="Completed", session_id=session.id)
    task_repo.transition_task_state(t_comp.id, to_status=TaskStatus.EXECUTING, expected_version=1)
    task_repo.transition_task_state(t_comp.id, to_status=TaskStatus.COMPLETED, expected_version=2)

    t_canc = task_repo.create_task(prompt="Cancelled", session_id=session.id)
    task_repo.transition_task_state(t_canc.id, to_status=TaskStatus.CANCELLED, expected_version=1)

    t_fail = task_repo.create_task(prompt="Failed", session_id=session.id)
    task_repo.transition_task_state(t_fail.id, to_status=TaskStatus.FAILED, expected_version=1)
    db1.close()

    # Boot Process 2 and run recovery
    db2 = DatabaseManager(db_path=str(disk_db_path), enable_wal=True)
    recovery = StartupRecoveryEngine(db2)
    summary = recovery.reconcile()

    assert summary.interrupted_tasks_found == 0
    task_repo2 = TaskRepository(db2)
    assert task_repo2.get(t_comp.id).status == TaskStatus.COMPLETED
    assert task_repo2.get(t_canc.id).status == TaskStatus.CANCELLED
    assert task_repo2.get(t_fail.id).status == TaskStatus.FAILED
    db2.close()


def test_recovery_batch_interrupted_mixed_tasks(disk_db_path: Path) -> None:
    """Test 9: Multiple interrupted tasks in a batch across crash boundary."""
    db1 = DatabaseManager(db_path=str(disk_db_path), enable_wal=True)
    MigrationRunner.apply_all(db1.get_connection())
    session_repo = SessionRepository(db1)
    task_repo = TaskRepository(db1)
    tool_repo = ToolExecutionRepository(db1)

    session = session_repo.create_session(title="Batch Session")

    # Task A: no tools -> safe to resume
    t_a = task_repo.create_task(prompt="Task A", session_id=session.id)
    task_repo.transition_task_state(t_a.id, to_status=TaskStatus.EXECUTING, expected_version=1)

    # Task B: read_file -> safe to resume
    t_b = task_repo.create_task(prompt="Task B", session_id=session.id)
    task_repo.transition_task_state(t_b.id, to_status=TaskStatus.EXECUTING, expected_version=1)
    tool_repo.record_started(
        task_id=t_b.id,
        call_id="call-b-1",
        tool_name="read_file",
        arguments={"path": "a.txt"},
        is_mutation=False,
        idempotent=True,
    )

    # Task C: write_file -> needs review
    t_c = task_repo.create_task(prompt="Task C", session_id=session.id)
    task_repo.transition_task_state(t_c.id, to_status=TaskStatus.EXECUTING, expected_version=1)
    tool_repo.record_started(
        task_id=t_c.id,
        call_id="call-c-1",
        tool_name="write_file",
        arguments={"path": "b.txt", "content": "123"},
        is_mutation=True,
        idempotent=False,
    )
    db1.close()  # Crash

    # Boot Process 2
    db2 = DatabaseManager(db_path=str(disk_db_path), enable_wal=True)
    recovery = StartupRecoveryEngine(db2)
    summary = recovery.reconcile()

    assert summary.interrupted_tasks_found == 3
    assert summary.safe_to_resume_count == 2
    assert summary.needs_review_count == 1

    task_repo2 = TaskRepository(db2)
    assert task_repo2.get(t_a.id).recovery_status == RecoveryStatus.INTERRUPTED_SAFE
    assert task_repo2.get(t_b.id).recovery_status == RecoveryStatus.INTERRUPTED_SAFE
    assert task_repo2.get(t_c.id).recovery_status == RecoveryStatus.NEEDS_REVIEW
    db2.close()
