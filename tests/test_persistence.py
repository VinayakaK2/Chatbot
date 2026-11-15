"""Unit and repository tests for Phase 7 SQLite persistence layer.

Verifies DatabaseManager, MigrationRunner, and type-safe repositories:
SessionRepository, TaskRepository, TranscriptRepository, ToolExecutionRepository,
ModelCallRepository, and ApprovalRepository.
"""

import sqlite3
import pytest
from pathlib import Path
from typing import Generator

from api.schemas.tasks import TaskStatus
from storage.database import DatabaseManager
from storage.migrations import MigrationRunner, MIGRATIONS
from storage.models import (
    ApprovalRecord,
    ApprovalStatus,
    ExecutionState,
    ModelCallRecord,
    RecoveryClassification,
    RecoveryStatus,
    SessionRecord,
    SessionStatus,
    TaskRecord,
    TranscriptMessageRecord,
)
from storage.repositories import (
    ApprovalRepository,
    InvalidStateTransitionError,
    ModelCallRepository,
    SessionRepository,
    StaleVersionError,
    TaskRepository,
    ToolExecutionRepository,
    TranscriptRepository,
)


@pytest.fixture
def temp_db(tmp_path: Path) -> Generator[DatabaseManager, None, None]:
    """Provide a fresh on-disk SQLite DatabaseManager instance with migrations applied."""
    db_file = tmp_path / "test_persistence.db"
    db_url = f"sqlite:///{db_file.as_posix()}"
    manager = DatabaseManager(database_url=db_url)
    
    # Run migrations to ensure full schema is created
    runner = MigrationRunner(manager)
    runner.run_migrations()
    
    yield manager
    manager.close()


def test_database_manager_pragmas_and_wal(temp_db: DatabaseManager) -> None:
    """Verify that DatabaseManager enforces WAL mode, foreign keys, and busy timeout."""
    with temp_db.connection() as conn:
        journal_mode = conn.execute("PRAGMA journal_mode;").fetchone()[0]
        foreign_keys = conn.execute("PRAGMA foreign_keys;").fetchone()[0]
        busy_timeout = conn.execute("PRAGMA busy_timeout;").fetchone()[0]

    assert journal_mode.upper() == "WAL"
    assert foreign_keys == 1
    assert busy_timeout == 5000


def test_database_transaction_commit_and_rollback(temp_db: DatabaseManager) -> None:
    """Verify that transaction() context manager commits on success and rolls back on exception."""
    # Successful commit
    with temp_db.transaction() as conn:
        conn.execute("INSERT INTO sessions (id, title, workspace_path, status, created_at, updated_at) VALUES ('s1', 'Test', '/ws', 'active', '2026-01-01', '2026-01-01');")

    with temp_db.connection() as conn:
        row = conn.execute("SELECT id, title FROM sessions WHERE id = 's1';").fetchone()
        assert row is not None
        assert row["title"] == "Test"

    # Rollback on exception
    with pytest.raises(ValueError, match="Deliberate failure"):
        with temp_db.transaction() as conn:
            conn.execute("INSERT INTO sessions (id, title, workspace_path, status, created_at, updated_at) VALUES ('s2', 'Should Rollback', '/ws', 'active', '2026-01-01', '2026-01-01');")
            raise ValueError("Deliberate failure")

    with temp_db.connection() as conn:
        row = conn.execute("SELECT id FROM sessions WHERE id = 's2';").fetchone()
        assert row is None


def test_migration_runner_idempotency(temp_db: DatabaseManager) -> None:
    """Verify that running migrations multiple times is idempotent."""
    runner = MigrationRunner(temp_db)
    # Rerunning should not apply any new migrations or raise errors
    applied = runner.run_migrations()
    assert len(applied) == 0

    # Verify migration table record
    with temp_db.connection() as conn:
        migrations = conn.execute("SELECT version, description FROM schema_migrations;").fetchall()
        assert len(migrations) == len(MIGRATIONS)
        assert migrations[0]["description"] == "001_initial_schema"


