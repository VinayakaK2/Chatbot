"""Reactive multi-turn Agent Execution Loop with budget bounds, tool dispatch, and event streaming."""

import asyncio
from datetime import datetime, timezone
import json
import logging
import time
from typing import Any, Callable, Dict, List, Optional, Set
import uuid
from pydantic import BaseModel, ConfigDict, Field

from api.schemas.events import RuntimeEvent, RuntimeEventType
from api.schemas.tasks import TaskStatus
from core.message_models import (
    AgentMessage,
    AgentMessageRole,
    ToolCallRecord,
    ToolCallStatus,
)
from core.tool_contracts import ToolResultEnvelope
from core.tool_dispatcher import ToolDispatcher
from core.tool_registry import ToolRegistry, get_global_registry
from core.context_manager import ContextManager
from core.context_models import (
    ContextBudgetConfig,
    MandatoryContextOverflowError,
)
from model.contracts import ModelError, ModelProvider, ModelResponse
from storage.models import ExecutionState, ModelCallRecord, TranscriptMessageRecord
from storage.repositories import (
    ModelCallRepository,
    ToolExecutionRepository,
    TranscriptRepository,
)

logger = logging.getLogger("agent_runtime.core.agent_loop")


def sanitize_greeting_response(user_prompt: str, response_text: str) -> str:
    """Ensure casual user greetings receive clean, polite, and human responses instead of robotic boot headers."""
    cleaned = response_text.strip()
    prompt_simple = user_prompt.strip().lower().rstrip("!.,?:; ")
    is_greeting = prompt_simple in {
        "hi", "hello", "hey", "hii", "hiii", "helloo", "hey there", "hello there",
        "greetings", "good morning", "good evening", "good afternoon", "namaste", "hola",
    }

    robotic_patterns = [
        "session initialized",
        "system initialized",
        "ready for development",
        "status: online",
        "capabilities & operations",
        "ready to assist",
    ]
    has_robotic = any(p in cleaned.lower() for p in robotic_patterns)

    if is_greeting and has_robotic:
        return "Hello! How can I help you with your code or project today?"

    return cleaned


DEFAULT_SYSTEM_PROMPT: str = """You are a helpful, conversational, and highly skilled software engineering AI assistant.

COMMUNICATION INSTRUCTIONS:
1. GREETINGS & CASUAL CONVERSATION:
   - When the user greets you (e.g., 'hi', 'hello', 'hey', 'good morning', etc.), always greet them back naturally, warmly, and briefly!
   - Example greeting response: "Hello! How can I help you with your code or project today?"
   - Keep greetings simple and polite (1-2 sentences).
   - NEVER respond to a greeting with robotic system status notices (e.g., 'Ready for Development', 'System Initialized'), boot banners, or unsolicited capability lists.

2. ADAPTIVE LENGTH & SCOPE:
   - Match the depth and length of your response to the user's prompt:
     * Quick / direct questions (e.g., factual queries, simple definitions, file locations, small clarifications): Provide concise, direct answers without unnecessary filler, boilerplate, or unsolicited essays.
     * Complex engineering tasks (e.g., architecture design, multi-file code implementation, debugging, audits): Provide thorough, structured, production-grade solutions.
   - Use clean GitHub-Flavored Markdown:
     * Fenced code blocks with explicit language tags (```python, ```typescript, ```bash, etc.) for code.
     * Inline code backticks (`...`) for file paths, variable names, function names, and commands.
     * Clear headings (### ...) and bullet points with bold lead-ins (- **Component:** Explanation) when explaining multi-step designs or solutions.
     * Markdown tables when presenting comparative data, benchmarks, or test execution summaries.
   - Keep technical explanations focused, accurate, structured, and actionable.

3. TOOL USAGE:
   - Tool calls and execution are handled by the runtime harness. Do not output raw tool execution JSON payloads inside your conversational text.
"""


