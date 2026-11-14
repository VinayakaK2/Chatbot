"""SQLite database connection management, WAL-mode configuration, and transactional boundaries."""

from contextlib import contextmanager
import logging
import os
import sqlite3
import threading
from typing import Generator, Optional

logger = logging.getLogger("agent_runtime.storage.database")


class DatabaseManager:
    """Manages thread-safe SQLite connections with WAL mode and strict PRAGMA configurations."""

    def __init__(
        self,
        db_path: Optional[str] = None,
        database_url: Optional[str] = None,
        busy_timeout_ms: int = 5000,
        enable_wal: bool = True,
    ) -> None:
        """Initialize database manager for disk or in-memory SQLite store."""
        raw_path = database_url or db_path or "chatbot.db"
        if raw_path.startswith("sqlite:///"):
            raw_path = raw_path[len("sqlite:///") :]
        self.db_path = raw_path
        self.busy_timeout_ms = busy_timeout_ms
        self.enable_wal = enable_wal
        self._local = threading.local()
        self._shared_memory_conn: Optional[sqlite3.Connection] = None
        self._lock = threading.RLock()

        # If disk path, ensure parent directory exists
        if not self._is_memory_db(self.db_path):
            abs_path = os.path.abspath(self.db_path)
            parent_dir = os.path.dirname(abs_path)
            if parent_dir and not os.path.exists(parent_dir):
                os.makedirs(parent_dir, exist_ok=True)

        # Initialize base connection to run pragma setup and verify reachability
        with self.get_connection() as conn:
            self._apply_pragmas(conn)

    @contextmanager
    def connection(self) -> Generator[sqlite3.Connection, None, None]:
        """Context manager yielding the active connection for queries."""
        yield self.get_connection()

    @staticmethod
    def _is_memory_db(path: str) -> bool:
        """Check if database path targets in-memory storage."""
        return path == ":memory:" or "mode=memory" in path

    def _apply_pragmas(self, conn: sqlite3.Connection) -> None:
        """Apply required performance, integrity, and concurrency PRAGMAs."""
        cursor = conn.cursor()
        cursor.execute("PRAGMA foreign_keys = ON;")
        cursor.execute(f"PRAGMA busy_timeout = {self.busy_timeout_ms};")

        # WAL mode is applicable to disk files
        if self.enable_wal and not self._is_memory_db(self.db_path):
            cursor.execute("PRAGMA journal_mode = WAL;")
            cursor.execute("PRAGMA synchronous = NORMAL;")
        else:
            cursor.execute("PRAGMA synchronous = NORMAL;")

    def _create_connection(self) -> sqlite3.Connection:
        """Create and configure a fresh raw SQLite connection."""
        is_uri = "file:" in self.db_path or "?mode=" in self.db_path
        conn = sqlite3.connect(
            self.db_path,
            timeout=self.busy_timeout_ms / 1000.0,
            check_same_thread=False,
            uri=is_uri,
        )
        conn.row_factory = sqlite3.Row
        self._apply_pragmas(conn)
        return conn

    def get_connection(self) -> sqlite3.Connection:
        """Retrieve a thread-safe connection instance."""
        # For :memory: databases without URI cache=shared, keep a single master connection
        if self._is_memory_db(self.db_path):
            with self._lock:
                if self._shared_memory_conn is None:
                    self._shared_memory_conn = self._create_connection()
                return self._shared_memory_conn

        # For file-backed databases, use thread-local connection pool
        conn = getattr(self._local, "connection", None)
        if conn is None:
            conn = self._create_connection()
            self._local.connection = conn
        return conn

    @contextmanager
    def transaction(self) -> Generator[sqlite3.Connection, None, None]:
        """Context manager providing an atomic SQLite transaction with commit and rollback.

        Yields:
            sqlite3.Connection: Active connection within transaction.
        """
        conn = self.get_connection()
        # Acquire lock to ensure serialized transaction execution if shared connection
        with self._lock:
            try:
                yield conn
                conn.commit()
            except Exception as exc:
                conn.rollback()
                logger.debug("Rolled back transaction due to error: %s", type(exc).__name__)
                raise

    def close(self) -> None:
        """Close all connections held by this manager."""
        with self._lock:
            if self._shared_memory_conn is not None:
                try:
                    self._shared_memory_conn.close()
                except Exception:
                    pass
                self._shared_memory_conn = None

            conn = getattr(self._local, "connection", None)
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass
                self._local.connection = None
