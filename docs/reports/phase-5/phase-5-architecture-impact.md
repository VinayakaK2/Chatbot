# Phase 5 — Architecture & Change-Impact Report
## Dependency Graph, Layer Boundaries & Backward Compatibility

**Document Reference:** `docs/reports/phase-5/phase-5-architecture-impact.md`  
**Phase:** Phase 5 — Shell Execution, Approvals, and Permission Policy  
**Baseline Anchor:** `18c817331eeb8779dc22e2fde05183ab9d3a64fb` (Frozen Phase 4 Baseline)  
**Implementation Commit:** `92c194d2c3f46a171e31c4b2b3bcb7b2f0b52e0e`  
**Target Environment:** Windows 11 (NTFS), Python 3.14.6, FastAPI, React 19 + TypeScript  
**Applicable Research Foundation:** `2604.14228v3.pdf` (Sections 2, 3, 5, 9.2, 13.1)

---

## 1. Executive Summary

Phase 5 introduces shell execution and runtime-owned human decision authority without perturbing the frozen architectural boundaries established in Phases 0 through 4. The implementation strictly adheres to the dependency direction:
$$\text{Frontend (UI)} \longrightarrow \text{FastAPI (API)} \longrightarrow \text{Core Runtime (AgentLoop / Dispatcher / Permissions)} \longrightarrow \text{Tools / Model}$$

Zero circular dependencies, zero backward-incompatible contract mutations, and zero regressions across Phase 0–4 test suites were introduced.

---

## 2. Component Inventory & Responsibility Matrix

```mermaid
graph TD
    subgraph Presentation["Presentation Layer (Browser)"]
        UI["React 19 GUI (App.tsx)"]
        Modal["ApprovalModal.tsx"]
        Viewer["FileViewerPanel.tsx"]
    end

    subgraph API["Application & API Boundary"]
        FastAPI["FastAPI App (app.py)"]
        TaskRoute["tasks.py (/api/tasks)"]
        TaskService["TaskService (task_service.py)"]
    end

    subgraph Core["Core Agent Runtime"]
        Loop["AgentLoop (agent_loop.py)"]
        Disp["ToolDispatcher (tool_dispatcher.py)"]
        Perm["CommandPermissionEvaluator (permissions.py)"]
        Appr["ApprovalManager (approval.py)"]
    end

    subgraph Tools["Execution & Tool Layer"]
        Registry["ToolRegistry (tool_registry.py)"]
        Shell["PowerShellRunner (shell.py)"]
        FS["Filesystem Tools (Phase 4)"]
    end

    UI --> TaskRoute
    Modal --> TaskRoute
    TaskRoute --> TaskService
    TaskService --> Loop
    TaskService --> Appr
    Loop --> Disp
    Disp --> Perm
    Disp --> Appr
    Disp --> Registry
    Registry --> Shell
    Registry --> FS
```

### 2.1 File Change Log

