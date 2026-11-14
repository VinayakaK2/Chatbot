# Phase 4 Change Impact Findings & Remediation Proposals

**Target Phase:** Phase 4 Forensic Engineering Audit  
**Commit SHA:** `3b620e646637468702efdbee2b61d9ae031dd6d5`  
**Purpose:** Pre-change impact analysis for all findings identified during forensic audit (AUDIT-ONLY: No modifications applied).  

---

## 1. Change Impact Report: SEC-01 (Sensitive Data in Event Streams)

```
CHANGE IMPACT REPORT

Target file: core/agent_loop.py
Target symbol/function: AgentLoop._emit_event(), tool event construction lines 481, 509

Current behavior:
Full raw tool arguments ("arguments": tc.raw_arguments) are emitted in TOOL_REQUESTED events.
Full tool output ("output": envelope.output) is emitted in TOOL_COMPLETED events.
If a file read contains secrets or credentials, they are broadcast over the SSE stream and stored in memory.

Proposed change:
Add an event payload summarizer or redaction filter for TOOL_REQUESTED and TOOL_COMPLETED events.
For filesystem tools:
- write_file: emit {"path": path, "content_length": len(content), "preview": content[:100]} in events, while preserving full arguments for execution.
- read_file: emit {"path": path, "bytes_read": len(content), "preview": content[:200]} in event payload, while preserving full output in the AgentMessage history passed to the LLM.

Direct imports:
None. core/agent_loop.py already imports RuntimeEvent, RuntimeEventType.

Indirect dependencies:
core/task_service.py (subscribers read events from TaskService._events).
api/routes/tasks.py (streams events via SSE).

Function callers:
AgentLoop.run()

Downstream dependencies:
frontend/src/components/activity/ToolActivityItem.tsx
frontend/src/types/api.ts

API contracts affected:
SSE RuntimeEvent payload format. Frontend expects output string or object.
Preserving "output" key with truncated preview maintains backward compatibility.

Frontend consumers affected:
ToolActivityItem expects string or object; already handles truncation text.

Tests and fixtures affected:
tests/test_agent_loop.py (verifies emitted event payloads).
tests/test_filesystem_integration.py.

Frozen components affected:
Phase 3 event contracts. Needs explicit operator approval for event schema refinement.

Potential regressions:
If LLM message history used the summarized event instead of the ToolResultEnvelope, model context would lose file content. Remediation must separate event emission from conversation history update.

Security implications:
Greatly enhances security by preventing leakage of secrets over SSE.

Concurrency implications:
None.

Required regression tests:
tests/test_agent_loop.py, tests/test_filesystem_integration.py, tests/test_tasks_api.py.

Rollback strategy:
Revert commit if event consumers break.

Decision:
NEEDS CLARIFICATION / OPERATOR APPROVAL (Phase 3 contract touch).
```

---

## 2. Change Impact Report: CONC-01 (Concurrent Edit Overwrite)

```
CHANGE IMPACT REPORT

Target file: core/workspace.py
Target symbol/function: WorkspaceContext.edit_file_text()

Current behavior:
Checks substring presence (current_content.count(old_str)). If file was modified concurrently
by another task, but old_str still exists, edit proceeds and overwrites other changes without conflict detection.

Proposed change:
Option A (Optimistic concurrency): Accept expected_hash (SHA-256 of file at read time).
Verify hash(current_content) == expected_hash before applying edit.
Option B (Per-file lock): Implement threading.Lock / asyncio.Lock keyed by canonical path in WorkspaceContext.
Synchronize atomic_write_text and edit_file_text under per-file lock.

Direct imports:
None.

Indirect dependencies:
tools/filesystem.py (EditFileInput model would need optional expected_hash if Option A chosen).

Function callers:
tools/filesystem.py::create_filesystem_tools().edit_file()

Downstream dependencies:
AgentLoop tool calling.

API contracts affected:
None if Option B (internal locking) is chosen.
Tool schema changes if Option A is chosen.

Frontend consumers affected:
None.

Tests and fixtures affected:
tests/test_filesystem.py
tests/test_workspace_security.py

Frozen components affected:
None. Confined to Phase 4 workspace.py.

Potential regressions:
Per-file lock contention under heavy concurrency. Deadlock if multiple files locked out of order.
(Single-file locking is deadlock-free).

Security implications:
Eliminates race conditions and lost updates.

Concurrency implications:
Guarantees linearizability of file edits on disk.

Required regression tests:
tests/test_filesystem.py, tests/test_filesystem_integration.py.

Decision:
RECOMMENDED FOR PHASE 4 REMEDIATION (Option B: Internal per-file mutex).
```

---

## 3. Change Impact Report: SEC-03 (Latin-1 Binary Misclassification)

```
CHANGE IMPACT REPORT

Target file: core/workspace.py
Target symbol/function: WorkspaceContext.read_text_bounded()

Current behavior:
Calls is_binary_file() which only samples the first 4096 bytes. If bytes 0..4095 are ASCII,
is_binary_file() returns False. Then fallback cascade attempts latin-1, which decodes any byte sequence.

Proposed change:
Before falling back to latin-1, check raw_bytes for b"\x00" and control-character threshold.
If raw_bytes contains null bytes or high control character ratio, raise BinaryFileError instead of falling back to latin-1.

Direct imports:
None.

Function callers:
WorkspaceContext.read_text_bounded(), read_line_range(), edit_file_text().

Downstream dependencies:
tools/filesystem.py, tools/search.py.

API contracts affected:
None.

Frontend consumers affected:
None.

Tests and fixtures affected:
tests/test_workspace_security.py.

Frozen components affected:
None.

Potential regressions:
Legacy non-UTF8 text files using high-ASCII codepoints could be rejected if control threshold is too strict.

Decision:
APPROVED FOR FUTURE PATCH.
```

---

## 4. Change Impact Report: ARCH-03 (Truncation Metadata Discrepancy)

```
CHANGE IMPACT REPORT

Target file: core/tool_dispatcher.py
Target symbol/function: ToolDispatcher.dispatch() line 236-248

Current behavior:
truncated is only set to True if isinstance(output, str) and len(output) > max_output_chars.
Dictionary outputs from filesystem/search tools having output["truncated"] = True leave metadata["truncated"] = False.

Proposed change:
In ToolDispatcher.dispatch():
if isinstance(output, dict) and output.get("truncated") is True:
    truncated = True
elif isinstance(output, str) and len(output) > self.max_output_chars:
    ...

Direct imports:
None.

Function callers:
ToolDispatcher.dispatch()

Downstream dependencies:
AgentLoop, ToolResultEnvelope metadata.

API contracts affected:
None. ToolResultEnvelope.metadata schema already includes "truncated": bool.

Frontend consumers affected:
None.

Tests and fixtures affected:
tests/test_tool_dispatcher.py.

Frozen components affected:
Phase 2 ToolDispatcher.

Decision:
APPROVED FOR PHASE 4 POLISH.
```
