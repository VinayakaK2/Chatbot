"""Trusted verification subsystem: independent test/build execution, exit code capture, and bounded evidence records."""

import asyncio
from datetime import datetime, timezone
import logging
import os
import signal
import subprocess
import time
from typing import List, Optional
import uuid

from core.observability import EventType, StructuredEventEmitter, get_correlation_id
from storage.models import VerificationRecord, VerificationStatus, VerificationType
from storage.repositories import VerificationRepository
from tools.security import redact_secrets

logger = logging.getLogger("agent_runtime.core.verification")

# Maximum permitted byte cap for verification output capture (64 KB)
MAX_VERIFICATION_OUTPUT_BYTES = 64 * 1024


def truncate_output_middle(output: str, max_bytes: int = MAX_VERIFICATION_OUTPUT_BYTES) -> str:
    """Middle-truncate stdout/stderr output if exceeding maximum byte threshold."""
    encoded = output.encode("utf-8", errors="replace")
    if len(encoded) <= max_bytes:
        return output

    half = max_bytes // 2
    prefix = encoded[:half].decode("utf-8", errors="ignore")
    suffix = encoded[-half:].decode("utf-8", errors="ignore")
    marker = f"\n\n... [VERIFICATION OUTPUT TRUNCATED: {len(encoded)} bytes exceeded limit of {max_bytes} bytes] ...\n\n"
    return prefix + marker + suffix


async def _terminate_process_tree(proc: asyncio.subprocess.Process) -> None:
    """Safely terminate a subprocess and its child process tree across Windows and POSIX."""
    if proc.returncode is not None:
        return

    pid = proc.pid
    try:
        if os.name == "nt":
            # On Windows, taskkill /F /T kills the process and all child processes
            kill_proc = await asyncio.create_subprocess_exec(
                "taskkill", "/F", "/T", "/PID", str(pid),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            await kill_proc.wait()
        else:
            # On POSIX, kill process group if possible, else terminate process
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


class VerificationRunner:
    """Executes verification commands independently in controlled subprocesses and records verified evidence."""

    def __init__(
        self,
        verification_repo: VerificationRepository,
        event_emitter: Optional[StructuredEventEmitter] = None,
        default_timeout_seconds: float = 60.0,
        extra_secrets: Optional[List[str]] = None,
    ) -> None:
        self.verification_repo = verification_repo
        self.event_emitter = event_emitter
        self.default_timeout_seconds = max(1.0, default_timeout_seconds)
        self.extra_secrets = extra_secrets or []

    async def run_verification(
        self,
        task_id: str,
        command: str,
        verification_type: VerificationType = VerificationType.TEST,
        cwd: Optional[str] = None,
        timeout_seconds: Optional[float] = None,
        correlation_id: Optional[str] = None,
    ) -> VerificationRecord:
        """Execute a verification command in a subprocess, capture exit code and bounded output, and persist record.

        Args:
            task_id: Associated task UUID.
            command: Shell command to execute (e.g. 'pytest -q tests/test_foo.py' or 'npm test').
            verification_type: Category of check (TEST, BUILD, LINT, TYPECHECK, CUSTOM).
            cwd: Working directory for subprocess.
            timeout_seconds: Timeout limit in seconds.
            correlation_id: Associated trace correlation ID.

        Returns:
            VerificationRecord: Authoritative persisted verification record with classified result.
        """
        effective_timeout = timeout_seconds or self.default_timeout_seconds
        effective_cid = correlation_id or get_correlation_id()
        record_id = str(uuid.uuid4())

        logger.info(
            "Starting verification [%s] for task %s: '%s' (timeout=%.1fs)",
            verification_type.value,
            task_id,
            command,
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
                    "command": command,
                    "timeout_seconds": effective_timeout,
                },
            )

        start_time = time.monotonic()
        exit_code: Optional[int] = None
        raw_output = ""
        result = VerificationStatus.NOT_RUN

        try:
            # Spawn subprocess
            proc = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                cwd=cwd,
            )

            try:
                stdout_bytes, _ = await asyncio.wait_for(proc.communicate(), timeout=effective_timeout)
                exit_code = proc.returncode
                raw_output = stdout_bytes.decode("utf-8", errors="replace") if stdout_bytes else ""
                result = VerificationStatus.PASSED if exit_code == 0 else VerificationStatus.FAILED
            except asyncio.TimeoutError:
                logger.warning("Verification command timed out after %.1fs: %s", effective_timeout, command)
                await _terminate_process_tree(proc)
                exit_code = -1
                raw_output = f"ERROR: Verification execution exceeded timeout limit of {effective_timeout:.1f}s and was terminated."
                result = VerificationStatus.FAILED
        except Exception as exc:
            logger.error("Failed to execute verification command '%s': %s", command, str(exc))
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
            command=command,
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
                    "command": command,
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
