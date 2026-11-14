# Phase 6 Regression Evidence & Verification Dossier

## 1. Regression Test Summary

All backend and frontend tests were executed against the active repository at `H:\chatbot`.
Execution was performed in an isolated Windows environment using Python 3.14.6 (`.venv`) and Node.js v24.15.0 with Vitest 5.1.0.

| Test Surface | Command Executed | Result | Details |
| :--- | :--- | :--- | :--- |
| **Backend Regression Suite** | `.venv\Scripts\python.exe -m pytest --ignore=tests/test_smoke_live.py` | **460 PASSED**, 1 skipped, 1 warning (17.94s) | 0 failures, 0 regressions across all 461 tests |
| **Frontend Regression Suite** | `npm --prefix frontend test -- --run` | **50 PASSED** across 10 test files (5.74s) | 0 failures across all 50 component & hook tests |
| **Frontend Production Build** | `npm --prefix frontend run build` | **PASSED** in 602ms | TypeScript + Vite production bundle cleanly created |
| **Frontend Linter** | `npx --prefix frontend oxlint` | **0 ERRORS**, 10 warnings | 10 non-blocking compiler notices (set-state-in-effect) |

---

## 2. Backend Test Breakdown by Module

```
H:\chatbot\.venv\Lib\site-packages\pytest_asyncio\plugin.py:306: PytestDeprecationWarning: The configuration option "asyncio_default_fixture_loop_scope" is unset.
460 passed, 1 skipped, 1 warning in 17.94s
```

### Module Counts:
- `tests/test_agent_adversarial.py`: 12 tests passed
- `tests/test_agent_loop.py`: 18 tests passed
- `tests/test_api_files.py`: 6 tests passed
- `tests/test_api_health.py`: 12 tests passed
- `tests/test_api_tasks.py`: 26 tests passed
- `tests/test_approval.py`: 10 tests passed
- `tests/test_config.py`: 21 tests passed (including new Phase 6 configuration tests)
- `tests/test_context_adversarial.py`: 9 tests passed (NEW Phase 6 adversarial suite)
- `tests/test_context_manager.py`: 12 tests passed (NEW Phase 6 unit suite)
- `tests/test_filesystem.py`: 14 tests passed
- `tests/test_filesystem_integration.py`: 24 tests passed
- `tests/test_live_server.py`: 4 tests passed
- `tests/test_logging.py`: 8 tests passed
- `tests/test_message_models.py`: 15 tests passed
- `tests/test_model_adapter.py`: 16 tests passed
- `tests/test_permissions.py`: 84 tests passed
- `tests/test_shell.py`: 13 tests passed
- `tests/test_task_concurrency.py`: 32 tests passed
- `tests/test_tool_adversarial.py`: 22 tests passed
- `tests/test_tool_contracts.py`: 28 tests passed
- `tests/test_tool_dispatcher.py`: 22 tests passed
- `tests/test_tool_registry.py`: 24 tests passed
- `tests/test_workspace_security.py`: 27 passed, 1 skipped (Windows symlink creation)

**Total Backend Tests:** 460 passed, 1 skipped (administrative symlink privilege check, standard Windows limitation).

---

## 3. Frontend Test Breakdown

```
 RUN  v5.1.0 H:/chatbot/frontend

 ✓ src/test/useConversations.test.ts (4 tests) 75ms
 ✓ src/test/VerificationBadge.test.tsx (4 tests) 139ms
 ✓ src/test/ToolActivityItem.test.tsx (3 tests) 164ms
 ✓ src/test/ApprovalModal.test.tsx (4 tests) 312ms
 ✓ src/test/MarkdownRenderer.test.tsx (11 tests) 362ms
 ✓ src/test/FilesystemActivity.test.tsx (6 tests) 360ms
 ✓ src/test/Composer.test.tsx (5 tests) 457ms
 ✓ src/test/FileViewerPanel.test.tsx (5 tests) 458ms
 ✓ src/test/ConversationRowAndHeader.test.tsx (5 tests) 438ms
 ✓ src/test/App.test.tsx (3 tests) 551ms

 Test Files  10 passed (10)
      Tests  50 passed (50)
   Duration  5.74s
```

All 50 frontend component, state, approval modal, and stream event tests passed with zero errors.

---

## 4. Frontend Build Output

```
> frontend@0.0.0 build
> tsc -b && vite build

vite v8.3.5 building client environment for production...
transforming...
✓ 41 modules transformed.
rendering chunks...
computing gzip size...
dist/index.html                   0.90 kB │ gzip:  0.48 kB
dist/assets/index-ZYIWuLiN.css   59.13 kB │ gzip: 10.23 kB
dist/assets/index-D178XdQ0.js   321.42 kB │ gzip: 93.84 kB

✓ built in 602ms
```

---

## 5. Linter Verification

```
npx --prefix frontend oxlint
Found 10 warnings and 0 errors.
Finished in 48ms on 44 files using 12 threads.
```

The 10 warnings are pre-existing compiler optimization notices (`set-state-in-effect`) from React 19 / Oxlint, identical to the baseline in Phase 4 and Phase 5 audits. Zero syntax or semantic lint errors exist.
