"""Verification and diagnostics API routes."""

import logging
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel, Field

from core.diagnostics import run_diagnostics
from core.task_service import TaskService, get_task_service
from storage.models import VerificationRecord, VerificationType

router = APIRouter(prefix="/api", tags=["Verification & Diagnostics"])
logger = logging.getLogger("agent_runtime.api.verification")


class VerificationTriggerRequest(BaseModel):
    """Payload to trigger an independent verification command execution."""

    command: Optional[str] = Field(
        default=None,
        min_length=1,
        max_length=2048,
        description="Explicit verification command line string (requires operator origin)",
    )
    profile_name: Optional[str] = Field(
        default=None,
        description="Pre-approved trusted verification profile (e.g. 'test', 'build', 'lint', 'typecheck')",
    )
    origin: Optional[str] = Field(
        default="operator",
        description="Provenance of verification check ('operator', 'configured_profile', 'system')",
    )
    authorized_by: Optional[str] = Field(
        default=None,
        description="Operator or service identifier authorizing the verification execution",
    )
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
    x_operator_authorization: Optional[str] = Header(None, alias="X-Operator-Authorization"),
    x_operator_identity: Optional[str] = Header(None, alias="X-Operator-Identity"),
    task_service: TaskService = Depends(get_task_service),
) -> VerificationRecord:
    """Execute an independent verification command (e.g., test or build) in a controlled subprocess.

    Enforces trust boundaries:
    - Pre-approved profiles resolve from configuration and receive CONFIGURED_PROFILE origin.
    - Custom commands require explicit operator authorization via 'X-Operator-Authorization' header.
    - Untrusted callers cannot manufacture operator provenance.
    - Rejects trivial fake pass commands (e.g. echo, true, exit 0).
    - Captures exit code, duration, and stream-bounded output excerpt.
    """
    task = task_service.get_task(task_id)
    if not task:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Task with ID '{task_id}' not found.",
        )

    if not payload.command and not payload.profile_name:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Either 'command' or 'profile_name' must be specified for verification.",
        )

    from core.verification import VerificationOrigin

    # Authorization and Provenance Policy:
    # 1. Configured profiles are authenticated against trusted configuration
    if payload.profile_name:
        effective_origin = VerificationOrigin.CONFIGURED_PROFILE
        effective_authorized_by = "system_policy"
        effective_command = None
        effective_profile = payload.profile_name
    else:
        # 2. Custom commands strictly require explicit operator authorization header
        is_operator_authorized = (
            x_operator_authorization is not None
            and x_operator_authorization.strip().lower() in ("true", "1", "yes")
        )
        if not is_operator_authorized:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    "Custom verification commands require explicit operator authorization via "
                    "'X-Operator-Authorization' header. Untrusted callers must use a pre-approved profile_name."
                ),
            )
        effective_origin = VerificationOrigin.OPERATOR
        effective_authorized_by = (
            x_operator_identity.strip()
            if x_operator_identity and x_operator_identity.strip()
            else (payload.authorized_by or "operator")
        )
        effective_command = payload.command
        effective_profile = None

    try:
        record = await task_service.verification_runner.run_verification(
            task_id=task_id,
            command=effective_command,
            profile_name=effective_profile,
            origin=effective_origin,
            authorized_by=effective_authorized_by,
            verification_type=payload.verification_type,
            cwd=str(task_service.workspace_context.root_path),
            timeout_seconds=payload.timeout_seconds,
            correlation_id=task.correlation_id,
        )
        return record
    except ValueError as val_err:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Verification validation rejected: {str(val_err)}",
        ) from val_err
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