| File Path | Change Type | Lines Added/Removed | Purpose & Architectural Responsibility |
|:---|:---|:---|:---|
| `core/permissions.py` | **NEW** | +237 / -0 | Deny-first command classification engine (`DENY > ASK > ALLOW`), hard-deny regex catalog, dev command matcher. |
| `core/approval.py` | **NEW** | +384 / -0 | Volatile in-memory human approval store, exact-action hash binding, single-use token lifecycle, OBS-02 synchronization. |
| `tools/shell.py` | **NEW** | +341 / -0 | Constrained PowerShell runner, CWD containment, environment sanitization, process-tree kill via `taskkill`, secret redaction. |
| `tests/test_permissions.py` | **NEW** | +308 / -0 | 84 unit and adversarial tests covering hard denies, chaining, metacharacters, and category evaluations. |
| `tests/test_approval.py` | **NEW** | +219 / -0 | 10 tests for approval state transitions, exact-action hash verification, single-use consumption, expiration, cancellation. |
| `tests/test_shell.py` | **NEW** | +241 / -0 | 13 tests for execution, returncode preservation, stderr capture, cwd checks, truncation, secrets, and taskkill. |
| `core/tool_dispatcher.py` | **MODIFIED** | +182 / -32 | Implements `evaluate_permission`, coordinates with `ApprovalManager` for `ASK`, preserves `exit_code` metadata. Preserves `check_permission`. |
| `core/agent_loop.py` | **MODIFIED** | +19 / -2 | Dispatches structured `APPROVAL_NEEDED` and `PERMISSION_DENIED` runtime events via execution context `event_sink`. |
| `core/task_service.py` | **MODIFIED** | +49 / -4 | Wires `ApprovalManager` to `ToolDispatcher`, resolves approvals via `record_approval_decision`, coordinates task cancellation. Preserves Phase 4 registry invariant via `auto_register_shell_tools=False`. |
| `tools/registry_setup.py` | **MODIFIED** | +30 / -2 | Adds `register_shell_tools` while preserving `get_all_workspace_tools` and `register_workspace_tools`. |
| `frontend/src/components/common/Icons.tsx` | **MODIFIED** | +6 / -0 | Adds `IconMaximize` SVG icon for split-view layout. |
| `frontend/src/components/layout/FileViewerPanel.tsx`| **MODIFIED** | +125 / -2 | Resolves JSX closing tags, wires icons for document tabs and preview modes. |
| `frontend/src/components/layout/PreviewPanel.tsx` | **MODIFIED** | +86 / -22 | Integrates default tabs (`agent_loop.py`, `powershell.exe`) matching verified UI designs. |
| `docs/phases.md` | **MODIFIED** | +22 / -0 | Records Phase 5 implementation completion and readiness for operator audit. |

---

## 3. Boundary & Invariant Preservation Analysis

### 3.1 Phase 0–3 Invariants (Concurrency & Session Integrity)
- **F-02 Mutual Exclusion & Task Locks:** `TaskService._get_task_lock(task_id)` and linearizable state transitions (`_transition_task_state`) are unmodified. All approval resolutions and cancellations acquire the proper task locks.
- **OBS-01 Clean Task Locks:** Retained strictly.
- **OBS-02 Approval Store Concurrency:** The legacy placeholder in `TaskService._approvals` was upgraded to delegate to `ApprovalManager`, which uses dedicated `threading.RLock` and `asyncio.Event` primitives. Concurrent resolutions, queries, and expirations are completely synchronized and race-condition free.

### 3.2 Phase 4 Invariants (Filesystem Security & Atomic Operations)
- **Canonical Realpath Containment:** `WorkspaceContext` is unchanged. `PowerShellRunner` directly imports `WorkspaceContext` and verifies that `os.path.commonpath([workspace.root_path, target_cwd]) == workspace.root_path`.
- **ARCH-02 Registry Control Invariant:** In Phase 4, `TaskService(..., auto_register_tools=True)` asserts that exactly 8 workspace tools are registered. By defaulting `auto_register_shell_tools=False` in `TaskService.__init__`, Phase 4 integration tests pass with zero modification (`assert len(custom_registry) == 8`).

### 3.3 Event Stream Invariants (SSE Stream Integrity)
- The event schema `RuntimeEvent` is unchanged.
- `RuntimeEventType.APPROVAL_NEEDED` and `RuntimeEventType.PERMISSION_DENIED` were already defined in `api/schemas/events.py` and are emitted with sanitized payloads, ensuring 100% compatibility with frontend listeners.

---

## 4. Verification Evidence Summary

- **Backend Pytest:** 438 passed, 1 skipped in 18.50s (0 failures, 0 regressions).
- **Frontend Vitest:** 49 passed in 3.57s (100% pass rate).
- **Production Build:** `tsc -b && vite build` succeeded in 294ms with 0 errors.
- **Linter:** Oxlint completed with 0 errors and 10 non-blocking notices.

---
**Conclusion:** Architecture and contract impact is verified clean, backward-compatible, and production-ready.
