"""Constrained PowerShell subprocess runner for project tests and development commands.

Implements Section 5 of 2604.14228v3.pdf and Phase 5 of docs/phases.md:
- Explicit canonical working directory containment inside designated workspace
- Clean environment variable allowlist (stripping sensitive API keys and tokens)
- Bounded stdout and stderr capture with explicit truncation flags
- Accurate exit-code preservation (0, nonzero, or timeout)
- Windows process-tree termination via taskkill.exe /F /T /PID
- Secret-aware output redaction
- Tool registration for 'execute_command' with ToolRiskCategory.COMMAND_EXECUTION
"""

import asyncio
import logging
import os
import re
import sys
from typing import Any, Dict, List, Optional, Set
from pydantic import BaseModel, Field

from core.tool_contracts import ToolDefinition, ToolRiskCategory
from core.workspace import WorkspaceContext
from tools.security import SECRET_PATTERNS, redact_secrets

logger = logging.getLogger("agent_runtime.tools.shell")

# Maximum output characters for stdout and stderr before truncation
DEFAULT_MAX_OUTPUT_CHARS: int = 65_536

# Safe Windows environment variable allowlist
SAFE_ENV_VARS: Set[str] = {
    "SYSTEMROOT",
    "SYSTEMDRIVE",
    "COMSPEC",
    "PATH",
    "PATHEXT",
    "TEMP",
    "TMP",
    "USERPROFILE",
    "LOCALAPPDATA",
    "APPDATA",
    "ALLUSERSPROFILE",
    "PROGRAMDATA",
    "NUMBER_OF_PROCESSORS",
    "OS",
    "PROCESSOR_ARCHITECTURE",
    "PROCESSOR_IDENTIFIER",
    "PROCESSOR_LEVEL",
    "PROCESSOR_REVISION",
}

class CommandExecutionResult(BaseModel):
    """Structured result returned by the PowerShell subprocess runner."""

    command: str = Field(..., description="Executed command string")
    working_dir: str = Field(..., description="Working directory where command was executed")
    exit_code: int = Field(..., description="Subprocess return code (-1 indicates timeout or kill)")
    stdout: str = Field(default="", description="Captured standard output")
    stderr: str = Field(default="", description="Captured standard error")
    duration_ms: float = Field(default=0.0, description="Total execution time in milliseconds")
    truncated: bool = Field(default=False, description="Whether output exceeded maximum character limit")
    timed_out: bool = Field(default=False, description="Whether process exceeded timeout and was terminated")


def get_sanitized_environment(workspace_root: str) -> Dict[str, str]:
    """Construct a clean, minimal environment dictionary for subprocess execution."""
    sanitized: Dict[str, str] = {}

    for key, value in os.environ.items():
        if key.upper() in SAFE_ENV_VARS:
            sanitized[key] = value

    # If project workspace has a .venv, ensure it is prepended to PATH
    venv_scripts = os.path.join(workspace_root, ".venv", "Scripts")
    if os.path.isdir(venv_scripts):
        current_path = sanitized.get("PATH", "")
        if venv_scripts.lower() not in current_path.lower():
            sanitized["PATH"] = f"{venv_scripts};{current_path}"
        sanitized["VIRTUAL_ENV"] = os.path.join(workspace_root, ".venv")

    # Set Python unbuffered output for clean streaming
    sanitized["PYTHONUNBUFFERED"] = "1"
    sanitized["PYTHONDONTWRITEBYTECODE"] = "1"

    return sanitized