def test_session_repository_crud(temp_db: DatabaseManager) -> None:
    """Verify SessionRepository create, read, list, and status updates."""
    repo = SessionRepository(temp_db)
    
    # Create
    session = repo.create_session(
        title="Refactor Architecture",
        workspace_path="/test/workspace",
        metadata={"priority": "high"},
    )
    assert session.id is not None
    assert session.title == "Refactor Architecture"
    assert session.status == SessionStatus.ACTIVE
    assert session.metadata == {"priority": "high"}

    # Get by ID
    fetched = repo.get_session(session.id)
    assert fetched is not None
    assert fetched.id == session.id
    assert fetched.workspace_path == "/test/workspace"

    # List
    sessions = repo.list_sessions(limit=10, offset=0)
    assert len(sessions) == 1
    assert sessions[0].id == session.id

    # Update status
    updated = repo.update_session_status(session.id, SessionStatus.ARCHIVED)
    assert updated is not None
    assert updated.status == SessionStatus.ARCHIVED

    # Verify updated status on fresh fetch
    re_fetched = repo.get_session(session.id)
    assert re_fetched is not None
    assert re_fetched.status == SessionStatus.ARCHIVED


def test_task_repository_creation_and_state_transitions(temp_db: DatabaseManager) -> None:
    """Verify TaskRepository creation, optimistic locking, and state transitions."""
    session_repo = SessionRepository(temp_db)
    task_repo = TaskRepository(temp_db)

    session = session_repo.create_session(title="Main Session")
    task = task_repo.create_task(
        prompt="Build test harness",
        session_id=session.id,
        metadata={"component": "harness"},
    )
    assert task.status == TaskStatus.PENDING
    assert task.version == 1
    assert task.session_id == session.id

    # Valid transition PENDING -> EXECUTING
    executing_task = task_repo.transition_task_state(
        task_id=task.id,
        to_status="EXECUTING",
        expected_version=1,
        reason="Agent loop started",
    )
    assert executing_task.status == TaskStatus.EXECUTING
    assert executing_task.version == 2

    # Attempt transition with stale version (expected 1, but is now 2) -> StaleVersionError
    with pytest.raises(StaleVersionError, match="Stale version detected"):
        task_repo.transition_task_state(
            task_id=task.id,
            to_status="COMPLETED",
            expected_version=1,
            reason="Stale update",
        )

    # Valid transition EXECUTING -> COMPLETED
    completed_task = task_repo.transition_task_state(
        task_id=task.id,
        to_status="COMPLETED",
        expected_version=2,
        reason="Task completed successfully",
    )
    assert completed_task.status == TaskStatus.COMPLETED
    assert completed_task.version == 3

    # Attempt transition from terminal state -> InvalidStateTransitionError
    with pytest.raises(InvalidStateTransitionError, match="Cannot transition task"):
        task_repo.transition_task_state(
            task_id=task.id,
            to_status="EXECUTING",
            expected_version=3,
            reason="Illegal reactivation",
        )

    # Verify state transitions history recorded
    transitions = task_repo.get_state_transitions(task.id)
    assert len(transitions) == 3
    assert transitions[0].from_state == "NONE"
    assert transitions[0].to_state == "pending"
    assert transitions[1].from_state == "pending"
    assert transitions[1].to_state == "executing"
    assert transitions[2].from_state == "executing"
    assert transitions[2].to_state == "completed"


