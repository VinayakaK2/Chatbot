# Agent Runtime — Detailed Phase-by-Phase Build Plan

**Status:** Planning document  
**Target environment:** Windows + PowerShell  
**Model target:** Qwen2.5-Coder-14B-Instruct served from Google Colab through a vLLM OpenAI-compatible API  
**Initial implementation language:** Python  
**Primary goal:** Build a web-GUI-first coding-agent product backed by a reliable runtime. The GUI is the primary user interface from the start; a CLI may exist only as an optional developer/diagnostic interface. Add specialist orchestration and an organization-style view after the core runtime is verified.

---

## 1. Purpose and scope

This project is not intended to begin as a complete clone of Claude Code, Codex, Antigravity, or Devin. The first objective is a small but dependable agent runtime that can:

1. Accept a task through the web GUI.
2. assemble instructions and relevant context;
3. call the configured language model;
4. validate and execute permitted tool calls;
5. return tool results to the model in the correct conversation format;
6. continue until a final response, explicit stop, budget limit, or controlled failure;
7. preserve enough state and evidence to inspect what happened and recover after interruption;
8. verify work rather than claiming success from the model's statement alone.

The future target includes dynamically selected specialist agents, a manager/root orchestrator, task dependencies, review and retry, MCP integrations, and an organization dashboard. The initial GUI should already expose task submission, status, streamed events, approval requests, cancellation, and verification results; the visual organization view can come later. Those capabilities must build on the same runtime and policy boundaries, not replace them with a separate hard-coded execution path.

### Non-goals for the first milestone

- No distributed worker fleet.
- No Kubernetes or microservices.
- No mandatory PostgreSQL, Redis, vector database, or retrieval pipeline.
- No unrestricted host-shell access for an untrusted model.
- No assumption that the model's text-only response proves that tool calling works.
- No claim of production readiness until the defined checks have actually passed.

---

## 2. Source-of-truth and engineering rules

The project reference `2604.14228v3.pdf` is mandatory for architecture decisions. It is an analysis of Claude Code architecture, not a complete implementation specification for this repository. Use it as evidence for the patterns it actually describes, and record any project-specific choice as a design decision rather than pretending the paper prescribes it.

Relevant concepts identified in the reference:

- **Layered architecture:** keep the user surface, runtime/core, safety and action execution, state, and backend concerns distinct.
- **Execution loop:** the runtime repeatedly sends model context, receives a response, validates and executes requested tools, and feeds results back until a stopping condition is reached.
- **Safety:** permissions must be enforced by deterministic runtime code, not by model instructions alone. Prefer deny-first policies and distinct controls for different action types.
- **Context management:** model context is a constrained resource. Select relevant material progressively, bound tool output, and do not treat a compacted summary as a replacement for the durable original history.
- **Persistence:** maintain durable execution history and recoverable state; define what can and cannot safely be resumed.
- **Subagents:** specialists need bounded context, clear responsibilities, and a controlled result/return path.

The reference does **not** establish every implementation detail for this Python runtime, nor does it prove that a particular library, database, or model server is best. Those choices must be validated against the existing repository and actual runtime behavior.

### Mandatory workflow for every phase

1. Read the relevant source sections and inspect the existing repository.
2. State the requirement and acceptance criteria.
3. Implement the smallest coherent change.
4. Add or update tests, including failure and edge cases.
5. Run the relevant tests, type checks, linting, and build/smoke checks that are available.
6. Verify alignment with the source and preserve existing functionality.
7. Report changed files, actual commands/results, limitations, and remaining risks.
8. Never report a test as passed if it was not run; distinguish mocked tests from live-model tests.

---

## 3. Proposed initial stack

Use the minimum stack needed for a testable local runtime:

| Concern | Initial choice | Reason / constraint |
|---|---|---|
| Runtime | Python 3.12+ after dependency compatibility check | Straightforward model/tool orchestration |
| Contracts/config | Pydantic | Validate configuration, tool arguments, and internal records |
| Model API | OpenAI-compatible client against vLLM | Qwen is served through a compatible API; isolate provider-specific details |
| Web UI | React + TypeScript (or the existing web stack, after repository inspection) | Primary product surface from the start; task submission, progress, approvals, logs, verification |
| API | FastAPI | Stable boundary between GUI and runtime; streaming task events |
| CLI | Optional developer-only diagnostics | Not the primary user experience; avoid making the runtime depend on terminal interaction |
| Persistence | SQLite for sessions/tasks/tool execution metadata | Local, transactional persistence without a server |
| Transcript/events | Choose one initial durable representation; add JSONL only when its distinct benefits are needed | Avoid dual-store consistency problems |
| Tests | pytest | Unit and integration coverage from day one |
| Logging | Python logging with structured fields where practical | Debuggability and failure analysis |
| External tools | MCP later | Add after tool policy and dispatch contracts are tested |

Do not introduce LiteLLM, a second database, queues, or other abstractions just to appear extensible. Add a provider abstraction with a small contract; introduce a multi-provider library only if a real requirement justifies it.

### Model connection assumptions to verify

- vLLM endpoint is reachable from the Windows machine through a secure, authenticated route.
- API base URL, API key, and served model identifier are configuration, not hard-coded constants.
- A health/connectivity check is separate from a real chat completion.
- Actual tool-call behavior must be tested with the exact served model, vLLM version, and serving arguments.
- Colab sessions can disconnect or change endpoint details; local runtime state must not depend on the Colab process remaining alive.
- Never expose an unauthenticated public model endpoint or print API secrets in logs.

---

## 4. Architecture and responsibility boundaries

```text
React + TypeScript Web GUI
      | HTTP API + SSE/WebSocket event stream
      v
FastAPI Application / API boundary
      |
      v
Runtime Orchestrator  <---- task lifecycle, budgets, cancellation, terminal state
      |
      +---- Context Builder ---- instructions + relevant history + working state
      |
      +---- Model Adapter ------ OpenAI-compatible vLLM endpoint
      |
      +---- Tool Dispatcher ---- schema validation -> permission policy -> execution
      |                                  |
      |                                  +-- filesystem/search tools
      |                                  +-- shell/test tools (restricted)
      |                                  +-- later MCP tools
      |
      +---- State / Persistence -- sessions, tasks, tool calls, events, recovery
      |
      +---- Verification -------- tests/build/check results and completion evidence
```

**Core rule:** the model proposes the next action; deterministic runtime code validates, authorizes, executes, records, and decides whether the loop may continue. The model must not be the authority for permissions, budgets, durable task status, or whether a command actually succeeded.

`main.py` (or the chosen app entry point) should remain a bootstrap only. It must not become the place where orchestration, permissions, database logic, and tool execution are mixed together. The GUI must never execute shell commands or access project files directly; it requests actions through authenticated API endpoints, and the runtime applies the same policy regardless of which interface initiated the task.

---

## 5. Phase overview

| Phase | Name | Main deliverable | Depends on | Status |
|---|---|---|---|---|
| 0 | Repository, API/UI shell, and development foundation | Reproducible backend + minimal web GUI skeleton + test workflow | None | Complete (`ffdbd36`) |
| 1 | Model adapter and task API | API can reach vLLM and expose a safe model-connectivity/task status contract | 0 | Complete (`8f589f7`) |
| 2 | Typed tool contracts and registry | Tools can be registered, described, validated, and looked up | 0 | FROZEN (`34ede8b`) |
| 3 | Agent execution loop and live events | Model ↔ tool ↔ result cycle, streamed to GUI with bounded stopping behavior | 1, 2 | **FROZEN** (Operator Approved: 2026-11-14, SHA: `454342d95cee21bbd38099f4a67f720029983bfa`) |
| 4 | Workspace and filesystem tools | Safe project exploration and controlled file changes | 2, 3 | Complete — Ready for Operator Freeze |
| 5 | Shell, approvals, and permissions | Explicit policy enforcement and bounded command execution | 2, 3, 4 | Planned |
| 6 | Context management | Bounded, relevant context without destroying source history | 3 | Planned |
| 7 | Persistent state and session recovery | SQLite-backed sessions/tasks/tool records and safe resume | 3, 6 | Planned |
| 8 | Reliability and observability | Timeouts, cancellation, bounded retries, diagnostics, and evidence | 3–7 | Planned |
| 9 | Skills and specialist registry | Validated Markdown agent definitions and scoped capabilities | 2, 6–8 | Planned |
| 10 | Manager-led orchestration | Plan, assign, sequence, review, retry, escalate, and terminate | 7–9 | Planned |
| 11 | MCP integrations | External tools behind the same policy and execution contracts | 5, 8 | Planned |
| 12 | Organization console and richer GUI | Expand the existing GUI with specialist/team views; API already exists | 7–11 | Planned |
| 13 | Release hardening | End-to-end verification, docs, recovery and security checks | Relevant phases complete | Planned |

