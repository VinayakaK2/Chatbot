"""Observability subsystem: correlation ID tracking, structured lifecycle events, and bounded logging."""

from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
import json
import logging
from typing import Any, Callable, Dict, Generator, List, Optional
import uuid

from storage.models import PersistedEventRecord
from storage.repositories import RuntimeEventRepository
from tools.security import redact_secrets

logger = logging.getLogger("agent_runtime.core.observability")

# Maximum permitted payload size for a structured event (64 KB)
MAX_EVENT_PAYLOAD_BYTES = 64 * 1024

# Context variables for ambient correlation tracking across asynchronous tasks
_current_correlation_id: ContextVar[Optional[str]] = ContextVar("correlation_id", default=None)
_current_task_id: ContextVar[Optional[str]] = ContextVar("task_id", default=None)
_current_session_id: ContextVar[Optional[str]] = ContextVar("session_id", default=None)


class EventType:
    """Standardized dot-delimited lifecycle event names."""

    SESSION_STARTED = "session.started"
    SESSION_COMPLETED = "session.completed"
    TASK_SUBMITTED = "task.submitted"
    TASK_STARTED = "task.started"
    TASK_COMPLETED = "task.completed"
    TASK_FAILED = "task.failed"
    TASK_CANCELLED = "task.cancelled"
    MODEL_REQUEST_STARTED = "model.request.started"
    MODEL_REQUEST_COMPLETED = "model.request.completed"
    MODEL_REQUEST_FAILED = "model.request.failed"
    MODEL_RETRY_SCHEDULED = "model.retry.scheduled"
    TOOL_DISPATCH_STARTED = "tool.dispatch.started"
    TOOL_DISPATCH_COMPLETED = "tool.dispatch.completed"
    TOOL_DISPATCH_FAILED = "tool.dispatch.failed"
    TOOL_LOOP_DETECTED = "tool.loop.detected"
    VERIFICATION_STARTED = "verification.started"
    VERIFICATION_COMPLETED = "verification.completed"


def get_correlation_id() -> str:
    """Return the current context correlation ID, or generate and bind a fresh UUID if unset."""
    cid = _current_correlation_id.get()
    if not cid:
        cid = str(uuid.uuid4())
        _current_correlation_id.set(cid)
    return cid


def get_current_task_id() -> Optional[str]:
    """Return the ambient task ID if bound in current execution context."""
    return _current_task_id.get()


def get_current_session_id() -> Optional[str]:
    """Return the ambient session ID if bound in current execution context."""
    return _current_session_id.get()


@contextmanager
def correlation_context(
    correlation_id: Optional[str] = None,
    task_id: Optional[str] = None,
    session_id: Optional[str] = None,
) -> Generator[str, None, None]:
    """Context manager binding correlation and task IDs to current async task."""
    effective_cid = correlation_id or str(uuid.uuid4())
    token_cid = _current_correlation_id.set(effective_cid)
    token_task = _current_task_id.set(task_id) if task_id else None
    token_session = _current_session_id.set(session_id) if session_id else None
    try:
        yield effective_cid
    finally:
        _current_correlation_id.reset(token_cid)
        if token_task:
            _current_task_id.reset(token_task)
        if token_session:
            _current_session_id.reset(token_session)


def bound_and_scrub_payload(payload: Dict[str, Any], extra_secrets: Optional[List[str]] = None) -> str:
    """Serialize payload to JSON with secret scrubbing and hard byte cap."""
    try:
        raw_json = json.dumps(payload, default=str)
    except Exception:
        raw_json = str(payload)

    scrubbed = redact_secrets(raw_json, extra_secrets=extra_secrets)
    if len(scrubbed.encode("utf-8")) > MAX_EVENT_PAYLOAD_BYTES:
        keep = MAX_EVENT_PAYLOAD_BYTES // 2
        scrubbed = (
            scrubbed[:keep]
            + f"\n... [PAYLOAD TRUNCATED: exceeds {MAX_EVENT_PAYLOAD_BYTES} bytes] ...\n"
            + scrubbed[-keep:]
        )
    return scrubbed


class StructuredEventEmitter:
    """Event emitter persisting structured lifecycle records to SQLite and notifying subscribers."""

    def __init__(
        self,
        event_repo: Optional[RuntimeEventRepository] = None,
        extra_secrets: Optional[List[str]] = None,
    ) -> None:
        self.event_repo = event_repo
        self.extra_secrets = extra_secrets or []
        self._listeners: List[Callable[[PersistedEventRecord], None]] = []

    def add_listener(self, listener: Callable[[PersistedEventRecord], None]) -> None:
        """Register a synchronous or thread-safe callback for live event delivery."""
        if listener not in self._listeners:
            self._listeners.append(listener)

    def remove_listener(self, listener: Callable[[PersistedEventRecord], None]) -> None:
        """Unregister an event listener."""
        if listener in self._listeners:
            self._listeners.remove(listener)

    def emit(
        self,
        event_type: str,
        task_id: Optional[str] = None,
        session_id: Optional[str] = None,
        correlation_id: Optional[str] = None,
        payload: Optional[Dict[str, Any]] = None,
        parent_event_id: Optional[str] = None,
    ) -> PersistedEventRecord:
        """Create, persist, log, and broadcast a structured lifecycle event."""
        effective_cid = correlation_id or get_correlation_id()
        effective_task_id = task_id or get_current_task_id()
        effective_session_id = session_id or get_current_session_id()

        payload_dict = payload or {}
        scrubbed_payload_json = bound_and_scrub_payload(payload_dict, extra_secrets=self.extra_secrets)

        event = PersistedEventRecord(
            id=str(uuid.uuid4()),
            event_type=event_type,
            task_id=effective_task_id,
            session_id=effective_session_id,
            correlation_id=effective_cid,
            parent_event_id=parent_event_id,
            payload_json=scrubbed_payload_json,
            created_at=datetime.now(timezone.utc).isoformat(),
        )

        logger.info(
            "EVENT [%s] task=%s corr=%s: %s",
            event.event_type,
            event.task_id or "-",
            event.correlation_id or "-",
            scrubbed_payload_json[:180],
        )

        if self.event_repo:
            try:
                self.event_repo.append_event(event)
            except Exception as e:
                logger.error("Failed to persist event %s to SQLite: %s", event.id, str(e))

        for listener in list(self._listeners):
            try:
                listener(event)
            except Exception as e:
                logger.error("Error invoking event listener for %s: %s", event_type, str(e))

        return event