def test_transcript_repository_ordering_and_retrieval(temp_db: DatabaseManager) -> None:
    """Verify TranscriptRepository sequence number ordering and message retrieval."""
    session_repo = SessionRepository(temp_db)
    task_repo = TaskRepository(temp_db)
    transcript_repo = TranscriptRepository(temp_db)

    session = session_repo.create_session(title="Transcript Session")
    task = task_repo.create_task(prompt="Transcript task", session_id=session.id)

    # Append sequential messages
    m1 = transcript_repo.append_message(
        session_id=session.id,
        task_id=task.id,
        role="user",
        content="Hello agent",
    )
    assert m1.sequence_number == 1

    m2 = transcript_repo.append_message(
        session_id=session.id,
        task_id=task.id,
        role="assistant",
        content="I will run the command",
    )
    assert m2.sequence_number == 2

    m3 = transcript_repo.append_message(
        session_id=session.id,
        task_id=task.id,
        role="tool",
        content="Command stdout result",
    )
    assert m3.sequence_number == 3

    # Retrieve by task
    task_msgs = transcript_repo.get_messages_by_task(task.id)
    assert len(task_msgs) == 3
    assert [m.sequence_number for m in task_msgs] == [1, 2, 3]
    assert task_msgs[0].role == "user"
    assert task_msgs[1].role == "assistant"
    assert task_msgs[2].role == "tool"

    # Retrieve by session
    session_msgs = transcript_repo.get_messages_by_session(session.id)
    assert len(session_msgs) == 3


def test_tool_execution_repository_lifecycle_and_scrubbing(temp_db: DatabaseManager) -> None:
    """Verify ToolExecutionRepository lifecycle recording and secret redaction."""
    session_repo = SessionRepository(temp_db)
    task_repo = TaskRepository(temp_db)
    tool_repo = ToolExecutionRepository(temp_db)

    session = session_repo.create_session(title="Tool Session")
    task = task_repo.create_task(prompt="Tool task", session_id=session.id)

    # 1. Record tool started with sensitive input
    sensitive_input = {
        "command": "git push https://ghp_secretTokenABC123@github.com/repo.git",
        "env": {"API_KEY": "sk-ant-api03-secretValueXYZ"},
    }
    execution = tool_repo.record_started(
        task_id=task.id,
        call_id="call-001",
        tool_name="execute_command",
        arguments=sensitive_input,
    )
    assert execution.execution_state == ExecutionState.STARTED
    # Verify secrets scrubbed from persisted record
    assert "ghp_secretTokenABC123" not in execution.arguments_json
    assert "[REDACTED]" in execution.arguments_json or "REDACTED" in execution.arguments_json
    assert "sk-ant-api03-secretValueXYZ" not in execution.arguments_json

    # 2. Record tool completed with sensitive output
    sensitive_output = {
        "output": "Authorized with Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.tokenPayload.sig",
    }
    completed = tool_repo.record_completed(
        execution_id=execution.id,
        result=sensitive_output,
        duration_ms=120,
    )
    assert completed.execution_state == ExecutionState.COMPLETED
    assert completed.duration_ms == 120
    assert "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9" not in completed.result_json
    assert "[REDACTED_TOKEN]" in completed.result_json

    # In-flight tools check
    in_flight = tool_repo.get_in_flight_tools(task.id)
    assert len(in_flight) == 0  # Tool was completed, so 0 in flight


def test_model_call_repository(temp_db: DatabaseManager) -> None:
    """Verify ModelCallRepository records token usage and duration accurately."""
    session_repo = SessionRepository(temp_db)
    task_repo = TaskRepository(temp_db)
    model_repo = ModelCallRepository(temp_db)

    session = session_repo.create_session(title="Model Session")
    task = task_repo.create_task(prompt="Model task", session_id=session.id)

    call = model_repo.record_call(
        task_id=task.id,
        model_name="Qwen2.5-Coder-14B",
        prompt_tokens=450,
        completion_tokens=85,
        total_tokens=535,
        duration_ms=1450,
        finish_reason="stop",
        turn_number=1,
    )
    assert call.id is not None
    assert call.total_tokens == 535
    assert call.finish_reason == "stop"

    calls = model_repo.get_calls_by_task(task.id)
    assert len(calls) == 1
    assert calls[0].model_name == "Qwen2.5-Coder-14B"