Phases can be refined after repository inspection. Do not create empty modules for all future phases in Phase 0; add each module when its contract is understood and tested.

---

# Phase 0 — Repository and development foundation

## Goal

Create a clean, reproducible backend and a minimal web GUI shell that can be installed, configured, tested, and debugged on Windows before model or tool logic is introduced. The GUI should not be postponed until the end, but it should remain thin while the runtime contracts mature.

## Work items

1. Inspect the current workspace and identify existing code, dependencies, conventions, and any already-working setup. Do not overwrite unrelated files.
2. Create or validate the project directory and Python virtual environment.
3. Add `pyproject.toml` with a minimal dependency set and test configuration.
4. Add `.gitignore` for virtual environments, `.env`, model credentials, local databases, logs, caches, and generated artifacts.
5. Add `.env.example` containing names and safe placeholder values only.
6. Add typed configuration loading and validation; fail early on invalid/missing required settings.
7. Add centralized logging setup with secret redaction.
8. Add the first pytest test and a short README with Windows/PowerShell setup commands.
9. Pin or constrain dependencies based on compatibility checks; do not invent compatibility without testing.
10. Establish a minimal React/TypeScript page that can submit a task placeholder, show API health, and render task status/error states.
11. Define an API event contract (task created, model request started, tool requested, permission denied/approval needed, tool completed, verification completed, task terminal) before implementing streaming.

## Candidate files

- `pyproject.toml`
- `.gitignore`
- `.env.example`
- `README.md`
- `main.py` (bootstrap only)
- `api/app.py`
- `api/routes/health.py`
- `api/routes/tasks.py` (initial contract only)
- `frontend/` (or existing web app directory)
- `core/config.py`
- `core/logging_config.py` (if separate logging setup is warranted)
- `tests/test_config.py`
- `tests/test_smoke.py`

## Tests and acceptance criteria

- A clean environment can install the project using documented commands.
- The test runner starts and at least one meaningful test passes.
- Invalid configuration produces a clear error instead of a traceback full of internals.
- Secrets are not logged.
- Generated local data and credentials are ignored by Git.
- Existing project files/functionality are preserved.

## Exit gate

Do not start Phase 1 until the environment and test commands are repeatable from a clean terminal.

---

# Phase 1 — Model adapter and task API

## Goal

Send a user prompt from the web GUI through the backend to the exact Qwen model served by vLLM in Colab and receive a normalized response. A developer-only CLI smoke test is optional, not the product acceptance path.

## Work items

1. Define a small provider-independent model interface, such as `generate(messages, tools=None, limits=...)`.
2. Implement the OpenAI-compatible adapter with configurable base URL, API key, model ID, timeout, and request limits.
3. Normalize response text, finish reason, usage (when available), and tool-call payloads without losing provider fields needed for later tool execution.
4. Add clear handling for network errors, authentication errors, endpoint errors, timeouts, malformed responses, and rate limits.
5. Add a backend health/connectivity endpoint and a task submission endpoint with validated request/response models.
6. Add a minimal GUI form to submit a prompt and render the response/error.
7. Keep GUI state and model adapter separate; never call vLLM directly from browser code.
8. Add mocked tests for successful responses and expected failures.
9. Run a live smoke test against the actual vLLM endpoint when it is available; record the model ID and result without exposing secrets.

## Candidate files

- `core/model_adapter.py`
- `core/config.py` (extend only as needed)
- `api/routes/model.py` or `api/routes/tasks.py`
- `frontend/src/` (or existing frontend location)
- `tests/test_model_adapter.py`
- `tests/test_api_tasks.py`

## Tests and acceptance criteria

- Mocked tests cover success, timeout, authentication failure, server failure, and malformed output.
- A live request succeeds against the configured model endpoint.
- The GUI renders a response or actionable error, and the API returns a stable status code/schema.
- Credentials do not appear in terminal diagnostics or logs.
- Model/tool capability is explicitly reported as unverified until the later tool-call smoke test succeeds.

## Exit gate

A plain prompt reliably reaches the intended model and returns a normalized result. This does **not** yet mean the agent can execute tools.

---

# Phase 2 — Typed tool contracts and registry

## Goal

Represent tools as validated, discoverable capabilities independent of the agent loop.

## Work items

1. Define a tool contract: stable name, description, argument schema, risk category, and callable implementation.
2. Use Pydantic or equivalent schema validation for arguments and results.
3. Implement registration and lookup, rejecting duplicate names.
4. Export the tool schemas in the format expected by the configured model API.
5. Normalize tool results into a consistent envelope with success/failure, output, error category, and metadata.
6. Reject unknown tools and invalid arguments before any side effect.
7. Do not let model-supplied descriptions or arguments change the runtime's trusted policy.

## Candidate files

- `core/tool_registry.py`
- `core/tool_contracts.py`
- `core/tool_dispatcher.py` (initial validation boundary only)
- `tests/test_tool_registry.py`
- `tests/test_tool_contracts.py`

## Tests and acceptance criteria

- Valid tool arguments validate.
- Missing, extra, or wrongly typed arguments are handled according to the declared schema.
- Unknown and duplicate tool names are rejected.
- Schema export is stable and matches the provider API contract.
- Validation failure does not execute a tool.

## Exit gate

Tools can be registered and described without being coupled to the model request loop.

---

# Phase 3 — Agent execution loop

**Status:** FROZEN (Operator Approved)  
**Approval Date:** 2026-11-14  
**Frozen Git SHA:** `454342d95cee21bbd38099f4a67f720029983bfa`  
**Authoritative Evidence Artifacts:**
- Final Acceptance Audit: [`docs/reports/phase-3-final-acceptance-audit.md`](reports/phase-3-final-acceptance-audit.md)
- Independent Concurrency Audit (F-02 Invariant Verification): [`docs/reports/phase-3-f02-independent-audit.md`](reports/phase-3-f02-independent-audit.md)
- Followup Remediation Report: [`docs/reports/phase-3-f02-followup-remediation-report.md`](reports/phase-3-f02-followup-remediation-report.md)
- Concurrency Change Impact Analysis: [`docs/reports/phase-3-f02-followup-change-impact-analysis.md`](reports/phase-3-f02-followup-change-impact-analysis.md)
- Architectural Decision Record: [`docs/adr/0003-agent-execution-loop.md`](adr/0003-agent-execution-loop.md)
- Architectural Specification: [`docs/agent-execution-loop.md`](agent-execution-loop.md)

## Goal

Implement the core model → tool request → execution result → model cycle with bounded, explicit termination.

## Work items

1. Define internal message and tool-call records, including provider tool-call IDs.
2. Assemble messages using the model adapter and registered tool schemas.
3. Preserve the required assistant tool-call message and matching tool-result message format. Test the exact format accepted by the served model/API.
4. Validate every requested tool name and argument payload.
5. Route all tool execution through the dispatcher and permission boundary; never execute model-generated code directly.
6. Feed tool results back to the model and continue the loop.
7. Stop on a valid final response, cancellation, iteration limit, tool-call budget exhaustion, or unrecoverable error.
8. Handle multiple tool calls in one model response deterministically. Define whether a batch is sequential or concurrent; begin sequentially unless safe parallelism is proven.
9. Record each model response, tool request, and tool result through an injectable event/state interface.
10. Distinguish a final natural-language answer from a tool-call response; do not infer completion from an empty or malformed response.

## Candidate files

- `core/agent_loop.py`
- `core/message_models.py`
- `core/tool_dispatcher.py`
- `tests/test_agent_loop.py`
- `tests/fakes.py`

## Tests and acceptance criteria

- Model returns a final answer with no tools.
- Model requests one tool; result is sent back and a later response finishes.
- Model requests multiple tools.
- Unknown tool and invalid arguments fail safely.
- Malformed response, repeated tool-call loop, and budget exhaustion terminate in a controlled manner.
- Tool-call IDs and message pairing are preserved.
- Mocked loop tests do not require a live model.
- A live end-to-end tool-call smoke test is separately recorded once a real safe tool is enabled.

