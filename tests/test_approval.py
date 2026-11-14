"""Unit tests for runtime-owned human approval lifecycle and exact-action binding."""

import asyncio
from datetime import datetime, timezone
import pytest
from core.approval import (
    ApprovalAlreadyConsumedError,
    ApprovalExpiredError,
    ApprovalManager,
    ApprovalNotFoundError,
    ApprovalStatus,
    InvalidApprovalTokenError,
    compute_action_hash,
)
from core.tool_contracts import ToolRiskCategory


@pytest.fixture
def manager() -> ApprovalManager:
    """Fixture providing a fresh in-memory ApprovalManager."""
    mgr = ApprovalManager(default_ttl_seconds=300.0)
    mgr.reset()
    return mgr


class TestApprovalManagerLifecycle:
    """Verify approval state transitions: PENDING -> APPROVED / DENIED / EXPIRED / CANCELLED -> CONSUMED."""

    def test_create_and_get_request(self, manager: ApprovalManager) -> None:
        """Create a request and verify all attributes and exact-action hash."""
        args = {"command": "pytest -q", "working_dir": "H:\\project"}
        req = manager.create_request(
            task_id="task-1",
            call_id="call-1",
            tool_name="execute_command",
            arguments=args,
            risk_category=ToolRiskCategory.COMMAND_EXECUTION,
            reason="Requires approval",
            working_dir="H:\\project",
            command="pytest -q",
        )

        assert req.status == ApprovalStatus.PENDING
        assert req.task_id == "task-1"
        assert req.call_id == "call-1"
        assert req.tool_name == "execute_command"
        assert req.action_hash == compute_action_hash("task-1", "execute_command", args, "H:\\project")

        fetched = manager.get_request(req.request_id)
        assert fetched is not None
        assert fetched.request_id == req.request_id

        fetched_by_call = manager.get_request_by_call("task-1", "call-1")
        assert fetched_by_call is not None
        assert fetched_by_call.request_id == req.request_id

    def test_resolve_approved(self, manager: ApprovalManager) -> None:
        """Resolving a pending request as approved transitions to APPROVED."""
        req = manager.create_request(
            task_id="task-1",
            call_id="call-1",
            tool_name="execute_command",
            arguments={"command": "npm test"},
            risk_category=ToolRiskCategory.COMMAND_EXECUTION,
            reason="Requires approval",
        )

        resolved = manager.resolve_request(req.request_id, approved=True, reason="Operator confirmed")
        assert resolved.status == ApprovalStatus.APPROVED
        assert resolved.approver_reason == "Operator confirmed"
        assert resolved.decided_at is not None

    def test_resolve_denied(self, manager: ApprovalManager) -> None:
        """Resolving a pending request as rejected transitions to DENIED."""
        req = manager.create_request(
            task_id="task-1",
            call_id="call-1",
            tool_name="execute_command",
            arguments={"command": "npm test"},
            risk_category=ToolRiskCategory.COMMAND_EXECUTION,
            reason="Requires approval",
        )

        resolved = manager.resolve_request(req.request_id, approved=False, reason="Too risky")
        assert resolved.status == ApprovalStatus.DENIED
        assert resolved.approver_reason == "Too risky"

    def test_resolve_by_call_id(self, manager: ApprovalManager) -> None:
        """Can resolve via call_id and task_id."""
        req = manager.create_request(
            task_id="task-2",
            call_id="call-2",
            tool_name="execute_command",
            arguments={"command": "pytest"},
            risk_category=ToolRiskCategory.COMMAND_EXECUTION,
            reason="Requires approval",
        )

        resolved = manager.resolve_request("call-2", approved=True, task_id="task-2")
        assert resolved.request_id == req.request_id
        assert resolved.status == ApprovalStatus.APPROVED

    def test_resolve_nonexistent_raises_not_found(self, manager: ApprovalManager) -> None:
        """Resolving a missing request ID raises ApprovalNotFoundError."""
        with pytest.raises(ApprovalNotFoundError):
            manager.resolve_request("nonexistent-id", approved=True)

    def test_single_use_token_consumption(self, manager: ApprovalManager) -> None:
        """An approved token can only be consumed once; second consumption raises error."""
        args = {"command": "pytest"}
        req = manager.create_request(
            task_id="task-1",
            call_id="call-1",
            tool_name="execute_command",
            arguments=args,
            risk_category=ToolRiskCategory.COMMAND_EXECUTION,
            reason="Requires approval",
        )
        manager.resolve_request(req.request_id, approved=True)

        # 1. First consumption succeeds
        manager.consume_token(req.request_id, expected_action_hash=req.action_hash)
        consumed = manager.get_request(req.request_id)
        assert consumed is not None
        assert consumed.status == ApprovalStatus.CONSUMED

        # 2. Second consumption raises ApprovalAlreadyConsumedError
        with pytest.raises(ApprovalAlreadyConsumedError):
            manager.consume_token(req.request_id, expected_action_hash=req.action_hash)

    def test_action_hash_mismatch_rejected(self, manager: ApprovalManager) -> None:
        """Presenting an approved token with altered parameters raises InvalidApprovalTokenError."""
        args = {"command": "pytest"}
        req = manager.create_request(
            task_id="task-1",
            call_id="call-1",
            tool_name="execute_command",
            arguments=args,
            risk_category=ToolRiskCategory.COMMAND_EXECUTION,
            reason="Requires approval",
        )
        manager.resolve_request(req.request_id, approved=True)

        tampered_hash = compute_action_hash("task-1", "execute_command", {"command": "pytest; evil"}, None)
        with pytest.raises(InvalidApprovalTokenError):
            manager.consume_token(req.request_id, expected_action_hash=tampered_hash)

    def test_expiration_handling(self, manager: ApprovalManager) -> None:
        """Requests past their TTL are marked EXPIRED and cannot be approved."""
        # Create request with 0.01s TTL
        req = manager.create_request(
            task_id="task-1",
            call_id="call-1",
            tool_name="execute_command",
            arguments={"command": "pytest"},
            risk_category=ToolRiskCategory.COMMAND_EXECUTION,
            reason="Requires approval",
            ttl_seconds=0.01,
        )

        import time
        time.sleep(0.05)

        # get_request lazily transitions expired request
        fetched = manager.get_request(req.request_id)
        assert fetched is not None
        assert fetched.status == ApprovalStatus.EXPIRED

        with pytest.raises(ApprovalExpiredError):
            manager.resolve_request(req.request_id, approved=True)

    def test_cancellation_for_task(self, manager: ApprovalManager) -> None:
        """Cancelling a task marks all pending requests as CANCELLED."""
        req1 = manager.create_request(
            task_id="task-cancel",
            call_id="call-c1",
            tool_name="execute_command",
            arguments={"command": "pytest"},
            risk_category=ToolRiskCategory.COMMAND_EXECUTION,
            reason="test",
        )
        req2 = manager.create_request(
            task_id="task-cancel",
            call_id="call-c2",
            tool_name="execute_command",
            arguments={"command": "npm test"},
            risk_category=ToolRiskCategory.COMMAND_EXECUTION,
            reason="test",
        )

        cancelled = manager.cancel_requests_for_task("task-cancel")
        assert len(cancelled) == 2
        assert all(r.status == ApprovalStatus.CANCELLED for r in cancelled)

        # Status persisted in get_request
        assert manager.get_request(req1.request_id).status == ApprovalStatus.CANCELLED
        assert manager.get_request(req2.request_id).status == ApprovalStatus.CANCELLED

    @pytest.mark.asyncio
    async def test_wait_for_decision_async_unblock(self, manager: ApprovalManager) -> None:
        """wait_for_decision asynchronously blocks until resolve_request is called."""
        req = manager.create_request(
            task_id="task-async",
            call_id="call-a1",
            tool_name="execute_command",
            arguments={"command": "pytest"},
            risk_category=ToolRiskCategory.COMMAND_EXECUTION,
            reason="test",
        )

        async def resolve_later():
            await asyncio.sleep(0.05)
            manager.resolve_request(req.request_id, approved=True, reason="Async operator")

        asyncio.create_task(resolve_later())

        resolved = await manager.wait_for_decision(req.request_id, timeout_seconds=1.0)
        assert resolved.status == ApprovalStatus.APPROVED
        assert resolved.approver_reason == "Async operator"
