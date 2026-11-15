# Phase 7 — Persistent State & Session Recovery: Adversarial & Edge-Case Verification Report

**Status:** `VERIFIED · ALL ADVERSARIAL PASSES GREEN`  
**Phase Target:** `Phase 7 — Persistent State and Session Recovery`  
**Mandatory Authorities:** `sources/2604.14228v3.pdf` (§5, §9.1, §9.2), `AGENTS.md`, `docs/phases.md`  
**Date:** 2026-11-15  

---

## 1. Adversarial Test Objective

To prove beyond doubt that the Phase 7 persistence and session recovery subsystem behaves defensively, deterministically, and securely under hostile conditions, process crashes, concurrency races, tampering attempts, and data corruption.

Every test was executed against real temporary on-disk SQLite databases on Windows (`tmp_path / "*.db"`) to eliminate any possibility of in-memory artifacts masking crash-recovery behaviors.

---

## 2. Adversarial Test Matrix & Verification Evidence

### ADV-01: Crash Boundary During Mutating Tool Execution (Zero Blind Replay)
- **Scenario:** A task is executing on worker thread; a non-idempotent, mutating tool (`write_file` or `execute_command`) begins execution. The process terminates abruptly before tool completion.
- **Threat:** If the runtime blindly replays the interrupted task upon restart, duplicate files may be created, destructive overwrite may occur, or commands like `git push` may execute twice.
- **Defense Implementation:** `StartupRecoveryEngine` queries `tool_executions` for any records where `execution_state == 'started'` and `is_mutation == 1` or `idempotent == 0`.
- **Observed Evidence:**
  - Task state: Transitioned to `FAILED` with `recovery_status = NEEDS_REVIEW`.
  - Termination reason: `interrupted_during_write_file` (or `interrupted_during_execute_command`).
  - Error diagnostic: `"Task execution interrupted during non-idempotent tool call(s): write_file. External state cannot be automatically determined. Manual inspection required before re-executing."`
  - In-flight tool record: Transitioned to `execution_state = 'interrupted'`.
  - Replay behavior: **Zero blind replay.** Task is in terminal `FAILED` state and will never resume automatically.
- **Test Proof:** `tests/test_session_recovery.py::test_recovery_interrupted_executing_task_mutating_tool_needs_review_no_blind_replay` (PASS) and `tests/test_session_recovery.py::test_recovery_interrupted_executing_task_shell_command_needs_review` (PASS).

---

### ADV-02: Volatile Human Approval Invalidation Across Process Boundaries
- **Scenario:** An operator requested human approval for `execute_command("rm -rf build/")`. The approval is in `PENDING` state with a valid cryptographic token hash. The server crashes or restarts.
- **Threat:** Re-granting stale approvals or allowing pre-restart approvals to be confirmed and executed after restart violates `2604.14228v3.pdf` (§5, §9.2).
- **Defense Implementation:** `StartupRecoveryEngine` executes `ApprovalRepository.invalidate_pending_approvals(reason="process_restart")`. All approvals in `status == 'pending'` are transitioned to `status = 'expired'` with `decision_reason = 'Invalidated on process_restart'`.
- **Observed Evidence:**
  - On restart, `summary.stale_approvals_invalidated` equals 1.
  - Fetching the approval record confirms `status == 'expired'`.
  - Calling `consume_token()` returns `False` immediately.
  - Stale approval tokens are completely unconsumable.
- **Test Proof:** `tests/test_session_recovery.py::test_recovery_pending_approvals_invalidated_across_restart` (PASS).

---

### ADV-03: Optimistic Concurrency Locking & Stale Version Collision
- **Scenario:** Two concurrent processes or asynchronous coroutines attempt to transition the same task from version 1. Worker A transitions it to version 2; Worker B attempts to commit using expected version 1.
- **Threat:** Lost update anomaly, race condition where late-arriving cancellation or completion overwrites an authoritative state transition.
- **Defense Implementation:** `TaskRepository.transition_state` checks `if expected_version is not None and current_version != expected_version: raise StaleVersionError(...)`.
- **Observed Evidence:**
  - First transition succeeds: version bumped from 1 to 2.
  - Second transition using stale version 1 immediately raises `StaleVersionError("Stale version detected on task ...")`.
  - Task state in SQLite remains consistent and uncorrupted.
