"""Verification and diagnostics API routes."""

import logging
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from core.diagnostics import run_diagnostics
from core.task_service import TaskService, get_task_service
from storage.models import VerificationRecord, VerificationType

router = APIRouter(prefix="/api", tags=["Verification & Diagnostics"])
logger = logging.getLogger("agent_runtime.api.verification")


class VerificationTriggerRequest(BaseModel):
    """Payload to trigger an independent verification command execution."""

    command: str = Field(..., min_length=1, max_length=2048, description="Verification command line string")
    verification_type: VerificationType = Field(
        default=VerificationType.TEST,
        description="Category of check ('test', 'build', 'lint', 'typecheck', 'custom')",
    )
    timeout_seconds: Optional[float] = Field(
        default=60.0,
        ge=1.0,
        le=600.0,
        description="Execution timeout in seconds",
    )


@router.post(
    "/tasks/{task_id}/verify",
    response_model=VerificationRecord,
    status_code=status.HTTP_201_CREATED,
    summary="Execute an independent verification check and record authoritative evidence",
)
async def trigger_task_verification(
    task_id: str,
    payload: VerificationTriggerRequest,
    task_service: TaskService = Depends(get_task_service),
) -> VerificationRecord:
    """Execute an independent verification command (e.g., test or build) in a controlled subprocess.

    Captures exit code, duration, and bounded stdout/stderr, and persists a verification record.
    """
    task = task_service.get_task(task_id)
    if not task:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Task with ID '{task_id}' not found.",
        )

    try:
        record = await task_service.verification_runner.run_verification(
            task_id=task_id,
            command=payload.command,
            verification_type=payload.verification_type,
            cwd=str(task_service.workspace_context.root_path),
            timeout_seconds=payload.timeout_seconds,
            correlation_id=task.correlation_id,
        )
        return record
    except Exception as exc:
        logger.error("Error executing verification for task %s: %s", task_id, str(exc))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to execute verification: {str(exc)}",
        ) from exc


@router.get(
    "/tasks/{task_id}/verifications",
    response_model=List[VerificationRecord],
    status_code=status.HTTP_200_OK,
    summary="List all recorded verification checks for a task",
)
def list_task_verifications(
    task_id: str,
    task_service: TaskService = Depends(get_task_service),
) -> List[VerificationRecord]:
    """Retrieve all verification records associated with a task in chronological order."""
    task = task_service.get_task(task_id)
    if not task:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Task with ID '{task_id}' not found.",
        )
    return task_service.verification_repo.list_for_task(task_id)


@router.get(
    "/diagnostics",
    status_code=status.HTTP_200_OK,
    summary="Runtime configuration presence, endpoint reachability, and SQLite database health",
)
async def get_runtime_diagnostics(
    task_service: TaskService = Depends(get_task_service),
) -> Dict[str, Any]:
    """Execute runtime diagnostics checking model connectivity, database integrity, and settings without printing secrets."""
    return await run_diagnostics(
        settings=task_service.settings,
        db_manager=task_service.database_manager,
    )
