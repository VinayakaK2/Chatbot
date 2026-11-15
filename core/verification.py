"""Trusted verification subsystem: independent test/build execution, exit code capture, and bounded evidence records."""

import asyncio
from collections import deque
from datetime import datetime, timezone
from enum import Enum
import logging
import os
import re
import signal
import subprocess
import time
from typing import Dict, List, Optional
import uuid

from core.observability import EventType, StructuredEventEmitter, get_correlation_id
from storage.models import VerificationRecord, VerificationStatus, VerificationType
from storage.repositories import VerificationRepository
from tools.security import redact_secrets

logger = logging.getLogger("agent_runtime.core.verification")

# Maximum permitted byte cap for verification output capture (64 KB)
MAX_VERIFICATION_OUTPUT_BYTES = 64 * 1024

# Default pre-approved trusted verification profiles
DEFAULT_VERIFICATION_PROFILES: Dict[str, str] = {
    "test": "pytest",
    "test_unit": "pytest tests/ -k 'not smoke'",
    "build": "npm run build",
    "lint": "ruff check .",
    "typecheck": "mypy .",
}

# Regex detecting trivial fake pass commands that attempt to manufacture an exit code of 0 without running tests
TRIVIAL_PASS_COMMAND_PATTERN = re.compile(
    r"^\s*(echo\b|true\b|exit\s+0\b|printf\b|:|rem\b)",
    re.IGNORECASE,
)

# Regex detecting shell chaining / pipeline injection in raw verification commands
SHELL_CHAINING_PATTERN = re.compile(r"(?:&&|\|\||[;&|`]|(?:\$\())")

# Safe environment variable allowlist for verification subprocesses
SAFE_ENV_KEYS = {
    "PATH",
    "SYSTEMROOT",
    "WINDIR",
    "COMSPEC",
    "PATHEXT",
    "TEMP",
    "TMP",
    "PYTHONPATH",
    "VIRTUAL_ENV",
    "HOME",
    "USERPROFILE",
    "LANG",
    "LC_ALL",
    "TERM",
}


class VerificationOrigin(str, Enum):
    """Categorization of verification command provenance and authorization authority."""

    CONFIGURED_PROFILE = "configured_profile"
    OPERATOR = "operator"
    SYSTEM = "system"