- **Test Proof:** `tests/test_persistence.py::test_task_repository_creation_and_state_transitions` (PASS).

---

### ADV-04: Terminal State Immutability Enforcement
- **Scenario:** A task has entered a terminal state (`COMPLETED`, `CANCELLED`, `FAILED`, `LIMIT_REACHED`). An adversarial or buggy routine attempts to reactivate it to `EXECUTING` or `PENDING`.
- **Threat:** Ghost tasks, illegal resurrection of finished or cancelled work.
- **Defense Implementation:** `TaskRepository.transition_state` checks `if current_status in TERMINAL_TASK_STATUSES: raise InvalidStateTransitionError(...)`.
- **Observed Evidence:**
  - Calling `transition_task_state` on a `COMPLETED` task raises `InvalidStateTransitionError("Cannot transition task ... from terminal state 'completed' to 'executing'")`.
  - Audit log in `state_transitions` table records zero illegitimate transitions.
- **Test Proof:** `tests/test_persistence.py::test_task_repository_creation_and_state_transitions` (PASS) and `tests/test_session_recovery.py::test_recovery_terminal_tasks_immutable_across_recovery` (PASS).

---

### ADV-05: Secret Redaction & Credential Scrubbing at Persistence Boundary
- **Scenario:** The model generates a tool call containing API keys (`sk-ant-...`), GitHub tokens (`ghp_...`), or bearer tokens (`Bearer eyJ...`), and the tool returns output containing authorization headers.
- **Threat:** Disk persistence of plain-text credentials in SQLite databases, which could leak into backups or forensic exports.
- **Defense Implementation:** `ToolExecutionRepository.record_started` and `record_completed` pass inputs and outputs through `tools.security.redact_secrets()`.
- **Observed Evidence:**
  - Persisted `arguments_json` contains `[REDACTED]` and `Bearer [REDACTED_TOKEN]`.
  - Raw strings `sk-ant-api03-secretValueXYZ` and `ghp_secretTokenABC123` are completely absent from the SQLite table on disk.
  - Persisted `result_output` contains `[REDACTED_TOKEN]`.
- **Test Proof:** `tests/test_persistence.py::test_tool_execution_repository_lifecycle_and_scrubbing` (PASS).

---

### ADV-06: Foreign Key Enforcement & Cascading Cleanup
- **Scenario:** An operator deletes a session or a task.
- **Threat:** Orphaned records in `transcript_messages`, `tool_executions`, or `approvals`.
- **Defense Implementation:** `DatabaseManager` runs `PRAGMA foreign_keys = ON;` on every connection. Tables declare `ON DELETE CASCADE` on `task_id` and `session_id`.
- **Observed Evidence:**
  - When `TaskRepository.delete(task_id)` is invoked, associated rows in `tool_executions`, `transcript_messages`, and `approvals` are automatically and atomically purged by SQLite.
  - PRAGMA verification confirms `PRAGMA foreign_keys == 1`.
- **Test Proof:** `tests/test_persistence.py::test_database_manager_pragmas_and_wal` (PASS).

---

### ADV-07: Direct SQL Mutation Bypass of Full-Row Terminal Immutability Blocked by Database Engine Triggers
- **Scenario:** An attacker, flawed migration, or rogue script executes a raw SQL `UPDATE` statement directly against the SQLite database connection, attempting to modify `status`, `prompt`, `error`, `response_text`, `recovery_status`, or other columns of a `completed` or `failed` task without going through Python application repositories.
- **Threat:** Bypassing application-level immutability checks by updating non-status columns via raw SQL.
- **Defense Implementation:** Migration `002_immutability_triggers_and_continuations` installs SQLite engine trigger `trg_prevent_terminal_task_update` on `BEFORE UPDATE ON tasks`.
- **Observed Evidence:**
  - Raw SQL `UPDATE tasks SET status = 'executing' WHERE id = ?;` immediately raises `sqlite3.IntegrityError: Illegal database state transition: terminal tasks (completed, cancelled, failed, limit_reached) are completely immutable`.
  - Raw SQL `UPDATE tasks SET prompt = '...'` or `SET error = '...'` or `SET response_text = '...'` on a terminal task immediately raises the same `sqlite3.IntegrityError`.
  - Raw SQL jump from `pending` directly to `completed` raises `sqlite3.IntegrityError: Illegal database state transition: pending tasks may only transition to executing, cancelled, or failed`.
  - The entire terminal task row remains unmodified in SQLite.
