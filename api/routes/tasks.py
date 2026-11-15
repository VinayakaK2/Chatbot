"""Task management endpoints for submission, status retrieval, streaming, and approvals."""

import asyncio
import json
import logging
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from api.schemas.events import RuntimeEvent
from api.schemas.tasks import TaskCreateRequest, TaskResponse
from core.task_service import TaskService, get_task_service

router = APIRouter(prefix="/api/tasks", tags=["Agent Tasks"])
logger = logging.getLogger("agent_runtime.api.tasks")


class ToolDecisionRequest(BaseModel):
    """Payload for human decision authority on a requested tool call."""

    call_id: str = Field(..., description="Unique tool call identifier to approve or reject")
    reason: Optional[str] = Field(default=None, description="Optional explanation or directive")


@router.post(
    "",
    response_model=TaskResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Submit a task or prompt for processing",
)
async def submit_task(
    request: TaskCreateRequest,
    task_service: TaskService = Depends(get_task_service),
) -> TaskResponse:
    """Validate and submit a task prompt to the execution service."""
    try:
        response = await task_service.submit_task(request)
        return response
    except Exception as exc:
        logger.error("Error processing task submission: %s", type(exc).__name__)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal error processing task request.",
        ) from exc


@router.get(
    "/{task_id}",
    response_model=TaskResponse,
    status_code=status.HTTP_200_OK,
    summary="Retrieve task status and result by ID",
)
def get_task_by_id(
    task_id: str,
    task_service: TaskService = Depends(get_task_service),
) -> TaskResponse:
    """Retrieve stored task record by identifier."""
    task = task_service.get_task(task_id)
    if not task:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Task with ID '{task_id}' not found.",
        )
    return task


@router.post(
    "/{task_id}/cancel",
    response_model=TaskResponse,
    status_code=status.HTTP_200_OK,
    summary="Cancel an active task",
)
def cancel_task_by_id(
    task_id: str,
    task_service: TaskService = Depends(get_task_service),
) -> TaskResponse:
    """Request immediate cancellation of a running or queued task."""
    cancelled = task_service.cancel_task(task_id)
    if not cancelled:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Task with ID '{task_id}' not found.",
        )
    return cancelled


@router.post(
    "/{task_id}/resume",
    response_model=TaskResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Resume an interrupted task by creating a linked continuation task",
)
async def resume_interrupted_task(
    task_id: str,
    task_service: TaskService = Depends(get_task_service),
) -> TaskResponse:
    """Safely resume an interrupted task with INTERRUPTED_SAFE recovery status.

    Preserves the original interrupted task as immutable terminal FAILED,
    and returns a newly created linked continuation task in PENDING status.
    """
    task = task_service.get_task(task_id)
    if not task:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Task with ID '{task_id}' not found.",
        )
    try:
        continuation = await task_service.resume_task(task_id)
        return continuation
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    except Exception as exc:
        logger.error("Error resuming task %s: %s", task_id, type(exc).__name__)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to resume task: {str(exc)}",
        ) from exc


@router.get(
    "/{task_id}/events/history",
    response_model=List[RuntimeEvent],
    status_code=status.HTTP_200_OK,
    summary="Retrieve recorded runtime events for a task",
)
def get_task_event_history(
    task_id: str,
    task_service: TaskService = Depends(get_task_service),
) -> List[RuntimeEvent]:
    """Retrieve all structured lifecycle events emitted during task execution."""
    task = task_service.get_task(task_id)
    if not task:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Task with ID '{task_id}' not found.",
        )
    return task_service.get_events(task_id)


@router.get(
    "/{task_id}/events",
    summary="Stream live runtime events via Server-Sent Events (SSE)",
)
async def stream_task_events(
    task_id: str,
    request: Request,
    task_service: TaskService = Depends(get_task_service),
) -> StreamingResponse:
    """Stream live execution events for a task over SSE."""
    task = task_service.get_task(task_id)
    if not task:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Task with ID '{task_id}' not found.",
        )

    async def event_generator():
        # First replay existing events
        existing_events = task_service.get_events(task_id)
        for ev in existing_events:
            ev_data = json.dumps(ev.model_dump())
            yield f"id: {ev.event_id}\nevent: message\ndata: {ev_data}\n\n"

        # Check if already in terminal state
        current = task_service.get_task(task_id)
        if current and current.status in ("completed", "failed", "cancelled", "limit_reached"):
            yield f"event: done\ndata: {json.dumps({'status': current.status})}\n\n"
            return

        # Listen for real-time events via an async queue
        queue: asyncio.Queue[Optional[RuntimeEvent]] = asyncio.Queue()
        task_service.subscribe_events(task_id, queue, loop=asyncio.get_running_loop())

        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=1.0)
                    if event is None:
                        break
                    ev_data = json.dumps(event.model_dump())
                    yield f"id: {event.event_id}\nevent: message\ndata: {ev_data}\n\n"
                    if event.event_type == "task_terminal":
                        yield f"event: done\ndata: {json.dumps({'status': 'terminal'})}\n\n"
                        break
                except asyncio.TimeoutError:
                    # Send periodic keep-alive comment
                    yield ": ping\n\n"
        finally:
            task_service.unsubscribe_events(task_id, queue)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post(
    "/{task_id}/approve",
    status_code=status.HTTP_200_OK,
    summary="Approve a pending tool operation",
)
def approve_tool_operation(
    task_id: str,
    payload: ToolDecisionRequest,
    task_service: TaskService = Depends(get_task_service),
) -> Dict[str, Any]:
    """Record human approval for a pending tool execution."""
    task = task_service.get_task(task_id)
    if not task:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Task with ID '{task_id}' not found.",
        )
    task_service.record_approval_decision(task_id, payload.call_id, approved=True, reason=payload.reason)
    return {"status": "approved", "task_id": task_id, "call_id": payload.call_id}


@router.post(
    "/{task_id}/reject",
    status_code=status.HTTP_200_OK,
    summary="Reject a pending tool operation",
)
def reject_tool_operation(
    task_id: str,
    payload: ToolDecisionRequest,
    task_service: TaskService = Depends(get_task_service),
) -> Dict[str, Any]:
    """Record human rejection for a pending tool execution."""
    task = task_service.get_task(task_id)
    if not task:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Task with ID '{task_id}' not found.",
        )
    task_service.record_approval_decision(task_id, payload.call_id, approved=False, reason=payload.reason)
    return {"status": "rejected", "task_id": task_id, "call_id": payload.call_id}
