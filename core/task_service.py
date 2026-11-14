"""Task coordination service separating API routes from execution orchestration."""

import asyncio
from datetime import datetime, timezone
import json
import logging
import os
import threading
from typing import Any, Dict, List, Optional, Set, Tuple, Union
import uuid

from api.schemas.events import RuntimeEvent, RuntimeEventType
from api.schemas.tasks import TaskCreateRequest, TaskResponse, TaskStatus
from core.agent_loop import AgentExecutionRequest, AgentLoop, DEFAULT_SYSTEM_PROMPT
from core.config import Settings, get_settings
from core.context_models import ContextBudgetConfig
from core.approval import ApprovalManager, ApprovalStatus, get_global_approval_manager
from core.recovery import StartupRecoveryEngine
from core.session_manager import SessionManager
from core.tool_dispatcher import ToolDispatcher
from core.tool_registry import ToolRegistry, get_global_registry
from core.workspace import WorkspaceContext
from model.contracts import ModelError, ModelProvider
from model.vllm_adapter import VLLMAdapter
from storage.database import DatabaseManager
from storage.migrations import MigrationRunner
from storage.models import (
    ApprovalRecord,
    RecoveryStatus,
    SessionRecord,
    SessionStatus,
    TaskRecord,
)
from storage.repositories import (
    ApprovalRepository,
    InvalidStateTransitionError,
    ModelCallRepository,
    SessionRepository,
    TaskRepository,
    ToolExecutionRepository,
    TranscriptRepository,
)
from tools.registry_setup import register_shell_tools, register_workspace_tools

logger = logging.getLogger("agent_runtime.core.task_service")

# Strict set of terminal task states that cannot be overwritten once entered
TERMINAL_STATUSES: Set[TaskStatus] = {
    TaskStatus.COMPLETED,
    TaskStatus.CANCELLED,
    TaskStatus.FAILED,
    TaskStatus.LIMIT_REACHED,
}


class EventSubscriber:
    """Encapsulates an active SSE event queue and its owning asyncio event loop."""

    def __init__(
        self,
        queue: asyncio.Queue[Optional[RuntimeEvent]],
        loop: asyncio.AbstractEventLoop,
    ) -> None:
        self.queue = queue
        self.loop = loop