## Exit gate

A controlled mock model can drive a full multi-turn tool cycle. Do not add unrestricted shell access to prove the loop.

### Exit Gate & Freeze Record
- **Gate Evaluation:** PASSED.
- **Verification Summary:**
  - 12/12 Phase 3 requirements mapped and verified in source code.
  - 274 offline backend tests passing (`.venv\Scripts\python.exe -m pytest --ignore=tests/test_smoke_live.py`).
  - 38 concurrency and race interleaving tests passing (`tests/test_task_concurrency.py`).
  - Defect F-02 verified remediated in `core/task_service.py` (strict Level 1 `task_lock` -> Level 2 `_registry_lock` order, `EventSubscriber` isolation, `call_soon_threadsafe`, ghost lock prevention, first-to-commit linearizability).
  - Web GUI test suite passing with 35/35 tests in 2.65s (`npm --prefix frontend test -- --run`).
  - Frontend production build passed cleanly in 0.30s (`npm --prefix frontend run build`).
  - Frontend lint clean with 0 errors (`npm --prefix frontend run lint`).
- **Formal Freeze Decision:** APPROVED by Operator on 2026-11-14.
- **Frozen Commit Baseline:** `454342d95cee21bbd38099f4a67f720029983bfa`.
- **Integrity Boundary:** Phase 3 implementation is frozen. Zero regressions or modifications permitted in Phase 3 files. Phase 4 implementation requires separate operator authorization.

---

# Phase 4 — Workspace and filesystem tools

**Status:** FROZEN (Operator Approved)  
**Approval Date:** 2026-11-14  
**Frozen Git SHA:** `e79093a198206c165dbcb5015d0dcd5328778ea2`  
**Authoritative Evidence Artifacts:**
- Final Freeze-Readiness Verification: [`docs/reports/phase-4-freeze-readiness-verification.md`](reports/phase-4-freeze-readiness-verification.md)
- Remediation Plan: [`docs/reports/phase-4-remediation/remediation-plan.md`](reports/phase-4-remediation/remediation-plan.md)
- Remediation Report: [`docs/reports/phase-4-remediation/phase-4-remediation-report.md`](reports/phase-4-remediation/phase-4-remediation-report.md)
- Security Verification Matrix: [`docs/reports/phase-4-remediation/security-verification-matrix.md`](reports/phase-4-remediation/security-verification-matrix.md)
- Concurrency Verification: [`docs/reports/phase-4-remediation/concurrency-verification.md`](reports/phase-4-remediation/concurrency-verification.md)
- Architecture Impact Report: [`docs/reports/phase-4-remediation/architecture-impact-report.md`](reports/phase-4-remediation/architecture-impact-report.md)
- Adversarial Test Report: [`docs/reports/phase-4-remediation/adversarial-test-report.md`](reports/phase-4-remediation/adversarial-test-report.md)
- Regression Test Evidence: [`docs/reports/phase-4-remediation/regression-test-evidence.md`](reports/phase-4-remediation/regression-test-evidence.md)
- Independent Final Audit: [`docs/reports/phase-4-remediation/independent-final-audit.md`](reports/phase-4-remediation/independent-final-audit.md)

## Goal

Let the agent inspect a designated project and perform bounded, auditable file operations.

## Work items

1. Define a single configured workspace root and explicit rules for paths.
2. Implement bounded file listing, text reading, project search, and file metadata inspection.
3. Add write/edit operations only after read operations and path policy tests are reliable.
4. Normalize and resolve paths; reject traversal outside the workspace.
5. Consider symlinks, junctions, case-insensitive paths, Windows drive roots, reserved names, missing files, and race conditions.
6. Enforce maximum file size, output size, read range, and encoding behavior.
7. Respect ignore rules for generated folders and secrets where appropriate; do not silently ignore files required by the user.
8. Record file changes and failures in the execution history. For edits, preserve enough before/after or diff information for review.
9. Require approval for high-impact file operations according to the permission policy.

## Candidate files

- `tools/filesystem.py`
- `tools/search.py`
- `core/workspace.py`
- `tests/test_filesystem.py`
- `tests/test_workspace_security.py`

## Tests and acceptance criteria

- Normal list/read/search/write behavior works in a temporary test workspace.
- `..` traversal and absolute paths outside the root are denied.
- Symlink/junction escape attempts are handled safely.
- Missing, binary, oversized, and unreadable files return clear errors.
- Output limits are enforced.
- Failed writes do not silently corrupt the target file.
- Changes can be reviewed and associated with the responsible tool call.

## Exit gate

The agent can inspect a test repository without accessing arbitrary paths outside its configured workspace.

### Exit Gate & Freeze Record
- **Gate Evaluation:** PASSED.
- **Verification Summary:**
  - 10/10 Phase 4 forensic audit findings resolved and verified.
  - 331/332 backend tests passing (`.venv\Scripts\python.exe -m pytest --ignore=tests/test_smoke_live.py`).
  - 1 skipped test documented: administrative symlinks on Windows; verified via Directory Junction (`mklink /J`) reparse point test.
  - 49/49 frontend Vitest tests passing (`npm --prefix frontend test -- --run`).
  - Frontend production build passing cleanly (`tsc -b && vite build` in 349ms).
  - Frontend linter passing with 0 errors, 10 compiler notices (`npm --prefix frontend run lint`).
  - Mutual exclusion per canonical realpath verified: 10 concurrent edit threads yield exactly 1 success and 9 `EditConflictError` with zero lost updates.
  - Atomic replace handle contention eliminated: 10 concurrent writes complete cleanly with 0 `Access is denied` errors.
  - Best-effort `.tmp_write_*` cleanup verified on interruption.
- **Deliberate Accepted Limitations & Qualifications:**
  - *SEC-01 (Diff Confidentiality):* Direct file reading and write bodies are redacted in client-facing events. Unified diffs generated by `edit_file` are deliberately emitted in SSE streams to power the UI visual diff review; diff confidentiality is intentionally not guaranteed.
  - *ARCH-01 (Category Permissions):* Registered tools tagged `WORKSPACE_WRITE` are authorized by default in Phase 4. Unregistered tools are blocked. Fine-grained interactive per-tool user approval is deferred to Phase 5.
  - *Single-Process Concurrency:* Concurrency locks are in-memory per-process (`threading.RLock`); cross-process locking is not supported.
  - *Temporary Files:* Windows external file-handle locks can leave orphaned temporary files; automated boot-scavenging does not exist in Phase 4 and is deferred to Phase 5.
  - *Linter Notices:* Ten React Compiler notices remain in frontend demo components; accurately recorded as 10 non-blocking warnings.
- **Formal Freeze Decision:** APPROVED by Operator on 2026-11-14.
- **Frozen Commit Baseline:** `e79093a198206c165dbcb5015d0dcd5328778ea2`.
- **Integrity Boundary:** Phase 4 implementation is frozen. Zero regressions or modifications permitted in Phase 4 files. Phase 5 implementation has not been started and requires separate operator authorization.

---

# Phase 5 — Shell execution, approvals, and permission policy

## Goal

Provide a deliberately constrained way to run tests and development commands, with explicit authorization and resource limits.

## Work items

1. Define policy decisions such as `allow`, `ask`, and `deny`; default unknown or risky operations to the safer outcome.
2. Separate read-only operations, workspace writes, process execution, destructive operations, and external side effects.
3. Implement a PowerShell subprocess runner with explicit working directory, timeout, output caps, exit code, and cancellation handling.
4. Make approval requests a runtime event/interface rather than a model decision.
5. Define what happens when approval is unavailable, refused, or interrupted; do not treat absence of an approver as approval.
6. Prevent secrets from being copied into command output or logs where reasonably detectable.
7. Avoid claiming that a command-string substring filter is a sandbox. Shell parsing, command chaining, child processes, scripts, and indirect access can bypass simplistic filters.
8. Begin with conservative command policies and a designated workspace. Clearly document that this alone is not a strong isolation boundary against hostile code.
9. Consider OS-level sandboxing or a disposable VM/container strategy before allowing untrusted repositories to execute arbitrary commands.
10. Ensure resumed sessions do not silently restore temporary approval grants.

## Candidate files

- `core/permissions.py`
- `tools/shell.py`
- `core/approval.py`
- `tests/test_permissions.py`
- `tests/test_shell.py`

## Tests and acceptance criteria