def test_approval_repository_lifecycle_and_single_use(temp_db: DatabaseManager) -> None:
    """Verify ApprovalRepository persistence, status transitions, and single-use token consumption."""
    session_repo = SessionRepository(temp_db)
    task_repo = TaskRepository(temp_db)
    approval_repo = ApprovalRepository(temp_db)

    session = session_repo.create_session(title="Approval Session")
    task = task_repo.create_task(prompt="Approval task", session_id=session.id)

    # 1. Create approval request
    rec = approval_repo.create_request(
        task_id=task.id,
        action="execute_command",
        action_summary="git push origin main",
        command_details={"cmd": "git push"},
        token_hash="hash_token_abc123",
    )
    assert rec.status == "pending"
    assert rec.token_consumed is False

    # 2. Resolve approval
    resolved = approval_repo.resolve_request(
        approval_id=rec.id,
        status="approved",
        resolved_by="user_operator",
    )
    assert resolved is not None
    assert resolved.status == "approved"
    assert resolved.resolved_by == "user_operator"

    # 3. Consume token
    consumed = approval_repo.consume_token(rec.id)
    assert consumed is True

    # 4. Attempt to consume token again -> must return False (single-use enforced)
    consumed_again = approval_repo.consume_token(rec.id)
    assert consumed_again is False

    # 5. Bulk expire pending approvals
    rec2 = approval_repo.create_request(
        task_id=task.id,
        action="execute_command",
        action_summary="dangerous command",
        command_details={},
        token_hash="hash_token_def456",
    )
    assert rec2.status == "pending"
    expired_count = approval_repo.expire_all_pending(reason="test_restart")
    assert expired_count == 1

    fetched_rec2 = approval_repo.get_by_id(rec2.id)
    assert fetched_rec2 is not None
    assert fetched_rec2.status == "expired"
    assert "test_restart" in fetched_rec2.resolution_reason


def test_database_level_trigger_terminal_immutability(temp_db: DatabaseManager) -> None:
    """Verify F7-02: SQLite triggers enforce terminal immutability at the database engine level.

    Direct raw SQL updates bypassing Python application code MUST be aborted by SQLite triggers.
    """
    session_repo = SessionRepository(temp_db)
    task_repo = TaskRepository(temp_db)

    session = session_repo.create_session(title="Trigger Test Session")
    task = task_repo.create_task(prompt="Trigger immutability test", session_id=session.id)

    # 1. Transition task through repository to terminal COMPLETED
    task_repo.transition_task_state(task.id, to_status="EXECUTING", expected_version=1)
    task_repo.transition_task_state(task.id, to_status="COMPLETED", expected_version=2)

    # 2. Direct raw SQL update attempting to reactivate completed task to EXECUTING
    with temp_db.connection() as conn:
        with pytest.raises(sqlite3.IntegrityError, match="terminal tasks.*are completely immutable"):
            conn.execute(
                "UPDATE tasks SET status = 'executing' WHERE id = ?;",
                (task.id,),
            )

    # 3. Direct raw SQL update attempting to tamper with OTHER columns of a terminal task (Full-row immutability)
    with temp_db.connection() as conn:
        for col_update in [
            "UPDATE tasks SET prompt = 'Tampered prompt' WHERE id = ?;",
            "UPDATE tasks SET response_text = 'Tampered response' WHERE id = ?;",
            "UPDATE tasks SET error = 'Tampered error' WHERE id = ?;",
            "UPDATE tasks SET termination_reason = 'tampered' WHERE id = ?;",
            "UPDATE tasks SET recovery_status = 'clean' WHERE id = ?;",
        ]:
            with pytest.raises(sqlite3.IntegrityError, match="terminal tasks.*are completely immutable"):
                conn.execute(col_update, (task.id,))

    # Verify task status and columns in database were NOT changed
    with temp_db.connection() as conn:
        row = conn.execute("SELECT status, prompt FROM tasks WHERE id = ?;", (task.id,)).fetchone()
        assert row["status"] == "completed"
        assert row["prompt"] == "Trigger immutability test"

    # 4. Create another task and set to terminal FAILED
    task2 = task_repo.create_task(prompt="Trigger fail test", session_id=session.id)
    task_repo.transition_task_state(task2.id, to_status="EXECUTING", expected_version=1)
    task_repo.transition_task_state(task2.id, to_status="FAILED", expected_version=2)

    # Direct raw SQL update attempting to set failed task back to PENDING or modify error
    with temp_db.connection() as conn:
        with pytest.raises(sqlite3.IntegrityError, match="terminal tasks.*are completely immutable"):
            conn.execute(
                "UPDATE tasks SET status = 'pending' WHERE id = ?;",
                (task2.id,),
            )
        with pytest.raises(sqlite3.IntegrityError, match="terminal tasks.*are completely immutable"):
            conn.execute(
                "UPDATE tasks SET error = 'Rewritten error' WHERE id = ?;",
                (task2.id,),
            )

    # 5. Direct raw SQL update attempting illegal pending -> completed jump
    task3 = task_repo.create_task(prompt="Trigger jump test", session_id=session.id)
    with temp_db.connection() as conn:
        with pytest.raises(sqlite3.IntegrityError, match="Illegal database state transition: pending tasks"):
            conn.execute(
                "UPDATE tasks SET status = 'completed' WHERE id = ?;",
                (task3.id,),
            )


