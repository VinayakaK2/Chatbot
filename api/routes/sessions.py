"""Session management endpoints for creating, listing, inspecting, and reconstructing sessions."""

import logging
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from core.task_service import TaskService, get_task_service
from storage.models import SessionRecord

router = APIRouter(prefix="/api/sessions", tags=["Agent Sessions"])
logger = logging.getLogger("agent_runtime.api.sessions")


class SessionCreateRequest(BaseModel):
    """Payload to create a new agent session."""

    title: str = Field(..., min_length=1, max_length=256, description="Human-readable title or prompt excerpt")
    workspace_path: Optional[str] = Field(default=None, description="Optional target workspace directory")
    metadata: Optional[Dict[str, Any]] = Field(default=None, description="Optional metadata dictionary")


@router.post(
    "",
    response_model=SessionRecord,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new agent session",
)
def create_session(
    request: SessionCreateRequest,
    task_service: TaskService = Depends(get_task_service),
) -> SessionRecord:
    """Create and persist a new user/agent session."""
    try:
        session = task_service.create_session(
            title=request.title,
            workspace_path=request.workspace_path,
            metadata=request.metadata,
        )
        return session
    except Exception as exc:
        logger.error("Error creating session: %s", type(exc).__name__, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create session.",
        ) from exc


@router.get(
    "",
    response_model=List[SessionRecord],
    status_code=status.HTTP_200_OK,
    summary="List all agent sessions",
)
def list_sessions(
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    task_service: TaskService = Depends(get_task_service),
) -> List[SessionRecord]:
    """Retrieve sessions ordered by last update descending."""
    return task_service.list_sessions(limit=limit, offset=offset)


@router.get(
    "/{session_id}",
    response_model=SessionRecord,
    status_code=status.HTTP_200_OK,
    summary="Retrieve session details by ID",
)
def get_session(
    session_id: str,
    task_service: TaskService = Depends(get_task_service),
) -> SessionRecord:
    """Get session metadata and status by ID."""
    session = task_service.get_session(session_id)
    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session with ID '{session_id}' not found.",
        )
    return session


@router.get(
    "/{session_id}/context",
    status_code=status.HTTP_200_OK,
    summary="Reconstruct full session historical context",
)
def load_session_context(
    session_id: str,
    task_service: TaskService = Depends(get_task_service),
) -> Dict[str, Any]:
    """Reconstruct session, tasks, and ordered transcript messages from durable SQLite store."""
    context = task_service.load_session_context(session_id)
    if not context:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session with ID '{session_id}' not found.",
        )
    return context
