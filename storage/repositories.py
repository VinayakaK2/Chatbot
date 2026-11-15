"""Storage repositories providing type-safe CRUD, state machine enforcement, and audit logs."""

from datetime import datetime, timezone
import json
import logging
import sqlite3
from typing import Any, Dict, List, Optional
import uuid

from api.schemas.tasks import TaskStatus
from storage.database import DatabaseManager
from storage.models import (
    ApprovalRecord,
    ApprovalStatus,
    ExecutionState,
    MessageRole,
    ModelCallRecord,
    PersistedEventRecord,
    RecoveryStatus,
    SessionRecord,
    SessionStatus,
    StateTransitionRecord,
    TaskRecord,
    ToolExecutionRecord,
    TranscriptMessageRecord,
    VerificationRecord,
    VerificationStatus,
    VerificationType,
)
from tools.security import redact_secrets

logger = logging.getLogger("agent_runtime.storage.repositories")

TERMINAL_TASK_STATUSES = {
    TaskStatus.COMPLETED,
    TaskStatus.CANCELLED,
    TaskStatus.FAILED,
    TaskStatus.LIMIT_REACHED,
}


class StorageError(Exception):
    """Base exception for storage and repository operations."""


class InvalidStateTransitionError(StorageError):
    """Raised when an illegal state machine transition is attempted."""


class StaleVersionError(StorageError):
    """Raised when an optimistic concurrency version check fails."""


class TaskNotFoundError(StorageError):
    """Raised when querying or mutating a non-existent task."""


class SessionNotFoundError(StorageError):
    """Raised when querying or mutating a non-existent session."""


def _row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
    """Convert an SQLite row object into a standard python dictionary."""
    return {k: row[k] for k in row.keys()}


