"""Task coordination service separating API routes from execution orchestration."""

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from api.schemas.tasks import TaskCreateRequest, TaskResponse, TaskStatus
from core.config import Settings, get_settings

logger = logging.getLogger("agent_runtime.core.task_service")


class TaskService:
    """Coordinates task validation, submission, and result retrieval."""

    def __init__(self, settings: Optional[Settings] = None, model_provider: Optional[Any] = None) -> None:
        """Initialize the task service with optional model adapter provider."""
        self.settings = settings or get_settings()
        self.model_provider = model_provider
        self._tasks: Dict[str, TaskResponse] = {}

    async def submit_task(self, request: TaskCreateRequest) -> TaskResponse:
        """Submit and process a user task prompt."""
        task_id = str(uuid.uuid4())
        created_at = datetime.now(timezone.utc).isoformat()

        # In Phase 0 or if no model provider is attached, record task as pending placeholder
        if self.model_provider is None:
            task = TaskResponse(
                task_id=task_id,
                prompt=request.prompt,
                status=TaskStatus.PENDING,
                response_text="[Phase 0 Foundation] Task contract accepted. Model integration is active in Phase 1.",
                finish_reason="phase_0_placeholder",
                model="none",
                created_at=created_at,
                completed_at=created_at,
            )
            self._tasks[task_id] = task
            return task

        # In Phase 1: execute through model provider adapter
        return await self._execute_with_model(task_id, request, created_at)

    async def _execute_with_model(
        self,
        task_id: str,
        request: TaskCreateRequest,
        created_at: str,
    ) -> TaskResponse:
        """Execute task prompt through the configured model provider adapter."""
        # Initial executing record
        executing_record = TaskResponse(
            task_id=task_id,
            prompt=request.prompt,
            status=TaskStatus.EXECUTING,
            created_at=created_at,
        )
        self._tasks[task_id] = executing_record

        try:
            result = await self.model_provider.generate(
                prompt=request.prompt,
                max_tokens=request.max_tokens,
                temperature=request.temperature,
            )
            completed_at = datetime.now(timezone.utc).isoformat()
            completed_task = TaskResponse(
                task_id=task_id,
                prompt=request.prompt,
                status=TaskStatus.COMPLETED,
                response_text=result.text,
                finish_reason=result.finish_reason,
                model=result.model,
                usage=result.usage,
                created_at=created_at,
                completed_at=completed_at,
            )
            self._tasks[task_id] = completed_task
            return completed_task
        except Exception as exc:
            logger.error("Model generation failed for task %s: %s", task_id, type(exc).__name__)
            failed_task = TaskResponse(
                task_id=task_id,
                prompt=request.prompt,
                status=TaskStatus.FAILED,
                error=str(exc),
                created_at=created_at,
                completed_at=datetime.now(timezone.utc).isoformat(),
            )
            self._tasks[task_id] = failed_task
            return failed_task

    def get_task(self, task_id: str) -> Optional[TaskResponse]:
        """Retrieve stored task record by identifier."""
        return self._tasks.get(task_id)


# Global task service instance
_task_service_instance: Optional[TaskService] = None


def get_task_service() -> TaskService:
    """Retrieve or initialize the singleton task service."""
    global _task_service_instance
    if _task_service_instance is None:
        _task_service_instance = TaskService()
    return _task_service_instance


def set_task_service(service: TaskService) -> None:
    """Override singleton task service for testing or provider injection."""
    global _task_service_instance
    _task_service_instance = service