class BoundedStreamCapture:
    """Incrementally consumes subprocess stdout/stderr streams to bound memory consumption at capture time."""

    def __init__(self, max_bytes: int = MAX_VERIFICATION_OUTPUT_BYTES, chunk_size: int = 4096) -> None:
        self.max_bytes = max_bytes
        # Reserve 512 bytes for truncation marker so assembled string stays strictly within max_bytes
        self.half_bytes = max(1024, (max_bytes - 512) // 2)
        self.head_buffer = bytearray()
        self.tail_buffer: deque[int] = deque(maxlen=self.half_bytes)
        self.total_bytes = 0
        self.chunk_size = chunk_size

    async def consume_stream(self, stream: Optional[asyncio.StreamReader]) -> None:
        """Read stream asynchronously in small chunks, capping memory usage to max_bytes at all times."""
        if not stream:
            return

        while True:
            chunk = await stream.read(self.chunk_size)
            if not chunk:
                break
            self.total_bytes += len(chunk)

            # 1. Fill head buffer up to half_bytes
            if len(self.head_buffer) < self.half_bytes:
                needed = self.half_bytes - len(self.head_buffer)
                self.head_buffer.extend(chunk[:needed])
                chunk = chunk[needed:]

            # 2. Append remainder into sliding circular tail buffer
            if chunk:
                self.tail_buffer.extend(chunk)

    def get_bounded_text(self) -> str:
        """Decode and assemble captured head and tail buffers with truncation evidence."""
        head_str = self.head_buffer.decode("utf-8", errors="replace")
        if self.total_bytes <= self.max_bytes:
            tail_str = bytes(self.tail_buffer).decode("utf-8", errors="replace")
            return head_str + tail_str

        omitted = self.total_bytes - len(self.head_buffer) - len(self.tail_buffer)
        tail_str = bytes(self.tail_buffer).decode("utf-8", errors="replace")
        marker = (
            f"\n\n... [STREAMED OUTPUT TRUNCATED: {self.total_bytes} bytes streamed, "
            f"{omitted} bytes discarded at capture-time to bound memory] ...\n\n"
        )
        return head_str + marker + tail_str


def truncate_output_middle(output: str, max_bytes: int = MAX_VERIFICATION_OUTPUT_BYTES) -> str:
    """Middle-truncate stdout/stderr output if exceeding maximum byte threshold."""
    if "[STREAMED OUTPUT TRUNCATED:" in output:
        return output

    encoded = output.encode("utf-8", errors="replace")
    if len(encoded) <= max_bytes:
        return output

    half = (max_bytes - 512) // 2
    prefix = encoded[:half].decode("utf-8", errors="ignore")
    suffix = encoded[-half:].decode("utf-8", errors="ignore")
    marker = f"\n\n... [VERIFICATION OUTPUT TRUNCATED: {len(encoded)} bytes exceeded limit of {max_bytes} bytes] ...\n\n"
    return prefix + marker + suffix


def _build_safe_env(extra_env: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    """Construct a clean, minimal environment allowlist preventing host secret exposure to verification subprocesses."""
    safe_env: Dict[str, str] = {}
    for k in SAFE_ENV_KEYS:
        if k in os.environ:
            safe_env[k] = os.environ[k]
        elif k.upper() in os.environ:
            safe_env[k] = os.environ[k.upper()]

    if extra_env:
        for k, v in extra_env.items():
            if not any(sub in k.upper() for sub in ("KEY", "SECRET", "TOKEN", "PASS")):
                safe_env[k] = v

    return safe_env


def validate_verification_command(
    command: str,
    allow_shell_chaining: bool = False,
) -> None:
    """Validate verification command against security constraints and trivial self-certification attempts.

    Raises:
        ValueError: If command violates trust boundary rules.
    """
    cmd = command.strip()
    if not cmd:
        raise ValueError("Verification command cannot be empty or whitespace.")

    # Rule 1: Reject trivial pass commands
    if TRIVIAL_PASS_COMMAND_PATTERN.search(cmd):
        raise ValueError(
            f"Trivial self-certification commands ('{cmd.split()[0]}') are forbidden as trusted verification commands."
        )

    # Rule 2: Disallow unauthorized shell chaining / script injection
    if not allow_shell_chaining and SHELL_CHAINING_PATTERN.search(cmd):
        raise ValueError(
            "Shell chaining (&&, ||, ;, |, `) is forbidden in verification commands without explicit script encapsulation."
        )


async def _terminate_process_tree(proc: asyncio.subprocess.Process) -> None:
    """Safely terminate a subprocess and all its descendant processes across Windows and POSIX."""
    if proc.returncode is not None:
        return

    pid = proc.pid
    try:
        if os.name == "nt":
            # On Windows, taskkill /F /T terminates the process and all child/grandchild processes
            kill_proc = await asyncio.create_subprocess_exec(
                "taskkill", "/F", "/T", "/PID", str(pid),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            try:
                await asyncio.wait_for(kill_proc.wait(), timeout=3.0)
            except asyncio.TimeoutError:
                pass
        else:
            # On POSIX, kill process group if possible
            try:
                os.killpg(os.getpgid(pid), signal.SIGKILL)
            except Exception:
                proc.kill()
    except Exception as e:
        logger.debug("Exception terminating subprocess PID %s: %s", pid, str(e))
        try:
            proc.kill()
        except Exception:
            pass

    # Final verification: check if process is still running
    try:
        os.kill(pid, 0)
        # If still alive, attempt direct kill
        proc.kill()
    except OSError:
        pass  # Process has terminated cleanly


class VerificationRunner:
    """Executes verification commands independently in controlled subprocesses and records verified evidence."""

    def __init__(
        self,
        verification_repo: VerificationRepository,
        event_emitter: Optional[StructuredEventEmitter] = None,
        default_timeout_seconds: float = 60.0,
        extra_secrets: Optional[List[str]] = None,
        profiles: Optional[Dict[str, str]] = None,
    ) -> None:
        self.verification_repo = verification_repo
        self.event_emitter = event_emitter
        self.default_timeout_seconds = max(1.0, default_timeout_seconds)
        self.extra_secrets = extra_secrets or []
        self.profiles = dict(DEFAULT_VERIFICATION_PROFILES)
        if profiles:
            self.profiles.update(profiles)

    async def run_verification(
        self,
        task_id: str,
        command: Optional[str] = None,
        profile_name: Optional[str] = None,
        origin: VerificationOrigin = VerificationOrigin.OPERATOR,
        authorized_by: Optional[str] = None,
        verification_type: VerificationType = VerificationType.TEST,
        cwd: Optional[str] = None,
        timeout_seconds: Optional[float] = None,
        correlation_id: Optional[str] = None,
        cancellation_token: Optional[asyncio.Event] = None,
    ) -> VerificationRecord:
        """Execute a verification command in a subprocess, capture exit code and bounded output, and persist record.

        Args:
            task_id: Associated task UUID.
            command: Shell command line to execute.
            profile_name: Pre-approved profile name (e.g. 'test', 'build'). Takes precedence over raw command.
            origin: Command provenance (OPERATOR, CONFIGURED_PROFILE, SYSTEM).
            authorized_by: Explicit authorizer identity.
            verification_type: Category of check (TEST, BUILD, LINT, TYPECHECK, CUSTOM).
            cwd: Working directory for subprocess.
            timeout_seconds: Timeout limit in seconds.
            correlation_id: Associated trace correlation ID.
            cancellation_token: Event signaling user cancellation.

        Returns:
            VerificationRecord: Authoritative persisted verification record with classified result.
        """
        effective_timeout = timeout_seconds or self.default_timeout_seconds
        effective_cid = correlation_id or get_correlation_id()
        record_id = str(uuid.uuid4())

        # 1. Resolve command from profile or validate raw command
        resolved_command = command
        effective_origin = origin
        if profile_name:
            if profile_name not in self.profiles:
                raise ValueError(
                    f"Unknown verification profile '{profile_name}'. Must be one of: {list(self.profiles.keys())}"
                )
            resolved_command = self.profiles[profile_name]
            effective_origin = VerificationOrigin.CONFIGURED_PROFILE

        if not resolved_command:
            raise ValueError("Either command or profile_name must be provided for verification.")

        # Validate command against trivial self-certification and injection
        validate_verification_command(
            resolved_command,
            allow_shell_chaining=(effective_origin == VerificationOrigin.OPERATOR),
        )

        logger.info(
            "Starting verification [%s] (origin=%s) for task %s: '%s' (timeout=%.1fs)",
            verification_type.value,
            effective_origin.value,
            task_id,
            resolved_command,
            effective_timeout,
        )

        if self.event_emitter:
            self.event_emitter.emit(
                EventType.VERIFICATION_STARTED,
                task_id=task_id,
                correlation_id=effective_cid,
                payload={
                    "verification_id": record_id,
                    "verification_type": verification_type.value,
                    "command": resolved_command,
                    "origin": effective_origin.value,
                    "profile_name": profile_name,
                    "timeout_seconds": effective_timeout,
                },
            )

        start_time = time.monotonic()
        exit_code: Optional[int] = None
        raw_output = ""
        result = VerificationStatus.NOT_RUN

        # Check early cancellation
        if cancellation_token and cancellation_token.is_set():
            result = VerificationStatus.FAILED
            raw_output = "ERROR: Verification execution was cancelled before start."
            exit_code = -1
        else:
            try:
                # Spawn subprocess with clean environment allowlist
                safe_env = _build_safe_env()
                proc = await asyncio.create_subprocess_shell(
                    resolved_command,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                    cwd=cwd,
                    env=safe_env,
                )

                # Initialize capture-time streaming memory bound
                stream_capture = BoundedStreamCapture(max_bytes=MAX_VERIFICATION_OUTPUT_BYTES)

                async def _consume_and_wait() -> int:
                    await stream_capture.consume_stream(proc.stdout)
                    return await proc.wait()

                consume_task = asyncio.create_task(_consume_and_wait())

                # Monitor with timeout and cancellation
                wait_tasks: List[asyncio.Task] = [consume_task]
                cancel_task: Optional[asyncio.Task] = None
                if cancellation_token:
                    cancel_task = asyncio.create_task(cancellation_token.wait())
                    wait_tasks.append(cancel_task)

                try:
                    done, pending = await asyncio.wait(
                        wait_tasks,
                        timeout=effective_timeout,
                        return_when=asyncio.FIRST_COMPLETED,
                    )

                    if cancel_task and cancel_task in done:
                        # Cancelled!
                        logger.warning("Verification command cancelled by operator: %s", resolved_command)
                        await _terminate_process_tree(proc)
                        consume_task.cancel()
                        exit_code = -1
                        result = VerificationStatus.FAILED
                        raw_output = "ERROR: Verification execution was cancelled by user."
                    elif consume_task in done:
                        exit_code = consume_task.result()
                        raw_output = stream_capture.get_bounded_text()
                        result = VerificationStatus.PASSED if exit_code == 0 else VerificationStatus.FAILED
                    else:
                        # Timeout!
                        logger.warning("Verification command timed out after %.1fs: %s", effective_timeout, resolved_command)
                        await _terminate_process_tree(proc)
                        consume_task.cancel()
                        exit_code = -1
                        raw_output = (
                            stream_capture.get_bounded_text()
                            + f"\nERROR: Verification execution exceeded timeout limit of {effective_timeout:.1f}s and was terminated."
                        )
                        result = VerificationStatus.FAILED
                finally:
                    for t in pending:
                        t.cancel()

            except Exception as exc:
                logger.error("Failed to execute verification command '%s': %s", resolved_command, str(exc))
                exit_code = -1
                raw_output = f"ERROR: Subprocess execution failed: {str(exc)}"
                result = VerificationStatus.BLOCKED

        duration_ms = (time.monotonic() - start_time) * 1000.0

        # Scrub secrets and cap output length
        clean_output = redact_secrets(raw_output, extra_secrets=self.extra_secrets)
        bounded_output = truncate_output_middle(clean_output)

        record = VerificationRecord(
            id=record_id,
            task_id=task_id,
            verification_type=verification_type,
            command=resolved_command,
            origin=effective_origin.value,
            profile_name=profile_name,
            authorized_by=authorized_by,
            exit_code=exit_code,
            result=result,
            output_excerpt=bounded_output,
            duration_ms=round(duration_ms, 2),
            correlation_id=effective_cid,
            created_at=datetime.now(timezone.utc).isoformat(),
        )

        # Persist record authoritatively
        persisted = self.verification_repo.create(record)

        if self.event_emitter:
            self.event_emitter.emit(
                EventType.VERIFICATION_COMPLETED,
                task_id=task_id,
                correlation_id=effective_cid,
                payload={
                    "verification_id": record.id,
                    "verification_type": verification_type.value,
                    "command": resolved_command,
                    "origin": effective_origin.value,
                    "profile_name": profile_name,
                    "result": result.value,
                    "exit_code": exit_code,
                    "duration_ms": duration_ms,
                },
            )

        logger.info(
            "Verification [%s] finished for task %s: result=%s, exit_code=%s, duration=%.1fms",
            verification_type.value,
            task_id,
            result.value,
            str(exit_code),
            duration_ms,
        )

        return persisted

    def get_latest_status(self, task_id: str) -> VerificationStatus:
        """Query the latest verification result for a task, returning NOT_RUN if no checks exist."""
        records = self.verification_repo.list_for_task(task_id)
        if not records:
            return VerificationStatus.NOT_RUN
        return records[-1].result