class SessionRepository:
    """Repository managing lifecycle and queries for agent sessions."""

    def __init__(self, db: DatabaseManager) -> None:
        self.db = db

    def create(self, session: SessionRecord) -> SessionRecord:
        """Persist a new session record with secret scrubbing."""
        clean_title = redact_secrets(session.title)
        clean_meta = redact_secrets(session.metadata_json) if session.metadata_json else None

        with self.db.transaction() as conn:
            conn.execute(
                """
                INSERT INTO sessions (id, title, workspace_path, status, metadata_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    session.id,
                    clean_title,
                    session.workspace_path,
                    session.status.value,
                    clean_meta,
                    session.created_at,
                    session.updated_at,
                ),
            )
        return self.get(session.id)

    def create_session(
        self,
        title: str,
        workspace_path: str = ".",
        metadata: Optional[Dict[str, Any]] = None,
        status: SessionStatus = SessionStatus.ACTIVE,
    ) -> SessionRecord:
        """Factory helper creating and persisting a session record."""
        session_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        rec = SessionRecord(
            id=session_id,
            title=title,
            workspace_path=workspace_path,
            status=status,
            metadata_json=json.dumps(metadata) if metadata else None,
            created_at=now,
            updated_at=now,
        )
        return self.create(rec)

    def get_session(self, session_id: str) -> Optional[SessionRecord]:
        """Alias for get()."""
        return self.get(session_id)

    def get(self, session_id: str) -> Optional[SessionRecord]:
        """Retrieve session record by ID."""
        conn = self.db.get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM sessions WHERE id = ?;", (session_id,))
        row = cursor.fetchone()
        if not row:
            return None
        d = _row_to_dict(row)
        d["status"] = SessionStatus(d["status"])
        return SessionRecord(**d)

    def list_all(self, limit: int = 50, offset: int = 0) -> List[SessionRecord]:
        """List sessions ordered by update timestamp descending."""
        conn = self.db.get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM sessions ORDER BY updated_at DESC LIMIT ? OFFSET ?;",
            (limit, offset),
        )
        results: List[SessionRecord] = []
        for row in cursor.fetchall():
            d = _row_to_dict(row)
            d["status"] = SessionStatus(d["status"])
            results.append(SessionRecord(**d))
        return results

    def update_status(self, session_id: str, status: SessionStatus) -> Optional[SessionRecord]:
        """Update session status and bump updated_at timestamp."""
        now_iso = datetime.now(timezone.utc).isoformat()
        with self.db.transaction() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE sessions SET status = ?, updated_at = ? WHERE id = ?;",
                (status.value, now_iso, session_id),
            )
            if cursor.rowcount == 0:
                return None
        return self.get(session_id)

    list_sessions = list_all
    update_session_status = update_status


class TaskRepository:
    """Repository enforcing task state transitions, optimistic locking, and relational persistence."""

    def __init__(self, db: DatabaseManager) -> None:
        self.db = db

    def create(self, task: TaskRecord) -> TaskRecord:
        """Create and persist a new task record with transition audit logging and secret scrubbing."""
        clean_prompt = redact_secrets(task.prompt)
        clean_resp = redact_secrets(task.response_text) if task.response_text else None
        clean_err = redact_secrets(task.error) if task.error else None
        clean_term = redact_secrets(task.termination_reason) if task.termination_reason else None

        v_status = task.verification_status.value if hasattr(task, "verification_status") and task.verification_status else VerificationStatus.NOT_RUN.value
        corr_id = task.correlation_id if hasattr(task, "correlation_id") else None

        with self.db.transaction() as conn:
            conn.execute(
                """
                INSERT INTO tasks (
                    id, session_id, parent_task_id, prompt, status, recovery_status, response_text,
                    finish_reason, model, usage_json, error, iterations, tool_calls_count,
                    termination_reason, created_at, started_at, completed_at, version,
                    verification_status, correlation_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    task.id,
                    task.session_id,
                    task.parent_task_id,
                    clean_prompt,
                    task.status.value,
                    task.recovery_status.value if task.recovery_status else None,
                    clean_resp,
                    task.finish_reason,
                    task.model,
                    task.usage_json,
                    clean_err,
                    task.iterations,
                    task.tool_calls_count,
                    clean_term,
                    task.created_at,
                    task.started_at,
                    task.completed_at,
                    task.version,
                    v_status,
                    corr_id,
                ),
            )
            # Record initial creation transition
            conn.execute(
                """
                INSERT INTO state_transitions (id, entity_type, entity_id, from_state, to_state, reason, actor, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    str(uuid.uuid4()),
                    "task",
                    task.id,
                    "NONE",
                    task.status.value,
                    "Initial task creation",
                    "runtime",
                    task.created_at,
                ),
            )
        return self.get(task.id)

    def _enrich_task_row(self, cursor: sqlite3.Cursor, d: Dict[str, Any]) -> TaskRecord:
        """Enrich database row dict with parsed enums and latest verification record status."""
        d["status"] = TaskStatus(d["status"])
        if d.get("recovery_status"):
            d["recovery_status"] = RecoveryStatus(d["recovery_status"])
        
        # Check verification_records for the authoritative latest verification outcome
        task_id = d["id"]
        cursor.execute("SELECT result FROM verification_records WHERE task_id = ? ORDER BY created_at DESC LIMIT 1;", (task_id,))
        vrow = cursor.fetchone()
        if vrow:
            d["verification_status"] = VerificationStatus(vrow[0])
        elif d.get("verification_status"):
            d["verification_status"] = VerificationStatus(d["verification_status"])
        else:
            d["verification_status"] = VerificationStatus.NOT_RUN
        return TaskRecord(**d)

    def get(self, task_id: str) -> Optional[TaskRecord]:
        """Retrieve task record by ID."""
        conn = self.db.get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM tasks WHERE id = ?;", (task_id,))
        row = cursor.fetchone()
        if not row:
            return None
        return self._enrich_task_row(cursor, _row_to_dict(row))

    def get_by_parent_task_id(self, parent_task_id: str) -> Optional[TaskRecord]:
        """Retrieve the continuation task record linked to a given parent task ID, if one exists."""
        conn = self.db.get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM tasks WHERE parent_task_id = ? LIMIT 1;", (parent_task_id,))
        row = cursor.fetchone()
        if not row:
            return None
        return self._enrich_task_row(cursor, _row_to_dict(row))

    def list_for_session(self, session_id: str) -> List[TaskRecord]:
        """List tasks belonging to a session in chronological order."""
        conn = self.db.get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM tasks WHERE session_id = ? ORDER BY created_at ASC;",
            (session_id,),
        )
        return [self._enrich_task_row(cursor, _row_to_dict(row)) for row in cursor.fetchall()]

    def transition_state(
        self,
        task_id: str,
        target_status: TaskStatus,
        expected_version: Optional[int] = None,
        recovery_status: Optional[RecoveryStatus] = None,
        response_text: Optional[str] = None,
        finish_reason: Optional[str] = None,
        model: Optional[str] = None,
        usage_json: Optional[str] = None,
        error: Optional[str] = None,
        iterations: Optional[int] = None,
        tool_calls_count: Optional[int] = None,
        termination_reason: Optional[str] = None,
        actor: str = "runtime",
        reason: Optional[str] = None,
    ) -> TaskRecord:
        """Atomically validate and execute a task state transition with audit logging.

        Raises:
            TaskNotFoundError: If task_id does not exist.
            InvalidStateTransitionError: If transition violates linearizable rules or mutates a terminal state.
        """
        now_iso = datetime.now(timezone.utc).isoformat()

        with self.db.transaction() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM tasks WHERE id = ?;", (task_id,))
            row = cursor.fetchone()
            if not row:
                raise TaskNotFoundError(f"Task with ID '{task_id}' not found.")

            current = _row_to_dict(row)
            current_status = TaskStatus(current["status"])
            current_version = current["version"]

            # Optimistic concurrency check
            if expected_version is not None and current_version != expected_version:
                raise StaleVersionError(
                    f"Stale version detected on task {task_id}: expected version {expected_version}, got {current_version}"
                )

            # 1. Terminal immutability: Once terminal, NO FURTHER transitions are legal
            if current_status in TERMINAL_TASK_STATUSES:
                raise InvalidStateTransitionError(
                    f"Cannot transition task {task_id} from terminal state '{current_status.value}' to '{target_status.value}'."
                )

            # 2. Transition validity matrix
            valid = False
            if current_status == TaskStatus.PENDING:
                valid = target_status in (
                    TaskStatus.EXECUTING,
                    TaskStatus.CANCELLED,
                    TaskStatus.FAILED,
                )
            elif current_status == TaskStatus.EXECUTING:
                valid = target_status in TERMINAL_TASK_STATUSES

            if not valid:
                raise InvalidStateTransitionError(
                    f"Illegal state transition requested for task {task_id}: '{current_status.value}' -> '{target_status.value}'"
                )

            # Compute updated fields
            new_version = current_version + 1
            completed_at = now_iso if target_status in TERMINAL_TASK_STATUSES else current["completed_at"]
            started_at = now_iso if (target_status == TaskStatus.EXECUTING and not current["started_at"]) else current["started_at"]

            raw_response = response_text if response_text is not None else current["response_text"]
            final_response = redact_secrets(raw_response) if raw_response else None
            final_finish_reason = finish_reason if finish_reason is not None else current["finish_reason"]
            final_model = model if model is not None else current["model"]
            final_usage = usage_json if usage_json is not None else current["usage_json"]
            raw_error = error if error is not None else current["error"]
            final_error = redact_secrets(raw_error) if raw_error else None
            final_iterations = iterations if iterations is not None else current["iterations"]
            final_tool_count = tool_calls_count if tool_calls_count is not None else current["tool_calls_count"]
            raw_termination = termination_reason if termination_reason is not None else current["termination_reason"]
            final_termination = redact_secrets(raw_termination) if raw_termination else None
            final_recovery = recovery_status.value if recovery_status is not None else current["recovery_status"]
            clean_transition_reason = redact_secrets(reason or f"Transition to {target_status.value}")

            cursor.execute(
                """
                UPDATE tasks SET
                    status = ?,
                    recovery_status = ?,
                    response_text = ?,
                    finish_reason = ?,
                    model = ?,
                    usage_json = ?,
                    error = ?,
                    iterations = ?,
                    tool_calls_count = ?,
                    termination_reason = ?,
                    started_at = ?,
                    completed_at = ?,
                    version = ?
                WHERE id = ?;
                """,
                (
                    target_status.value,
                    final_recovery,
                    final_response,
                    final_finish_reason,
                    final_model,
                    final_usage,
                    final_error,
                    final_iterations,
                    final_tool_count,
                    final_termination,
                    started_at,
                    completed_at,
                    new_version,
                    task_id,
                ),
            )

            # Audit log transition
            cursor.execute(
                """
                INSERT INTO state_transitions (id, entity_type, entity_id, from_state, to_state, reason, actor, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    str(uuid.uuid4()),
                    "task",
                    task_id,
                    current_status.value,
                    target_status.value,
                    clean_transition_reason,
                    actor,
                    now_iso,
                ),
            )

        updated_task = self.get(task_id)
        if not updated_task:
            raise TaskNotFoundError(f"Task with ID '{task_id}' could not be reloaded after update.")
        return updated_task

    def create_task(
        self,
        prompt: str,
        session_id: Optional[str] = None,
        status: TaskStatus = TaskStatus.PENDING,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> TaskRecord:
        """Create and persist a new task record with default values."""
        task_id = str(uuid.uuid4())
        rec = TaskRecord(
            id=task_id,
            session_id=session_id,
            prompt=prompt,
            status=status,
        )
        return self.create(rec)

    def transition_task_state(
        self,
        task_id: str,
        to_status: str | TaskStatus,
        expected_version: Optional[int] = None,
        reason: Optional[str] = None,
    ) -> TaskRecord:
        """Transition task state using string or TaskStatus with version checking."""
        target = TaskStatus(to_status.lower() if isinstance(to_status, str) else to_status)
        return self.transition_state(
            task_id=task_id,
            target_status=target,
            expected_version=expected_version,
            reason=reason,
        )

    def get_state_transitions(self, task_id: str) -> List[StateTransitionRecord]:
        """Retrieve audit history of state machine transitions for a task."""
        conn = self.db.get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM state_transitions WHERE entity_id = ? ORDER BY created_at ASC;",
            (task_id,),
        )
        results: List[StateTransitionRecord] = []
        for row in cursor.fetchall():
            results.append(StateTransitionRecord(**_row_to_dict(row)))
        return results

    def delete(self, task_id: str) -> bool:
        """Delete a task and its cascading associations from the database.

        Returns:
            bool: True if a task record was deleted, False otherwise.
        """
        with self.db.transaction() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM tasks WHERE id = ?;", (task_id,))
            return cursor.rowcount > 0


