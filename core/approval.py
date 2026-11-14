"""Runtime-owned human approval lifecycle management and exact-action binding.

Implements Section 5.1 & 9.2 of 2604.14228v3.pdf and Phase 5 of docs/phases.md:
- In-memory volatile approval store (zero persistence across restarts)
- Exact-action binding via deterministic cryptographic hash (SHA-256)
- Single-use token consumption (APPROVED -> CONSUMED)
- Thread-safe & asyncio-compatible synchronization (resolving OBS-02)
- State transitions: PENDING -> APPROVED / DENIED / EXPIRED / CANCELLED -> CONSUMED
"""

import asyncio
from datetime import datetime, timezone
from enum import Enum
import hashlib
import json
import logging
import threading
from typing import Any, Dict, List, Optional
import uuid
from pydantic import BaseModel, ConfigDict, Field

from core.tool_contracts import ToolRiskCategory

logger = logging.getLogger("agent_runtime.core.approval")

# Default Time-to-Live for human approval requests before automatic expiration
DEFAULT_APPROVAL_TTL_SECONDS: float = 300.0


class ApprovalStatus(str, Enum):
    """Lifecycle states for human decision authority requests."""

    PENDING = "pending"
    APPROVED = "approved"
    DENIED = "denied"
    EXPIRED = "expired"
    CANCELLED = "cancelled"
    CONSUMED = "consumed"


class ApprovalError(Exception):
    """Base exception for approval lifecycle and validation failures."""

    def __init__(self, message: str) -> None:
        """Initialize approval error with diagnostic message."""
        super().__init__(message)
        self.message = message


class ApprovalNotFoundError(ApprovalError):
    """Raised when querying or resolving a non-existent approval request."""


class ApprovalExpiredError(ApprovalError):
    """Raised when attempting to resolve or consume an expired approval."""


class ApprovalAlreadyConsumedError(ApprovalError):
    """Raised when an approval token is presented more than once."""


class InvalidApprovalTokenError(ApprovalError):
    """Raised when an approval token's action hash fails cryptographic verification."""


def compute_action_hash(
    task_id: str,
    tool_name: str,
    arguments: Dict[str, Any],
    working_dir: Optional[str] = None,
) -> str:
    """Compute a deterministic SHA-256 hash binding an approval to exact invocation parameters."""
    normalized_cwd = (working_dir or "").strip().lower()
    canonical_args = json.dumps(arguments, sort_keys=True, separators=(",", ":"))
    payload = f"{task_id}|{tool_name}|{normalized_cwd}|{canonical_args}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