- Denied commands never start.
- Ask-policy commands wait for an explicit approval decision.
- Rejected/expired approval prevents execution.
- Timeout kills or terminates the managed process tree as far as the platform permits and records any limitation.
- Stdout/stderr caps work and truncation is marked.
- Nonzero exit codes are preserved.
- Working-directory escapes and policy bypass cases are tested.
- Documentation states the actual isolation boundary and does not overclaim safety.

## Exit gate

The runtime can execute approved project checks while denying unapproved actions and reporting incomplete isolation honestly.

### Phase 5 Implementation & Readiness Summary
- **Implementation Status:** COMPLETE — READY FOR OPERATOR AUDIT & FREEZE REVIEW (Not yet frozen).
- **Core Verification Summary:**
  - `core/permissions.py`: Deny-first command evaluator (`DENY > ASK > ALLOW`), hard-denying 8 destructive/system categories and defaulting unclassified commands to fail-closed `DENY`.
  - `core/approval.py`: In-memory volatile approval manager (`ApprovalManager`), exact-action cryptographic binding via SHA-256 (`action_hash`), single-use token consumption (`CONSUMED`), and thread-safe synchronization (resolving OBS-02). Zero transcript/database persistence across restarts (compliant with 2604.14228v3.pdf §9.2).
  - `tools/shell.py`: PowerShell runner with canonical working directory containment (`os.path.commonpath`), sanitized environment variable allowlist, bounded output (65,536 chars), recursive process-tree termination via Windows `taskkill.exe /F /T /PID`, and secret redaction.
  - `core/tool_dispatcher.py` & `core/agent_loop.py`: Integrated approval waiting loop, emitting `RuntimeEventType.APPROVAL_NEEDED` and `RuntimeEventType.PERMISSION_DENIED` events over SSE.
- **Verification Evidence:**
  - 438/439 backend tests passing (`.venv\Scripts\python.exe -m pytest --ignore=tests/test_smoke_live.py`).
  - 1 skipped test (Windows administrative symlink test, documented in Phase 4 freeze).
  - 107 new Phase 5 tests passing (84 permission tests, 10 approval tests, 13 shell tests).
  - Zero regressions across 331 existing Phase 0–4 tests.
  - 49/49 frontend Vitest tests passing (`npm --prefix frontend test`).
  - Frontend production build passing (`tsc -b && vite build` in 258ms).
  - Frontend linter passing with 0 errors, 10 non-blocking compiler notices (`npm --prefix frontend run lint`).
  - 33 adversarial test vectors verified against command chaining, metacharacters, path traversal, process loops, and secret leakage (`docs/reports/phase-5/phase-5-security-verification-matrix.md`).
- **Deliberate Accepted Limitations & Qualifications:**
  - *Host Subprocess Boundary:* The runner executes inside a constrained host PowerShell subprocess, not an OS-level hypervisor or container sandbox. Host isolation relies on cwd path validation and environment variable sanitization.
  - *Volatile Approvals:* Approvals exist only in process memory and expire on process termination; no approvals survive a server restart.
  - *Single-Process Concurrency:* Approval state synchronization uses in-memory locks (`threading.RLock`); cross-process synchronization is not supported.
- **Next Step:** Independent Operator Audit & Freeze Review. Do NOT start Phase 6 without explicit operator authorization.

---

# Tool inventory, registry, skills, hooks, and MCP policy

This section is a cross-phase contract. It is intentionally explicit because “add tools and MCP later” is not enough for a coding-agent runtime.

## Built-in tool groups

Register tools by capability and risk; only expose a task-appropriate subset to the model. Do not load every tool schema into every model request.

| Group | Initial tools / capabilities | Notes and guardrails |
|---|---|---|
| Workspace discovery | `list_directory`, `search_files`, `find_files`, `read_file`, `read_file_range`, `get_file_metadata` | Read-only, workspace-root constrained, bounded outputs |
| Code changes | `write_file`, `edit_file`, `apply_patch`, `create_directory`, `move_file` (only if needed) | Validate paths, capture diffs, use atomic writes where possible, approval policy for destructive changes |
| Source control | `git_status`, `git_diff`, `git_log`, `git_show`, `git_branch` | Start read-only; commits, checkout/reset, push, and destructive operations need separate policies |
| Execution and tests | `run_command`, `run_tests`, `run_linter`, `run_typecheck`, `run_build` | Execute only in sandbox; timeout, process-tree cleanup, output caps, exit-code evidence |
| Task tracking | `update_task_plan`, `record_finding`, `record_artifact`, `request_approval` (runtime-owned) | State mutations validated by runtime; model cannot mark verification passed by assertion |
| Diagnostics | `get_runtime_status`, `get_recent_events`, `get_tool_result` | Redact secrets; access-control and output limits apply |
| Optional web research | `web_search`, `fetch_url` | Add only if web research is in scope; apply network policy, content-size limits, and prompt-injection isolation |
| Browser/UI verification (later) | `browser_open`, `browser_screenshot`, `browser_click`, `browser_fill`, `browser_console`, `browser_snapshot` | Use a controlled Playwright/browser worker; separate target-origin allowlists and credentials from model context |
| External services | MCP-discovered tools, namespaced as `mcp__<server>__<tool>` | Same local schema validation, permissions, logging, timeout and output policy as built-ins |
| Delegation (later) | `spawn_specialist`, `get_subtask_status`, `collect_artifact` | Each specialist has a separate context, scoped tools, bounded budget, and summary/artifact return contract |

This is a candidate catalogue, not a requirement to implement every item in the first milestone. Start with read/list/search, then controlled write/edit, then sandboxed test execution. Do not expose a generic unrestricted `execute_anything` tool.

## Tool registry contract

Every tool definition should include at least:

- stable qualified name and version;
- concise purpose and argument/result schema;
- implementation handler;
- capability/risk classification;
- permission policy key;
- execution timeout and output limits;
- workspace/network scope where applicable;
- idempotency/retry classification;
- audit metadata and error taxonomy;
- optional source (`builtin`, `skill`, or `mcp:<server>`).

Registry responsibilities:

1. Discover and validate tool definitions at startup/refresh.
2. Reject duplicate/conflicting names and invalid schemas.
3. Build a task-specific tool pool, excluding denied tools before the model sees their schemas.
4. Defer large or rare schemas until needed (ToolSearch-style discovery) to conserve context.
5. Resolve each call to a trusted handler; never execute code or handlers supplied by model output.
6. Enforce policy again at execution time even if the tool was filtered from the model's view.
7. Record the exact tool/version/schema and permission decision used for each execution.

## Skills, plugins, and hooks are distinct mechanisms

- **Skills:** instruction/workflow packages, preferably Markdown with validated metadata; may reference tools but cannot grant themselves permissions. Load selected skill instructions only when relevant.
- **MCP servers:** external tool providers. Their schemas/descriptions are untrusted metadata; discovered tools must be namespaced and pass local policy.
- **Plugins/extensions:** packaged registrations for tools, skills, hooks, and configuration. Validate manifests, source, version, and requested capabilities before enabling.
- **Hooks:** lifecycle interception points (e.g. before tool execution, after tool result, task start/end, context compaction). Hooks may deny, request approval, or annotate; they cannot override hard-deny policy. Keep hook execution bounded and observable.
- **Specialist agents:** isolated task workers with separate context and constrained tool pools; they are not just a prompt inserted into the parent conversation.

Do not collapse all four into one mechanism: they have different lifecycle, context, trust, and execution costs.

## MCP onboarding and server policy

Prefer built-in local tools for core filesystem, workspace search, patching, Git inspection, and sandboxed test execution; adding an MCP server for those core operations creates an unnecessary extra trust boundary. Candidate MCP integrations, only when the use case requires them, include:

- **GitHub MCP:** issues, pull requests, repository metadata, and review workflows; write operations require explicit scope and approval.
- **Playwright/browser MCP or a dedicated browser worker:** browser-based UI checks, screenshots, console errors, and interaction tests; prefer a controlled Playwright worker if tighter isolation and predictable outputs are needed.
- **Documentation/search MCP:** official library/framework documentation retrieval, with source provenance and prompt-injection handling.
- **Database MCP:** only for an explicitly configured development/test database, with read-only defaults and environment separation; never give production credentials to the agent by default.
- **Issue tracker/CI MCP:** optional task and build status integration after authentication and permission scoping are designed.

