"""Versioned database migrations runner and initial DDL definitions."""

from datetime import datetime, timezone
import logging
import sqlite3
from typing import Any, Dict, List, Optional, Tuple
import uuid

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
    (
        2,
        "002_immutability_triggers_and_continuations",
        """
        -- Add parent_task_id to tasks for linked continuation tasks across recovery
        ALTER TABLE tasks ADD COLUMN parent_task_id TEXT REFERENCES tasks(id) ON DELETE SET NULL;
        
        -- Unique partial index enforcing AT MOST ONE continuation task per parent task at the database engine level
        CREATE UNIQUE INDEX IF NOT EXISTS uq_tasks_parent_task ON tasks(parent_task_id) WHERE parent_task_id IS NOT NULL;

        -- Database-level trigger: Abort ANY column update on terminal tasks (full-row immutability)
        DROP TRIGGER IF EXISTS trg_prevent_terminal_task_status_update;
        CREATE TRIGGER IF NOT EXISTS trg_prevent_terminal_task_update
        BEFORE UPDATE ON tasks
        FOR EACH ROW
        WHEN OLD.status IN ('completed', 'cancelled', 'failed', 'limit_reached')
        BEGIN
            SELECT RAISE(ABORT, 'Illegal database state transition: terminal tasks (completed, cancelled, failed, limit_reached) are completely immutable');
        END;

        -- Database-level trigger: Abort illegal transitions from pending
        CREATE TRIGGER IF NOT EXISTS trg_prevent_illegal_pending_transition
        BEFORE UPDATE OF status ON tasks
        FOR EACH ROW
        WHEN OLD.status = 'pending' AND NEW.status NOT IN ('executing', 'cancelled', 'failed')
        BEGIN
            SELECT RAISE(ABORT, 'Illegal database state transition: pending tasks may only transition to executing, cancelled, or failed');
        END;

        -- Database-level trigger: Abort illegal transitions from executing
        CREATE TRIGGER IF NOT EXISTS trg_prevent_illegal_executing_transition
        BEFORE UPDATE OF status ON tasks
        FOR EACH ROW
        WHEN OLD.status = 'executing' AND NEW.status NOT IN ('completed', 'failed', 'cancelled', 'limit_reached')
        BEGIN
            SELECT RAISE(ABORT, 'Illegal database state transition: executing tasks may only transition to terminal states');
        END;
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
            if version == 2:
                cls._apply_migration_002(conn)
            else:
                cursor.executescript(ddl)
            now_iso = datetime.now(timezone.utc).isoformat()
            cursor.execute(
                "INSERT INTO schema_migrations (version, description, applied_at) VALUES (?, ?, ?);",
                (version, description, now_iso),
            )
            new_count += 1

        return new_count

    @classmethod
    def _apply_migration_002(cls, conn: sqlite3.Connection) -> None:
        """Apply migration 002 with column existence check and non-destructive continuation deduplication.

        Handles:
        1. Adding parent_task_id column if not present (PRAGMA table_info check).
        2. Re-linking any existing duplicate continuations into chronological linear chains
           to satisfy unique constraint uq_tasks_parent_task WITHOUT deleting any historical records.
        3. Installing unique partial index uq_tasks_parent_task.
        4. Installing database-level triggers enforcing full-row terminal immutability and valid state transitions.
        """
        cursor = conn.cursor()

        # 1. Safely add parent_task_id column if not already present
        cursor.execute("PRAGMA table_info(tasks);")
        existing_cols = [row[1] for row in cursor.fetchall()]
        if "parent_task_id" not in existing_cols:
            cursor.execute("ALTER TABLE tasks ADD COLUMN parent_task_id TEXT REFERENCES tasks(id) ON DELETE SET NULL;")

        # 2. Re-link duplicate continuations to satisfy unique partial index without deleting history
        cursor.execute(
            """
            SELECT parent_task_id, COUNT(*) as cnt
            FROM tasks
            WHERE parent_task_id IS NOT NULL
            GROUP BY parent_task_id
            HAVING cnt > 1;
            """
        )
        duplicate_parents = [row[0] for row in cursor.fetchall()]

        for parent_id in duplicate_parents:
            cursor.execute(
                """
                SELECT id, status, created_at
                FROM tasks
                WHERE parent_task_id = ?
                ORDER BY created_at ASC, id ASC;
                """,
                (parent_id,),
            )
            children = cursor.fetchall()
            if len(children) <= 1:
                continue

            # First child remains linked to parent_id (winner/primary continuation)
            # Subsequent duplicate children are chained to form a valid linear lineage
            current_parent_id = children[0][0]
            for dup_row in children[1:]:
                dup_id = dup_row[0]
                dup_status = dup_row[1]

                # Find tail of chain starting from current_parent_id to avoid creating new duplicates
                tail_id = current_parent_id
                while True:
                    cursor.execute("SELECT id FROM tasks WHERE parent_task_id = ? LIMIT 1;", (tail_id,))
                    next_child = cursor.fetchone()
                    if next_child and next_child[0] != dup_id:
                        tail_id = next_child[0]
                    else:
                        break

                # Re-link duplicate to tail_id
                cursor.execute(
                    "UPDATE tasks SET parent_task_id = ? WHERE id = ?;",
                    (tail_id, dup_id),
                )
                now_iso = datetime.now(timezone.utc).isoformat()
                cursor.execute(
                    """
                    INSERT INTO state_transitions (id, entity_type, entity_id, from_state, to_state, reason, actor, created_at)
                    VALUES (?, 'task', ?, ?, ?, ?, 'migration_002', ?);
                    """,
                    (
                        str(uuid.uuid4()),
                        dup_id,
                        dup_status,
                        dup_status,
                        f"Migration 002: Re-parented duplicate continuation of parent {parent_id} to {tail_id} to enforce uniqueness without record deletion",
                        now_iso,
                    ),
                )
                logger.warning(
                    "Migration 002: Re-linked duplicate continuation task %s (parent was %s) to %s to satisfy uniqueness constraint without deleting records",
                    dup_id,
                    parent_id,
                    tail_id,
                )
                current_parent_id = dup_id

        # 3. Create unique partial index
        cursor.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS uq_tasks_parent_task
            ON tasks(parent_task_id)
            WHERE parent_task_id IS NOT NULL;
            """
        )

        # 4. Install database triggers
        cursor.execute("DROP TRIGGER IF EXISTS trg_prevent_terminal_task_status_update;")
        cursor.execute(
            """
            CREATE TRIGGER IF NOT EXISTS trg_prevent_terminal_task_update
            BEFORE UPDATE ON tasks
            FOR EACH ROW
            WHEN OLD.status IN ('completed', 'cancelled', 'failed', 'limit_reached')
            BEGIN
                SELECT RAISE(ABORT, 'Illegal database state transition: terminal tasks (completed, cancelled, failed, limit_reached) are completely immutable');
            END;
            """
        )
        cursor.execute(
            """
            CREATE TRIGGER IF NOT EXISTS trg_prevent_illegal_pending_transition
            BEFORE UPDATE OF status ON tasks
            FOR EACH ROW
            WHEN OLD.status = 'pending' AND NEW.status NOT IN ('executing', 'cancelled', 'failed')
            BEGIN
                SELECT RAISE(ABORT, 'Illegal database state transition: pending tasks may only transition to executing, cancelled, or failed');
            END;
            """
        )
        cursor.execute(
            """
            CREATE TRIGGER IF NOT EXISTS trg_prevent_illegal_executing_transition
            BEFORE UPDATE OF status ON tasks
            FOR EACH ROW
            WHEN OLD.status = 'executing' AND NEW.status NOT IN ('completed', 'failed', 'cancelled', 'limit_reached')
            BEGIN
                SELECT RAISE(ABORT, 'Illegal database state transition: executing tasks may only transition to terminal states');
            END;
            """
        )