- **Test Proof:** `tests/test_persistence.py::test_database_level_trigger_terminal_immutability` (PASS).

---

### ADV-08: Database Flag Forgery & Unregistered Tool Safety Overridden by ToolRegistry
- **Scenario:** A corrupted database record, untrusted MCP tool, or adversary marks an in-flight tool with `is_mutation = 0` and `idempotent = 1` in `tool_executions` to trick the recovery engine into classifying an interrupted mutating tool as `SAFE_TO_RESUME`.
- **Threat:** Blind replay of unknown or mutating tools caused by relying on database flags alone.
- **Defense Implementation:** `StartupRecoveryEngine` queries the active runtime `ToolRegistry`. Only tools registered in the runtime with `ToolRiskCategory.READ_ONLY` are considered safe. Unknown/unregistered tools default fail-closed to `NEEDS_REVIEW`.
- **Observed Evidence:**
  - An unregistered tool with `is_mutation=0, idempotent=1` in the database is classified as `NEEDS_REVIEW`.
  - A registered mutating tool (`execute_command`) with forged database flags `is_mutation=0, idempotent=1` is overridden by `ToolRegistry` and classified as `NEEDS_REVIEW`.
  - Only registered `READ_ONLY` tools (`read_file`) with matching flags are classified as `SAFE_TO_RESUME`.
- **Test Proof:** `tests/test_session_recovery.py::test_recovery_runtime_tool_registry_authoritative_classification` (PASS).

---

### ADV-09: Complete 6-State Approval Lifecycle & Restart Invalidation
- **Scenario:** An operator approves a sensitive command (`execute_command`). The approval status is `approved`, but the application crashes or restarts before the command token is consumed by the runner. Other approvals exist in `pending`, `consumed`, `denied`, `expired`, and `cancelled` states.
- **Threat:** Weaponizing stale pre-restart authorizations in a newly booted environment (`2604.14228v3.pdf` §5, §9.2), or mutating terminal approval states.
- **Defense Implementation:** `StartupRecoveryEngine` invalidates all authorizations `WHERE status IN ('pending', 'approved')` to `status = 'expired'`. Terminal states (`consumed`, `denied`, `expired`, `cancelled`) are preserved.
- **Observed Evidence:**
  - After restart, both `pending` and unconsumed `approved` records transition to `status == 'expired'`.
  - Terminal resolution records remain unchanged: `consumed` -> `consumed`, `denied` -> `denied`, `expired` -> `expired`, `cancelled` -> `cancelled`.
  - Calling `consume_token()` on expired or cancelled records returns `False` immediately.
- **Test Proof:** `tests/test_session_recovery.py::test_recovery_all_six_approval_states_on_restart` (PASS).

---

### ADV-10: Secret Sanitization Defense-in-Depth Across Every SQLite Table
- **Scenario:** Raw secrets (Anthropic keys, GitHub PATs, Bearer tokens, passwords) are passed into task prompts, error logs, response text, transcript turns, tool arguments, approval requests, and session metadata.
- **Threat:** Plain-text credential persistence in SQLite database files.
- **Defense Implementation:** `redact_secrets` is wired into every repository write path: `tasks`, `transcript_messages`, `tool_executions`, `approvals`, `state_transitions`, `sessions`, and event logs.
- **Observed Evidence:**
  - Raw SQL inspection of all SQLite table rows confirms that tested plain-text secret strings (`sk-ant-api03-...`, `ghp_...`, Bearer payloads) are 100% absent across every column and table.
  - Defense-in-depth sanitization replaces credentials with `[REDACTED]`, `[REDACTED_TOKEN]`, or `***`.
  - Qualified as defense-in-depth sanitization: verifies that tested token patterns are detected and scrubbed, without claiming mathematical impossibility of leaking unmodeled secret formats.
- **Test Proof:** `tests/test_persistence.py::test_secret_scrubbing_across_all_tables` (PASS).

---