class TranscriptRepository:
    """Repository managing append-only durable conversation transcript messages."""

    def __init__(self, db: DatabaseManager) -> None:
        self.db = db

    def append(self, message: TranscriptMessageRecord) -> TranscriptMessageRecord:
        """Persist a message turn to the transcript with secret scrubbing."""
        clean_content = redact_secrets(message.content) if message.content else None
        clean_tool_calls = redact_secrets(message.tool_calls_json) if message.tool_calls_json else None

        with self.db.transaction() as conn:
            conn.execute(
                """
                INSERT INTO transcript_messages (
                    id, session_id, task_id, sequence_num, role, content,
                    name, tool_call_id, tool_calls_json, is_derived, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    message.id,
                    message.session_id,
                    message.task_id,
                    message.sequence_num,
                    message.role.value,
                    clean_content,
                    message.name,
                    message.tool_call_id,
                    clean_tool_calls,
                    1 if message.is_derived else 0,
                    message.created_at,
                ),
            )
        return message

    def append_message(
        self,
        session_id: Optional[str],
        task_id: Optional[str],
        role: str | MessageRole,
        content: Optional[str],
        sequence_num: Optional[int] = None,
        name: Optional[str] = None,
        tool_call_id: Optional[str] = None,
        tool_calls_json: Optional[str] = None,
        is_derived: bool = False,
    ) -> TranscriptMessageRecord:
        """Create and append a transcript message with automatic sequence numbering."""
        seq = sequence_num
        if seq is None:
            conn = self.db.get_connection()
            cursor = conn.cursor()
            if session_id:
                cursor.execute(
                    "SELECT COALESCE(MAX(sequence_num), 0) FROM transcript_messages WHERE session_id = ?;",
                    (session_id,),
                )
            else:
                cursor.execute(
                    "SELECT COALESCE(MAX(sequence_num), 0) FROM transcript_messages WHERE task_id = ?;",
                    (task_id,),
                )
            seq = cursor.fetchone()[0] + 1

        target_role = MessageRole(role.lower() if isinstance(role, str) else role.value)
        rec = TranscriptMessageRecord(
            id=str(uuid.uuid4()),
            session_id=session_id,
            task_id=task_id,
            sequence_num=seq,
            role=target_role,
            content=content,
            name=name,
            tool_call_id=tool_call_id,
            tool_calls_json=tool_calls_json,
            is_derived=is_derived,
        )
        return self.append(rec)

    def list_for_session(self, session_id: str) -> List[TranscriptMessageRecord]:
        """Retrieve all messages for a session ordered by sequence number."""
        conn = self.db.get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM transcript_messages WHERE session_id = ? ORDER BY sequence_num ASC;",
            (session_id,),
        )
        results: List[TranscriptMessageRecord] = []
        for row in cursor.fetchall():
            d = _row_to_dict(row)
            d["role"] = MessageRole(d["role"])
            d["is_derived"] = bool(d["is_derived"])
            results.append(TranscriptMessageRecord(**d))
        return results

    def list_for_task(self, task_id: str) -> List[TranscriptMessageRecord]:
        """Retrieve all messages associated with a specific task."""
        conn = self.db.get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM transcript_messages WHERE task_id = ? ORDER BY sequence_num ASC;",
            (task_id,),
        )
        results: List[TranscriptMessageRecord] = []
        for row in cursor.fetchall():
            d = _row_to_dict(row)
            d["role"] = MessageRole(d["role"])
            d["is_derived"] = bool(d["is_derived"])
            results.append(TranscriptMessageRecord(**d))
        return results

    get_messages_by_task = list_for_task
    get_messages_by_session = list_for_session


class ToolExecutionRepository:
    """Repository managing tool execution lifecycle records with secret scrubbing."""

    def __init__(self, db: DatabaseManager, extra_secrets: Optional[List[str]] = None) -> None:
        self.db = db
        self.extra_secrets = extra_secrets or []

    def record_started(
        self,
        task_id: str,
        call_id: str,
        tool_name: str,
        arguments: Any,
        is_mutation: bool = False,
        idempotent: bool = False,
        correlation_id: Optional[str] = None,
        retry_count: int = 0,
    ) -> ToolExecutionRecord:
        """Log the initiation of a tool invocation before dispatching to execution environment."""
        if isinstance(arguments, dict):
            args_str = json.dumps(arguments)
        else:
            args_str = str(arguments)

        scrubbed_args = redact_secrets(args_str, extra_secrets=self.extra_secrets)
        exec_id = str(uuid.uuid4())
        now_iso = datetime.now(timezone.utc).isoformat()

        record = ToolExecutionRecord(
            id=exec_id,
            task_id=task_id,
            call_id=call_id,
            tool_name=tool_name,
            arguments_json=scrubbed_args,
            execution_state=ExecutionState.STARTED,
            is_mutation=is_mutation,
            idempotent=idempotent,
            correlation_id=correlation_id,
            retry_count=retry_count,
            created_at=now_iso,
            started_at=now_iso,
        )

        with self.db.transaction() as conn:
            conn.execute(
                """
                INSERT INTO tool_executions (
                    id, task_id, call_id, tool_name, arguments_json,
                    execution_state, is_mutation, idempotent, created_at, started_at,
                    correlation_id, retry_count
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    record.id,
                    record.task_id,
                    record.call_id,
                    record.tool_name,
                    record.arguments_json,
                    record.execution_state.value,
                    1 if record.is_mutation else 0,
                    1 if record.idempotent else 0,
                    record.created_at,
                    record.started_at,
                    record.correlation_id,
                    record.retry_count,
                ),
            )
        return record

    def record_completed(
        self,
        call_id: Optional[str] = None,
        execution_id: Optional[str] = None,
        state: ExecutionState = ExecutionState.COMPLETED,
        exit_code: Optional[int] = None,
        result_output: Optional[Any] = None,
        result: Optional[Any] = None,
        error: Optional[str] = None,
        error_category: Optional[str] = None,
        duration_ms: Optional[float] = None,
    ) -> Optional[ToolExecutionRecord]:
        """Update tool execution record upon completion, failure, or interruption."""
        lookup_id = call_id or execution_id
        effective_output = result if result is not None else result_output

        out_str = None
        if effective_output is not None:
            if isinstance(effective_output, (dict, list)):
                out_str = json.dumps(effective_output)
            else:
                out_str = str(effective_output)
            out_str = redact_secrets(out_str, extra_secrets=self.extra_secrets)

        err_str = None
        if error is not None:
            err_str = redact_secrets(str(error), extra_secrets=self.extra_secrets)

        now_iso = datetime.now(timezone.utc).isoformat()

        with self.db.transaction() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                UPDATE tool_executions SET
                    execution_state = ?,
                    exit_code = ?,
                    result_output = ?,
                    error = ?,
                    error_category = ?,
                    duration_ms = ?,
                    completed_at = ?
                WHERE call_id = ? OR id = ?;
                """,
                (
                    state.value,
                    exit_code,
                    out_str,
                    err_str,
                    error_category,
                    duration_ms,
                    now_iso,
                    lookup_id,
                    lookup_id,
                ),
            )

        return self.get_by_call_id(lookup_id) if lookup_id else None

    def get_by_call_id(self, call_id: str) -> Optional[ToolExecutionRecord]:
        """Retrieve tool execution record by call_id or record id."""
        conn = self.db.get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM tool_executions WHERE call_id = ? OR id = ? LIMIT 1;", (call_id, call_id))
        row = cursor.fetchone()
        if not row:
            return None
        d = _row_to_dict(row)
        d["execution_state"] = ExecutionState(d["execution_state"])
        d["is_mutation"] = bool(d["is_mutation"])
        d["idempotent"] = bool(d["idempotent"])
        return ToolExecutionRecord(**d)

    def list_for_task(self, task_id: str) -> List[ToolExecutionRecord]:
        """List all tool executions recorded for a task."""
        conn = self.db.get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM tool_executions WHERE task_id = ? ORDER BY created_at ASC;",
            (task_id,),
        )
        results: List[ToolExecutionRecord] = []
        for row in cursor.fetchall():
            d = _row_to_dict(row)
            d["execution_state"] = ExecutionState(d["execution_state"])
            d["is_mutation"] = bool(d["is_mutation"])
            d["idempotent"] = bool(d["idempotent"])
            results.append(ToolExecutionRecord(**d))
        return results

    def get_inflight_for_task(self, task_id: str) -> List[ToolExecutionRecord]:
        """Find tool executions that started but never recorded completion or failure."""
        conn = self.db.get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM tool_executions WHERE task_id = ? AND execution_state = 'started';",
            (task_id,),
        )
        results: List[ToolExecutionRecord] = []
        for row in cursor.fetchall():
            d = _row_to_dict(row)
            d["execution_state"] = ExecutionState(d["execution_state"])
            d["is_mutation"] = bool(d["is_mutation"])
            d["idempotent"] = bool(d["idempotent"])
            results.append(ToolExecutionRecord(**d))
        return results

    get_in_flight_tools = get_inflight_for_task


class ModelCallRepository:
    """Repository managing model completion telemetry records."""

    def __init__(self, db: DatabaseManager) -> None:
        self.db = db

    def record(self, call: ModelCallRecord) -> ModelCallRecord:
        """Persist a model completion record."""
        with self.db.transaction() as conn:
            conn.execute(
                """
                INSERT INTO model_calls (
                    id, task_id, iteration, model, prompt_tokens,
                    completion_tokens, total_tokens, finish_reason, latency_ms, created_at,
                    request_id, correlation_id, retry_count, failure_category
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    call.id,
                    call.task_id,
                    call.iteration,
                    call.model,
                    call.prompt_tokens,
                    call.completion_tokens,
                    call.total_tokens,
                    call.finish_reason,
                    call.latency_ms,
                    call.created_at,
                    call.request_id,
                    call.correlation_id,
                    call.retry_count,
                    call.failure_category,
                ),
            )
        return call

    def record_call(
        self,
        task_id: str,
        model_name: str,
        prompt_tokens: int,
        completion_tokens: int,
        total_tokens: int,
        duration_ms: float,
        finish_reason: Optional[str] = None,
        turn_number: int = 1,
        request_id: Optional[str] = None,
        correlation_id: Optional[str] = None,
        retry_count: int = 0,
        failure_category: Optional[str] = None,
    ) -> ModelCallRecord:
        """Create and record a model completion telemetry entry."""
        rec = ModelCallRecord(
            id=str(uuid.uuid4()),
            task_id=task_id,
            request_id=request_id,
            correlation_id=correlation_id,
            iteration=turn_number,
            model=model_name,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            finish_reason=finish_reason,
            latency_ms=duration_ms,
            retry_count=retry_count,
            failure_category=failure_category,
        )
        return self.record(rec)

    def list_for_task(self, task_id: str) -> List[ModelCallRecord]:
        """Retrieve all model calls for a task."""
        conn = self.db.get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM model_calls WHERE task_id = ? ORDER BY iteration ASC;",
            (task_id,),
        )
        results: List[ModelCallRecord] = []
        for row in cursor.fetchall():
            results.append(ModelCallRecord(**_row_to_dict(row)))
        return results

    get_calls_by_task = list_for_task


