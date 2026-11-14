# Phase 4 Remediation Plan: Security Hardening, Concurrency & Observability

**Target Phase:** Phase 4 — Workspace and Filesystem Tools  
**Commit Baseline:** `3b620e646637468702efdbee2b61d9ae031dd6d5`  
**Date:** 2026-11-14  
**Status:** Approved for Implementation  

---

## 1. Remediation Scope & Objective

This plan addresses all 10 verified findings from the independent forensic audit:
1. **SEC-01 (High):** Sensitive file contents and write payloads exposed in client-facing SSE progress events (`core/agent_loop.py`).
2. **CONC-01 (High):** Concurrent edit lost updates due to un-synchronized read-validate-swap critical section (`core/workspace.py`).
3. **SEC-02 (Medium):** Absolute host paths disclosed in `PathOutsideWorkspaceError` and `WorkspaceIOError` strings (`core/workspace.py`).
4. **SEC-03 (Medium):** Binary misclassification on Latin-1 fallback for files with ASCII headers exceeding 4096 bytes (`core/workspace.py`).
5. **CONC-02 (Medium):** Windows file handle contention raising `[WinError 5] Access is denied` during concurrent writes (`core/workspace.py`).
6. **CONC-03 (Low):** Orphaned `.tmp_write_*` files remaining after non-OSError cancellation/interruptions (`core/workspace.py`).
7. **ARCH-01 (Medium):** Granularity documentation and policy alignment for `WORKSPACE_WRITE` (`core/tool_dispatcher.py`).
8. **ARCH-02 (Low):** Explicit `auto_register_tools` flag in `TaskService` to prevent unintentional registry mutation (`core/task_service.py`).
9. **ARCH-03 (Informational):** Truncation metadata propagation for structured dictionary tool outputs (`core/tool_dispatcher.py`).
10. **OBS-01 (Medium):** Targeted frontend unit tests for filesystem activities, diff rendering, and error states (`frontend/src/test/FilesystemActivity.test.tsx`).

---

## 2. Technical Design & Architecture Invariants

### 2.1 SEC-01: Client-Facing Event Stream vs Internal LLM Context
- **Boundary Rule:** The LLM's multi-turn message history (`AgentMessageRole.TOOL`) must retain full tool outputs so the model can inspect code, analyze files, and propose modifications.
- **Remediation:** In `core/agent_loop.py`, introduce a dedicated `_sanitize_event_payload()` helper:
  - For `TOOL_REQUESTED`: If tool is `write_file` or `edit_file`, sanitize raw arguments to replace `"content"` or `"new_str"` with `"<redacted: N bytes>"`, extract `file_path`, and compute byte counts.
  - For `TOOL_COMPLETED`: If tool is `read_file` or `read_file_range`, emit a clean structured summary (`{"path": ..., "bytes_read": ..., "lines": ..., "truncated": ...}`) without broadcasting raw file contents over SSE.
  - If tool is `edit_file`, emit `{"path": ..., "replacements_made": ..., "diff": ...}`.
  - The internal `AgentMessage` appended to `history` continues to receive the full unredacted `ToolResultEnvelope` output.

### 2.2 CONC-01 & CONC-02: Canonical Path Mutex in WorkspaceContext
- **Remediation:** Add a re-entrant path lock manager in `WorkspaceContext`:
  ```python
  self._path_locks: Dict[str, threading.RLock] = {}
  self._path_locks_lock = threading.Lock()
  ```
- In `atomic_write_text()` and `edit_file_text()`, acquire `_get_path_lock(canonical_target)` across the entire critical section:
  `Acquire Lock -> Read Content -> Check Conflict -> Stage Temp File -> Atomic Swap -> Release Lock`
- Serialization prevents race conditions, eliminates lost updates in concurrent edits, and prevents Windows `[WinError 5] Access is denied` handle contention.

### 2.3 CONC-03: Reliable Temp-File Cleanup via `finally`
- Change `atomic_write_text()` exception block to use `try ... finally: if temp_path.exists(): temp_path.unlink()`.
- Guarantees that any exception (including `asyncio.CancelledError`, `KeyboardInterrupt`, or `MemoryError`) cleans up the temporary file immediately.

### 2.4 SEC-02: Host Path Sanitization
- In `WorkspaceContext.resolve_path()` and error generators, replace absolute path references with workspace-relative paths or sanitized tokens:
  - `Path '<path>' resolves outside workspace boundary.` (without mentioning `self._root_path`).
  - Strip Windows drive roots and absolute temporary directory prefixes from exception strings.

### 2.5 SEC-03: Full Read Buffer Binary Defense
- In `read_text_bounded()`, before attempting `latin-1` decode:
  - Check if `b"\x00" in raw_bytes`.
  - Calculate control-character ratio across `raw_bytes`.
  - If binary indicators are present, raise `BinaryFileError` rather than decoding as Latin-1 text.

### 2.6 ARCH-03: Dictionary Truncation Propagation
- In `ToolDispatcher.dispatch()`, update:
  ```python
  if isinstance(output, str) and len(output) > self.max_output_chars:
      truncated = True
  elif isinstance(output, dict) and output.get("truncated") is True:
      truncated = True
  ```

### 2.7 ARCH-02: TaskService Auto-Registration Control
- Add `auto_register_tools: bool = True` to `TaskService.__init__`. Callers supplying an isolated custom registry can pass `auto_register_tools=False` to prevent unexpected tool injection.

### 2.8 OBS-01: Targeted Frontend Tests
- Add `frontend/src/test/FilesystemActivity.test.tsx` verifying:
  - `read_file` activity rendering (collapsed, expanded, byte count, icon).
  - `write_file` activity rendering with sanitized arguments.
  - `edit_file` activity with diff display via `FileDiffViewer`.
  - `PathOutsideWorkspaceError` and denied status badges.
  - Truncated output indicators.
