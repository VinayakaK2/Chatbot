"""Task coordination service separating API routes from execution orchestration."""

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from api.schemas.tasks import TaskCreateRequest, TaskResponse, TaskStatus
from core.config import Settings, get_settings
from model.contracts import ModelError, ModelProvider
from model.vllm_adapter import VLLMAdapter

logger = logging.getLogger("agent_runtime.core.task_service")


class TaskService:
    """Coordinates task validation, submission, and model execution orchestration."""

    def __init__(
        self,
        settings: Optional[Settings] = None,
        model_provider: Optional[ModelProvider] = None,
    ) -> None:
        """Initialize the task service with optional model adapter provider."""
        self.settings = settings or get_settings()

        if model_provider is not None:
            self.model_provider: Optional[ModelProvider] = model_provider
        elif self.settings.is_model_configured and self.settings.agent_model_base_url and self.settings.agent_model_name:
            self.model_provider = VLLMAdapter(
                base_url=self.settings.agent_model_base_url,
                model_name=self.settings.agent_model_name,
                api_key=self.settings.agent_model_api_key,
                timeout_seconds=self.settings.agent_model_timeout_seconds,
                max_retries=self.settings.agent_model_max_retries,
            )
        else:
            self.model_provider = None

        self._tasks: Dict[str, TaskResponse] = {}

    async def submit_task(self, request: TaskCreateRequest) -> TaskResponse:
        """Submit a prompt and process execution via the model provider."""
        task_id = str(uuid.uuid4())
        created_at = datetime.now(timezone.utc).isoformat()

        # Handle unconfigured model provider state safely
        if self.model_provider is None:
            task = TaskResponse(
                task_id=task_id,
                prompt=request.prompt,
                status=TaskStatus.FAILED,
                error="Model endpoint is unconfigured. Set AGENT_MODEL_BASE_URL to connect to vLLM.",
                created_at=created_at,
                completed_at=created_at,
            )
            self._tasks[task_id] = task
            return task

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
        except ModelError as err:
            logger.warning("Model provider error for task %s: %s", task_id, err.message)
            failed_task = TaskResponse(
                task_id=task_id,
                prompt=request.prompt,
                status=TaskStatus.FAILED,
                error=err.message,
                created_at=created_at,
                completed_at=datetime.now(timezone.utc).isoformat(),
            )
            self._tasks[task_id] = failed_task
            return failed_task
        except Exception as exc:
            logger.error("Unexpected execution error for task %s: %s", task_id, type(exc).__name__)
            failed_task = TaskResponse(
                task_id=task_id,
                prompt=request.prompt,
                status=TaskStatus.FAILED,
                error="An unexpected internal error occurred during model generation.",
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