class ApprovalRequest(BaseModel):
    """Structured record encapsulating a pending or resolved tool approval request."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    request_id: str = Field(..., description="Unique UUID for this approval request")
    task_id: str = Field(..., description="Task to which this approval belongs")
    call_id: str = Field(..., description="Tool call identifier from the agent loop")
    tool_name: str = Field(..., description="Name of the tool requiring authorization")
    arguments: Dict[str, Any] = Field(default_factory=dict, description="Raw arguments proposed for the tool")
    command: Optional[str] = Field(default=None, description="Shell command string if applicable")
    working_dir: Optional[str] = Field(default=None, description="Target working directory if applicable")
    risk_category: ToolRiskCategory = Field(..., description="Risk tier of the proposed operation")
    reason: str = Field(..., description="Policy assessment explaining why approval is required")
    action_hash: str = Field(..., description="SHA-256 hash binding this approval to its exact parameters")
    status: ApprovalStatus = Field(default=ApprovalStatus.PENDING, description="Current lifecycle state")
    created_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(),
        description="ISO 8601 UTC timestamp of creation",
    )
    expires_at: str = Field(..., description="ISO 8601 UTC timestamp when this request expires")
    decided_at: Optional[str] = Field(default=None, description="ISO 8601 UTC timestamp of decision")
    approver_reason: Optional[str] = Field(default=None, description="Optional explanation from the human approver")

    def is_expired(self) -> bool:
        """Check whether the approval request has exceeded its Time-to-Live."""
        try:
            exp = datetime.fromisoformat(self.expires_at)
            return datetime.now(timezone.utc) >= exp
        except Exception:
            return False


class ApprovalManager:
    """Thread-safe and asyncio-compatible in-memory manager for human tool execution approvals."""

    def __init__(self, default_ttl_seconds: float = DEFAULT_APPROVAL_TTL_SECONDS) -> None:
        """Initialize in-memory volatile approval store with locking infrastructure."""
        self.default_ttl_seconds = default_ttl_seconds
        self._requests: Dict[str, ApprovalRequest] = {}
        # Task ID -> Call ID -> Request ID lookup
        self._task_call_map: Dict[str, Dict[str, str]] = {}
        # Request ID -> asyncio.Event
        self._events: Dict[str, asyncio.Event] = {}
        self._lock = threading.RLock()

    def create_request(
        self,
        task_id: str,
        call_id: str,
        tool_name: str,
        arguments: Dict[str, Any],
        risk_category: ToolRiskCategory,
        reason: str,
        working_dir: Optional[str] = None,
        command: Optional[str] = None,
        ttl_seconds: Optional[float] = None,
    ) -> ApprovalRequest:
        """Create and register a new pending approval request with exact-action hash binding."""
        ttl = ttl_seconds if ttl_seconds is not None else self.default_ttl_seconds
        now_dt = datetime.now(timezone.utc)
        exp_dt = datetime.fromtimestamp(now_dt.timestamp() + ttl, tz=timezone.utc)

        action_hash = compute_action_hash(
            task_id=task_id,
            tool_name=tool_name,
            arguments=arguments,
            working_dir=working_dir,
        )

        req_id = str(uuid.uuid4())
        request = ApprovalRequest(
            request_id=req_id,
            task_id=task_id,
            call_id=call_id,
            tool_name=tool_name,
            arguments=arguments,
            command=command,
            working_dir=working_dir,
            risk_category=risk_category,
            reason=reason,
            action_hash=action_hash,
            status=ApprovalStatus.PENDING,
            created_at=now_dt.isoformat(),
            expires_at=exp_dt.isoformat(),
        )

        with self._lock:
            self._requests[req_id] = request
            if task_id not in self._task_call_map:
                self._task_call_map[task_id] = {}
            self._task_call_map[task_id][call_id] = req_id
            self._events[req_id] = asyncio.Event()

        logger.info(
            "Created approval request '%s' for task '%s' call '%s' [tool=%s, risk=%s]",
            req_id,
            task_id,
            call_id,
            tool_name,
            risk_category.value,
        )
        return request

    def get_request(self, request_id: str) -> Optional[ApprovalRequest]:
        """Retrieve approval request by its unique UUID, lazily evaluating expiration."""
        with self._lock:
            req = self._requests.get(request_id)
            if req and req.status == ApprovalStatus.PENDING and req.is_expired():
                # Transition to EXPIRED
                updated = ApprovalRequest(
                    **{
                        **req.model_dump(),
                        "status": ApprovalStatus.EXPIRED,
                        "decided_at": datetime.now(timezone.utc).isoformat(),
                    }
                )
                self._requests[request_id] = updated
                req = updated
                ev = self._events.get(request_id)
                if ev:
                    ev.set()
            return req

    def get_request_by_call(self, task_id: str, call_id: str) -> Optional[ApprovalRequest]:
        """Look up approval request by task ID and tool call ID."""
        with self._lock:
            calls = self._task_call_map.get(task_id, {})
            req_id = calls.get(call_id)
            if not req_id:
                return None
            return self.get_request(req_id)

    def resolve_request(
        self,
        call_id_or_request_id: str,
        approved: bool,
        reason: Optional[str] = None,
        task_id: Optional[str] = None,
    ) -> ApprovalRequest:
        """Record human approval or rejection for a pending request and unblock any waiting listeners."""
        with self._lock:
            req_id: Optional[str] = None
            if call_id_or_request_id in self._requests:
                req_id = call_id_or_request_id
            elif task_id and task_id in self._task_call_map:
                req_id = self._task_call_map[task_id].get(call_id_or_request_id)

            if not req_id or req_id not in self._requests:
                raise ApprovalNotFoundError(f"Approval request '{call_id_or_request_id}' not found.")

            req = self.get_request(req_id)
            if not req:
                raise ApprovalNotFoundError(f"Approval request '{req_id}' not found.")

            if req.status == ApprovalStatus.EXPIRED or req.is_expired():
                raise ApprovalExpiredError(f"Approval request '{req_id}' has expired and cannot be resolved.")

            if req.status != ApprovalStatus.PENDING:
                logger.warning(
                    "Attempted to resolve non-pending approval request '%s' (current status: %s)",
                    req_id,
                    req.status.value,
                )
                return req

            target_status = ApprovalStatus.APPROVED if approved else ApprovalStatus.DENIED
            decided_at = datetime.now(timezone.utc).isoformat()
            updated = ApprovalRequest(
                **{
                    **req.model_dump(),
                    "status": target_status,
                    "decided_at": decided_at,
                    "approver_reason": reason,
                }
            )
            self._requests[req_id] = updated

            # Notify waiters
            ev = self._events.get(req_id)
            if ev:
                ev.set()

            logger.info("Resolved approval request '%s' -> %s", req_id, target_status.value)
            return updated

    async def wait_for_decision(
        self,
        request_id: str,
        timeout_seconds: Optional[float] = None,
    ) -> ApprovalRequest:
        """Asynchronously wait for an approval decision or timeout."""
        with self._lock:
            ev = self._events.get(request_id)
            if not ev:
                raise ApprovalNotFoundError(f"Approval request '{request_id}' not found.")
            req = self.get_request(request_id)
            if not req:
                raise ApprovalNotFoundError(f"Approval request '{request_id}' not found.")
            if req.status != ApprovalStatus.PENDING:
                return req

        timeout = timeout_seconds if timeout_seconds is not None else self.default_ttl_seconds
        try:
            await asyncio.wait_for(ev.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            with self._lock:
                req = self.get_request(request_id)
                if req and req.status == ApprovalStatus.PENDING:
                    updated = ApprovalRequest(
                        **{
                            **req.model_dump(),
                            "status": ApprovalStatus.EXPIRED,
                            "decided_at": datetime.now(timezone.utc).isoformat(),
                        }
                    )
                    self._requests[request_id] = updated
                    return updated

        req = self.get_request(request_id)
        if not req:
            raise ApprovalNotFoundError(f"Approval request '{request_id}' not found.")
        return req

    def consume_token(self, request_id: str, expected_action_hash: str) -> None:
        """Consume a single-use approval token prior to tool execution, validating cryptographic action hash."""
        with self._lock:
            req = self.get_request(request_id)
            if not req:
                raise ApprovalNotFoundError(f"Approval request '{request_id}' not found.")

            if req.status == ApprovalStatus.CONSUMED:
                raise ApprovalAlreadyConsumedError(
                    f"Approval token '{request_id}' has already been consumed (single-use violation)."
                )

            if req.status != ApprovalStatus.APPROVED:
                raise ApprovalError(
                    f"Cannot consume approval token '{request_id}' in state '{req.status.value}'."
                )

            if req.action_hash != expected_action_hash:
                raise InvalidApprovalTokenError(
                    f"Action hash mismatch for token '{request_id}'. "
                    "Invocation parameters do not match approved parameters."
                )

            consumed = ApprovalRequest(
                **{
                    **req.model_dump(),
                    "status": ApprovalStatus.CONSUMED,
                }
            )
            self._requests[request_id] = consumed
            logger.info("Consumed single-use approval token '%s'", request_id)

    def cancel_requests_for_task(self, task_id: str) -> List[ApprovalRequest]:
        """Cancel all pending approval requests when a task is aborted or cancelled."""
        cancelled: List[ApprovalRequest] = []
        with self._lock:
            calls = self._task_call_map.get(task_id, {})
            for call_id, req_id in list(calls.items()):
                req = self._requests.get(req_id)
                if req and req.status == ApprovalStatus.PENDING:
                    updated = ApprovalRequest(
                        **{
                            **req.model_dump(),
                            "status": ApprovalStatus.CANCELLED,
                            "decided_at": datetime.now(timezone.utc).isoformat(),
                        }
                    )
                    self._requests[req_id] = updated
                    cancelled.append(updated)
                    ev = self._events.get(req_id)
                    if ev:
                        ev.set()
        logger.info("Cancelled %d pending approval requests for task '%s'", len(cancelled), task_id)
        return cancelled

    def reset(self) -> None:
        """Purge all in-memory approval records (used in tests or system restart)."""
        with self._lock:
            self._requests.clear()
            self._task_call_map.clear()
            self._events.clear()


# Global in-memory approval manager singleton
_global_approval_manager: Optional[ApprovalManager] = None
_global_approval_lock = threading.Lock()


def get_global_approval_manager() -> ApprovalManager:
    """Retrieve or initialize the process-wide in-memory approval manager."""
    global _global_approval_manager
    with _global_approval_lock:
        if _global_approval_manager is None:
            _global_approval_manager = ApprovalManager()
        return _global_approval_manager


def set_global_approval_manager(manager: Optional[ApprovalManager]) -> None:
    """Override the process-wide approval manager (useful for isolated unit testing)."""
    global _global_approval_manager
    with _global_approval_lock:
        _global_approval_manager = manager