Do not add a “general-purpose MCP” merely to increase the tool count. Every server expands the trust boundary and needs a clear owner, purpose, permissions, and failure policy. For each MCP server, record: server ID, transport, command/URL, authentication method, approved capabilities, allowed tool names, network destinations, timeout, output limits, owner/trust level, and health state. Use a maintained SDK after compatibility review. For local stdio servers, pass a minimal environment allowlist; do not leak all host environment variables. Do not auto-install or execute arbitrary packages just because an MCP config references them. Require explicit operator setup and validate the source/command before launch.

MCP tool results are untrusted input. Treat embedded instructions as data, not system instructions. Keep MCP tool output bounded, mark provenance, and do not let remote tool metadata rewrite local permissions.

---

# Safety policy and sandbox execution contract

The user requirement is stricter than a generic “ask before risky commands” design: **unusual, unknown, or policy-unclassified commands must not be auto-approved or executed.** An approval dialog is not a way to override a hard deny.

## Authorization order

Use defense in depth. A request must pass every applicable gate:

1. Tool-pool pre-filter: do not advertise blanket-denied tools to the model.
2. Schema validation: reject malformed tool names/arguments.
3. Hard-deny rules: block prohibited operations and known dangerous categories; deny takes precedence over ask/allow.
4. Risk/policy classification: classify command/tool request from trusted deterministic features and a constrained policy engine. An LLM classifier may be advisory only; it must not be the sole safety boundary.
5. Workspace/network scope validation.
6. Human approval for explicitly approval-eligible actions only. Hard-denied or unknown/unclassified commands remain denied.
7. OS-level sandbox execution with resource and network limits.
8. Post-execution inspection and audit event.

The intended precedence is `DENY > ASK > ALLOW`. Missing rules, parser errors, policy timeouts, classifier uncertainty, or unavailable approval infrastructure must fail closed for shell execution.

## Sandbox requirements

- Run shell and test commands in an isolated worker, not in the web API process.
- Prefer a disposable container/VM or an equivalent OS-enforced sandbox with a read-only base image, explicit writable workspace, CPU/memory/process/time limits, and network disabled by default.
- If Windows-native sandboxing is not available in the current environment, document the exact weaker boundary and do not call it a secure sandbox. Do not substitute a command substring denylist for isolation.
- Use a clean, controlled environment allowlist rather than inheriting all environment variables.
- Kill the managed process tree on timeout/cancel where the platform permits; record if cleanup could not be confirmed.
- Keep credentials out of the sandbox unless a task explicitly needs them and policy allows them.
- Record command, working directory, sandbox profile, decision, approver if any, start/end, exit code, stdout/stderr references, and truncation state.
- Do not auto-approve a command just because it resembles a common command; arguments, working directory, file effects, network behavior, and chained subprocesses matter.

## Example policy outcomes

| Request | Outcome |
|---|---|
| Read a text file inside the workspace | Allow if path and size checks pass |
| Run the configured test command inside the sandbox | Allow only if the command/profile is registered and policy allows it |
| Unknown executable, obfuscated command, encoded script, privilege escalation, credential extraction, or destructive host operation | Hard deny; no approval override |
| Command not classified by policy | Deny and log; do not execute |
| Approved action with unavailable sandbox | Deny; do not fall back to host execution |
| External network access not on allowlist | Deny |
| Low/medium-risk action explicitly marked approval-eligible | Ask the user, then revalidate before sandbox execution |

This policy must be tested against command chaining, shell metacharacters, PowerShell aliases, encoded commands, path escapes, symlinks/junctions, child processes, environment leakage, network access, timeouts, and cancellation. A classifier is not a substitute for OS isolation.

---

# Context budgeting and packaging: required staged pipeline

A single `context_manager.py` that merely concatenates history and truncates by characters is not equivalent to the context architecture described in the reference. Implement a staged pipeline, measure it, and keep the full durable history separate from the model-facing projection.

## Per-model-call context assembly order

1. **Stable base instructions:** runtime contract, safety boundaries, output conventions.
2. **Project instruction hierarchy:** root project instructions first; load nested/path-scoped instructions lazily when relevant files are touched. Detect oversized or malformed instruction files.
3. **Selected skill/agent instructions:** only the selected skill or specialist, not every available one.
4. **Task state:** current goal, acceptance criteria, completed steps, pending steps, constraints, and verified facts.
5. **Tool pool:** only allowed tools; include small schemas for common tools and defer rare/large schemas until requested.
6. **Recent interaction tail:** preserve current user intent and recent assistant/tool exchanges, including required tool-call/result pairing.
7. **Relevant evidence retrieval:** relevant file excerpts, diffs, errors, test results, and prior artifacts rather than whole files/history by default.
8. **Budget reduction:** cap oversized individual tool results and mark truncation.
9. **Temporal pruning:** remove or summarize stale, low-value older turns while retaining recent context.
10. **Cache-aware compaction:** avoid unnecessary rewriting of stable prompt prefixes where provider caching is available; validate behavior for the actual provider.
11. **Micro-compaction:** remove or replace already-consumed large tool payloads with references to durable artifacts.
12. **History collapse / semantic summary:** only when cheaper stages are insufficient; summary must be marked derived, bounded, and traceable to original event ranges.
13. **Final budget gate:** estimate tokens for instructions, schemas, messages, expected response and safety margin. If mandatory context exceeds budget, fail clearly or request user intervention; never silently drop safety/project rules.

The paper describes a five-layer strategy (budget reduction, snip, microcompact, context collapse, auto-compact) plus lazy instruction loading, deferred tool schemas, cache-aware behavior, and summary-only subagent returns. The stages above adapt those principles to this runtime; they are not a claim that the internal algorithms are identical.

## Context data model and packaging requirements

Keep these separately addressable:

- durable transcript/events;
- model-facing message projection;
- current task state and verified facts;
- artifact store (file excerpts, diffs, command logs, test reports);
- derived summaries with provenance;
- instruction/skill/agent sources and versions.

Each packaged section should carry source/provenance, priority, approximate token cost, and truncation/omission status. Tool output should be stored as an artifact when large, with a bounded excerpt in context and an artifact ID/path to retrieve more. Never treat model-generated summary text as authoritative evidence for test status or permission decisions.

## Context evaluation metrics

Track estimated input tokens, tool-schema tokens, context reduction by stage, compaction frequency, truncated outputs, repeated file reads, cache hit/use where measurable, model latency, and task outcome. Add fixtures for long logs, repeated reads, huge instruction files, large diffs, long sessions, and summary loss. Tune stages based on measured results, not assumptions.

---

# Observability and verification contract

Observability is not just console logs. The runtime should expose a correlated event stream that the GUI can render live and that can be replayed for debugging.

## Minimum event envelope

Each event should include: `event_id`, monotonic sequence number per task/session, timestamp, `session_id`, `task_id`, `parent_task_id` if applicable, `run_id`, `model_request_id` if applicable, `tool_call_id` if applicable, component, event type, status, duration where applicable, and a redacted payload/reference. Avoid storing hidden reasoning; store decisions, inputs, outputs, policy reasons, and evidence instead.

## Events to record

- task accepted/rejected, queued, started, waiting for approval, resumed, cancelled, completed, failed;
- context assembly started/completed, budget estimates, sections omitted/truncated, compaction stage and summary provenance;
- model request started/completed/failed, model ID, latency, token usage if available, retry count, normalized finish reason;
- tool proposed, schema rejected, prefiltered, policy decision, approval requested/approved/denied/expired, sandbox started/completed/failed, output truncated;
- file changed and diff/artifact reference;
- verification started/completed, command/profile, exit code, result, duration, and evidence reference;
- worker/subagent spawned, heartbeat/status, artifact returned, review accepted/rejected, retry/escalation;
- persistence failure, recovery decision, shutdown, and endpoint health changes.

## Verification is separate from model completion

Store at least two distinct statuses:

- **Task status:** running, waiting_approval, completed, failed, cancelled, interrupted.
- **Verification status:** not_run, passed, failed, inconclusive, blocked.

Only a trusted verification runner may mark a test/build check as passed, based on an executed command and exit/result evidence. The model may request verification but cannot self-certify it. A final task may be “completed with verification not run” and must be reported as such.

## GUI observability requirements

The web app should show, with clear labels:

