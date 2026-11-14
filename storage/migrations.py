"""Versioned database migrations runner and initial DDL definitions."""

from datetime import datetime, timezone
import logging
import sqlite3
from typing import Dict, List, Tuple

logger = logging.getLogger("agent_runtime.storage.migrations")

# Migration definitions: (version, description, SQL DDL script)
MIGRATIONS: List[Tuple[int, str, str]] = [
    (
        1,
        "001_initial_schema",
        """
        -- Sessions table
        CREATE TABLE IF NOT EXISTS sessions (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            workspace_path TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('active', 'completed', 'archived')),
            metadata_json TEXT DEFAULT '{}',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        -- Tasks table
        CREATE TABLE IF NOT EXISTS tasks (
            id TEXT PRIMARY KEY,
            session_id TEXT REFERENCES sessions(id) ON DELETE SET NULL,
            prompt TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('pending', 'executing', 'completed', 'failed', 'cancelled', 'limit_reached')),
            recovery_status TEXT DEFAULT NULL CHECK(recovery_status IN (NULL, 'clean', 'interrupted_safe', 'needs_review', 'repaired')),
            response_text TEXT DEFAULT NULL,
            finish_reason TEXT DEFAULT NULL,
            model TEXT DEFAULT NULL,
            usage_json TEXT DEFAULT NULL,
            error TEXT DEFAULT NULL,
            iterations INTEGER DEFAULT 0,
            tool_calls_count INTEGER DEFAULT 0,
            termination_reason TEXT DEFAULT NULL,
            created_at TEXT NOT NULL,
            started_at TEXT DEFAULT NULL,
            completed_at TEXT DEFAULT NULL,
            version INTEGER DEFAULT 1
        );

        -- State transitions audit log
        CREATE TABLE IF NOT EXISTS state_transitions (
            id TEXT PRIMARY KEY,
            entity_type TEXT NOT NULL,
            entity_id TEXT NOT NULL,
            from_state TEXT NOT NULL,
            to_state TEXT NOT NULL,
            reason TEXT DEFAULT NULL,
            actor TEXT NOT NULL DEFAULT 'runtime',
            created_at TEXT NOT NULL
        );

        -- Transcript messages
        CREATE TABLE IF NOT EXISTS transcript_messages (
            id TEXT PRIMARY KEY,
            session_id TEXT REFERENCES sessions(id) ON DELETE CASCADE,
            task_id TEXT REFERENCES tasks(id) ON DELETE CASCADE,
            sequence_num INTEGER NOT NULL,
            role TEXT NOT NULL CHECK(role IN ('system', 'user', 'assistant', 'tool')),
            content TEXT DEFAULT NULL,
            name TEXT DEFAULT NULL,
            tool_call_id TEXT DEFAULT NULL,
            tool_calls_json TEXT DEFAULT NULL,
            is_derived INTEGER DEFAULT 0,
            created_at TEXT NOT NULL
        );

        -- Tool executions
        CREATE TABLE IF NOT EXISTS tool_executions (
            id TEXT PRIMARY KEY,
            task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
            call_id TEXT NOT NULL,
            tool_name TEXT NOT NULL,
            arguments_json TEXT NOT NULL,
            execution_state TEXT NOT NULL CHECK(execution_state IN ('requested', 'started', 'completed', 'failed', 'interrupted')),
            is_mutation INTEGER NOT NULL DEFAULT 0,
            idempotent INTEGER NOT NULL DEFAULT 0,
            exit_code INTEGER DEFAULT NULL,
            result_output TEXT DEFAULT NULL,
            error TEXT DEFAULT NULL,
            error_category TEXT DEFAULT NULL,
            duration_ms REAL DEFAULT NULL,
            created_at TEXT NOT NULL,
            started_at TEXT DEFAULT NULL,
            completed_at TEXT DEFAULT NULL
        );

        -- Model calls
        CREATE TABLE IF NOT EXISTS model_calls (
            id TEXT PRIMARY KEY,
            task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
            iteration INTEGER NOT NULL,
            model TEXT NOT NULL,
            prompt_tokens INTEGER DEFAULT 0,
            completion_tokens INTEGER DEFAULT 0,
            total_tokens INTEGER DEFAULT 0,
            finish_reason TEXT DEFAULT NULL,
            latency_ms REAL DEFAULT NULL,
            created_at TEXT NOT NULL
        );

        -- Approvals
        CREATE TABLE IF NOT EXISTS approvals (
            id TEXT PRIMARY KEY,
            task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
            call_id TEXT NOT NULL,
            tool_name TEXT NOT NULL,
            risk_category TEXT NOT NULL,
            action_hash TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('pending', 'approved', 'denied', 'expired', 'cancelled', 'consumed')),
            reason TEXT NOT NULL,
            decision_reason TEXT DEFAULT NULL,
            requested_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            decided_at TEXT DEFAULT NULL
        );

        -- Indexes for query performance and referential lookups
        CREATE INDEX IF NOT EXISTS idx_tasks_session_id ON tasks(session_id);
        CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
        CREATE INDEX IF NOT EXISTS idx_state_transitions_entity ON state_transitions(entity_type, entity_id);
        CREATE INDEX IF NOT EXISTS idx_transcript_session_seq ON transcript_messages(session_id, sequence_num);
        CREATE INDEX IF NOT EXISTS idx_transcript_task ON transcript_messages(task_id);
        CREATE INDEX IF NOT EXISTS idx_tool_executions_task ON tool_executions(task_id);
        CREATE INDEX IF NOT EXISTS idx_tool_executions_call ON tool_executions(call_id);
        CREATE INDEX IF NOT EXISTS idx_tool_executions_state ON tool_executions(execution_state);
        CREATE INDEX IF NOT EXISTS idx_model_calls_task ON model_calls(task_id);
        CREATE INDEX IF NOT EXISTS idx_approvals_task ON approvals(task_id);
        CREATE INDEX IF NOT EXISTS idx_approvals_status ON approvals(status);
        """,
    ),
]


