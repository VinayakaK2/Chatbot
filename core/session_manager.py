"""Session lifecycle coordinator managing sessions, task associations, and context reconstruction."""

from datetime import datetime, timezone
import json
import logging
from typing import Any, Dict, List, Optional
import uuid

from storage.database import DatabaseManager
from storage.models import SessionRecord, SessionStatus
from storage.repositories import (
    SessionRepository,
    TaskRepository,
    TranscriptRepository,
)

logger = logging.getLogger("agent_runtime.core.session_manager")


class SessionManager:
    """Manages high-level session creation, inspection, and context reconstruction."""

    def __init__(self, db: DatabaseManager) -> None:
        self.db = db
        self.session_repo = SessionRepository(db)
        self.task_repo = TaskRepository(db)
        self.transcript_repo = TranscriptRepository(db)

    def create_session(
        self,
        title: str,
        workspace_path: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> SessionRecord:
        """Create and persist a new user/agent session."""
        session_id = str(uuid.uuid4())
        now_iso = datetime.now(timezone.utc).isoformat()
        metadata_str = json.dumps(metadata or {})

        record = SessionRecord(
            id=session_id,
            title=title,
            workspace_path=workspace_path,
            status=SessionStatus.ACTIVE,
            metadata_json=metadata_str,
            created_at=now_iso,
            updated_at=now_iso,
        )
        return self.session_repo.create(record)

    def get_session(self, session_id: str) -> Optional[SessionRecord]:
        """Retrieve session record by identifier."""
        return self.session_repo.get(session_id)

    def list_sessions(self, limit: int = 50, offset: int = 0) -> List[SessionRecord]:
        """List sessions ordered by update time descending."""
        return self.session_repo.list_all(limit=limit, offset=offset)

    def update_status(self, session_id: str, status: SessionStatus) -> Optional[SessionRecord]:
        """Update session status."""
        return self.session_repo.update_status(session_id, status)

    def get_or_create_default_session(self, workspace_path: str) -> SessionRecord:
        """Ensure an active default session exists for the workspace."""
        sessions = self.list_sessions(limit=1)
        if sessions and sessions[0].status == SessionStatus.ACTIVE:
            return sessions[0]
        return self.create_session(
            title="Default Workspace Session",
            workspace_path=workspace_path,
            metadata={"is_default": True},
        )

    def load_session_context(self, session_id: str) -> Optional[Dict[str, Any]]:
        """Reconstruct full historical context for a session from durable SQLite store."""
        session = self.get_session(session_id)
        if not session:
            return None

        tasks = self.task_repo.list_for_session(session_id)
        transcript = self.transcript_repo.list_for_session(session_id)

        return {
            "session": session.model_dump(),
            "tasks": [t.model_dump() for t in tasks],
            "transcript": [m.model_dump() for m in transcript],
        }