class ApprovalRepository:
    """Repository managing durable tracking of human approvals and startup invalidation."""

    def __init__(self, db: DatabaseManager) -> None:
        self.db = db

    def create(self, approval: ApprovalRecord) -> ApprovalRecord:
        """Persist an approval request record with secret scrubbing."""
        clean_reason = redact_secrets(approval.reason) if approval.reason else None
        clean_decision_reason = redact_secrets(approval.decision_reason) if approval.decision_reason else None

        with self.db.transaction() as conn:
            conn.execute(
                """
                INSERT INTO approvals (
                    id, task_id, call_id, tool_name, risk_category, action_hash,
                    status, reason, decision_reason, requested_at, expires_at, decided_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    approval.id,
                    approval.task_id,
                    approval.call_id,
                    approval.tool_name,
                    approval.risk_category,
                    approval.action_hash,
                    approval.status.value,
                    clean_reason,
                    clean_decision_reason,
                    approval.requested_at,
                    approval.expires_at,
                    approval.decided_at,
                ),
            )
        return approval

    def create_request(
        self,
        task_id: str,
        action: str,
        action_summary: str,
        command_details: Optional[Dict[str, Any]] = None,
        token_hash: Optional[str] = None,
        call_id: Optional[str] = None,
        risk_category: str = "command_execution",
        expires_in_seconds: float = 300.0,
    ) -> ApprovalRecord:
        """Convenience method to construct and persist an approval request."""
        now_dt = datetime.now(timezone.utc)
        expires_dt = datetime.fromtimestamp(now_dt.timestamp() + expires_in_seconds, tz=timezone.utc)
        rec = ApprovalRecord(
            id=str(uuid.uuid4()),
            task_id=task_id,
            call_id=call_id or str(uuid.uuid4()),
            tool_name=action,
            risk_category=risk_category,
            action_hash=token_hash or "hash_token",
            status=ApprovalStatus.PENDING,
            reason=action_summary,
            requested_at=now_dt.isoformat(),
            expires_at=expires_dt.isoformat(),
        )
        return self.create(rec)

    def get_by_call_id(self, call_id: str) -> Optional[ApprovalRecord]:
        """Retrieve approval record by call_id or id."""
        conn = self.db.get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM approvals WHERE call_id = ? OR id = ? LIMIT 1;", (call_id, call_id))
        row = cursor.fetchone()
        if not row:
            return None
        d = _row_to_dict(row)
        d["status"] = ApprovalStatus(d["status"])
        return ApprovalRecord(**d)

    get_by_id = get_by_call_id

    def resolve(
        self,
        call_id: str,
        status: ApprovalStatus,
        decision_reason: Optional[str] = None,
    ) -> Optional[ApprovalRecord]:
        """Update approval record decision with secret scrubbing."""
        clean_decision_reason = redact_secrets(decision_reason) if decision_reason else None
        now_iso = datetime.now(timezone.utc).isoformat()
        with self.db.transaction() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE approvals SET status = ?, decision_reason = ?, decided_at = ? WHERE call_id = ? OR id = ?;",
                (status.value, clean_decision_reason, now_iso, call_id, call_id),
            )
        return self.get_by_call_id(call_id)

    def resolve_request(
        self,
        approval_id: str,
        status: str | ApprovalStatus,
        resolved_by: Optional[str] = None,
    ) -> Optional[ApprovalRecord]:
        """Resolve approval by ID or call_id with string status."""
        target_status = ApprovalStatus(status.lower() if isinstance(status, str) else status)
        return self.resolve(call_id=approval_id, status=target_status, decision_reason=resolved_by)

    def consume_token(self, approval_id_or_call_id: str) -> bool:
        """Mark approval token as consumed. Single-use: returns True on first consume, False thereafter."""
        now_iso = datetime.now(timezone.utc).isoformat()
        with self.db.transaction() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE approvals SET status = 'consumed', decided_at = ? WHERE (id = ? OR call_id = ?) AND status = 'approved';",
                (now_iso, approval_id_or_call_id, approval_id_or_call_id),
            )
            return cursor.rowcount > 0

    def invalidate_pending_approvals(self, reason: str = "process_restart") -> int:
        """Expire all pending and approved-unconsumed approvals across restart. No grants survive process termination."""
        now_iso = datetime.now(timezone.utc).isoformat()
        with self.db.transaction() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE approvals SET status = 'expired', decision_reason = ?, decided_at = ? WHERE status IN ('pending', 'approved');",
                (f"Invalidated on {reason}", now_iso),
            )
            count = cursor.rowcount
            if count > 0:
                logger.info("Invalidated %d stale pending/approved authorizations due to %s", count, reason)
        return count

    expire_all_pending = invalidate_pending_approvals


class VerificationRepository:
    """Repository managing trusted verification execution records."""

    def __init__(self, db: DatabaseManager, extra_secrets: Optional[List[str]] = None) -> None:
        self.db = db
        self.extra_secrets = extra_secrets or []

    def create(self, record: VerificationRecord) -> VerificationRecord:
        """Persist a verification record with secret scrubbing and output truncation."""
        clean_command = redact_secrets(record.command, extra_secrets=self.extra_secrets)
        clean_excerpt = redact_secrets(record.output_excerpt, extra_secrets=self.extra_secrets) if record.output_excerpt else None

        with self.db.transaction() as conn:
            conn.execute(
                """
                INSERT INTO verification_records (
                    id, task_id, verification_type, command, exit_code,
                    result, output_excerpt, duration_ms, correlation_id, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    record.id,
                    record.task_id,
                    record.verification_type.value,
                    clean_command,
                    record.exit_code,
                    record.result.value,
                    clean_excerpt,
                    record.duration_ms,
                    record.correlation_id,
                    record.created_at,
                ),
            )
        return record

    def record_verification(
        self,
        task_id: str,
        verification_type: VerificationType,
        command: str,
        result: VerificationStatus,
        exit_code: Optional[int] = None,
        output_excerpt: Optional[str] = None,
        duration_ms: Optional[float] = None,
        correlation_id: Optional[str] = None,
    ) -> VerificationRecord:
        """Factory helper creating and persisting an authoritative verification record."""
        rec = VerificationRecord(
            id=str(uuid.uuid4()),
            task_id=task_id,
            verification_type=verification_type,
            command=command,
            exit_code=exit_code,
            result=result,
            output_excerpt=output_excerpt,
            duration_ms=duration_ms,
            correlation_id=correlation_id,
        )
        return self.create(rec)

    def get(self, record_id: str) -> Optional[VerificationRecord]:
        """Retrieve a verification record by ID."""
        conn = self.db.get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM verification_records WHERE id = ?;", (record_id,))
        row = cursor.fetchone()
        if not row:
            return None
        d = _row_to_dict(row)
        d["verification_type"] = VerificationType(d["verification_type"])
        d["result"] = VerificationStatus(d["result"])
        return VerificationRecord(**d)

    def list_for_task(self, task_id: str) -> List[VerificationRecord]:
        """List all verification records for a specific task in chronological order."""
        conn = self.db.get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM verification_records WHERE task_id = ? ORDER BY created_at ASC;",
            (task_id,),
        )
        results: List[VerificationRecord] = []
        for row in cursor.fetchall():
            d = _row_to_dict(row)
            d["verification_type"] = VerificationType(d["verification_type"])
            d["result"] = VerificationStatus(d["result"])
            results.append(VerificationRecord(**d))
        return results

    def list_by_correlation(self, correlation_id: str) -> List[VerificationRecord]:
        """List verification records matching a correlation ID."""
        conn = self.db.get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM verification_records WHERE correlation_id = ? ORDER BY created_at ASC;",
            (correlation_id,),
        )
        results: List[VerificationRecord] = []
        for row in cursor.fetchall():
            d = _row_to_dict(row)
            d["verification_type"] = VerificationType(d["verification_type"])
            d["result"] = VerificationStatus(d["result"])
            results.append(VerificationRecord(**d))
        return results