class TaskService:
    """Coordinates task validation, submission, agent loop execution, and runtime events."""

    def __init__(
        self,
        settings: Optional[Settings] = None,
        model_provider: Optional[ModelProvider] = None,
        tool_registry: Optional[ToolRegistry] = None,
        tool_dispatcher: Optional[ToolDispatcher] = None,
        agent_loop: Optional[AgentLoop] = None,
        workspace_context: Optional[WorkspaceContext] = None,
        approval_manager: Optional[ApprovalManager] = None,
        database_manager: Optional[DatabaseManager] = None,
        session_manager: Optional[SessionManager] = None,
        auto_register_tools: bool = True,
        auto_register_shell_tools: bool = False,
        auto_run_recovery: bool = True,
    ) -> None:
        """Initialize the task service with model provider, registry, dispatcher, persistence, and agent loop."""
        self.settings = settings or get_settings()
        self.tool_registry = tool_registry or get_global_registry()
        self.approval_manager = approval_manager or get_global_approval_manager()
        self.tool_dispatcher = tool_dispatcher or ToolDispatcher(
            registry=self.tool_registry,
            approval_manager=self.approval_manager,
            default_timeout_seconds=self.settings.agent_tool_timeout_seconds,
        )
        self.workspace_context = workspace_context or (
            WorkspaceContext(self.settings.workspace_root)
            if self.settings.workspace_root
            else WorkspaceContext(root_path=os.getcwd())
        )

        # Persistence subsystem initialization
        if database_manager is not None:
            self.database_manager = database_manager
        elif self.settings.database_url:
            self.database_manager = DatabaseManager(
                db_path=self.settings.database_url,
                busy_timeout_ms=self.settings.database_busy_timeout_ms,
                enable_wal=self.settings.database_wal_mode,
            )
        elif os.environ.get("PYTEST_CURRENT_TEST"):
            self.database_manager = DatabaseManager(
                db_path=f"file:mem_{uuid.uuid4().hex}?mode=memory&cache=shared",
                busy_timeout_ms=self.settings.database_busy_timeout_ms,
                enable_wal=False,
            )
        else:
            self.database_manager = DatabaseManager(
                db_path="state.db",
                busy_timeout_ms=self.settings.database_busy_timeout_ms,
                enable_wal=self.settings.database_wal_mode,
            )

        # Apply schema migrations
        MigrationRunner.apply_all(self.database_manager.get_connection())

        # Storage repositories
        self.session_repo = SessionRepository(self.database_manager)
        self.task_repo = TaskRepository(self.database_manager)
        self.transcript_repo = TranscriptRepository(self.database_manager)
        extra_sec = [self.settings.agent_model_api_key.get_secret_value()] if self.settings.agent_model_api_key else None
        self.tool_repo = ToolExecutionRepository(self.database_manager, extra_secrets=extra_sec)
        self.model_call_repo = ModelCallRepository(self.database_manager)
        self.approval_repo = ApprovalRepository(self.database_manager)
        self.approval_manager.approval_repo = self.approval_repo
        self.session_manager = session_manager or SessionManager(self.database_manager)
        self.recovery_engine = StartupRecoveryEngine(self.database_manager)

        # Automatic startup recovery reconciliation
        if auto_run_recovery:
            try:
                self.recovery_engine.reconcile()
            except Exception as exc:
                logger.warning("Startup recovery reconciliation warning: %s", str(exc))

        # Register standard Phase 4 workspace tools into registry if enabled and not already present
        if auto_register_tools and not self.tool_registry.has("read_file"):
            register_workspace_tools(registry=self.tool_registry, workspace=self.workspace_context)
        # Register Phase 5 shell tools if explicitly requested and not already present
        if auto_register_shell_tools and not self.tool_registry.has("execute_command"):
            register_shell_tools(
                registry=self.tool_registry,
                workspace=self.workspace_context,
                default_timeout_seconds=self.settings.agent_tool_timeout_seconds,
                extra_secrets=[self.settings.agent_model_api_key] if self.settings.agent_model_api_key else None,
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
            # Inject storage repositories if not already set
            if getattr(self.agent_loop, "tool_execution_repo", None) is None:
                self.agent_loop.tool_execution_repo = self.tool_repo
            if getattr(self.agent_loop, "model_call_repo", None) is None:
                self.agent_loop.model_call_repo = self.model_call_repo
            if getattr(self.agent_loop, "transcript_repo", None) is None:
                self.agent_loop.transcript_repo = self.transcript_repo
        elif self.model_provider is not None:
            budget_config = ContextBudgetConfig(
                total_context_tokens=self.settings.context_window_tokens,
                reserved_output_tokens=self.settings.context_reserve_output_tokens,
                safety_margin_tokens=self.settings.context_safety_margin_tokens,
                max_tool_output_tokens=self.settings.context_max_tool_output_tokens,
            )
            self.agent_loop = AgentLoop(
                model_provider=self.model_provider,
                dispatcher=self.tool_dispatcher,
                registry=self.tool_registry,
                budget_config=budget_config,
                tool_execution_repo=self.tool_repo,
                model_call_repo=self.model_call_repo,
                transcript_repo=self.transcript_repo,
            )
        else:
            self.agent_loop = None

        self._tasks: Dict[str, TaskResponse] = {}
        self._events: Dict[str, List[RuntimeEvent]] = {}
        self._event_subscribers: Dict[str, List[EventSubscriber]] = {}
        self._approvals: Dict[str, Dict[str, Any]] = {}
        self._cancellation_tokens: Dict[str, asyncio.Event] = {}
        self._task_loops: Dict[str, asyncio.AbstractEventLoop] = {}
        self._task_locks: Dict[str, threading.RLock] = {}
        self._cleaned_up_tasks: Set[str] = set()
        self._registry_lock = threading.Lock()

    @staticmethod
    def _safe_put_nowait(q: asyncio.Queue, item: Any) -> None:
        """Safely put an item to an asyncio queue, silently ignoring QueueFull."""
        try:
            q.put_nowait(item)
        except asyncio.QueueFull:
            pass

    def _get_task_lock(self, task_id: str, create: bool = True) -> threading.RLock:
        """Retrieve or optionally initialize a dedicated re-entrant lock for the specified task."""
        with self._registry_lock:
            if task_id not in self._task_locks:
                if not create and task_id not in self._tasks:
                    return None
                self._task_locks[task_id] = threading.RLock()
            return self._task_locks[task_id]

    def _transition_task_state(
        self,
        task_id: str,
        target_task: TaskResponse,
        terminal_event_payload: Optional[Dict[str, Any]] = None,
        intermediate_event: Optional[Tuple[RuntimeEventType, Dict[str, Any]]] = None,
    ) -> Tuple[bool, TaskResponse]:
        """Atomically validate and execute a task state transition under the task's lock.

        Enforces a linearizable first-to-commit policy:
        - If cancellation commits first, late completion is rejected.
        - If completion commits first, late cancellation is rejected.
        - Terminal states (COMPLETED, CANCELLED, FAILED, LIMIT_REACHED) are immutable.

        Returns:
            Tuple[bool, TaskResponse]: (transition_applied, resulting_task)
        """
        lock = self._get_task_lock(task_id, create=True)
        with lock:
            current = self._tasks.get(task_id)

            # 1. Initial task insertion validation (F-02-D)
            if current is None:
                if target_task.status not in (
                    TaskStatus.PENDING,
                    TaskStatus.EXECUTING,
                    TaskStatus.FAILED,
                ):
                    logger.warning(
                        "Rejected invalid initial state transition for new task %s: target=%s",
                        task_id,
                        target_task.status.value,
                    )
                    return False, target_task

                self._tasks[task_id] = target_task
                try:
                    task_rec = TaskRecord(
                        id=task_id,
                        session_id=target_task.session_id,
                        prompt=target_task.prompt,
                        status=target_task.status,
                        recovery_status=RecoveryStatus(target_task.recovery_status) if target_task.recovery_status else None,
                        response_text=target_task.response_text,
                        finish_reason=target_task.finish_reason,
                        model=target_task.model,
                        usage_json=json.dumps(target_task.usage) if target_task.usage else None,
                        error=target_task.error,
                        iterations=target_task.iterations or 0,
                        tool_calls_count=target_task.tool_calls_count or 0,
                        termination_reason=target_task.termination_reason,
                        created_at=target_task.created_at,
                        started_at=target_task.created_at if target_task.status == TaskStatus.EXECUTING else None,
                        completed_at=target_task.completed_at,
                    )
                    self.task_repo.create(task_rec)
                except Exception as exc:
                    logger.debug("Database initial task insert notice for %s: %s", task_id, str(exc))

                if target_task.status in TERMINAL_STATUSES:
                    payload = terminal_event_payload or {
                        "status": target_task.status.value,
                        "termination_reason": target_task.termination_reason,
                        "error": target_task.error,
                    }
                    self._record_event(
                        task_id=task_id,
                        event_type=RuntimeEventType.TASK_TERMINAL,
                        payload=payload,
                    )
                return True, target_task

            # 2. Strict terminal state immutability
            if current.status in TERMINAL_STATUSES:
                logger.debug(
                    "Rejected state transition for task %s: current state %s is terminal, target=%s",
                    task_id,
                    current.status.value,
                    target_task.status.value,
                )
                return False, current

            # 3. Transition validity rules
            valid = False
            if current.status == TaskStatus.PENDING:
                valid = target_task.status in (
                    TaskStatus.EXECUTING,
                    TaskStatus.CANCELLED,
                    TaskStatus.FAILED,
                )
            elif current.status == TaskStatus.EXECUTING:
                valid = target_task.status in TERMINAL_STATUSES

            if not valid:
                logger.warning(
                    "Invalid state transition requested for task %s: %s -> %s",
                    task_id,
                    current.status.value,
                    target_task.status.value,
                )
                return False, current

            # 4. Atomic state update in SQLite database
            try:
                self.task_repo.transition_state(
                    task_id=task_id,
                    target_status=target_task.status,
                    recovery_status=RecoveryStatus(target_task.recovery_status) if target_task.recovery_status else None,
                    response_text=target_task.response_text,
                    finish_reason=target_task.finish_reason,
                    model=target_task.model,
                    usage_json=json.dumps(target_task.usage) if target_task.usage else None,
                    error=target_task.error,
                    iterations=target_task.iterations,
                    tool_calls_count=target_task.tool_calls_count,
                    termination_reason=target_task.termination_reason,
                    reason=terminal_event_payload.get("termination_reason") if terminal_event_payload else None,
                )
            except InvalidStateTransitionError as exc:
                logger.warning("Rejected state transition by database constraint: %s", str(exc))
                return False, current
            except Exception as exc:
                logger.debug("Database transition notice on task %s: %s", task_id, str(exc))

            # 5. Atomic state update in memory
            self._tasks[task_id] = target_task

            # 5. Emit intermediate lifecycle event if supplied (e.g. TASK_CANCELLED)
            if intermediate_event:
                ev_type, ev_payload = intermediate_event
                self._record_event(task_id=task_id, event_type=ev_type, payload=ev_payload)

            # 6. Emit terminal lifecycle event if entering terminal state
            if target_task.status in TERMINAL_STATUSES:
                payload = terminal_event_payload or {
                    "status": target_task.status.value,
                    "termination_reason": target_task.termination_reason,
                    "error": target_task.error,
                }
                self._record_event(
                    task_id=task_id,
                    event_type=RuntimeEventType.TASK_TERMINAL,
                    payload=payload,
                )

            return True, target_task

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
        """Append event to historical record and notify live streaming subscriber queues.

        Guarantees thread-safe queue dispatching (Rule 5 & 6):
        - Never touches private _loop state on asyncio.Queue.
        - Dispatches cross-thread via sub.loop.call_soon_threadsafe.
        - Safely handles closed event loops without leaking memory.
        """
        task_id = event.task_id
        lock = self._get_task_lock(task_id, create=True)
        with lock:
            if task_id not in self._events:
                self._events[task_id] = []
            self._events[task_id].append(event)

            if task_id in self._event_subscribers:
                dead_subscribers: List[EventSubscriber] = []
                for sub in list(self._event_subscribers[task_id]):
                    # Check for closed event loop
                    if sub.loop.is_closed():
                        dead_subscribers.append(sub)
                        continue

                    try:
                        current_loop = asyncio.get_running_loop()
                    except RuntimeError:
                        current_loop = None

                    try:
                        if current_loop is sub.loop:
                            # Same event loop: direct synchronous put
                            self._safe_put_nowait(sub.queue, event)
                        else:
                            # Worker thread or foreign loop: cross-thread threadsafe dispatch
                            sub.loop.call_soon_threadsafe(self._safe_put_nowait, sub.queue, event)
                    except RuntimeError:
                        # Event loop may have closed concurrently
                        dead_subscribers.append(sub)

                for dead in dead_subscribers:
                    if dead in self._event_subscribers[task_id]:
                        self._event_subscribers[task_id].remove(dead)

        return event

    def subscribe_events(
        self,
        task_id: str,
        queue: asyncio.Queue[Optional[RuntimeEvent]],
        loop: Optional[asyncio.AbstractEventLoop] = None,
    ) -> None:
        """Thread-safely register an event listener queue and its owning event loop."""
        if loop is None:
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError as exc:
                raise ValueError("An active event loop must be running or explicitly passed to subscribe_events.") from exc

        lock = self._get_task_lock(task_id, create=True)
        with lock:
            if task_id not in self._event_subscribers:
                self._event_subscribers[task_id] = []
            self._event_subscribers[task_id].append(EventSubscriber(queue=queue, loop=loop))

    def unsubscribe_events(
        self,
        task_id: str,
        queue: asyncio.Queue[Optional[RuntimeEvent]],
    ) -> None:
        """Thread-safely unregister an event listener queue."""
        lock = self._get_task_lock(task_id, create=False)
        if lock is None:
            return
        with lock:
            if task_id in self._event_subscribers:
                self._event_subscribers[task_id] = [
                    sub for sub in self._event_subscribers[task_id] if sub.queue is not queue
                ]

    def cleanup_task(self, task_id: str) -> bool:
        """Safely remove a terminal task and all associated resources from memory.

        Enforces strict lock hierarchy (Rules 1, 2, 3):
        1. Look up task lock under _registry_lock (released immediately).
        2. Acquire task_lock independently (never hold _registry_lock while waiting for task_lock).
        3. Verify task is terminal.
        4. Prune registries under _registry_lock while holding task_lock.
        5. Release task_lock.

        Returns:
            bool: True if task was found and safely removed; False otherwise.
        """
        # Step 1: Look up lock reference under _registry_lock without holding it across task_lock acquisition
        with self._registry_lock:
            lock = self._task_locks.get(task_id)
            if lock is None or task_id not in self._tasks:
                return False

        # Step 2: Acquire task lock independently (NO lock-order inversion!)
        with lock:
            task = self._tasks.get(task_id)
            if task is None or task.status not in TERMINAL_STATUSES:
                logger.warning(
                    "Cannot cleanup task %s: status %s is not terminal",
                    task_id,
                    task.status.value if task else "None",
                )
                return False

            # Step 3: Prune registries under _registry_lock while task_lock is held
            with self._registry_lock:
                self._tasks.pop(task_id, None)
                self._events.pop(task_id, None)
                self._event_subscribers.pop(task_id, None)
                self._approvals.pop(task_id, None)
                self._cancellation_tokens.pop(task_id, None)
                self._task_loops.pop(task_id, None)
                self._task_locks.pop(task_id, None)
                self._cleaned_up_tasks.add(task_id)
                try:
                    self.task_repo.delete(task_id)
                except Exception as exc:
                    logger.debug("Database cleanup notice for task %s: %s", task_id, str(exc))
                logger.info("Task %s and its associated resources were safely cleaned up", task_id)
                return True

    async def submit_task(self, request: TaskCreateRequest) -> TaskResponse:
        """Submit a prompt and process execution via the agent execution loop."""
        task_id = str(uuid.uuid4())
        created_at = datetime.now(timezone.utc).isoformat()

        # Attach session if provided or ensure default workspace session
        session_id = getattr(request, "session_id", None)
        if not session_id:
            try:
                def_session = self.session_manager.get_or_create_default_session(
                    workspace_path=str(self.workspace_context.root_path)
                )
                session_id = def_session.id
            except Exception as exc:
                logger.debug("Default session assignment notice: %s", str(exc))
                session_id = None

        # Emit task_created lifecycle event
        self._record_event(
            task_id=task_id,
            event_type=RuntimeEventType.TASK_CREATED,
            payload={"prompt": request.prompt, "max_tokens": request.max_tokens},
        )

        # Handle unconfigured model provider state safely
        if self.model_provider is None or self.agent_loop is None:
            err_msg = "Model endpoint is unconfigured. Set AGENT_MODEL_BASE_URL to connect to vLLM."
            failed_task = TaskResponse(
                task_id=task_id,
                session_id=session_id,
                prompt=request.prompt,
                status=TaskStatus.FAILED,
                error=err_msg,
                termination_reason="unconfigured_model",
                created_at=created_at,
                completed_at=created_at,
            )
            _, stored_task = self._transition_task_state(
                task_id=task_id,
                target_task=failed_task,
                terminal_event_payload={"status": TaskStatus.FAILED.value, "error": err_msg},
            )
            return stored_task

        return await self._execute_with_loop(task_id, request, created_at, session_id=session_id)

    async def _execute_with_loop(
        self,
        task_id: str,
        request: TaskCreateRequest,
        created_at: str,
        session_id: Optional[str] = None,
    ) -> TaskResponse:
        """Execute task through the AgentLoop engine with cancellation and streaming."""
        executing_record = TaskResponse(
            task_id=task_id,
            session_id=session_id,
            prompt=request.prompt,
            status=TaskStatus.EXECUTING,
            created_at=created_at,
        )

        cancellation_token = asyncio.Event()
        try:
            current_loop = asyncio.get_running_loop()
        except RuntimeError:
            current_loop = None

        lock = self._get_task_lock(task_id, create=True)
        with lock:
            self._cancellation_tokens[task_id] = cancellation_token
            if current_loop is not None:
                self._task_loops[task_id] = current_loop
            success, current = self._transition_task_state(task_id, executing_record)
            if not success:
                # Task was already transitioned to a terminal state (e.g. cancelled)
                return current

        effective_system_prompt = (
            getattr(request, "system_prompt", None)
            or DEFAULT_SYSTEM_PROMPT
        )

        exec_request = AgentExecutionRequest(
            task_id=task_id,
            prompt=request.prompt,
            system_prompt=effective_system_prompt,
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
                session_id=session_id,
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

            terminal_payload = {
                "status": loop_result.status.value,
                "model": loop_result.model,
                "usage": loop_result.usage,
                "iterations": loop_result.iterations,
                "tool_calls_count": loop_result.tool_calls_count,
                "termination_reason": loop_result.termination_reason,
                "error": loop_result.error,
            }

            _, stored_task = self._transition_task_state(
                task_id=task_id,
                target_task=final_task,
                terminal_event_payload=terminal_payload,
            )
            return stored_task

        except Exception as exc:
            logger.error("Unexpected execution error for task %s: %s", task_id, type(exc).__name__, exc_info=True)
            err_msg = "An unexpected internal error occurred during agent execution."
            failed_task = TaskResponse(
                task_id=task_id,
                session_id=session_id,
                prompt=request.prompt,
                status=TaskStatus.FAILED,
                error=err_msg,
                termination_reason="unexpected_crash",
                created_at=created_at,
                completed_at=datetime.now(timezone.utc).isoformat(),
            )
            _, stored_task = self._transition_task_state(
                task_id=task_id,
                target_task=failed_task,
                terminal_event_payload={"status": TaskStatus.FAILED.value, "error": err_msg},
            )
            return stored_task

    def get_task(self, task_id: str) -> Optional[TaskResponse]:
        """Retrieve stored task record by identifier under atomic lock."""
        with self._registry_lock:
            if task_id in self._cleaned_up_tasks:
                return None

        lock = self._get_task_lock(task_id, create=False)
        task = None
        if lock is None:
            with self._registry_lock:
                task = self._tasks.get(task_id)
        else:
            with lock:
                task = self._tasks.get(task_id)

        if task is not None:
            return task

        # Check durable SQLite storage if not present in memory (e.g. after restart)
        try:
            db_task = self.task_repo.get(task_id)
            if db_task:
                task_resp = TaskResponse(
                    task_id=db_task.id,
                    session_id=db_task.session_id,
                    prompt=db_task.prompt,
                    status=db_task.status,
                    recovery_status=db_task.recovery_status.value if db_task.recovery_status else None,
                    response_text=db_task.response_text,
                    finish_reason=db_task.finish_reason,
                    model=db_task.model,
                    usage=json.loads(db_task.usage_json) if db_task.usage_json else None,
                    error=db_task.error,
                    iterations=db_task.iterations,
                    tool_calls_count=db_task.tool_calls_count,
                    termination_reason=db_task.termination_reason,
                    created_at=db_task.created_at,
                    completed_at=db_task.completed_at,
                )
                with self._registry_lock:
                    self._tasks[task_id] = task_resp
                return task_resp
        except Exception as exc:
            logger.debug("Error loading task %s from database: %s", task_id, str(exc))

        return None

    def cancel_task(self, task_id: str) -> Optional[TaskResponse]:
        """Cancel an in-progress or queued task safely by signaling its cancellation token."""
        lock = self._get_task_lock(task_id, create=True)
        with lock:
            task = self._tasks.get(task_id)
            if not task:
                return None

            # Safe cross-thread cancellation token signaling (Rule 7)
            token = self._cancellation_tokens.get(task_id)
            task_loop = self._task_loops.get(task_id)
            if token is not None:
                try:
                    current_loop = asyncio.get_running_loop()
                except RuntimeError:
                    current_loop = None

                if task_loop and not task_loop.is_closed() and current_loop is not task_loop:
                    task_loop.call_soon_threadsafe(token.set)
                else:
                    token.set()

            # Cancel any pending approval requests for this task in ApprovalManager
            try:
                self.approval_manager.cancel_requests_for_task(task_id)
            except Exception as exc:
                logger.debug("Error cancelling approval requests for task %s: %s", task_id, str(exc))

            # If task is already in a terminal state, return current record without modification
            if task.status in TERMINAL_STATUSES:
                return task

            cancelled_task = TaskResponse(
                task_id=task_id,
                prompt=task.prompt,
                status=TaskStatus.CANCELLED,
                error="Task was cancelled by user.",
                termination_reason="cancelled",
                created_at=task.created_at,
                completed_at=datetime.now(timezone.utc).isoformat(),
            )

            _, stored_task = self._transition_task_state(
                task_id=task_id,
                target_task=cancelled_task,
                terminal_event_payload={
                    "status": TaskStatus.CANCELLED.value,
                    "error": "Task was cancelled by user.",
                    "termination_reason": "cancelled",
                },
                intermediate_event=(
                    RuntimeEventType.TASK_CANCELLED,
                    {"status": "cancelled", "error": "Task was cancelled by user."},
                ),
            )
            return stored_task

    def get_events(self, task_id: str) -> List[RuntimeEvent]:
        """Retrieve all recorded events for a task."""
        lock = self._get_task_lock(task_id, create=False)
        if lock is None:
            with self._registry_lock:
                return list(self._events.get(task_id, []))
        with lock:
            return list(self._events.get(task_id, []))

    def record_approval_decision(
        self,
        task_id: str,
        call_id: str,
        approved: bool,
        reason: Optional[str] = None,
    ) -> bool:
        """Record human decision authority outcome on an eligible tool call."""
        # 1. Resolve in ApprovalManager to unblock waiting async tool execution
        try:
            self.approval_manager.resolve_request(
                call_id_or_request_id=call_id,
                approved=approved,
                reason=reason,
                task_id=task_id,
            )
        except Exception as exc:
            logger.debug("ApprovalManager resolution notice for task %s call %s: %s", task_id, call_id, str(exc))

        # Resolve in durable ApprovalRepository
        try:
            self.approval_repo.resolve(
                call_id=call_id,
                status=ApprovalStatus.APPROVED if approved else ApprovalStatus.DENIED,
                decision_reason=reason,
            )
        except Exception as exc:
            logger.debug("ApprovalRepository resolution notice for task %s call %s: %s", task_id, call_id, str(exc))

        # 2. Thread-safe internal record for task service
        with self._registry_lock:
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

    def create_session(
        self,
        title: str,
        workspace_path: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> SessionRecord:
        """Create and persist a new session."""
        target_path = str(workspace_path) if workspace_path else str(self.workspace_context.root_path)
        return self.session_manager.create_session(title=title, workspace_path=target_path, metadata=metadata)

    def get_session(self, session_id: str) -> Optional[SessionRecord]:
        """Retrieve a session by ID."""
        return self.session_manager.get_session(session_id)

    def list_sessions(self, limit: int = 50, offset: int = 0) -> List[SessionRecord]:
        """List sessions ordered by update timestamp descending."""
        return self.session_manager.list_sessions(limit=limit, offset=offset)

    def load_session_context(self, session_id: str) -> Optional[Dict[str, Any]]:
        """Reconstruct full historical context for a session from durable SQLite store."""
        return self.session_manager.load_session_context(session_id)

    def set_workspace_root(self, new_root: Union[str, Path]) -> WorkspaceContext:
        """Atomically update active workspace context and re-bind workspace tools."""
        new_ctx = WorkspaceContext(
            root_path=new_root,
            max_file_size_bytes=self.settings.max_file_size_bytes,
            max_read_bytes=self.settings.max_read_bytes,
            max_directory_entries=self.settings.max_directory_entries,
            max_search_results=self.settings.max_search_results,
            max_write_bytes=self.settings.max_write_bytes,
            max_edit_bytes=self.settings.max_edit_bytes,
        )
        self.workspace_context = new_ctx
        from tools.registry_setup import get_all_workspace_tools
        new_tools = get_all_workspace_tools(new_ctx)
        for t in new_tools:
            self.tool_registry.unregister(t.name)
            self.tool_registry.register(t)
        logger.info("Updated task service workspace root to: %s", new_ctx.root_path)
        return new_ctx


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