class AgentExecutionRequest(BaseModel):
    """Encapsulates the complete context and constraints to execute an agent task."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    task_id: str = Field(..., description="Unique task identifier")
    prompt: str = Field(..., description="Primary user prompt string")
    system_prompt: Optional[str] = Field(
        default=None,
        description="Optional system prompt defining agent persona and operational constraints",
    )
    initial_messages: Optional[List[AgentMessage]] = Field(
        default=None,
        description="Prior conversation history if resuming or multi-turn",
    )
    max_iterations: int = Field(
        default=20,
        ge=1,
        le=100,
        description="Maximum multi-turn loop iterations allowed",
    )
    max_tool_calls: int = Field(
        default=50,
        ge=1,
        le=200,
        description="Maximum total tool calls permitted across the task",
    )
    max_tokens: int = Field(
        default=2048,
        ge=1,
        le=8192,
        description="Max tokens requested per model turn",
    )
    temperature: float = Field(
        default=0.2,
        ge=0.0,
        le=2.0,
        description="Sampling temperature",
    )
    task_timeout_seconds: float = Field(
        default=300.0,
        gt=0.0,
        le=1800.0,
        description="Wall-clock execution ceiling for the entire task",
    )
    tool_timeout_seconds: float = Field(
        default=30.0,
        gt=0.0,
        le=300.0,
        description="Timeout for individual tool executions",
    )
    cancellation_token: Optional[asyncio.Event] = Field(
        default=None,
        description="Async signal token to interrupt in-flight execution",
    )
    event_sink: Optional[Callable[[RuntimeEvent], Any]] = Field(
        default=None,
        description="Callable receiver for structured real-time lifecycle events",
    )
    allowed_tools: Optional[Set[str]] = Field(
        default=None,
        description="Optional allowlist of tool names exposed to the model",
    )


class AgentExecutionResult(BaseModel):
    """Normalized terminal outcome of an agent execution loop run."""

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    task_id: str
    status: TaskStatus
    final_response: Optional[str] = None
    finish_reason: Optional[str] = None
    iterations: int = 0
    tool_calls_count: int = 0
    history: List[AgentMessage] = Field(default_factory=list)
    model: Optional[str] = None
    usage: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    termination_reason: str


def _sanitize_tool_requested_payload(tc_id: str, tool_name: str, raw_args_str: str) -> Dict[str, Any]:
    """Sanitize client-facing TOOL_REQUESTED event payload to prevent exposing sensitive write content."""
    file_path = None
    operation = f"Execute {tool_name}"
    risk_category = "read_only"

    parsed_args = {}
    if raw_args_str:
        try:
            parsed_args = json.loads(raw_args_str)
        except Exception:
            parsed_args = {"raw": raw_args_str[:200]}

    sanitized_args: Dict[str, Any] = {}
    if isinstance(parsed_args, dict):
        file_path = parsed_args.get("path")
        for k, v in parsed_args.items():
            if k in ("content", "new_str", "text", "body") and isinstance(v, str):
                byte_len = len(v.encode("utf-8", errors="replace"))
                sanitized_args[k] = f"<redacted: {byte_len} bytes>"
                sanitized_args[f"{k}_bytes"] = byte_len
            elif k in ("old_str",) and isinstance(v, str):
                if len(v) > 200:
                    byte_len = len(v.encode("utf-8", errors="replace"))
                    sanitized_args[k] = f"<redacted: {byte_len} bytes>"
                else:
                    sanitized_args[k] = v
            else:
                sanitized_args[k] = v
    else:
        sanitized_args = {"raw": str(parsed_args)[:200]}

    if tool_name in ("write_file", "edit_file"):
        risk_category = "workspace_write"
        operation = f"Write {file_path}" if file_path else f"Execute {tool_name}"
    elif tool_name in ("read_file", "read_file_range", "list_directory", "search_files", "find_files"):
        risk_category = "read_only"
        operation = f"Read {file_path}" if file_path else f"Execute {tool_name}"

    payload: Dict[str, Any] = {
        "call_id": tc_id,
        "tool_name": tool_name,
        "operation": operation,
        "risk_category": risk_category,
        "arguments": sanitized_args,
    }
    if file_path:
        payload["file_path"] = str(file_path)
    return payload


def _sanitize_tool_completed_payload(tc_id: str, tool_name: str, envelope: ToolResultEnvelope) -> Dict[str, Any]:
    """Sanitize client-facing TOOL_COMPLETED event payload to prevent exposing sensitive read file contents."""
    output = envelope.output
    sanitized_output = output
    diff_payload = None

    if isinstance(output, dict):
        sanitized_dict = dict(output)
        if "content" in sanitized_dict and isinstance(sanitized_dict["content"], str):
            content_str = sanitized_dict["content"]
            byte_len = len(content_str.encode("utf-8", errors="replace"))
            sanitized_dict["content"] = f"<redacted: {byte_len} bytes>"
            sanitized_dict["content_redacted"] = True
            sanitized_dict["bytes_read"] = byte_len
        if "diff" in sanitized_dict:
            diff_payload = sanitized_dict.get("diff")
        sanitized_output = sanitized_dict
    elif isinstance(output, str):
        if len(output) > 2000:
            sanitized_output = output[:2000] + f"\n... [Truncated {len(output) - 2000} chars for progress stream]"

    payload: Dict[str, Any] = {
        "call_id": tc_id,
        "tool_name": tool_name,
        "output": sanitized_output,
        "metadata": envelope.metadata,
        "success": envelope.success,
    }
    if diff_payload is not None:
        payload["diff"] = diff_payload
    return payload


class AgentLoop:
    """Reactive agent execution engine coordinating model reasoning, tool dispatch, and state."""

    def __init__(
        self,
        model_provider: ModelProvider,
        dispatcher: Optional[ToolDispatcher] = None,
        registry: Optional[ToolRegistry] = None,
        context_manager: Optional[ContextManager] = None,
        budget_config: Optional[ContextBudgetConfig] = None,
        tool_execution_repo: Optional[ToolExecutionRepository] = None,
        model_call_repo: Optional[ModelCallRepository] = None,
        transcript_repo: Optional[TranscriptRepository] = None,
    ) -> None:
        """Initialize the agent loop with model provider, tool dispatcher, registry, context manager, and storage repos."""
        self.model_provider = model_provider
        self.registry = registry or get_global_registry()
        self.dispatcher = dispatcher or ToolDispatcher(registry=self.registry)
        self.context_manager = context_manager or ContextManager(budget_config)
        self.budget_config = budget_config or self.context_manager.default_budget
        self.tool_execution_repo = tool_execution_repo
        self.model_call_repo = model_call_repo
        self.transcript_repo = transcript_repo

    def _record_transcript_msg(self, task_id: str, msg: AgentMessage, seq_num: int) -> None:
        """Persist a message turn to the durable transcript if repository is configured."""
        if not self.transcript_repo:
            return
        try:
            tool_calls_json = None
            if msg.tool_calls:
                tool_calls_json = json.dumps([tc.model_dump() for tc in msg.tool_calls])
            self.transcript_repo.append(
                TranscriptMessageRecord(
                    id=str(uuid.uuid4()),
                    task_id=task_id,
                    sequence_num=seq_num,
                    role=msg.role,
                    content=msg.content,
                    name=msg.name,
                    tool_call_id=msg.tool_call_id,
                    tool_calls_json=tool_calls_json,
                )
            )
        except Exception as exc:
            logger.debug("Failed to record transcript message for task %s: %s", task_id, str(exc))

    def _emit_event(
        self,
        request: AgentExecutionRequest,
        event_type: RuntimeEventType,
        payload: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Construct and dispatch an immutable runtime lifecycle event to the event sink."""
        if not request.event_sink:
            return

        event = RuntimeEvent(
            event_id=str(uuid.uuid4()),
            task_id=request.task_id,
            event_type=event_type,
            timestamp=datetime.now(timezone.utc).isoformat(),
            payload=payload or {},
        )
        try:
            res = request.event_sink(event)
            if asyncio.iscoroutine(res):
                asyncio.create_task(res)
        except Exception as exc:
            logger.warning("Event sink exception on task %s: %s", request.task_id, str(exc))

    async def run(self, request: AgentExecutionRequest) -> AgentExecutionResult:
        """Execute the multi-turn agent loop until a valid terminal condition is satisfied."""
        start_time = time.perf_counter()
        logger.info("Initiating AgentLoop for task '%s' (max_iter=%d)", request.task_id, request.max_iterations)

        # 1. Assemble Initial Conversation History
        history: List[AgentMessage] = []
        if request.system_prompt:
            history.append(AgentMessage.create_system(request.system_prompt))
        if request.initial_messages:
            history.extend(request.initial_messages)
        else:
            history.append(AgentMessage.create_user(request.prompt))

        seq_counter = 0
        for m in history:
            self._record_transcript_msg(request.task_id, m, seq_counter)
            seq_counter += 1

        # 2. Export Model-Facing Tool Schemas
        tool_schemas = self.registry.export_model_schemas(allowed_names=request.allowed_tools)

        iterations = 0
        total_tool_calls = 0
        accumulated_usage: Dict[str, int] = {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        }
        last_model_id: Optional[str] = None

        # 3. Reactive Execution Loop
        while True:
            # 3.1 Check Task-Level Wall-Clock Timeout
            elapsed = time.perf_counter() - start_time
            if elapsed >= request.task_timeout_seconds:
                logger.warning("Task %s reached wall-clock timeout (%.2fs)", request.task_id, elapsed)
                self._emit_event(
                    request,
                    RuntimeEventType.TASK_LIMIT_REACHED,
                    {"limit_type": "task_timeout", "elapsed_seconds": elapsed},
                )
                return AgentExecutionResult(
                    task_id=request.task_id,
                    status=TaskStatus.LIMIT_REACHED,
                    final_response=None,
                    iterations=iterations,
                    tool_calls_count=total_tool_calls,
                    history=history,
                    model=last_model_id,
                    usage=accumulated_usage,
                    error=f"Task exceeded execution time limit of {request.task_timeout_seconds}s.",
                    termination_reason="task_timeout",
                )

            # 3.2 Check User Cancellation Token
            if request.cancellation_token and request.cancellation_token.is_set():
                logger.info("Task %s cancelled by operator before iteration %d", request.task_id, iterations + 1)
                self._emit_event(
                    request,
                    RuntimeEventType.TASK_CANCELLED,
                    {"iteration": iterations, "elapsed_seconds": elapsed},
                )
                return AgentExecutionResult(
                    task_id=request.task_id,
                    status=TaskStatus.CANCELLED,
                    final_response=None,
                    iterations=iterations,
                    tool_calls_count=total_tool_calls,
                    history=history,
                    model=last_model_id,
                    usage=accumulated_usage,
                    error="Task was cancelled by user.",
                    termination_reason="cancelled",
                )

            # 3.3 Check Iteration Budget
            if iterations >= request.max_iterations:
                logger.warning("Task %s reached max iterations limit (%d)", request.task_id, request.max_iterations)
                self._emit_event(
                    request,
                    RuntimeEventType.TASK_LIMIT_REACHED,
                    {"limit_type": "max_iterations", "iterations": iterations},
                )
                return AgentExecutionResult(
                    task_id=request.task_id,
                    status=TaskStatus.LIMIT_REACHED,
                    final_response=None,
                    iterations=iterations,
                    tool_calls_count=total_tool_calls,
                    history=history,
                    model=last_model_id,
                    usage=accumulated_usage,
                    error=f"Task reached maximum allowed iterations limit ({request.max_iterations}).",
                    termination_reason="max_iterations",
                )

            iterations += 1
            logger.info("AgentLoop turn %d/%d for task %s", iterations, request.max_iterations, request.task_id)

            # 3.4 Assemble Bounded Model Context via ContextManager
            try:
                context_result = self.context_manager.assemble_context(
                    task_id=request.task_id,
                    user_prompt=request.prompt,
                    global_instructions=DEFAULT_SYSTEM_PROMPT,
                    agent_instructions=request.system_prompt,
                    task_state={
                        "task_id": request.task_id,
                        "iteration": iterations,
                        "max_iterations": request.max_iterations,
                        "total_tool_calls": total_tool_calls,
                    },
                    history=history,
                    budget_config=self.budget_config,
                )
                assembled_messages = context_result.messages
            except MandatoryContextOverflowError as exc:
                logger.error("Mandatory context overflow on turn %d: %s", iterations, exc.message)
                self._emit_event(
                    request,
                    RuntimeEventType.TASK_LIMIT_REACHED,
                    {"limit_type": "mandatory_context_overflow", "error": exc.message},
                )
                return AgentExecutionResult(
                    task_id=request.task_id,
                    status=TaskStatus.FAILED,
                    final_response=None,
                    iterations=iterations,
                    tool_calls_count=total_tool_calls,
                    history=history,
                    model=last_model_id,
                    usage=accumulated_usage,
                    error=exc.message,
                    termination_reason="mandatory_context_overflow",
                )

            # 3.5 Dispatch Model Request
            self._emit_event(
                request,
                RuntimeEventType.MODEL_REQUEST_STARTED,
                {
                    "iteration": iterations,
                    "message_count": len(assembled_messages),
                    "tools_count": len(tool_schemas),
                    "tokens_used_estimate": context_result.diagnostics.tokens_used_estimate,
                },
            )

            model_call_start = time.perf_counter()
            try:
                model_response = await self.model_provider.generate(
                    prompt=request.prompt,  # fallback for single-prompt adapters
                    max_tokens=request.max_tokens,
                    temperature=request.temperature,
                    messages=assembled_messages,
                    tools=tool_schemas if tool_schemas else None,
                )
            except ModelError as err:
                logger.error("Model provider error on turn %d: %s", iterations, err.message)
                return AgentExecutionResult(
                    task_id=request.task_id,
                    status=TaskStatus.FAILED,
                    final_response=None,
                    iterations=iterations,
                    tool_calls_count=total_tool_calls,
                    history=history,
                    model=last_model_id,
                    usage=accumulated_usage,
                    error=err.message,
                    termination_reason="model_error",
                )
            except Exception as exc:
                logger.error("Unexpected exception invoking model on turn %d: %s", iterations, str(exc), exc_info=True)
                return AgentExecutionResult(
                    task_id=request.task_id,
                    status=TaskStatus.FAILED,
                    final_response=None,
                    iterations=iterations,
                    tool_calls_count=total_tool_calls,
                    history=history,
                    model=last_model_id,
                    usage=accumulated_usage,
                    error=f"Internal model execution error: {str(exc)}",
                    termination_reason="internal_error",
                )

            model_latency_ms = (time.perf_counter() - model_call_start) * 1000.0
            if self.model_call_repo:
                try:
                    self.model_call_repo.record(
                        ModelCallRecord(
                            id=str(uuid.uuid4()),
                            task_id=request.task_id,
                            iteration=iterations,
                            model=model_response.model or "unknown",
                            prompt_tokens=model_response.usage.get("prompt_tokens", 0) if model_response.usage else 0,
                            completion_tokens=model_response.usage.get("completion_tokens", 0) if model_response.usage else 0,
                            total_tokens=model_response.usage.get("total_tokens", 0) if model_response.usage else 0,
                            finish_reason=model_response.finish_reason,
                            latency_ms=model_latency_ms,
                        )
                    )
                except Exception as exc:
                    logger.debug("Failed to record model call telemetry for task %s: %s", request.task_id, str(exc))

            last_model_id = model_response.model
            if model_response.usage:
                accumulated_usage["prompt_tokens"] += model_response.usage.get("prompt_tokens", 0)
                accumulated_usage["completion_tokens"] += model_response.usage.get("completion_tokens", 0)
                accumulated_usage["total_tokens"] += model_response.usage.get("total_tokens", 0)

            # 3.5 Emit Model Response Received Event
            tool_calls_list = model_response.tool_calls or []
            self._emit_event(
                request,
                RuntimeEventType.MODEL_RESPONSE_RECEIVED,
                {
                    "iteration": iterations,
                    "finish_reason": model_response.finish_reason,
                    "tool_calls_count": len(tool_calls_list),
                    "has_text": bool(model_response.text.strip()),
                },
            )

            # 3.6 Check for Tool Calls vs Final Answer
            if not tool_calls_list:
                # Stop Condition 1: Model produced text output only (no tool calls)
                # Verify that response is non-empty
                if not model_response.text or not model_response.text.strip():
                    logger.warning("Model returned empty text and zero tool calls on turn %d", iterations)
                    return AgentExecutionResult(
                        task_id=request.task_id,
                        status=TaskStatus.FAILED,
                        final_response=None,
                        iterations=iterations,
                        tool_calls_count=total_tool_calls,
                        history=history,
                        model=last_model_id,
                        usage=accumulated_usage,
                        error="Model returned empty completion without tool calls.",
                        termination_reason="empty_response",
                    )

                logger.info("AgentLoop completed successfully on turn %d", iterations)
                sanitized_text = sanitize_greeting_response(request.prompt, model_response.text)
                final_assistant_msg = AgentMessage.create_assistant(content=sanitized_text)
                history.append(final_assistant_msg)
                self._record_transcript_msg(request.task_id, final_assistant_msg, seq_counter)
                seq_counter += 1
                return AgentExecutionResult(
                    task_id=request.task_id,
                    status=TaskStatus.COMPLETED,
                    final_response=sanitized_text,
                    finish_reason=model_response.finish_reason or "stop",
                    iterations=iterations,
                    tool_calls_count=total_tool_calls,
                    history=history,
                    model=last_model_id,
                    usage=accumulated_usage,
                    error=None,
                    termination_reason="completed",
                )

            # 3.7 Tool Calls Detected: Check Tool Call Budget
            if total_tool_calls + len(tool_calls_list) > request.max_tool_calls:
                logger.warning(
                    "Task %s exceeded max tool calls limit (%d requested, budget %d)",
                    request.task_id,
                    total_tool_calls + len(tool_calls_list),
                    request.max_tool_calls,
                )
                self._emit_event(
                    request,
                    RuntimeEventType.TASK_LIMIT_REACHED,
                    {"limit_type": "max_tool_calls", "tool_calls_count": total_tool_calls},
                )
                return AgentExecutionResult(
                    task_id=request.task_id,
                    status=TaskStatus.LIMIT_REACHED,
                    final_response=None,
                    iterations=iterations,
                    tool_calls_count=total_tool_calls,
                    history=history,
                    model=last_model_id,
                    usage=accumulated_usage,
                    error=f"Task exceeded maximum tool calls budget of {request.max_tool_calls}.",
                    termination_reason="max_tool_calls",
                )

            # 3.8 Build and Append Assistant Message with Tool Calls
            tool_call_records: List[ToolCallRecord] = []
            for raw_tc in tool_calls_list:
                call_id = raw_tc.get("id") or f"call_{uuid.uuid4().hex[:8]}"
                fn_block = raw_tc.get("function", {})
                tool_name = fn_block.get("name") or "unknown_tool"
                raw_args = fn_block.get("arguments")
                if isinstance(raw_args, dict):
                    raw_args_str = json.dumps(raw_args)
                elif isinstance(raw_args, str):
                    raw_args_str = raw_args
                else:
                    raw_args_str = "{}"

                tool_call_records.append(
                    ToolCallRecord(
                        id=call_id,
                        name=tool_name,
                        raw_arguments=raw_args_str,
                        status=ToolCallStatus.REQUESTED,
                    )
                )

            assistant_msg = AgentMessage.create_assistant(
                content=model_response.text,
                tool_calls=tool_call_records,
            )
            history.append(assistant_msg)
            self._record_transcript_msg(request.task_id, assistant_msg, seq_counter)
            seq_counter += 1

            # 3.9 Sequential Tool Dispatch & Result Collection
            for tc in tool_call_records:
                # Check cancellation before dispatching each individual tool
                if request.cancellation_token and request.cancellation_token.is_set():
                    logger.info("Task %s cancelled prior to tool '%s'", request.task_id, tc.name)
                    self._emit_event(
                        request,
                        RuntimeEventType.TASK_CANCELLED,
                        {"iteration": iterations, "cancelled_at_tool": tc.name},
                    )
                    return AgentExecutionResult(
                        task_id=request.task_id,
                        status=TaskStatus.CANCELLED,
                        final_response=None,
                        iterations=iterations,
                        tool_calls_count=total_tool_calls,
                        history=history,
                        model=last_model_id,
                        usage=accumulated_usage,
                        error="Task was cancelled by user.",
                        termination_reason="cancelled",
                    )

                self._emit_event(
                    request,
                    RuntimeEventType.TOOL_REQUESTED,
                    _sanitize_tool_requested_payload(tc.id, tc.name, tc.raw_arguments),
                )

                self._emit_event(
                    request,
                    RuntimeEventType.TOOL_EXECUTION_STARTED,
                    {"call_id": tc.id, "tool_name": tc.name},
                )

                is_mutation = tc.name in ("write_file", "edit_file", "execute_command")
                idempotent = tc.name in ("read_file", "read_file_range", "list_directory", "search_files", "find_files")
                if self.tool_execution_repo:
                    try:
                        self.tool_execution_repo.record_started(
                            task_id=request.task_id,
                            call_id=tc.id,
                            tool_name=tc.name,
                            arguments=tc.raw_arguments,
                            is_mutation=is_mutation,
                            idempotent=idempotent,
                        )
                    except Exception as exc:
                        logger.debug("Failed to record tool start in repository for task %s: %s", request.task_id, str(exc))

                tool_start_time = time.perf_counter()
                envelope = await self.dispatcher.dispatch(
                    tool_name=tc.name,
                    arguments=tc.raw_arguments,
                    call_id=tc.id,
                    timeout_seconds=request.tool_timeout_seconds,
                    context={
                        "task_id": request.task_id,
                        "iteration": iterations,
                        "event_sink": lambda event_type, payload: self._emit_event(request, event_type, payload),
                    },
                )
                tool_duration_ms = (time.perf_counter() - tool_start_time) * 1000.0

                if self.tool_execution_repo:
                    try:
                        exit_code = envelope.metadata.get("exit_code") if envelope.metadata else None
                        self.tool_execution_repo.record_completed(
                            call_id=tc.id,
                            state=ExecutionState.COMPLETED if envelope.success else ExecutionState.FAILED,
                            exit_code=exit_code,
                            result_output=envelope.output,
                            error=envelope.error,
                            error_category=envelope.error_category,
                            duration_ms=tool_duration_ms,
                        )
                    except Exception as exc:
                        logger.debug("Failed to record tool completion in repository for task %s: %s", request.task_id, str(exc))

                total_tool_calls += 1

                # If permission was denied, emit structured PERMISSION_DENIED event
                if envelope.error_category == "permission_denied":
                    self._emit_event(
                        request,
                        RuntimeEventType.PERMISSION_DENIED,
                        {
                            "call_id": tc.id,
                            "tool_name": tc.name,
                            "error": envelope.error,
                            "reason": envelope.error,
                        },
                    )

                # Emit specific completion or failure event (with sanitized payload for client SSE stream)
                if envelope.success:
                    self._emit_event(
                        request,
                        RuntimeEventType.TOOL_COMPLETED,
                        _sanitize_tool_completed_payload(tc.id, tc.name, envelope),
                    )
                else:
                    self._emit_event(
                        request,
                        RuntimeEventType.TOOL_EXECUTION_FAILED,
                        {
                            "call_id": tc.id,
                            "tool_name": tc.name,
                            "error": envelope.error,
                            "error_category": envelope.error_category,
                        },
                    )

                # Append matching tool result message to conversation history
                result_msg = AgentMessage.create_tool_result(
                    tool_call_id=tc.id,
                    tool_name=tc.name,
                    result=envelope,
                )
                history.append(result_msg)
                self._record_transcript_msg(request.task_id, result_msg, seq_counter)
                seq_counter += 1

            # Loop repeats to feed updated conversation back to the model
