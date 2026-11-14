"""Task coordination service separating API routes from execution orchestration."""

import asyncio
from datetime import datetime, timezone
import logging
from typing import Any, Dict, List, Optional
import uuid

from api.schemas.events import RuntimeEvent, RuntimeEventType
from api.schemas.tasks import TaskCreateRequest, TaskResponse, TaskStatus
from core.agent_loop import AgentExecutionRequest, AgentLoop
from core.config import Settings, get_settings
from core.tool_dispatcher import ToolDispatcher
from core.tool_registry import ToolRegistry, get_global_registry
from model.contracts import ModelError, ModelProvider
from model.vllm_adapter import VLLMAdapter

logger = logging.getLogger("agent_runtime.core.task_service")


class TaskService:
    """Coordinates task validation, submission, agent loop execution, and runtime events."""

    def __init__(
        self,
        settings: Optional[Settings] = None,
        model_provider: Optional[ModelProvider] = None,
        tool_registry: Optional[ToolRegistry] = None,
        tool_dispatcher: Optional[ToolDispatcher] = None,
        agent_loop: Optional[AgentLoop] = None,
    ) -> None:
        """Initialize the task service with model provider, registry, dispatcher, and agent loop."""
        self.settings = settings or get_settings()
        self.tool_registry = tool_registry or get_global_registry()
        self.tool_dispatcher = tool_dispatcher or ToolDispatcher(
            registry=self.tool_registry,
            default_timeout_seconds=self.settings.agent_tool_timeout_seconds,
        )

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

        if agent_loop is not None:
            self.agent_loop = agent_loop
        elif self.model_provider is not None:
            self.agent_loop = AgentLoop(
                model_provider=self.model_provider,
                dispatcher=self.tool_dispatcher,
                registry=self.tool_registry,
            )
        else:
            self.agent_loop = None

        self._tasks: Dict[str, TaskResponse] = {}
        self._events: Dict[str, List[RuntimeEvent]] = {}
        self._event_queues: Dict[str, List[asyncio.Queue[Optional[RuntimeEvent]]]] = {}
        self._approvals: Dict[str, Dict[str, Any]] = {}
        self._cancellation_tokens: Dict[str, asyncio.Event] = {}

    def _record_event(
        self,
        task_id: str,
        event_type: RuntimeEventType,
        payload: Optional[Dict[str, Any]] = None,
    ) -> RuntimeEvent:
        """Record and broadcast a structured lifecycle runtime event."""
        event = RuntimeEvent(
            event_id=str(uuid.uuid4()),
            task_id=task_id,
            event_type=event_type,
            timestamp=datetime.now(timezone.utc).isoformat(),
            payload=payload or {},
        )
        return self._broadcast_event(event)

    def _broadcast_event(self, event: RuntimeEvent) -> RuntimeEvent:
        """Append event to historical record and notify live streaming subscriber queues."""
        task_id = event.task_id
        if task_id not in self._events:
            self._events[task_id] = []
        self._events[task_id].append(event)

        if task_id in self._event_queues:
            for q in self._event_queues[task_id]:
                try:
                    q.put_nowait(event)
                except asyncio.QueueFull:
                    pass
        return event

    async def submit_task(self, request: TaskCreateRequest) -> TaskResponse:
        """Submit a prompt and process execution via the agent execution loop."""
        task_id = str(uuid.uuid4())
        created_at = datetime.now(timezone.utc).isoformat()

        # Emit task_created lifecycle event
        self._record_event(
            task_id=task_id,
            event_type=RuntimeEventType.TASK_CREATED,
            payload={"prompt": request.prompt, "max_tokens": request.max_tokens},
        )

        # Handle unconfigured model provider state safely
        if self.model_provider is None or self.agent_loop is None:
            err_msg = "Model endpoint is unconfigured. Set AGENT_MODEL_BASE_URL to connect to vLLM."
            task = TaskResponse(
                task_id=task_id,
                prompt=request.prompt,
                status=TaskStatus.FAILED,
                error=err_msg,
                termination_reason="unconfigured_model",
                created_at=created_at,
                completed_at=created_at,
            )
            self._tasks[task_id] = task
            self._record_event(
                task_id=task_id,
                event_type=RuntimeEventType.TASK_TERMINAL,
                payload={"status": TaskStatus.FAILED.value, "error": err_msg},
            )
            return task

        return await self._execute_with_loop(task_id, request, created_at)

    async def _execute_with_loop(
        self,
        task_id: str,
        request: TaskCreateRequest,
        created_at: str,
    ) -> TaskResponse:
        """Execute task through the AgentLoop engine with cancellation and streaming."""
        executing_record = TaskResponse(
            task_id=task_id,
            prompt=request.prompt,
            status=TaskStatus.EXECUTING,
            created_at=created_at,
        )
        self._tasks[task_id] = executing_record

        cancellation_token = asyncio.Event()
        self._cancellation_tokens[task_id] = cancellation_token

        exec_request = AgentExecutionRequest(
            task_id=task_id,
            prompt=request.prompt,
            max_iterations=self.settings.agent_max_iterations,
            max_tool_calls=self.settings.agent_max_tool_calls,
            max_tokens=request.max_tokens or 2048,
            temperature=request.temperature if request.temperature is not None else 0.2,
            task_timeout_seconds=self.settings.agent_task_timeout_seconds,
            tool_timeout_seconds=self.settings.agent_tool_timeout_seconds,
            cancellation_token=cancellation_token,
            event_sink=lambda ev: self._broadcast_event(ev),
        )

        try:
            loop_result = await self.agent_loop.run(exec_request)
            completed_at = datetime.now(timezone.utc).isoformat()

            final_task = TaskResponse(
                task_id=task_id,
                prompt=request.prompt,
                status=loop_result.status,
                response_text=loop_result.final_response,
                finish_reason=loop_result.finish_reason or loop_result.termination_reason,
                model=loop_result.model,
                usage=loop_result.usage,
                error=loop_result.error,
                iterations=loop_result.iterations,
                tool_calls_count=loop_result.tool_calls_count,
                termination_reason=loop_result.termination_reason,
                created_at=created_at,
                completed_at=completed_at,
            )
            self._tasks[task_id] = final_task

            self._record_event(
                task_id=task_id,
                event_type=RuntimeEventType.TASK_TERMINAL,
                payload={
                    "status": loop_result.status.value,
                    "model": loop_result.model,
                    "usage": loop_result.usage,
                    "iterations": loop_result.iterations,
                    "tool_calls_count": loop_result.tool_calls_count,
                    "termination_reason": loop_result.termination_reason,
                    "error": loop_result.error,
                },
            )
            return final_task

        except Exception as exc:
            logger.error("Unexpected execution error for task %s: %s", task_id, type(exc).__name__, exc_info=True)
            err_msg = "An unexpected internal error occurred during agent execution."
            failed_task = TaskResponse(
                task_id=task_id,
                prompt=request.prompt,
                status=TaskStatus.FAILED,
                error=err_msg,
                termination_reason="unexpected_crash",
                created_at=created_at,
                completed_at=datetime.now(timezone.utc).isoformat(),
            )
            self._tasks[task_id] = failed_task
            self._record_event(
                task_id=task_id,
                event_type=RuntimeEventType.TASK_TERMINAL,
                payload={"status": TaskStatus.FAILED.value, "error": err_msg},
            )
            return failed_task

    def get_task(self, task_id: str) -> Optional[TaskResponse]:
        """Retrieve stored task record by identifier."""
        return self._tasks.get(task_id)

    def cancel_task(self, task_id: str) -> Optional[TaskResponse]:
        """Cancel an in-progress or queued task safely by signaling its cancellation token."""
        task = self._tasks.get(task_id)
        if not task:
            return None

        # Signal in-flight loop to abort
        if task_id in self._cancellation_tokens:
            self._cancellation_tokens[task_id].set()

        if task.status in (TaskStatus.PENDING, TaskStatus.EXECUTING):
            cancelled_task = TaskResponse(
                task_id=task_id,
                prompt=task.prompt,
                status=TaskStatus.CANCELLED,
                error="Task was cancelled by user.",
                termination_reason="cancelled",
                created_at=task.created_at,
                completed_at=datetime.now(timezone.utc).isoformat(),
            )
            self._tasks[task_id] = cancelled_task
            self._record_event(
                task_id=task_id,
                event_type=RuntimeEventType.TASK_CANCELLED,
                payload={"status": "cancelled", "error": "Task was cancelled by user."},
            )
            self._record_event(
                task_id=task_id,
                event_type=RuntimeEventType.TASK_TERMINAL,
                payload={"status": TaskStatus.CANCELLED.value, "error": "Task was cancelled by user."},
            )
            return cancelled_task
        return task

    def get_events(self, task_id: str) -> List[RuntimeEvent]:
        """Retrieve all recorded events for a task."""
        return list(self._events.get(task_id, []))

    def record_approval_decision(
        self,
        task_id: str,
        call_id: str,
        approved: bool,
        reason: Optional[str] = None,
    ) -> bool:
        """Record human decision authority outcome on an eligible tool call."""
        if task_id not in self._approvals:
            self._approvals[task_id] = {}
        self._approvals[task_id][call_id] = {
            "approved": approved,
            "reason": reason,
            "decided_at": datetime.now(timezone.utc).isoformat(),
        }
        event_type = RuntimeEventType.TOOL_REQUESTED if approved else RuntimeEventType.PERMISSION_DENIED
        self._record_event(
            task_id=task_id,
            event_type=event_type,
            payload={"call_id": call_id, "approved": approved, "reason": reason},
        )
        return True


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
