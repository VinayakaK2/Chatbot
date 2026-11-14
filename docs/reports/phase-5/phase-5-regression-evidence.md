# Phase 5 — Full Regression & Verification Evidence
## Shell Execution, Approvals & Permission Policy

**Document Reference:** `docs/reports/phase-5/phase-5-regression-evidence.md`  
**Execution Date:** 2026-11-14  
**Current HEAD Commit:** `d00385848bb44030d5b0c95029e248b64e0a4f66`  
**Branch:** `master`  
**Status:** `VERIFIED — 100% PASSING — ZERO REGRESSIONS`  

---

## 1. Executive Summary

This document certifies the reproducible test and build evidence for Phase 5 ("Shell Execution, Approvals & Permission Policy"). All test suites across the entire repository—encompassing Phase 0 through Phase 5—were executed against the current working tree on Windows 11 (NTFS) using Python 3.14.6 and Node.js / Vite.

Key Results:
- **Backend Test Suite:** 438 passed, 1 skipped, 1 warning in 33.35s.
- **Frontend Test Suite:** 50 passed in 4.95s across 10 test files.
- **Frontend Build (`tsc -b && vite build`):** 0 errors, 0 warnings (clean build in 333ms).
- **Frontend Linter (`oxlint`):** 0 errors, 10 non-blocking React Compiler optimization notices.
- **Phase 0–4 Regressions:** 0 regressions across 331 pre-existing tests.

---

## 2. Environment Specification

| Parameter | Specification | Verification Command & Measured Output |
|---|---|---|
| Operating System | Microsoft Windows 11 Pro (10.0.22631) | `[System.Environment]::OSVersion` $\to$ Microsoft Windows NT 10.0.22631.0 |
| Filesystem | NTFS (Case-preserving, ACLs enabled) | `Get-Volume` $\to$ NTFS |
| Python Runtime | Python 3.14.6 (64-bit) in `.venv` | `.venv\Scripts\python.exe --version` $\to$ `Python 3.14.6` |
| Pytest Version | pytest 8.3.4, pytest-asyncio 0.25.0 | `pytest --version` $\to$ `pytest 8.3.4` |
| Node.js Runtime | Node.js v24.15.0 (64-bit) | `node --version` $\to$ `v24.15.0` |
| Vite / Vitest | Vite 8.3.5, Vitest 5.1.0 | `npm test -- --version` $\to$ `vitest/5.1.0` |
| Subprocess Shell | `powershell.exe` (Windows PowerShell 5.1) | `$PSVersionTable.PSVersion` $\to$ `5.1.22621.4391` |
| Git Baseline | Phase 4 Frozen commit `e79093a198206c165dbcb5015d0dcd5328778ea2` | `git rev-parse HEAD` |

---

## 3. Backend Test Suite Breakdown

### 3.1 Phase 5 Specific Test Suites (107 Tests)

| Test File | Total Tests | Passed | Failed | Skipped | Focus Area |
|---|---|---|---|---|---|
| `tests/test_permissions.py` | 84 | 84 | 0 | 0 | Deny-first precedence (`DENY > ASK > ALLOW`), hard-deny regexes across 8 attack categories, command chaining (`&&`, `\|\|`, `;`, `\|`, `\n`), safe dev command patterns, fail-closed defaults. |
| `tests/test_approval.py` | 10 | 10 | 0 | 0 | Human approval lifecycle (`PENDING` $\to$ `APPROVED`/`DENIED`/`EXPIRED`/`CANCELLED`/`CONSUMED`), exact-action SHA-256 token binding, single-use enforcement, async resolution, thread synchronization (`threading.RLock`). |
| `tests/test_shell.py` | 13 | 13 | 0 | 0 | Constrained PowerShell runner, CWD containment (`os.path.commonpath`), environment sanitization (`SAFE_ENV_VARS`), stdout/stderr capture and 65KB bounding, exit code preservation, secret redaction, and `taskkill.exe /F /T /PID` process-tree termination. |

### 3.2 Regression Suite Across Previous Phases (331 Tests)

| Module / Phase Area | Test Files | Passed | Status |
|---|---|---|---|
| Phase 0: Types, Workspace, Config | `test_types.py`, `test_workspace.py`, `test_config.py` | 42 | PASS |
| Phase 1: Context & Instructions | `test_context.py`, `test_instructions.py`, `test_compaction.py` | 48 | PASS |
| Phase 2: Agent Loop & Streaming | `test_agent_loop.py`, `test_streaming.py`, `test_sse.py` | 68 | PASS |
| Phase 3: Persistence & Recovery | `test_persistence.py`, `test_db.py`, `test_task_service.py`, `test_concurrency.py` | 67 | PASS |
| Phase 4: Workspace & Filesystem Tools | `test_fs_tools.py`, `test_workspace_boundary.py`, `test_path_sanitization.py`, `test_audit_evidence.py` | 106 | PASS (1 skipped*) |
| **Total Pre-Existing Tests** | | **331** | **ZERO REGRESSIONS** |

*\*Note on Skipped Test:* `test_symlink_traversal_prevention` is skipped on non-elevated Windows developer environments because creating symbolic links requires `SeCreateSymbolicLinkPrivilege` or Developer Mode. This was formally accepted and recorded in the Phase 4 Freeze documentation.