class PowerShellRunner:
    """Manages constrained PowerShell subprocess execution with process tree termination and safety checks."""

    def __init__(
        self,
        workspace: WorkspaceContext,
        default_timeout_seconds: float = 30.0,
        max_output_chars: int = DEFAULT_MAX_OUTPUT_CHARS,
        extra_secrets: Optional[List[str]] = None,
    ) -> None:
        """Initialize runner with workspace context and output bounds."""
        self.workspace = workspace
        self.default_timeout_seconds = default_timeout_seconds
        self.max_output_chars = max_output_chars
        self.extra_secrets = extra_secrets or []

    def validate_working_dir(self, working_dir: Optional[str] = None) -> str:
        """Validate and resolve target working directory within the workspace boundaries."""
        workspace_root = str(self.workspace.root_path)
        if not working_dir:
            return workspace_root

        # Resolve realpath
        canonical_target = os.path.realpath(
            os.path.abspath(
                working_dir if os.path.isabs(working_dir) else os.path.join(workspace_root, working_dir)
            )
        )

        # Boundary containment check
        try:
            common = os.path.commonpath([workspace_root, canonical_target])
            if common != workspace_root:
                raise ValueError(
                    f"Target working directory '{working_dir}' resolves to '{canonical_target}', "
                    f"which is outside the workspace root '{workspace_root}'."
                )
        except ValueError as exc:
            raise ValueError(f"Invalid working directory '{working_dir}': {str(exc)}") from exc

        if not os.path.isdir(canonical_target):
            raise ValueError(f"Target working directory '{working_dir}' does not exist on disk.")

        return str(canonical_target)

    async def _terminate_process_tree(self, pid: int) -> None:
        """Forcibly terminate a Windows process and all its spawned child processes via taskkill."""
        if sys.platform != "win32":
            try:
                os.kill(pid, 9)
            except Exception:
                pass
            return

        logger.info("Terminating process tree for PID %d via taskkill /F /T", pid)
        try:
            kill_proc = await asyncio.create_subprocess_exec(
                "taskkill.exe",
                "/F",
                "/T",
                "/PID",
                str(pid),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await asyncio.wait_for(kill_proc.communicate(), timeout=5.0)
        except Exception as exc:
            logger.warning("taskkill failed for PID %d: %s", pid, str(exc))

    async def run(
        self,
        command: str,
        working_dir: Optional[str] = None,
        timeout_seconds: Optional[float] = None,
    ) -> CommandExecutionResult:
        """Execute command in a constrained PowerShell subprocess with bounded buffers and timeout."""
        canonical_cwd = self.validate_working_dir(working_dir)
        timeout = timeout_seconds if timeout_seconds is not None else self.default_timeout_seconds
        env = get_sanitized_environment(self.workspace.root_path)

        cmd_args = [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            command,
        ]

        logger.info("Spawning subprocess in '%s' with timeout %.1fs: %s", canonical_cwd, timeout, command)
        loop = asyncio.get_running_loop()
        start_time = loop.time()

        proc = await asyncio.create_subprocess_exec(
            *cmd_args,
            cwd=canonical_cwd,
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        stdout_bytes = b""
        stderr_bytes = b""
        timed_out = False
        truncated = False

        try:
            stdout_data, stderr_data = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            stdout_bytes = stdout_data or b""
            stderr_bytes = stderr_data or b""
            exit_code = proc.returncode if proc.returncode is not None else 0
        except asyncio.TimeoutError:
            logger.warning("Command '%s' timed out after %.1fs (PID %d)", command, timeout, proc.pid)
            timed_out = True
            exit_code = -1
            await self._terminate_process_tree(proc.pid)
            try:
                # Flush remaining output if any
                extra_out, extra_err = await asyncio.wait_for(proc.communicate(), timeout=2.0)
                stdout_bytes += extra_out or b""
                stderr_bytes += extra_err or b""
            except Exception:
                pass
        except asyncio.CancelledError:
            logger.info("Subprocess command '%s' cancelled (PID %d)", command, proc.pid)
            await self._terminate_process_tree(proc.pid)
            raise

        duration_ms = (loop.time() - start_time) * 1000.0

        # Decode output with error replacement
        raw_stdout = stdout_bytes.decode("utf-8", errors="replace")
        raw_stderr = stderr_bytes.decode("utf-8", errors="replace")

        # Truncation check
        if len(raw_stdout) > self.max_output_chars:
            raw_stdout = raw_stdout[: self.max_output_chars] + f"\n... [Output truncated to {self.max_output_chars} chars]"
            truncated = True

        if len(raw_stderr) > self.max_output_chars:
            raw_stderr = raw_stderr[: self.max_output_chars] + f"\n... [Stderr truncated to {self.max_output_chars} chars]"
            truncated = True

        # Redact secrets
        clean_stdout = redact_secrets(raw_stdout, self.extra_secrets)
        clean_stderr = redact_secrets(raw_stderr, self.extra_secrets)

        return CommandExecutionResult(
            command=command,
            working_dir=canonical_cwd,
            exit_code=exit_code,
            stdout=clean_stdout,
            stderr=clean_stderr,
            duration_ms=duration_ms,
            truncated=truncated,
            timed_out=timed_out,
        )


# ==============================================================================
# Tool Input Contract and Factory
# ==============================================================================


class ExecuteCommandInput(BaseModel):
    """Schema for execute_command tool requests."""

    command: str = Field(..., description="Shell command string to execute in project workspace")
    working_dir: Optional[str] = Field(
        default=None,
        description="Optional relative or absolute directory inside workspace root",
    )
    timeout_seconds: Optional[float] = Field(
        default=None,
        ge=1.0,
        le=300.0,
        description="Optional wall-clock timeout in seconds (default 30.0)",
    )


def create_shell_tools(
    workspace: WorkspaceContext,
    default_timeout_seconds: float = 30.0,
    extra_secrets: Optional[List[str]] = None,
) -> List[ToolDefinition]:
    """Construct and return the Phase 5 execute_command tool definition."""
    runner = PowerShellRunner(
        workspace=workspace,
        default_timeout_seconds=default_timeout_seconds,
        extra_secrets=extra_secrets,
    )

    async def execute_command_handler(
        command: str,
        working_dir: Optional[str] = None,
        timeout_seconds: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Execute a development command within the constrained PowerShell runner."""
        result = await runner.run(
            command=command,
            working_dir=working_dir,
            timeout_seconds=timeout_seconds,
        )
        return {
            "command": result.command,
            "working_dir": result.working_dir,
            "exit_code": result.exit_code,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "duration_ms": result.duration_ms,
            "truncated": result.truncated,
            "timed_out": result.timed_out,
        }

    execute_command_tool = ToolDefinition(
        name="execute_command",
        description=(
            "Execute a project test, build, lint, or development command in a constrained PowerShell "
            "subprocess with working directory and environment controls. Requires human approval."
        ),
        parameters_schema=ExecuteCommandInput.model_json_schema(),
        handler=execute_command_handler,
        risk_category=ToolRiskCategory.COMMAND_EXECUTION,
    )

    return [execute_command_tool]
