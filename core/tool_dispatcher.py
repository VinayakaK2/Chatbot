"""Trusted tool execution dispatcher, permission boundary, and result normalization."""

import asyncio
from datetime import datetime, timezone
import inspect
import json
import logging
import time
from typing import Any, Callable, Dict, Optional, Protocol, Set, Tuple, Union

from core.permissions import CommandPermissionEvaluator, PermissionDecision
from core.tool_contracts import (
    ToolDefinition,
    ToolNotFoundError,
    ToolResultEnvelope,
    ToolRiskCategory,
    ToolValidationError,
)
from core.tool_registry import ToolRegistry, get_global_registry

logger = logging.getLogger("agent_runtime.core.tool_dispatcher")

# Maximum string length for tool result output before truncation
MAX_TOOL_OUTPUT_CHARS: int = 65_536


class PermissionPolicy(Protocol):
    """Protocol defining the authorization boundary for tool execution."""

    async def check_permission(
        self,
        tool: ToolDefinition,
        arguments: Dict[str, Any],
        context: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """Evaluate whether the proposed tool invocation is permitted to execute."""
        ...


class DefaultPermissionPolicy:
    """Default runtime permission policy enforcing least privilege and safe defaults."""

    def __init__(
        self,
        allowed_categories: Optional[Set[ToolRiskCategory]] = None,
        allowed_tool_names: Optional[Set[str]] = None,
        denied_tool_names: Optional[Set[str]] = None,
        evaluator: Optional[CommandPermissionEvaluator] = None,
    ) -> None:
        """Initialize permission policy with explicit category, tool name, and command evaluator boundaries."""
        self.allowed_categories = allowed_categories or {
            ToolRiskCategory.READ_ONLY,
            ToolRiskCategory.SYSTEM_PROBE,
            ToolRiskCategory.WORKSPACE_WRITE,
        }
        self.allowed_tool_names = allowed_tool_names or set()
        self.denied_tool_names = denied_tool_names or set()
        self.evaluator = evaluator or CommandPermissionEvaluator()

    async def evaluate_permission(
        self,
        tool: ToolDefinition,
        arguments: Dict[str, Any],
        context: Optional[Dict[str, Any]] = None,
    ) -> Tuple[PermissionDecision, str]:
        """Deny-first permission check returning explicit PermissionDecision and reason."""
        # 1. Explicit deny list always wins
        if tool.name in self.denied_tool_names:
            logger.warning("Tool '%s' explicitly blocked by denied_tool_names", tool.name)
            return PermissionDecision.DENY, f"Tool '{tool.name}' is explicitly denied by policy."

        # 2. Explicit allow list overrides category defaults
        if tool.name in self.allowed_tool_names:
            return PermissionDecision.ALLOW, f"Tool '{tool.name}' is explicitly allowed by policy."

        # 3. Prohibit destructive operations
        if tool.risk_category == ToolRiskCategory.DESTRUCTIVE:
            return PermissionDecision.DENY, "Destructive operations are prohibited by default policy."

        # 4. Command Execution requires granular shell command evaluation (DENY > ASK > ALLOW)
        if tool.risk_category == ToolRiskCategory.COMMAND_EXECUTION:
            cmd = str(arguments.get("command", ""))
            cwd = arguments.get("working_dir")
            return self.evaluator.evaluate_command(cmd, cwd)

        # 5. Contained category allowlist check (READ_ONLY, SYSTEM_PROBE, WORKSPACE_WRITE)
        if tool.risk_category in self.allowed_categories:
            return PermissionDecision.ALLOW, f"Tool '{tool.name}' permitted by risk category policy."

        logger.warning(
            "Tool '%s' with risk category '%s' denied by default permission policy",
            tool.name,
            tool.risk_category.value,
        )
        return PermissionDecision.DENY, f"Tool '{tool.name}' denied by default permission policy."

    async def check_permission(
        self,
        tool: ToolDefinition,
        arguments: Dict[str, Any],
        context: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """Deny-first boolean check for backward compatibility with PermissionPolicy protocol."""
        decision, _ = await self.evaluate_permission(tool, arguments, context)
        return decision == PermissionDecision.ALLOW


class ToolDispatcher:
    """Validates arguments, enforces permissions, and securely dispatches trusted tool handlers."""

    def __init__(
        self,
        registry: Optional[ToolRegistry] = None,
        permission_policy: Optional[PermissionPolicy] = None,
        approval_manager: Optional[Any] = None,
        default_timeout_seconds: float = 30.0,
        max_output_chars: int = MAX_TOOL_OUTPUT_CHARS,
    ) -> None:
        """Initialize dispatcher with tool registry and permission policy dependencies."""
        self.registry = registry or get_global_registry()
        self.permission_policy = permission_policy or DefaultPermissionPolicy()
        self.approval_manager = approval_manager
        self.default_timeout_seconds = default_timeout_seconds
        self.max_output_chars = max_output_chars

    async def dispatch(
        self,
        tool_name: str,
        arguments: Union[str, Dict[str, Any], None],
        call_id: Optional[str] = None,
        timeout_seconds: Optional[float] = None,
        context: Optional[Dict[str, Any]] = None,
    ) -> ToolResultEnvelope:
        """Validate, authorize, and execute a tool call, returning a normalized result envelope."""
        start_time = time.perf_counter()
        timeout = timeout_seconds or self.default_timeout_seconds

        # 1. Trusted Registry Lookup
        try:
            tool = self.registry.get(tool_name)
        except ToolNotFoundError as exc:
            logger.warning("Tool lookup failed for '%s': not found in registry", tool_name)
            return ToolResultEnvelope.create_error(
                tool_name=tool_name,
                error=exc.message,
                error_category="tool_not_found",
                call_id=call_id,
                metadata={"elapsed_seconds": time.perf_counter() - start_time},
            )

        # 2. Safe JSON Argument Parsing
        parsed_args: Dict[str, Any]
        if isinstance(arguments, str):
            stripped_args = arguments.strip()
            if not stripped_args:
                parsed_args = {}
            else:
                try:
                    loaded = json.loads(stripped_args)
                    if not isinstance(loaded, dict):
                        return ToolResultEnvelope.create_error(
                            tool_name=tool_name,
                            error=f"Expected JSON object for tool arguments, got {type(loaded).__name__}.",
                            error_category="validation_error",
                            call_id=call_id,
                            metadata={"elapsed_seconds": time.perf_counter() - start_time},
                        )
                    parsed_args = loaded
                except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                    logger.warning("Malformed JSON arguments received for tool '%s': %s", tool_name, str(exc))
                    return ToolResultEnvelope.create_error(
                        tool_name=tool_name,
                        error=f"Malformed JSON argument payload: {str(exc)}",
                        error_category="validation_error",
                        call_id=call_id,
                        metadata={"elapsed_seconds": time.perf_counter() - start_time},
                    )
        elif isinstance(arguments, dict):
            parsed_args = arguments
        elif arguments is None:
            parsed_args = {}
        else:
            return ToolResultEnvelope.create_error(
                tool_name=tool_name,
                error=f"Invalid arguments type: expected dict or JSON string, got {type(arguments).__name__}.",
                error_category="validation_error",
                call_id=call_id,
                metadata={"elapsed_seconds": time.perf_counter() - start_time},
            )

        # 3. Contract Schema Validation
        try:
            validated_args = tool.validate_input(parsed_args)
        except ToolValidationError as exc:
            logger.warning("Input validation failed for tool '%s': %s", tool_name, exc.message)
            return ToolResultEnvelope.create_error(
                tool_name=tool_name,
                error=f"Validation failed for tool '{tool_name}': {exc.message}",
                error_category="validation_error",
                call_id=call_id,
                metadata={
                    "errors": exc.errors,
                    "elapsed_seconds": time.perf_counter() - start_time,
                },
            )

        # 4. Permission Boundary Evaluation (DENY > ASK > ALLOW)
        decision: PermissionDecision
        reason: str
        try:
            if hasattr(self.permission_policy, "evaluate_permission"):
                decision, reason = await self.permission_policy.evaluate_permission(
                    tool=tool,
                    arguments=validated_args,
                    context=context,
                )
            else:
                is_permitted = await self.permission_policy.check_permission(
                    tool=tool,
                    arguments=validated_args,
                    context=context,
                )
                decision = PermissionDecision.ALLOW if is_permitted else PermissionDecision.DENY
                reason = "Authorized by policy." if is_permitted else "Permission denied by policy."
        except Exception as exc:
            logger.error("Exception during permission check for tool '%s': %s", tool_name, str(exc))
            return ToolResultEnvelope.create_error(
                tool_name=tool_name,
                error=f"Permission check failed: {str(exc)}",
                error_category="permission_denied",
                call_id=call_id,
                metadata={"elapsed_seconds": time.perf_counter() - start_time},
            )

        if decision == PermissionDecision.DENY:
            logger.warning("Execution permission denied for tool '%s' [risk=%s]: %s", tool_name, tool.risk_category.value, reason)
            return ToolResultEnvelope.create_error(
                tool_name=tool_name,
                error=f"Permission denied for tool '{tool_name}' [risk={tool.risk_category.value}]: {reason}",
                error_category="permission_denied",
                call_id=call_id,
                metadata={"elapsed_seconds": time.perf_counter() - start_time},
            )

        if decision == PermissionDecision.ASK:
            from core.approval import ApprovalStatus, get_global_approval_manager

            appr_mgr = self.approval_manager or get_global_approval_manager()
            ctx = context or {}
            task_id = str(ctx.get("task_id", "default_task"))
            actual_call_id = call_id or f"call_{int(time.time() * 1000)}"
            cmd_str = validated_args.get("command")
            cwd_str = validated_args.get("working_dir")

            req = appr_mgr.create_request(
                task_id=task_id,
                call_id=actual_call_id,
                tool_name=tool_name,
                arguments=validated_args,
                risk_category=tool.risk_category,
                reason=reason,
                working_dir=cwd_str,
                command=cmd_str,
            )

            # Emit approval_needed event if sink is provided
            event_sink = ctx.get("event_sink")
            if callable(event_sink):
                from api.schemas.events import RuntimeEventType
                event_sink(
                    RuntimeEventType.APPROVAL_NEEDED,
                    {
                        "call_id": req.call_id,
                        "tool_name": tool_name,
                        "operation": f"Execute tool '{tool_name}'",
                        "command": cmd_str,
                        "working_dir": cwd_str,
                        "risk_category": tool.risk_category.value,
                        "reason": reason,
                    },
                )

            # Await human decision
            resolved = await appr_mgr.wait_for_decision(req.request_id, timeout_seconds=timeout)
            if resolved.status == ApprovalStatus.APPROVED:
                # Consume single-use token with action-hash validation
                try:
                    appr_mgr.consume_token(req.request_id, req.action_hash)
                except Exception as exc:
                    return ToolResultEnvelope.create_error(
                        tool_name=tool_name,
                        error=f"Approval token consumption failed: {str(exc)}",
                        error_category="permission_denied",
                        call_id=call_id,
                        metadata={"elapsed_seconds": time.perf_counter() - start_time},
                    )
            elif resolved.status == ApprovalStatus.DENIED:
                denial_reason = resolved.approver_reason or "Rejected by operator"
                return ToolResultEnvelope.create_error(
                    tool_name=tool_name,
                    error=f"Permission denied by human approver: {denial_reason}",
                    error_category="permission_denied",
                    call_id=call_id,
                    metadata={"elapsed_seconds": time.perf_counter() - start_time},
                )
            elif resolved.status == ApprovalStatus.EXPIRED:
                return ToolResultEnvelope.create_error(
                    tool_name=tool_name,
                    error="Approval request timed out without human authorization.",
                    error_category="approval_timeout",
                    call_id=call_id,
                    metadata={"elapsed_seconds": time.perf_counter() - start_time},
                )
            elif resolved.status == ApprovalStatus.CANCELLED:
                return ToolResultEnvelope.create_error(
                    tool_name=tool_name,
                    error="Approval request was cancelled.",
                    error_category="cancelled",
                    call_id=call_id,
                    metadata={"elapsed_seconds": time.perf_counter() - start_time},
                )
            else:
                return ToolResultEnvelope.create_error(
                    tool_name=tool_name,
                    error=f"Approval request ended in invalid state: {resolved.status.value}",
                    error_category="permission_denied",
                    call_id=call_id,
                    metadata={"elapsed_seconds": time.perf_counter() - start_time},
                )

        # 5. Handler Availability Check
        if tool.handler is None:
            logger.error("Tool '%s' has no executable handler registered", tool_name)
            return ToolResultEnvelope.create_error(
                tool_name=tool_name,
                error=f"Tool '{tool_name}' has no registered execution handler.",
                error_category="missing_handler",
                call_id=call_id,
                metadata={"elapsed_seconds": time.perf_counter() - start_time},
            )

        # 6. Trusted Handler Execution
        handler = tool.handler
        duration_ms: float = 0.0
        try:
            t0 = time.perf_counter()
            if inspect.iscoroutinefunction(handler):
                output = await asyncio.wait_for(handler(**validated_args), timeout=timeout)
            else:
                output = await asyncio.wait_for(
                    asyncio.to_thread(handler, **validated_args),
                    timeout=timeout,
                )
            duration_ms = (time.perf_counter() - t0) * 1000.0

            # 7. Output Normalization & Truncation Protection
            truncated = False
            if isinstance(output, str) and len(output) > self.max_output_chars:
                output = output[: self.max_output_chars] + f"\n... [Output truncated to {self.max_output_chars} chars]"
                truncated = True
            elif isinstance(output, dict) and output.get("truncated") is True:
                truncated = True

            metadata = {
                "duration_ms": duration_ms,
                "truncated": truncated,
                "risk_category": tool.risk_category.value,
            }
            if isinstance(output, dict) and "exit_code" in output:
                metadata["exit_code"] = output["exit_code"]

            logger.info("Executed tool '%s' successfully in %.2fms", tool_name, duration_ms)
            return ToolResultEnvelope.create_success(
                tool_name=tool_name,
                output=output,
                call_id=call_id,
                metadata=metadata,
            )

        except asyncio.TimeoutError:
            duration_ms = (time.perf_counter() - start_time) * 1000.0
            logger.warning("Tool '%s' timed out after %.2fs", tool_name, timeout)
            return ToolResultEnvelope.create_error(
                tool_name=tool_name,
                error=f"Tool '{tool_name}' execution timed out after {timeout} seconds.",
                error_category="timeout",
                call_id=call_id,
                metadata={"duration_ms": duration_ms, "timeout_seconds": timeout},
            )

        except asyncio.CancelledError:
            logger.info("Tool execution '%s' cancelled", tool_name)
            raise

        except Exception as exc:
            duration_ms = (time.perf_counter() - start_time) * 1000.0
            logger.error("Execution error in tool '%s': %s", tool_name, str(exc), exc_info=True)
            return ToolResultEnvelope.create_error(
                tool_name=tool_name,
                error=f"Tool execution failed: {str(exc)}",
                error_category="execution_error",
                call_id=call_id,
                metadata={"duration_ms": duration_ms, "exception_type": type(exc).__name__},
            )
