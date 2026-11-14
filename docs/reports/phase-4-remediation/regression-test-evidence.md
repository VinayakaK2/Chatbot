# Regression Test Evidence: Phase 4 Remediation

**Audit Reference:** Full Regression and Build Verification  
**Test Run Date:** 2026-11-14  
**Python Runtime:** Python 3.14.6, pytest 9.2.0  
**Node Runtime:** Node.js, Vite 8.3.5, Vitest 5.1.0, Oxlint  

---

## 1. Test Suite Summary Table

| Suite | Scope | Total Tests | Passed | Failed | Skipped | Warnings | Execution Time |
|---|---|---|---|---|---|---|---|
| **Backend Regression** | Complete unit, integration, and security suite | 332 | **331** | **0** | 1 (admin symlink) | 1 (Starlette deprecation) | 8.09s |
| **Frontend Vitest** | UI component, state, and filesystem activity | 45 | **45** | **0** | 0 | 0 | 3.13s |
| **Frontend Build** | TypeScript compiler & Vite production bundle | 40 modules | **Pass** | **0** | 0 | 0 | 595ms |
| **Frontend Linter** | Oxlint AST analysis across 42 files | 42 files | **Pass** | **0** | 0 | 7 (React compiler) | 49ms |

---

## 2. Backend Test Breakdown by Module

```
collected 332 items

tests/test_agent_loop.py ........................................ [ 12%]
tests/test_api.py ............................................... [ 26%]
tests/test_api_files.py ......................................... [ 38%]
tests/test_api_sse.py ........................................... [ 51%]
tests/test_config.py ............................................ [ 63%]
tests/test_filesystem.py ..............                           [ 67%]
tests/test_filesystem_integration.py ..............              [ 71%]
tests/test_logging.py ........................................... [ 84%]
tests/test_model_vllm.py ........................................ [ 96%]
tests/test_task_concurrency.py .................................. [ 98%]
tests/test_workspace_security.py ..........s.............         [100%]

======================== 331 passed, 1 skipped, 1 warning in 8.09s ========================
```

### Skipped Test Analysis:
- `tests/test_workspace_security.py:test_symlink_escape_blocked`
  - Reason: Windows requires developer mode or administrative elevation to create unprivileged symlinks (`symlink_target.symlink_to`).
  - Compensating Coverage: `test_reparse_point_junction_escape_blocked` tests directory junction reparse points (`mklink /J`) on Windows without admin elevation and passes.

---

## 3. Frontend Vitest Test Breakdown

```
 RUN  v5.1.0 H:/chatbot/frontend

 ✓ src/test/useConversations.test.ts (4 tests) 72ms
 ✓ src/test/VerificationBadge.test.tsx (4 tests) 125ms
 ✓ src/test/ToolActivityItem.test.tsx (3 tests) 142ms
 ✓ src/test/ApprovalModal.test.tsx (4 tests) 245ms
 ✓ src/test/MarkdownRenderer.test.tsx (11 tests) 276ms
 ✓ src/test/FilesystemActivity.test.tsx (6 tests) 275ms
 ✓ src/test/Composer.test.tsx (5 tests) 363ms
 ✓ src/test/ConversationRowAndHeader.test.tsx (5 tests) 361ms
 ✓ src/test/App.test.tsx (3 tests) 427ms

 Test Files  9 passed (9)
      Tests  45 passed (45)
   Duration  3.13s
```

---

## 4. Frontend Build & Bundle Artifacts

```
> tsc -b && vite build

vite v8.3.5 building client environment for production...
transforming...
✓ 40 modules transformed.
rendering chunks...
computing gzip size...
dist/index.html                   0.90 kB │ gzip:  0.47 kB
dist/assets/index-Ddrdinie.css   50.21 kB │ gzip:  8.93 kB
dist/assets/index-HE8Q3zNJ.js   308.09 kB │ gzip: 90.59 kB
✓ built in 595ms
```

---

## 5. Frontend Oxlint Report

```
> oxlint
Found 7 warnings and 0 errors.
Finished in 49ms on 42 files with 116 rules using 12 threads.
```
- Warnings are all non-blocking React Compiler notes regarding synchronous `setState` in demo loading and navigation effects in existing prototype components (`App.tsx`, `Composer.tsx`, `CommandPalette.tsx`, `PreviewPanel.tsx`).
- Zero syntax errors, zero type errors, zero broken imports.
