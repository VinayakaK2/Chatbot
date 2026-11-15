"""Startup reconciliation engine detecting interrupted work and classifying crash boundaries."""

from datetime import datetime, timezone
import logging
import sqlite3
from typing import List, Optional
import uuid

from api.schemas.tasks import TaskStatus
from core.tool_contracts import ToolRiskCategory
from core.tool_registry import ToolRegistry
from storage.database import DatabaseManager
from storage.models import (
    ExecutionState,
    RecoveryClassification,
    RecoveryStatus,
    RecoverySummary,
    TaskRecord,
)
from storage.repositories import (
    ApprovalRepository,
    TaskNotFoundError,
    TaskRepository,
    ToolExecutionRepository,
)

logger = logging.getLogger("agent_runtime.core.recovery")


class StartupRecoveryEngine:
    """Detects and reconciles interrupted executions and stale approvals after a process restart."""

    def __init__(self, db: DatabaseManager, tool_registry: Optional[ToolRegistry] = None) -> None:
        self.db = db
        self.tool_registry = tool_registry
        self.task_repo = TaskRepository(db)
        self.tool_repo = ToolExecutionRepository(db)
        self.approval_repo = ApprovalRepository(db)

    def reconcile(self, auto_create_continuations: bool = False) -> RecoverySummary:
        """Scan storage on application boot, classify interrupted tasks, and invalidate stale approvals.

        Args:
            auto_create_continuations: If True, automatically spawn pending continuation tasks
                for interrupted tasks classified as SAFE_TO_RESUME.

        Returns:
            RecoverySummary: Summary of identified tasks and recovery classifications.
        """
        logger.info("Executing startup recovery and session reconciliation...")
        summary = RecoverySummary()

        # 1. Invalidate all pending and approved authorizations across restart boundary
        invalidated_approvals = self.approval_repo.invalidate_pending_approvals(
            reason="process_restart"
        )
        summary.stale_approvals_invalidated = invalidated_approvals

        # 2. Query all tasks left in EXECUTING state
        conn = self.db.get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM tasks WHERE status = 'executing';")
        executing_ids: List[str] = [row[0] for row in cursor.fetchall()]
        summary.interrupted_tasks_found = len(executing_ids)

        if not executing_ids:
            logger.info("Startup reconciliation complete: 0 interrupted tasks found.")
            return summary

        # 3. Classify each interrupted task
        for task_id in executing_ids:
            inflight_tools = self.tool_repo.get_inflight_for_task(task_id)

            # Evaluate each in-flight tool against runtime-owned ToolRegistry
            # Unknown tools or tools claiming to be read-only in database records
            # must default to NEEDS_REVIEW unless verified by ToolRegistry (F7-05).
            is_mutating_or_unsafe = False
            mutating_names: List[str] = []
            read_only_names: List[str] = []

            for t in inflight_tools:
                tool_is_safe = False
                if self.tool_registry is not None:
                    if self.tool_registry.has(t.tool_name):
                        tool_def = self.tool_registry.get(t.tool_name)
                        if (
                            tool_def.risk_category == ToolRiskCategory.READ_ONLY
                            and not t.is_mutation
                            and t.idempotent
                        ):
                            tool_is_safe = True
                    else:
                        # Unknown tool: even if DB has is_mutation=0 or idempotent=1,
                        # database records could be unverified; default to unsafe!
                        tool_is_safe = False
                else:
                    if not t.is_mutation and t.idempotent:
                        tool_is_safe = True

                if tool_is_safe:
                    read_only_names.append(t.tool_name)
                else:
                    is_mutating_or_unsafe = True
                    mutating_names.append(t.tool_name)

            if is_mutating_or_unsafe:
                # Dangerous crash boundary: Non-idempotent, mutating, or unknown tool was executing!
                reason = f"interrupted_during_{'_'.join(mutating_names)}"
                error_msg = (
                    f"Task execution interrupted during unverified or non-idempotent tool call(s): {', '.join(mutating_names)}. "
                    "External state cannot be automatically determined. Manual inspection required before re-executing."
                )

                # Mark in-flight tool records as interrupted
                for t in inflight_tools:
                    self.tool_repo.record_completed(
                        call_id=t.call_id,
                        state=ExecutionState.INTERRUPTED,
                        error="Interrupted by process crash/restart",
                        error_category="process_restart",
                    )

                self.task_repo.transition_state(
                    task_id=task_id,
                    target_status=TaskStatus.FAILED,
                    recovery_status=RecoveryStatus.NEEDS_REVIEW,
                    error=error_msg,
                    termination_reason=reason,
                    actor="startup_recovery",
                    reason="Classified as NEEDS_REVIEW: in-flight mutating or unverified tool call detected at restart",
                )

                summary.needs_review_count += 1
                summary.details.append(
                    {
                        "task_id": task_id,
                        "classification": RecoveryClassification.NEEDS_REVIEW.value,
                        "reason": error_msg,
                        "tools": mutating_names,
                    }
                )
                logger.warning("Interrupted task %s classified as NEEDS_REVIEW (%s)", task_id, mutating_names)

            else:
                # Safe boundary: either no in-flight tools, or only registered read-only operations
                for t in inflight_tools:
                    self.tool_repo.record_completed(
                        call_id=t.call_id,
                        state=ExecutionState.INTERRUPTED,
                        error="Interrupted read-only operation during process restart",
                        error_category="process_restart",
                    )

                error_msg = "Task execution was interrupted by process restart. Safe to resume from prior state."
                self.task_repo.transition_state(
                    task_id=task_id,
                    target_status=TaskStatus.FAILED,
                    recovery_status=RecoveryStatus.INTERRUPTED_SAFE,
                    error=error_msg,
                    termination_reason="interrupted_safe",
                    actor="startup_recovery",
                    reason="Classified as SAFE_TO_RESUME: no in-flight mutating tool side-effects",
                )

                summary.safe_to_resume_count += 1
                summary.details.append(
                    {
                        "task_id": task_id,
                        "classification": RecoveryClassification.SAFE_TO_RESUME.value,
                        "reason": error_msg,
                        "tools": read_only_names,
                    }
                )
                logger.info("Interrupted task %s classified as SAFE_TO_RESUME", task_id)

                if auto_create_continuations:
                    continuation = self.create_continuation_task(task_id)
                    summary.continuation_task_ids.append(continuation.id)

        return summary

    def create_continuation_task(self, original_task_id: str) -> TaskRecord:
        """Create a new continuation task for an interrupted task safely preserved in terminal FAILED state.

        Per F7-01 and 2604.14228v3.pdf §9.2:
        1. Terminal tasks remain immutable in FAILED state with recovery_status=INTERRUPTED_SAFE.
        2. A new continuation task is spawned in PENDING state with parent_task_id pointing to original_task_id.
        3. Recovery status of continuation task is set to REPAIRED.
        """
        original = self.task_repo.get(original_task_id)
        if not original:
            raise TaskNotFoundError(f"Original task '{original_task_id}' not found.")

        # Verify recovery eligibility
        if original.recovery_status != RecoveryStatus.INTERRUPTED_SAFE:
            raise ValueError(
                f"Task '{original_task_id}' cannot be resumed: recovery status is '{original.recovery_status}', "
                f"expected '{RecoveryStatus.INTERRUPTED_SAFE.value}'."
            )

        # Check if an active continuation task already exists to prevent duplicate execution
        existing_rec = self.task_repo.get_by_parent_task_id(original_task_id)
        if existing_rec and existing_rec.status != TaskStatus.FAILED:
            logger.info(
                "Found existing continuation task %s for original task %s",
                existing_rec.id,
                original_task_id,
            )
            return existing_rec

        # Create new continuation task linked via parent_task_id
        continuation_id = str(uuid.uuid4())
        continuation = TaskRecord(
            id=continuation_id,
            session_id=original.session_id,
            parent_task_id=original.id,
            prompt=original.prompt,
            status=TaskStatus.PENDING,
            recovery_status=RecoveryStatus.REPAIRED,
            model=original.model,
            created_at=datetime.now(timezone.utc).isoformat(),
        )

        try:
            saved = self.task_repo.create(continuation)
            logger.info(
                "Created continuation task %s for interrupted task %s (session %s)",
                continuation_id,
                original_task_id,
                original.session_id,
            )
            return saved
        except sqlite3.IntegrityError as exc:
            err_msg = str(exc).lower()
            # Distinguish unique constraint conflict on parent_task_id from other integrity failures (FK, CHECK, NOT NULL)
            if "parent_task_id" in err_msg or "uq_tasks_parent_task" in err_msg:
                # Concurrency race on continuation uniqueness constraint.
                # The losing transaction was already rolled back by DatabaseManager.transaction().
                # Fetch the winning continuation committed by the winning transaction.
                winner_rec = self.task_repo.get_by_parent_task_id(original_task_id)
                if winner_rec:
                    logger.info(
                        "Resolved concurrent continuation race for %s: returning winner task %s",
                        original_task_id,
                        winner_rec.id,
                    )
                    return winner_rec
            # Non-uniqueness integrity failure (e.g. invalid FK, check, or null constraint) or missing winner: fail closed!
            logger.error(
                "IntegrityError during continuation creation for %s (not a resolved race): %s",
                original_task_id,
                exc,
            )
            raise
        except sqlite3.OperationalError as exc:
            # Distinguish database lock / busy operational errors from integrity conflicts
            logger.error(
                "OperationalError (busy/locked) during continuation creation for %s: %s",
                original_task_id,
                exc,
            )
            raise
