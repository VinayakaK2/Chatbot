"""Task management endpoints for submission and status retrieval."""

import logging
from fastapi import APIRouter, Depends, HTTPException, status

from api.schemas.tasks import TaskCreateRequest, TaskResponse
from core.task_service import TaskService, get_task_service

router = APIRouter(prefix="/api/tasks", tags=["Agent Tasks"])
logger = logging.getLogger("agent_runtime.api.tasks")


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