class MigrationRunner:
    """Discovers, tracks, and applies incremental SQL schema migrations."""

    def __init__(self, db: Optional[Any] = None) -> None:
        self.db = db

    def run_migrations(self) -> List[int]:
        """Run pending migrations against configured connection or manager.

        Returns:
            List[int]: Versions of newly applied migrations.
        """
        if self.db is None:
            return []
        conn = self.db.get_connection() if hasattr(self.db, "get_connection") else self.db
        applied_before = set(self.get_applied_versions(conn).keys())
        self.apply_all(conn)
        applied_after = set(self.get_applied_versions(conn).keys())
        return sorted(list(applied_after - applied_before))

    @staticmethod
    def initialize_migration_table(conn: sqlite3.Connection) -> None:
        """Ensure schema_migrations tracking table exists."""
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version INTEGER PRIMARY KEY,
                name TEXT NOT NULL DEFAULT '',
                description TEXT NOT NULL DEFAULT '',
                applied_at TEXT NOT NULL
            );
            """
        )

    @classmethod
    def get_applied_versions(cls, conn: sqlite3.Connection) -> Dict[int, str]:
        """Query all previously applied migration versions."""
        cls.initialize_migration_table(conn)
        cursor = conn.cursor()
        cursor.execute("SELECT version, applied_at FROM schema_migrations ORDER BY version ASC;")
        return {row[0]: row[1] for row in cursor.fetchall()}

    @classmethod
    def apply_all(cls, conn: sqlite3.Connection) -> int:
        """Apply all pending migrations in ascending order.

        Returns:
            int: Number of new migrations applied.
        """
        cls.initialize_migration_table(conn)
        applied = cls.get_applied_versions(conn)
        new_count = 0

        for version, description, ddl in sorted(MIGRATIONS, key=lambda m: m[0]):
            if version in applied:
                continue

            logger.info("Applying database migration %d: %s", version, description)
            cursor = conn.cursor()
            cursor.executescript(ddl)
            now_iso = datetime.now(timezone.utc).isoformat()
            cursor.execute(
                "INSERT INTO schema_migrations (version, name, description, applied_at) VALUES (?, ?, ?, ?);",
                (version, description, description, now_iso),
            )
            new_count += 1

        return new_count