class RuntimeEventRepository:
    """Repository managing structured runtime lifecycle events."""

    def __init__(self, db: DatabaseManager, extra_secrets: Optional[List[str]] = None) -> None:
        self.db = db
        self.extra_secrets = extra_secrets or []

    def append_event(self, event: PersistedEventRecord) -> PersistedEventRecord:
        """Persist a structured runtime lifecycle event."""
        clean_payload = redact_secrets(event.payload_json, extra_secrets=self.extra_secrets) if event.payload_json else "{}"

        with self.db.transaction() as conn:
            conn.execute(
                """
                INSERT INTO runtime_events (
                    id, event_type, task_id, session_id, correlation_id,
                    parent_event_id, payload_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    event.id,
                    event.event_type,
                    event.task_id,
                    event.session_id,
                    event.correlation_id,
                    event.parent_event_id,
                    clean_payload,
                    event.created_at,
                ),
            )
        return event

    def create_event(
        self,
        event_type: str,
        task_id: Optional[str] = None,
        session_id: Optional[str] = None,
        correlation_id: Optional[str] = None,
        parent_event_id: Optional[str] = None,
        payload: Optional[Dict[str, Any]] = None,
    ) -> PersistedEventRecord:
        """Factory helper creating and persisting a runtime lifecycle event."""
        payload_str = json.dumps(payload or {})
        rec = PersistedEventRecord(
            id=str(uuid.uuid4()),
            event_type=event_type,
            task_id=task_id,
            session_id=session_id,
            correlation_id=correlation_id,
            parent_event_id=parent_event_id,
            payload_json=payload_str,
        )
        return self.append_event(rec)

    def list_for_task(self, task_id: str) -> List[PersistedEventRecord]:
        """List all runtime events for a specific task in chronological order."""
        conn = self.db.get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM runtime_events WHERE task_id = ? ORDER BY created_at ASC;",
            (task_id,),
        )
        return [PersistedEventRecord(**_row_to_dict(row)) for row in cursor.fetchall()]

    def list_for_session(self, session_id: str) -> List[PersistedEventRecord]:
        """List all runtime events for a specific session in chronological order."""
        conn = self.db.get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM runtime_events WHERE session_id = ? ORDER BY created_at ASC;",
            (session_id,),
        )
        return [PersistedEventRecord(**_row_to_dict(row)) for row in cursor.fetchall()]

    def list_by_correlation(self, correlation_id: str) -> List[PersistedEventRecord]:
        """List all runtime events matching a correlation ID."""
        conn = self.db.get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM runtime_events WHERE correlation_id = ? ORDER BY created_at ASC;",
            (correlation_id,),
        )
        return [PersistedEventRecord(**_row_to_dict(row)) for row in cursor.fetchall()]