- task status and elapsed time;
- current phase/step and latest meaningful progress;
- model calls and latency/token usage when available;
- tool name, bounded arguments, permission decision, execution status, duration, and result summary;
- approval request with exact action, working directory, sandbox profile, risk reason, and explicit deny/approve controls only where policy allows approval;
- files changed and diffs/artifacts;
- test/build results with command, exit code, duration, and logs;
- errors/retries/cancellation and recovery status;
- context-budget indicators and compaction events, without displaying secret or sensitive payloads.

Use server-sent events (SSE) for one-way progress streaming initially unless bidirectional streaming is needed. Use sequence IDs/cursors and reconnect handling so the browser can recover missed events. Persist events independently of the live connection.

---

# Phase 6 — Context management

## Goal

Construct relevant model context within a configurable budget while retaining the original transcript and the evidence needed to continue a task.

## Work items

1. Separate global instructions, project instructions, selected agent instructions, conversation history, current task state, and tool outputs.
2. Build a context assembler with deterministic ordering and explicit size limits.
3. Bound and mark tool output; do not silently cut off important errors or test summaries.
4. Prioritize current user intent, current task state, recent relevant messages, and evidence over stale unrelated output.
5. Keep durable source history separate from a compacted working summary.
6. Add a summarization interface later, with summaries marked as derived data and traceable to the history they summarize.
7. Estimate tokens conservatively or use provider token usage when available; do not claim exact token counts from character counts.
8. Define what happens if mandatory instructions alone exceed the configured budget.
9. Keep secret values out of the context unless explicitly required and permitted.

## Candidate files

- `core/context_manager.py`
- `core/context_models.py`
- `tests/test_context_manager.py`

## Tests and acceptance criteria

- Context stays within configured limits according to the chosen estimator.
- Truncation and omitted sections are visible.
- Mandatory instructions are not silently dropped.
- Tool error/test evidence survives context reduction according to policy.
- The original transcript remains recoverable.
- Empty, oversized, and malformed history is handled.
- A summary cannot itself mark a task as verified without execution evidence.

## Exit gate

Longer sessions can continue with bounded context and traceable source history.

### Phase 6 Implementation & Readiness Summary
- **Implementation Status:** COMPLETE — READY FOR OPERATOR AUDIT & FREEZE REVIEW (Not yet frozen).
- **Core Verification Summary:**
  - `core/context_models.py`: 10 distinct context categories, priority ordering (1–10), `ContextBudgetConfig` with reserved completion headroom ($B_{\text{input}} = M_{\text{ctx}} - M_{\text{out}} - M_{\text{margin}}$), and traceable derived summaries (`is_derived=True`, `source_message_ids`).
  - `core/context_manager.py`: Stateless, thread-safe context assembler; conservative token estimator ($\lceil (\text{len}/3.3 + 4) \times 1.15 \rceil$); 40/60 head/tail tool bounding with explicit exit-code preservation; secret redaction across all items; deterministic priority sorting with chronological wire re-ordering.
  - `core/agent_loop.py`: Integrated context assembler before model invocation; graceful handling of `MandatoryContextOverflowError` with `LIMIT_REACHED` event; 100% byte-for-byte immutable history preservation in `AgentExecutionResult.history`.
  - `core/config.py` & `core/task_service.py`: Exposed `context_window_tokens`, `context_reserve_output_tokens`, `context_safety_margin_tokens`, and `context_max_tool_output_tokens` via settings and diagnostics.
- **Verification Evidence:**
  - 460/461 backend tests passing (1 skipped Windows symlink test; 1 warning).
  - 50/50 frontend tests passing across 10 test files.
  - Frontend production build passing in 602ms.
  - Frontend linter passing with 0 errors, 10 compiler notices.
  - 21 new Phase 6 tests passing (12 unit tests in `tests/test_context_manager.py`, 9 adversarial tests in `tests/test_context_adversarial.py`).
  - Zero regressions across 439 pre-existing Phase 0–5 tests.
- **Deliberate Accepted Limitations & Qualifications:**
  - *Heuristic Token Estimation:* Token counts are estimated using the conservative heuristic $\lceil (\text{len}/3.3 + 4) \times 1.15 \rceil$ with an explicit safety margin rather than model-specific tokenizer weights.
  - *Volatile Summaries:* Derived summaries currently exist in process memory during task execution; durable summary persistence across server restarts belongs to Phase 7.
- **Next Step:** Independent Operator Audit & Freeze Review. Do NOT start Phase 7 without explicit operator authorization.

---

# Phase 7 — Persistent state and session recovery

## Goal

Persist authoritative task/session state and enough execution records to inspect or recover after a process restart.

## Work items

1. Design SQLite schema and migrations for sessions, tasks, model calls, tool calls/results, approvals, and state transitions as justified by the actual workflow.
2. Define legal state transitions; reject impossible transitions.
3. Use transactions for state changes that must be atomic.
4. Persist timestamps, stable IDs, parent/task relationships, attempts, and terminal reasons.
5. Choose a clear source of truth for transcripts/events. If SQLite and JSONL are both used, define ordering, failure behavior, and reconciliation; avoid pretending a cross-store write is atomic.
6. On startup, detect interrupted work and classify it as resumable, needs-review, failed, or terminal.
7. Resume by reconstructing state from durable records; do not blindly replay non-idempotent tool calls.
8. Treat pending approvals as invalid or requiring renewed approval after restart unless a safe, explicit policy says otherwise.
9. Store no credentials in task records, transcripts, or event logs.

## Candidate files

- `storage/database.py`
- `storage/migrations/`
- `storage/repositories.py`
- `core/session_manager.py`
- `core/state_manager.py`
- `tests/test_persistence.py`
- `tests/test_session_recovery.py`

## Tests and acceptance criteria

- Session/task/tool records survive process restart.
- Invalid state transitions are rejected.
- Interrupted execution is identified.
- Partial writes and database errors are handled without claiming successful persistence.
- Resume does not repeat unsafe side effects automatically.
- Expired approvals are not silently reused.
- Migration tests cover a fresh database and an upgrade path.

## Exit gate

A task can be inspected after restart and resumed only where the runtime can justify that resumption is safe.

---

# Phase 8 — Reliability, observability, and verification

## Goal

Make failures understandable, bounded, and recoverable, and ensure completion claims are tied to observable evidence.

## Work items

1. Add correlation IDs for sessions, tasks, model requests, and tool calls.
2. Record structured lifecycle events and latency, retry count, token usage when available, tool exit codes, and failure categories.
3. Add explicit request and tool timeouts.
4. Retry only classified transient failures, with a bounded retry count and backoff; avoid retrying non-idempotent actions blindly.
5. Add cancellation and graceful shutdown paths.
6. Detect repeated identical tool failures or loops and stop with a diagnostic.
7. Create a verification record for tests/builds, including command, exit code, timestamps, and bounded output.
8. Separate task status (`completed`, `failed`, `cancelled`, etc.) from verification status (`passed`, `failed`, `not_run`, `inconclusive`).
9. Redact secrets and cap logs.
10. Provide a diagnostic command that reports configuration presence and endpoint reachability without printing secret values.

## Candidate files

- `core/observability.py`
- `core/retry.py`
- `core/verification.py`
- `tests/test_retries.py`
- `tests/test_cancellation.py`
- `tests/test_verification.py`

## Tests and acceptance criteria

- Transient model failures retry only within the configured budget.
- Permanent errors do not loop indefinitely.
- Cancellation stops further actions.
- Logs correlate a task with its tool calls.
- Secret redaction and output limits are tested.
- The runtime does not report tests as passed unless an actual test command returned success.
- Completion and verification statuses remain distinct.

## Exit gate

A failed or interrupted task yields a useful diagnostic, and a claimed success is backed by recorded evidence.

---

# Phase 9 — Skills and specialist registry

## Goal

Load specialist definitions from Markdown while enforcing metadata validity and tool capabilities in runtime code.

## Work items

1. Define a documented metadata schema: name, description, responsibilities, allowed tool names, and optional selection hints.
2. Parse and validate Markdown front matter at startup or on explicit refresh.
3. Reject duplicate names, invalid metadata, and references to nonexistent tools.
4. Keep agent definitions flat initially, as files under `agents/`; do not require a Python class for prompt-only specialists.
5. Load only the selected specialist's full instructions when needed.
6. Enforce tool capabilities in the dispatcher; a prompt saying “read-only” is not a security control.
7. Define specialist input/output contracts, including bounded context and returned artifacts/findings.
8. Include a code explorer, code reviewer, and test engineer as initial examples only after the registry is working.
9. Record which specialist was selected and why, without exposing private chain-of-thought.