### ADV-11: Broken Migration Transaction Rollback
- **Scenario:** A database migration contains invalid SQL syntax or fails halfway through execution.
- **Threat:** Partial schema application, broken tables, or corrupted `schema_migrations` tracking records.
- **Defense Implementation:** `MigrationRunner` executes schema changes within atomic SQLite transactions.
- **Observed Evidence:**
  - When an invalid statement is encountered, the transaction rolls back completely.
  - Partial tables (`broken_table`) are not created in `sqlite_master`.
  - `schema_migrations` remains clean with only fully committed versions.
- **Test Proof:** `tests/test_persistence.py::test_migration_failure_rollback` (PASS).

---

### ADV-12: Continuation Task Lineage & Idempotency
- **Scenario:** An interrupted task is classified as `SAFE_TO_RESUME`. The operator requests resumption.
- **Threat:** Violating terminal immutability by altering the original task, or double-spawning duplicate tasks.
- **Defense Implementation:** `StartupRecoveryEngine.create_continuation_task` keeps the original task in terminal `FAILED` (`recovery_status = 'interrupted_safe'`), and creates a new `PENDING` task linked via `parent_task_id`.
- **Observed Evidence:**
  - The original task remains strictly immutable in terminal `FAILED` state.
  - A new continuation task is created with `parent_task_id = original_task.id` and `recovery_status = 'repaired'`.
  - Re-requesting continuation on the same task returns the existing continuation idempotently.
  - Attempting to resume a `NEEDS_REVIEW` task is rejected with an explicit `ValueError`.
- **Test Proof:** `tests/test_session_recovery.py::test_recovery_continuation_task_lifecycle` (PASS).

---

### ADV-13: Concurrent Simultaneous Resume Race Condition Prevention
- **Scenario:** Two or more simultaneous API requests arrive concurrently attempting to resume the exact same interrupted task before either transaction commits.
- **Threat:** Duplicate continuation tasks created for the same parent task, violating task lineage integrity.
- **Defense Implementation:**
  - Migration 002 creates partial unique index `CREATE UNIQUE INDEX IF NOT EXISTS uq_tasks_parent_task ON tasks(parent_task_id) WHERE parent_task_id IS NOT NULL;`.
  - `create_continuation_task` handles concurrent `sqlite3.IntegrityError` by atomically catching the collision and returning the winning continuation task.
- **Observed Evidence:**
  - Tested with 10 concurrent threads simultaneously requesting continuation: zero unhandled exceptions, exactly ONE task persisted in SQLite with `parent_task_id = original_task.id`, and all 10 callers receive the identical continuation task record.
- **Test Proof:** `tests/test_session_recovery.py::test_recovery_continuation_task_concurrent_race_prevention` (PASS).

---

### ADV-14: Administrative Deletion vs Full-Row Update Immutability
- **Scenario:** An administrator or maintenance process invokes `task_repo.delete(task_id)` to purge a terminal task, or a rogue caller attempts an SQL `UPDATE` on a terminal task.
- **Threat:** Claiming terminal tasks "cannot be removed" when deletion is permitted, or permitting modification of terminal task fields.
- **Defense Implementation:**
  - `trg_prevent_terminal_task_update` strictly blocks all SQL `UPDATE` statements on terminal tasks (full-row update immutability).
  - Deletion is NOT exposed via the public runtime REST API, but is explicitly permitted at the administrative repository layer.
  - Foreign key cascades clean up child records (`transcript_messages`, `tool_executions`, `model_calls`, `approvals`), while `state_transitions` has NO foreign key cascade and preserves the linear audit trail permanently.
- **Observed Evidence:**
  - Direct SQL `UPDATE` fails with `sqlite3.IntegrityError: Illegal database state transition: terminal tasks (completed, cancelled, failed, limit_reached) are completely immutable`.
  - Administrative deletion removes the task row and cascades child rows; all historical `state_transitions` remain 100% intact.
- **Test Proof:** `tests/test_persistence.py::test_terminal_task_deletion_administrative_behavior` (PASS).

---

### ADV-15: Migration 002 Non-Destructive Deduplication on Pre-Existing Data
- **Scenario:** An existing database has duplicate continuation records pointing to the same `parent_task_id` before Migration 002 installs the unique partial index `uq_tasks_parent_task`.
- **Threat:** Migration failure on `CREATE UNIQUE INDEX`, or naive migrations silently deleting historical task rows to pass constraint checks.
- **Defense Implementation:**
  - Migration 002 inspects existing continuations before index creation.
  - The earliest primary continuation retains `parent_task_id = P` (true causal parentage), while subsequent duplicate concurrent attempts have `parent_task_id` set to `NULL` (safely detached), inserting audit records into `state_transitions` with provenance.
  - This avoids manufacturing false parent-child relationships between concurrent sibling tasks.
  - Zero historical task rows, transcripts, or execution records are deleted.
