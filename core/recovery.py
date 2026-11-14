"""Startup reconciliation engine detecting interrupted work and classifying crash boundaries."""

from datetime import datetime, timezone
import logging
from typing import List

from api.schemas.tasks import TaskStatus
from storage.database import DatabaseManager
from storage.models import (
    ExecutionState,
    RecoveryClassification,
    RecoveryStatus,
    RecoverySummary,
)
from storage.repositories import (
    ApprovalRepository,
    TaskRepository,
    ToolExecutionRepository,
)

logger = logging.getLogger("agent_runtime.core.recovery")


class StartupRecoveryEngine:
    """Detects and reconciles interrupted executions and stale approvals after a process restart."""

    def __init__(self, db: DatabaseManager) -> None:
        self.db = db
        self.task_repo = TaskRepository(db)
        self.tool_repo = ToolExecutionRepository(db)
        self.approval_repo = ApprovalRepository(db)

    def reconcile(self) -> RecoverySummary:
        """Scan storage on application boot, classify interrupted tasks, and invalidate stale approvals.

        Returns:
            RecoverySummary: Summary of identified tasks and recovery classifications.
        """
        logger.info("Executing startup recovery and session reconciliation...")
        summary = RecoverySummary()

        # 1. Invalidate all pending approvals across restart boundary
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

            has_mutating_inflight = any(
                t.is_mutation or not t.idempotent for t in inflight_tools
            )

            if has_mutating_inflight:
                # Dangerous crash boundary: Non-idempotent tool was executing!
                mutating_names = [
                    t.tool_name for t in inflight_tools if t.is_mutation or not t.idempotent
                ]
                reason = f"interrupted_during_{'_'.join(mutating_names)}"
                error_msg = (
                    f"Task execution interrupted during non-idempotent tool call(s): {', '.join(mutating_names)}. "
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
                    reason="Classified as NEEDS_REVIEW: in-flight mutating tool call detected at restart",
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
                # Safe boundary: either no in-flight tools, or only read-only/idempotent operations
                read_only_names = [t.tool_name for t in inflight_tools]
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

        return summary