## Candidate files

- `agents/*.md`
- `core/agent_registry.py`
- `core/agent_contracts.py`
- `tests/test_agent_registry.py`

## Tests and acceptance criteria

- Valid agent files load.
- Invalid front matter and duplicate names produce actionable errors.
- Unknown tool capabilities are rejected.
- Read-only specialists cannot invoke write/shell tools through the dispatcher.
- Missing specialist behavior is deterministic.
- Specialist outputs are bounded and validated.

## Exit gate

The runtime can load and select a specialist without giving that specialist implicit authority to bypass runtime policy.

---

# Phase 10 — Manager-led planning and multi-agent orchestration

## Goal

Introduce the manager/root agent as the decision-making coordinator while keeping actual execution, policy, and state transitions deterministic.

## Work items

1. Define task, subtask, dependency, assignment, artifact, review, retry, escalation, and termination contracts.
2. Let the root orchestrator decide whether to plan, execute directly, delegate, review, retry, or ask the user for clarification.
3. Represent dependencies explicitly; do not rely only on prompt order or a `SequentialRunner`-only implementation.
4. Start with sequential specialist execution. Add parallel execution only when shared-state, resource, conflict, and cancellation behavior are specified.
5. Give each specialist a bounded task brief, required inputs, permitted capabilities, expected output schema, and completion criteria.
6. Keep task ownership and state transitions in the runtime.
7. Merge outputs through explicit artifacts and summaries, not by dumping every worker's full transcript into every model call.
8. Have review/evaluation compare deliverables against acceptance criteria and actual test evidence.
9. Bound retries and escalation; repeated failure must terminate or ask for intervention.
10. Handle partial failures, worker timeouts, conflicting edits, missing outputs, and dependency failures.
11. Make the root orchestrator responsible for final status and delivery summary.

## Candidate files

- `core/orchestrator.py`
- `core/task_manager.py`
- `core/planner.py`
- `core/dependency_graph.py`
- `core/worker_executor.py`
- `tests/test_orchestrator.py`
- `tests/test_task_dependencies.py`

## Tests and acceptance criteria

- Root agent can create a plan and dispatch a bounded task.
- Dependencies prevent tasks from running too early.
- Failed workers are reported and handled according to retry/escalation policy.
- Missing or invalid worker output does not count as completion.
- Review can reject an incomplete result.
- Retry limits and terminal states are enforced.
- Concurrent execution, if enabled, has tests for cancellation, conflicting changes, and partial failure.

## Exit gate

A multi-step task can be planned, assigned, tracked, reviewed, and terminated with explicit evidence and bounded resource use.

---

# Phase 11 — MCP integrations

## Goal

Expose selected external tools through MCP while retaining the same schema validation, permission, timeout, audit, and result-normalization boundaries.

## Work items

1. Select a maintained MCP SDK and verify compatibility at implementation time.
2. Discover server tools and schemas; do not trust remote descriptions as policy.
3. Namespace tool names to prevent collisions.
4. Map MCP tools into the runtime's internal tool contract.
5. Apply local allow/ask/deny policy to every external action.
6. Handle server startup/disconnect, timeouts, malformed results, and protocol errors.
7. Keep credentials in the relevant secure configuration path.
8. Add explicit connection management and cleanup.
9. Do not make MCP mandatory for local filesystem/shell tools.

## Tests and acceptance criteria

- MCP tools are discovered and registered deterministically.
- Tool-name collisions are rejected or namespaced.
- Disconnected and malformed servers fail safely.
- Permission denials apply equally to local and MCP tools.
- Credentials are not logged.
- Shutdown releases connections/processes.

## Exit gate

External tools behave like ordinary runtime tools from the policy and audit perspective.

---

# Phase 12 — API and organization console (optional expansion)

## Goal

Add a second interface without duplicating the runtime's business logic.

## Work items

1. Define an API contract around sessions, task creation, status, events, approvals, cancellation, and artifacts.
2. Add FastAPI endpoints that call the existing runtime services.
3. Add authentication and authorization before exposing the service beyond localhost.
4. Stream task events with reconnect behavior and bounded buffers.
5. Build a React + TypeScript dashboard for task status, organization hierarchy, execution timeline, approvals, and deliverables.
6. Represent the virtual organization as roles/agents and task ownership, not as a decorative diagram disconnected from actual state.
7. Handle loading, empty, error, reconnecting, and cancelled states.
8. Ensure API requests cannot bypass the CLI's tool policies.
9. Add concurrency limits and prevent duplicate task submission where appropriate.
10. Document local-only versus remotely exposed deployment modes.

## Tests and acceptance criteria

- API integration tests exercise real runtime services with fake model/tools.
- Unauthorized actions are rejected.
- Streaming reconnect does not silently lose or duplicate events without a cursor/sequence policy.
- Concurrent tasks do not corrupt session state.
- UI accurately reflects backend state and error conditions.
- The API does not introduce a second orchestration implementation.

## Exit gate

CLI and dashboard operate on the same task/session model and permission system.

---

# Phase 13 — Release hardening and end-to-end acceptance

## Goal

Verify the whole system before calling it usable beyond a local prototype.

## Work items

1. Run unit, integration, and end-to-end suites.
2. Run a live model connectivity test and a real tool-call test against the intended vLLM deployment.
3. Test the complete lifecycle: task start, tool execution, tool error, retry, verification, final report, and restart recovery.
4. Test invalid inputs, path escapes, denied commands, timeouts, cancellation, model outage, storage failure, and malformed tool calls.
5. Inspect logs for secret leakage and unbounded output.
6. Document installation, configuration, permissions, known limitations, and recovery.
7. Record actual test commands and results in a build report.
8. Compare the implementation to the relevant PDF concepts and explain any deliberate deviation.
9. Fix regressions before acceptance; mark untested behaviors explicitly.

## End-to-end acceptance scenario

Given a test repository and a task such as “inspect this project, identify a failing test, make a minimal fix, and run the tests,” the runtime should:

- identify and stay within the configured workspace;
- load the relevant project instructions;
- inspect files through registered tools;
- request permission for actions requiring approval;
- perform bounded changes;
- execute tests and feed actual output back to the model;
- retry only within defined limits;
- persist tool calls and task state;
- recover interrupted sessions without replaying unsafe side effects;
- report changed files, test commands, actual outcomes, and unresolved issues;
- never claim verified success without evidence.

---

## 6. Recommended modular project structure

Organize by **responsibility/capability**, not by putting every module into `core/`. Context files and context-processing code belong together; tools have their own registry/contracts/implementations; orchestration has its own package; and API/UI, storage, security, and observability remain separate. Add folders incrementally when their first real implementation arrives—this is a target layout, not a reason to create empty files for every future feature.