---

## 4. Frontend Test Suite Breakdown (50 Tests)

Execution Command: `npm --prefix frontend test -- --run`

| Test File | Tests | Duration | Status | Focus |
|---|---|---|---|---|
| `src/test/useConversations.test.ts` | 4 | 0.14s | PASS | Conversation history, switching, and state management hooks |
| `src/test/VerificationBadge.test.tsx` | 4 | 0.29s | PASS | Verification badge rendering, status states, and verification evidence popovers |
| `src/test/ToolActivityItem.test.tsx` | 3 | 0.33s | PASS | Collapsible tool activity rendering, arguments, status, and execution metadata |
| `src/test/ApprovalModal.test.tsx` | 4 | 0.61s | PASS | Interactive approval modal, risk tier, command details, SHA-256 hash display, Approve/Deny actions |
| `src/test/MarkdownRenderer.test.tsx` | 11 | 0.66s | PASS | Headings, paragraphs, code blocks, lists, links, syntax highlighting |
| `src/test/FilesystemActivity.test.tsx` | 6 | 0.66s | PASS | Filesystem tool activity items, read/write/edit/search pills, diff rendering (OBS-01) |
| `src/test/Composer.test.tsx` | 5 | 0.86s | PASS | Prompt input composer, model selection, send button, keyboard shortcuts |
| `src/test/ConversationRowAndHeader.test.tsx` | 5 | 0.86s | PASS | Sidebar conversation row, pin/archive actions, header title, model display |
| `src/test/FileViewerPanel.test.tsx` | **5\*** | 0.96s | PASS | Tab switching, raw/preview markdown toggle, sidebar toggle, **and copy file content dropdown (Test #5)** |
| `src/test/App.test.tsx` | 3 | 1.15s | PASS | Full application shell, layout panels, command palette toggle, keyboard shortcuts |
| **Total Frontend** | **50** | **4.95s** | **100% PASS** | 10 test files, 50 unit and integration tests |

*\*Explanation of 50-Test Count (Issue A Resolution):*  
In the initial Phase 5 draft, `FileViewerPanel.test.tsx` contained 4 tests (total 49). In commit `d757f53`, a 5th test was added (`opens 3 dots menu and allows copying file content`) to verify the dropdown action and clipboard interaction. The final repository HEAD contains exactly **50 passing frontend tests**.

---

## 5. Frontend Build & Static Analysis

### 5.1 TypeScript Compilation & Vite Build
```
Command: npm --prefix frontend run build
Output:
> frontend@0.0.0 build
> tsc -b && vite build

vite v8.3.5 building client environment for production...
transforming...
✓ 41 modules transformed.
rendering chunks...
computing gzip size...
dist/index.html                   0.90 kB │ gzip:  0.48 kB
dist/assets/index-BjOOA5sa.css   57.62 kB │ gzip: 10.02 kB
dist/assets/index-CLSv9yNS.js   321.89 kB │ gzip: 93.86 kB
✓ built in 333ms
```
**Verdict:** Zero TypeScript errors, zero compiler warnings. Production bundle successfully generated in 333ms.

### 5.2 Linter Execution (`oxlint`)
```
Command: npx --prefix frontend oxlint
Output:
Finished in 233ms on 44 files using 12 threads.
Found 10 warnings and 0 errors.
```
**Diagnostic Breakdown of the 10 Warnings:**
- 8 `react(set-state-in-effect)` notices: React Compiler optimization suggestions regarding cascading render triggers in effects (`CommandPalette.tsx`, `Composer.tsx`, `FileViewerPanel.tsx`, `App.tsx`).
- 1 `react(purity)` notice: Calling `Date.now()` during tab ID initialization in `FileViewerPanel.tsx`.
- 1 file-length notice: In `App.tsx` (exceeds single-screen heuristic limit).
- **0 errors, 0 unused-variable warnings** (all dead imports removed).

---

## 6. Execution Policy Status (Issue B Resolution)

### 6.1 Current Active Implementation: `-ExecutionPolicy Bypass`
The runner in `tools/shell.py` currently executes:
```powershell
powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -Command <command>
```

### 6.2 Operational Justification & Accepted Limitation (**P5-LIMIT-05**)
- On default Windows client editions, the system execution policy is `Restricted`, which prohibits execution of `.ps1` script files (causing `.venv\Scripts\activate.ps1`, `npm.ps1`, `npx.ps1`, `pnpm.ps1`, and CLI wrappers to fail with `PSSecurityException`).
- `ExecutionPolicy Bypass` removes execution-policy restrictions for that process. It is **not** a sandbox and does **not** strengthen security.
- **Status:** `-ExecutionPolicy Bypass` is the **active, implemented setting** and is formally logged as an accepted operational limitation (**P5-LIMIT-05**).
- **Forward-Looking Recommendation:** Transitioning to `-ExecutionPolicy RemoteSigned -Scope Process` is recommended for future hardening to block unsigned remote scripts downloaded from the internet. This recommendation has **NOT** been applied to Phase 5 to avoid introducing untested breaking changes to local script runners prior to freeze.

---

## 7. Conclusion

All 438 backend tests and 50 frontend tests pass cleanly with zero regressions. All operational boundaries and accepted limitations are honestly and transparently documented.
