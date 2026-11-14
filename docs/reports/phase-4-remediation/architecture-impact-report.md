# Architecture Impact Report: Phase 4 Remediation

**Audit Reference:** Phase 4 Architectural Boundary & Frozen Phase Preservation  
**Status:** Invariants Preserved  
**Scope:** Architecture Layering, Contract Compatibility, and Non-Goals Enforcement  

---

## 1. Frozen Phase Boundary Integrity

A mandatory requirement of this remediation was to strictly preserve the frozen contracts of Phase 0, Phase 1, Phase 2, and Phase 3.

### Invariant Verification:
1. **Phase 0 & 1 (Project Architecture & Environment):**
   - No new external packages or Python dependencies introduced in `pyproject.toml` or `package.json`.
   - Maintained single-process runtime architecture.
2. **Phase 2 (Model Gateway & Event Contracts):**
   - `ModelProvider`, `ModelResponse`, `TokenUsage`, and `RuntimeEvent` schemas were not modified.
   - Streaming event protocols remained backward-compatible with the frontend SSE parser.
3. **Phase 3 (Task Lifecycle & Execution Loop):**
   - The F-02 atomic task-state transitions and cancellation invariants were maintained without change.
   - `AgentLoop.run()` preserved all budget checks (`max_iterations`, `max_tool_calls`, `task_timeout_seconds`, `cancellation_token`).
   - The only change in `core/agent_loop.py` was sanitizing progress event payloads before emitting them to the event sink.

---

## 2. Layer-by-Layer Remediation Impact

### 2.1 Layer 1: Workspace & Filesystem Core (`core/workspace.py`)
- **Changes:**
  - Added thread-safe canonical path mutex (`_path_locks`, `_get_path_lock`).
  - Added full read-buffer binary detection in `read_text_bounded()`.
  - Added `finally:` temporary file cleanup in `atomic_write_text()`.
  - Sanitized host paths in error strings.
- **Architectural Assessment:** No public API signatures were altered. `WorkspaceContext` remains completely self-contained and free of external runtime dependencies.

### 2.2 Layer 2: Tool Dispatcher & Permissions (`core/tool_dispatcher.py`)
- **Changes:**
  - In `DefaultPermissionPolicy`: Added clear architectural documentation defining `WORKSPACE_WRITE` boundaries (confined to workspace mutation operations).
  - In `ToolDispatcher.dispatch()`: Added dictionary truncation propagation into `ToolResultEnvelope.metadata["truncated"]`.
- **Architectural Assessment:** Aligns dispatcher envelope metadata with structured tool responses without breaking existing string-based truncation.

### 2.3 Layer 3: Agent Execution Loop (`core/agent_loop.py`)
- **Changes:**
  - Added `_sanitize_tool_requested_payload()` and `_sanitize_tool_completed_payload()`.
  - Mapped progress events to sanitized payloads.
- **Architectural Assessment:** Enforces the fundamental boundary rule: The internal LLM reasoning loop (`AgentMessageRole.TOOL`) retains full unredacted tool results, while external SSE progress broadcasts are sanitized to protect user secrets.

### 2.4 Layer 4: Task Service (`core/task_service.py`)
- **Changes:**
  - Added optional parameter `auto_register_tools: bool = True` to `TaskService.__init__`.
- **Architectural Assessment:** Prevents unintentional mutation of caller-supplied `ToolRegistry` instances when isolated toolsets are required, while retaining zero-config defaults for the application entry point.

### 2.5 Layer 5: Frontend Activity Visualization (`frontend/`)
- **Changes:**
  - Created `frontend/src/test/FilesystemActivity.test.tsx` containing 6 unit tests.
  - Removed 3 unused type imports in `frontend/src/services/api-client.ts`.
- **Architectural Assessment:** Validates UI diff visualization and activity pill rendering against backend runtime event contracts without altering UI layout or introducing external frontend dependencies.

---

## 3. Non-Goals & Scope Confinement

The remediation adhered strictly to the project non-goals:
- **No Database / SQLite:** State persists in memory and through structured workspace files; no SQL engine introduced.
- **No Shell Execution:** Arbitrary host shell access remains prohibited in Phase 4.
- **No Remote Workers / Microservices:** Execution is confined to the local FastAPI process.
- **No Premature Phase 5 Implementation:** Interactive user-approval modals for command execution and git tooling were deferred to Phase 5.