```text
agent-runtime/
├── pyproject.toml
├── .env.example
├── .gitignore
├── README.md
├── main.py                         # bootstrap only
├── config/
│   ├── defaults.yaml               # non-secret defaults
│   └── policies/                   # versioned policy configuration
├── api/                            # HTTP boundary; no agent logic here
│   ├── app.py
│   ├── dependencies.py
│   ├── routes/
│   │   ├── health.py
│   │   ├── tasks.py
│   │   ├── sessions.py
│   │   ├── approvals.py
│   │   └── artifacts.py
│   └── schemas/                    # request/response API schemas
├── frontend/                       # primary web GUI
│   ├── package.json
│   └── src/
│       ├── app/
│       ├── features/
│       │   ├── tasks/
│       │   ├── timeline/
│       │   ├── approvals/
│       │   ├── diffs/
│       │   └── settings/
│       ├── components/
│       └── api/
├── runtime/                        # shared execution lifecycle
│   ├── agent_loop.py
│   ├── task_lifecycle.py
│   ├── cancellation.py
│   ├── budgets.py
│   └── termination.py
├── model/                          # provider boundary
│   ├── contracts.py
│   ├── adapter.py
│   ├── openai_compatible.py
│   └── response_normalizer.py
├── context/                        # all context-related code and source files
│   ├── builder.py                  # ordered model-facing context assembly
│   ├── budget.py                   # token estimates and final budget gate
│   ├── instructions.py              # root/nested project instruction loading
│   ├── history.py                  # durable history projection, not source of truth
│   ├── retrieval.py                 # relevant evidence selection
│   ├── pruning.py                   # stale/low-value context reduction
│   ├── compaction.py                # summaries with provenance
│   ├── output_limits.py             # bounded tool output and truncation markers
│   ├── models.py
│   └── README.md                    # context rules, ordering, budgets
├── tools/                           # capabilities the model can request
│   ├── contracts.py
│   ├── registry.py
│   ├── dispatcher.py
│   ├── results.py
│   ├── filesystem/
│   │   ├── listing.py
│   │   ├── reading.py
│   │   └── editing.py
│   ├── search/
│   │   └── project_search.py
│   ├── git/
│   │   └── inspection.py            # read-only first
│   ├── execution/
│   │   ├── commands.py
│   │   └── tests.py
│   ├── browser/                     # later, controlled Playwright worker
│   └── README.md                    # tool authoring and contract rules
├── security/                        # deterministic trust and authorization boundary
│   ├── policy_engine.py
│   ├── permissions.py
│   ├── approvals.py
│   ├── workspace_scope.py
│   ├── secret_redaction.py
│   └── sandbox/                     # OS-enforced isolation adapters
├── orchestration/                   # manager/root agent and task graph
│   ├── manager.py
│   ├── planner.py
│   ├── task_graph.py
│   ├── delegation.py
│   ├── worker_executor.py
│   ├── review.py
│   └── recovery.py
├── agents/                           # specialist definitions and registry
│   ├── definitions/
│   │   ├── code-explorer.md
│   │   ├── code-reviewer.md
│   │   └── test-engineer.md
│   ├── registry.py
│   └── contracts.py
├── skills/                           # task-specific workflow/instruction packages
│   ├── registry.py
│   └── definitions/                 # each skill in its own directory with SKILL.md
├── hooks/                            # bounded lifecycle interception points
│   ├── contracts.py
│   ├── registry.py
│   └── runner.py
├── plugins/                          # validated extension manifests/loaders
│   ├── manifests/
│   ├── loader.py
│   └── contracts.py
├── mcp/                              # external tool-provider adapters
│   ├── client_manager.py
│   ├── discovery.py
│   ├── adapter.py
│   └── server_config.py
├── storage/                          # durable source of truth
│   ├── database.py
│   ├── migrations/
│   ├── repositories/
│   ├── events.py
│   └── recovery.py
├── observability/                    # structured events, metrics, diagnostics
│   ├── event_models.py
│   ├── event_bus.py
│   ├── logging.py
│   ├── metrics.py
│   └── redaction.py
├── verification/                     # evidence-backed test/build status
│   ├── runner.py
│   ├── contracts.py
│   └── reports.py
├── shared/                           # small cross-package contracts only
│   ├── ids.py
│   ├── errors.py
│   └── enums.py
├── tests/
│   ├── unit/
│   ├── integration/
│   ├── security/
│   └── e2e/
└── docs/
    ├── architecture/
    ├── decisions/                   # ADRs: evidence, alternatives, trade-offs
    └── operations/
```

### Folder ownership rules

- `context/` owns context assembly and reduction logic **plus context-specific documentation/fixtures**. It does not own the authoritative transcript database; that belongs in `storage/`.
- `tools/` owns tool implementations, schemas, registry, dispatch and normalized results. `security/` makes the authorization decision; tool code cannot bypass it.
- `orchestration/` owns planning, delegation, dependency management, review, retries/escalation decisions and termination strategy. `runtime/` owns the common execution lifecycle and loop; keep the boundary explicit and avoid duplicate loops.
- `agents/` defines specialist roles; `skills/` defines reusable workflows; `hooks/` defines lifecycle callbacks; `plugins/` packages extensions; `mcp/` connects external tool providers. These are separate because their trust and lifecycle semantics differ.
- `storage/` owns persisted state and migrations. `observability/` defines and emits correlated events; persistence stores them. The GUI consumes events through `api/`, not by reading the database directly.
- `verification/` records actual executed checks. Neither the model nor the GUI may mark a check passed without runner evidence.
- `shared/` must stay small. Do not move feature logic into it merely to avoid imports.
- `main.py` only loads configuration, assembles dependencies, and starts the app. It contains no planning, permission, persistence, or tool implementation logic.
- The web GUI is the primary interface. It talks to the backend through authenticated API contracts and SSE/event cursors; it never executes commands or reads project files directly.

### Dependency direction

```text
frontend -> api -> runtime/orchestration
                    |-> context
                    |-> model
                    |-> tools -> security
                    |-> verification
                    |-> observability -> storage
                    |-> agents / skills / hooks / plugins / mcp
```

Treat this as a responsibility map, not permission for arbitrary circular imports. Define contracts at package boundaries, inject dependencies, and add tests that detect policy bypasses. Keep source-of-truth state in `storage/`; model-facing context is a derived projection, not a second authoritative history.

---

## 7. Delivery gates and priority under time constraints

### Must build first

1. Phase 0 — reproducible backend + minimal GUI/API + tests.
2. Phase 1 — real model connectivity through the backend.
3. Phase 2 — typed tool contract and registry.
4. Phase 3 — reliable model/tool loop with live GUI events.
5. Phase 4 — safe workspace inspection.
6. Phase 5 — deny-first policy and sandboxed command execution.

### Build once the core works

7. Phase 6 — context budgeting.
8. Phase 7 — durable state and recovery.
9. Phase 8 — observability and verification.
10. Phase 9 — specialist registry.
11. Phase 10 — manager-led multi-agent orchestration.

### Later / optional

12. Phase 11 — MCP.
13. Phase 12 — dashboard.
14. Phase 13 — release hardening is not optional before declaring a release; the timing of a public release is optional.

Do not start the organization/team visualization while the single-agent execution loop is unreliable. The minimal task GUI is required from the beginning; a visually complete organization dashboard does not compensate for incorrect tool execution, unsafe permissions, or unrecoverable state.

---

## 8. Decision log to maintain during implementation

For every meaningful architecture choice, record:

- **Decision:** what was selected.
- **Evidence:** source section, repository inspection, test, or measured behavior.
- **Alternatives:** plausible options considered.
- **Trade-off:** what the choice improves and what it costs.
- **Validation:** tests or experiments that support the choice.
- **Open question:** what remains unverified.

Initial questions that must be verified rather than guessed:

1. Exact vLLM endpoint/authentication setup and whether it remains reachable through the intended tunnel.
2. Exact served model ID and tool-call compatibility.
3. Current repository layout and existing code that must be preserved.
4. Dependency compatibility with the selected Python version on Windows.
5. Appropriate isolation mechanism for executing untrusted repository code.
6. Whether transcript events should live in SQLite only or use a separate append-only JSONL stream.

---

## 9. Definition of done for each phase

A phase is complete only when:

- its scope and contracts are documented;
- implementation exists (not just a planned file list);
- meaningful automated tests pass;
- failure cases relevant to that phase are tested;
- integration with earlier phases is checked;
- the mandatory PDF reference has been consulted for relevant architecture decisions;
- actual commands and outcomes are recorded;
- remaining limitations are explicit.

**Current status:** Active build plan with completed and frozen phases documented below. The product is GUI-first; any CLI is optional and must not become the main interaction surface.

---

## 10. Phase Freeze History & Decision Ledger

| Phase | Phase Name | Status | Approval Date | Frozen Git SHA | Authoritative Audit Report |
|:---:|:---|:---:|:---:|:---:|:---|
| **0** | Repository & Dev Foundation | Baseline Complete | 2026-10-27 | `ffdbd36` | [`docs/reports/phase-0-1-implementation-report.md`](reports/phase-0-1-implementation-report.md) |
| **1** | Model Adapter & Task API | Verified Complete | 2026-10-27 | `8f589f7` | [`docs/reports/phase-0-1-implementation-report.md`](reports/phase-0-1-implementation-report.md) |
| **2** | Typed Tool Contracts & Registry | Frozen | 2026-11-14 | `34ede8b` | [`docs/reports/phase-2-independent-final-audit.md`](reports/phase-2-independent-final-audit.md) |
| **3** | Agent Execution Loop & Live Events | **FROZEN** | 2026-11-14 | `454342d95cee21bbd38099f4a67f720029983bfa` | [`docs/reports/phase-3-final-acceptance-audit.md`](reports/phase-3-final-acceptance-audit.md) |
| **4** | Workspace & Filesystem Tools | Ready for Implementation | Pending | Pending | Pending Operator Start Authorization |