- **Observed Evidence:**
  - Tested on a database with 3 duplicate continuation records: migration succeeds cleanly without error.
  - All 4 tasks (parent + 3 children) and their transcripts remain present; true causal lineage is preserved; unique partial index activates successfully.
- **Test Proof:**
  - `tests/test_persistence.py::test_migration_compatibility_clean_v1_database` (PASS)
  - `tests/test_persistence.py::test_migration_compatibility_v2_database_no_continuations` (PASS)
  - `tests/test_persistence.py::test_migration_compatibility_valid_continuation_records` (PASS)
  - `tests/test_persistence.py::test_migration_compatibility_duplicate_continuation_records_no_silent_deletion` (PASS).

---

### ADV-16: Continuation Transaction Rollback & Fail-Closed Integrity Handling
- **Scenario:** A competing continuation attempt collides with the unique constraint, or an unrelated database error occurs (e.g. invalid foreign key, check constraint, or database locked).
- **Threat:** Competing transactions leaving ghost state transitions or orphan task rows; or treating every `IntegrityError` as proof of duplicate continuation.
- **Defense Implementation:**
  - `DatabaseManager.transaction()` rolls back the losing transaction upon exception, leaving zero partial rows or ghost transitions.
  - `create_continuation_task` checks the error string: only specific unique constraint collisions on `parent_task_id` or `uq_tasks_parent_task` resolve to the winner.
  - Non-uniqueness errors (foreign key, check, not null) fail closed and re-raise.
  - Operational errors (`sqlite3.OperationalError` / database locked) are distinguished from integrity errors and re-raised cleanly.
- **Observed Evidence:**
  - Losing transaction leaves 0 orphan state transitions or task records.
  - Foreign key and check failures re-raise `sqlite3.IntegrityError` without returning a bogus winner.
  - Locked database errors re-raise `sqlite3.OperationalError`.
- **Test Proof:**
  - `tests/test_session_recovery.py::test_continuation_winning_and_losing_transaction_rollback` (PASS)
  - `tests/test_session_recovery.py::test_continuation_non_uniqueness_integrity_error_fails_closed` (PASS)
  - `tests/test_session_recovery.py::test_continuation_operational_error_busy_locked_distinguished` (PASS).

---

### ADV-17: Full-Row Trigger Non-Interference with Legitimate Operations
- **Scenario:** Normal operations run across tasks, state transitions, continuations, session context reconstruction, and startup recovery passes.
- **Threat:** A strict database trigger accidentally blocking valid updates on active tasks or breaking recovery flows.
- **Defense Implementation:**
  - `trg_prevent_terminal_task_update` specifically triggers `WHEN OLD.status IN ('completed', 'cancelled', 'failed', 'limit_reached')`.
  - Active tasks (`pending`, `executing`) are unhindered; state transitions, continuation creations, context loading, and startup recovery execute seamlessly.
- **Observed Evidence:**
  - Updating pending and executing tasks succeeds.
  - State transitions are logged accurately.
  - Continuation tasks are created for terminal parent tasks without updating the parent.
  - Session context reconstruction loads all data cleanly.
  - Startup recovery reconciles interrupted tasks to terminal failed status without conflict.
- **Test Proof:** `tests/test_session_recovery.py::test_full_row_trigger_does_not_block_legitimate_operations` (PASS).

---

## 3. Summary Verdict

All adversarial, edge-case, and crash-boundary scenarios pass with 100% compliance. The persistence and recovery subsystems guarantee:
1. Complete determinism under crash boundaries.
2. Zero blind replay of mutating actions.
3. Fail-closed security for human approvals across all six lifecycle states.
4. Defense-in-depth sanitization of tested credentials on disk across all tables.
5. Linearizable optimistic concurrency control and unique-index-enforced continuation concurrency safety.
6. Database-engine-level enforcement of full-row terminal update immutability via SQLite triggers.
7. Backward-compatible, non-destructive migration deduplication preserving 100% of historical records.
8. Full isolation and transactional rollback for competing concurrent continuation attempts.