def test_migration_upgrade_v1_to_v2_preserves_data(tmp_path: Path) -> None:
    """Verify F7-04: Upgrade from 001_initial_schema to 002_immutability_triggers_and_continuations.

    Existing data must be 100% preserved, new column parent_task_id added, and triggers installed.
    """
    db_file = tmp_path / "migration_upgrade.db"
    db_url = f"sqlite:///{db_file.as_posix()}"
    manager = DatabaseManager(database_url=db_url)

    # 1. Apply ONLY migration 1 manually
    v1_version, v1_desc, v1_sql = MIGRATIONS[0]
    assert v1_desc == "001_initial_schema"
    with manager.transaction() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version INTEGER PRIMARY KEY,
                description TEXT NOT NULL,
                applied_at TEXT NOT NULL
            );
            """
        )
        conn.executescript(v1_sql)
        conn.execute(
            "INSERT INTO schema_migrations (version, description, applied_at) VALUES (?, ?, ?);",
            (v1_version, v1_desc, "2026-01-01T00:00:00Z"),
        )

    # 2. Insert existing session and task records in v1 schema
    with manager.transaction() as conn:
        conn.execute(
            "INSERT INTO sessions (id, title, workspace_path, status, created_at, updated_at) "
            "VALUES ('s-legacy', 'Legacy Session', '/workspace', 'active', '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z');"
        )
        conn.execute(
            "INSERT INTO tasks (id, session_id, prompt, status, created_at, version) "
            "VALUES ('t-legacy', 's-legacy', 'Legacy Task Prompt', 'completed', '2026-01-01T00:00:00Z', 1);"
        )

    # 3. Run migration runner to apply remaining migrations (v2)
    runner = MigrationRunner(manager)
    applied = runner.run_migrations()
    assert len(applied) == 1
    assert applied[0] == 2

    # 4. Verify existing records are preserved and new column is accessible
    with manager.connection() as conn:
        task_row = conn.execute("SELECT id, session_id, prompt, status, parent_task_id FROM tasks WHERE id = 't-legacy';").fetchone()
        assert task_row is not None
        assert task_row["id"] == "t-legacy"
        assert task_row["prompt"] == "Legacy Task Prompt"
        assert task_row["status"] == "completed"
        assert task_row["parent_task_id"] is None

        # 5. Verify triggers are active on the upgraded database
        with pytest.raises(sqlite3.IntegrityError, match="terminal tasks.*are completely immutable"):
            conn.execute("UPDATE tasks SET status = 'executing' WHERE id = 't-legacy';")

    manager.close()


def test_migration_failure_rollback(tmp_path: Path) -> None:
    """Verify F7-04: Migration failure triggers automatic transactional rollback without partial states."""
    db_file = tmp_path / "migration_fail.db"
    db_url = f"sqlite:///{db_file.as_posix()}"
    manager = DatabaseManager(database_url=db_url)

    # Initialize schema migrations table
    runner = MigrationRunner(manager)
    runner.run_migrations()

    # Attempt to apply a multi-statement transaction that fails halfway
    with pytest.raises(sqlite3.OperationalError):
        with manager.transaction() as conn:
            conn.execute("CREATE TABLE broken_table (id TEXT PRIMARY KEY);")
            conn.execute("SYNTAX ERROR INVALID SQL;")

    # Verify broken_table was rolled back and does not exist in schema
    with manager.connection() as conn:
        table_exists = conn.execute(
            "SELECT count(*) FROM sqlite_master WHERE type='table' AND name='broken_table';"
        ).fetchone()[0]
        assert table_exists == 0

    manager.close()


def test_secret_scrubbing_across_all_tables(temp_db: DatabaseManager) -> None:
    """Verify F7-03: Secret scrubbing across all SQLite tables directly verified with raw SQL.

    Audits:
    - tasks: prompt, error, response_text, termination_reason
    - transcript_messages: content, tool_calls_json
    - tool_executions: arguments_json, result_output, error
    - approvals: reason, decision_reason
    - state_transitions: reason
    - sessions: metadata_json
    """
    session_repo = SessionRepository(temp_db)
    task_repo = TaskRepository(temp_db)
    transcript_repo = TranscriptRepository(temp_db)
    tool_repo = ToolExecutionRepository(temp_db)
    approval_repo = ApprovalRepository(temp_db)

    secret_key = "sk-ant-api03-TOPSECRET1234567890abcdef"
    secret_token = "ghp_PersonalAccessTokenABC123456789"
    secret_bearer = "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.supersecretpayload.xyz"

    # 1. Session with secret in metadata
    session = session_repo.create_session(
        title="Secret Audit Session",
        metadata={"api_key": secret_key, "token": secret_token},
    )

    # 2. Task with secrets in prompt, response, error, termination_reason
    task = task_repo.create_task(
        prompt=f"Please use token {secret_token} to deploy",
        session_id=session.id,
    )
    task_repo.transition_task_state(
        task_id=task.id,
        to_status="EXECUTING",
        expected_version=1,
        reason=f"Transition with secret {secret_key}",
    )
    task_repo.transition_state(
        task_id=task.id,
        target_status=TaskStatus.FAILED,
        response_text=f"Response containing {secret_bearer}",
        error=f"Connection failed using key {secret_key}",
        termination_reason=f"Terminated due to {secret_token}",
        reason=f"Audit transition reason {secret_key}",
    )

    # 3. Transcript messages with secrets
    transcript_repo.append_message(
        session_id=session.id,
        task_id=task.id,
        role="user",
        content=f"My secret key is {secret_key}",
        tool_calls_json=f'{{"token": "{secret_token}"}}',
    )

    # 4. Tool execution with secrets
    exec_rec = tool_repo.record_started(
        task_id=task.id,
        call_id="call-sec-01",
        tool_name="test_tool",
        arguments={"auth": secret_bearer, "api_key": secret_key},
    )
    tool_repo.record_completed(
        call_id=exec_rec.call_id,
        result_output={"response": f"Authenticated with {secret_token}"},
        error=f"Error containing {secret_key}",
    )

    # 5. Approval request with secrets
    app_rec = approval_repo.create_request(
        task_id=task.id,
        action="deploy",
        action_summary=f"Deploy using token {secret_token}",
    )
    approval_repo.resolve(
        call_id=app_rec.call_id,
        status=ApprovalStatus.APPROVED,
        decision_reason=f"Approved with key {secret_key}",
    )

    # Direct raw SQL audit across every table: assert NO raw secrets exist
    raw_secrets = [secret_key, secret_token, "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"]

    with temp_db.connection() as conn:
        for tbl in ["sessions", "tasks", "transcript_messages", "tool_executions", "approvals", "state_transitions"]:
            rows = conn.execute(f"SELECT * FROM {tbl};").fetchall()
            for row in rows:
                row_str = " ".join(str(v) for v in dict(row).values())
                for secret in raw_secrets:
                    assert secret not in row_str, f"Raw secret leaked in table {tbl}: column data contains '{secret}'"

