# Phase 3 Implementation Report: Agent Execution Loop

- **Project:** Chatbot — Agent Runtime
- **Target Phase:** Phase 3 — Agent Execution Loop
- **Status:** Complete — Recommended for Freeze (`PASS`)
- **Authorities:** `sources/2604.14228v3.pdf` (Sections 3.5, 4.1, 4.2, 4.4, 4.5, 5), `docs/phases.md` (Lines 281–322), `AGENTS.md`
- **Implementation Date:** 2026-11-14

---

## 1. Executive Summary

Phase 3 implements the core **Agent Execution Loop** (`Model → Tool Request → Validation → Permission → Dispatcher → Execution → Result → Model`).

The runtime is surrounded by deterministic harness infrastructure that enforces:
- Multi-turn conversation assembly and message integrity.
- Safe JSON argument parsing and strict Pydantic/OpenAPI schema validation.
- An explicit permission boundary (`PermissionPolicy`) enforcing deny-first defaults.
- Trusted dispatching of registered Python callables (supporting both async coroutines and thread-pooled sync functions).
- Bounded runtime budgets (iterations, tool calls, wall-clock timeouts).
- Real-time observable lifecycle events streamed via Server-Sent Events (SSE).
- Graceful error recovery: tool validation failures and permission denials are converted into structured `ToolResultEnvelope` messages, acting as routing signals to allow autonomous model self-correction.
- Zero premature Phase 4+ scope (no filesystem tools, shell execution, multi-agent hierarchies, or SQLite database migrations).

All 236 offline unit, integration, and adversarial tests pass (100% pass rate). In addition, an end-to-end live model tool-calling smoke test (`test_smoke_live.py`) executed against a live endpoint with verified 2-turn tool call generation, argument validation, handler execution, and final answer delivery.

---

## 2. Requirements-to-Implementation Matrix

| Requirement from `docs/phases.md` & PDF | Implementation Modules & Symbols | Concrete Code Evidence | Verification Evidence | Result |
|---|---|---|---|---|
| **1. Internal message & tool-call records** | `core/message_models.py`: `ToolCallRecord`, `AgentMessage` | Lines 20–135 | `tests/test_message_models.py` (15 tests) | `VERIFIED` |
| **2. Message assembly with tool schemas** | `core/agent_loop.py`: `AgentLoop.run` | Lines 150–225 | `tests/test_agent_loop.py::test_scenario_01_final_answer_no_tools` | `VERIFIED` |
| **3. OpenAI-compatible tool call & result format** | `core/message_models.py`: `to_openai_dict`, `model/vllm_adapter.py`: `generate` | Lines 110–135; Lines 100–120 | `tests/test_message_models.py::test_create_assistant_with_tool_calls` | `VERIFIED` |
| **4. Validate tool name and argument payload** | `core/tool_dispatcher.py`: `ToolDispatcher.dispatch` | Lines 85–140 | `tests/test_tool_dispatcher.py` (14 tests) | `VERIFIED` |
| **5. Route execution through dispatcher & permission boundary** | `core/tool_dispatcher.py`: `PermissionPolicy`, `DefaultPermissionPolicy` | Lines 25–80, 145–180 | `tests/test_tool_dispatcher.py::test_dispatch_permission_denied_default` | `VERIFIED` |
| **6. Feed tool results back and continue loop** | `core/agent_loop.py`: sequential dispatch & history update | Lines 370–425 | `tests/test_agent_loop.py::test_scenario_02_single_tool_call_and_completion` | `VERIFIED` |
| **7. Explicit stop conditions** | `core/agent_loop.py`: text-only, cancel, max_iter, timeout, tool_budget, model_error | Lines 170–210, 290–335 | `tests/test_agent_loop.py` (Scenarios 07, 08, 09, 10, 11, 12) | `VERIFIED` |
| **8. Deterministic sequential multi-tool batch execution** | `core/agent_loop.py`: sequential tool execution loop | Lines 365–420 | `tests/test_agent_loop.py::test_scenario_03_multiple_tool_calls_batch` | `VERIFIED` |
| **9. Structured lifecycle events & SSE streaming** | `core/agent_loop.py`: `_emit_event`, `core/task_service.py`: `_broadcast_event` | Lines 115–145; Lines 65–85 | `tests/test_agent_loop.py::test_scenario_15_runtime_events_streaming` | `VERIFIED` |
| **10. Distinguish final answer from tool-call response** | `core/agent_loop.py`: checks `model_response.tool_calls` and non-empty text | Lines 295–330 | `tests/test_agent_loop.py::test_scenario_01_final_answer_no_tools` | `VERIFIED` |
| **11. Live model tool-call smoke verification** | `tests/test_smoke_live.py`: `test_live_agent_loop_tool_calling_smoke` | Lines 65–125 | Live test execution log (2 turns, 1 tool call, 17.22s, output: 42) | `VERIFIED` |

---

## 3. Files Added

1. `core/message_models.py` (185 lines)
   - Defines `ToolCallRecord`, `ToolCallStatus`, `AgentMessageRole`, and `AgentMessage`.
   - Provides `to_openai_dict()` wire serialization for OpenAI function calling compatibility.
   - Provides `validate_conversation_integrity()` multi-turn sequence verification.
2. `core/tool_dispatcher.py` (270 lines)
   - Implements `ToolDispatcher` and `PermissionPolicy` protocol with `DefaultPermissionPolicy`.
   - Safely parses JSON argument payloads (rejecting non-dict structures).
   - Validates parameters against declared tool schemas.
   - Enforces execution timeouts and least-privilege permission boundaries.
   - Dispatches handlers asynchronously (running synchronous callables in worker threads).
   - Normalizes outputs into `ToolResultEnvelope` with truncation protection.
3. `core/agent_loop.py` (425 lines)
   - Implements `AgentLoop`, `AgentExecutionRequest`, and `AgentExecutionResult`.
   - Manages the multi-turn loop, turn bounds, tool call budgets, and cancellation tokens.
   - Emits granular real-time lifecycle events to event sinks.
4. `tests/fakes.py` (135 lines)
   - Implements deterministic `FakeModelProvider` for scripted multi-turn unit and adversarial testing.
5. `tests/test_message_models.py` (215 lines)
   - 15 unit tests verifying message model immutability, OpenAI formatting, and history integrity.
6. `tests/test_tool_dispatcher.py` (310 lines)
   - 14 unit tests covering sync/async execution, unknown tools, bad JSON, timeouts, and permissions.
7. `tests/test_agent_loop.py` (375 lines)
   - 17 unit tests verifying all 20 agent loop scenarios and stop conditions.
8. `tests/test_agent_adversarial.py` (220 lines)
   - 7 adversarial security tests probing prototype pollution, command injection, huge payloads, prompt injection isolation, and cancellation races.
9. `docs/agent-execution-loop.md`
   - Complete architectural specification with Mermaid flowcharts and FSM state transition diagrams.
10. `docs/adr/0003-agent-execution-loop.md`
    - Architectural Decision Record detailing the reactive loop, sequential execution, and state machine.
11. `docs/reports/phase-3-change-impact-analysis.md`
    - Pre-implementation impact and blast-radius analysis.

---

## 4. Files Modified & Justification

1. `model/contracts.py`:
   - *Modification:* Updated `ModelMessage` to allow `content: Optional[str] = ""` and added `tool_calls: Optional[List[Dict[str, Any]]] = None`.
   - *Justification:* In standard OpenAI wire format, assistant messages proposing tool calls often have null or empty content with populated `tool_calls`.
2. `model/vllm_adapter.py`:
   - *Modification:* In `generate()`, if `messages` is provided in `kwargs`, format them into OpenAI-compatible dictionaries instead of only sending `[{"role": "user", "content": prompt}]`.
   - *Justification:* Allows multi-turn conversation histories to be submitted to the model.
3. `api/schemas/events.py`:
   - *Modification:* Added missing lifecycle event types (`MODEL_RESPONSE_RECEIVED`, `TOOL_VALIDATED`, `TOOL_REJECTED`, `TOOL_EXECUTION_STARTED`, `TOOL_EXECUTION_FAILED`, `TASK_CANCELLED`, `TASK_LIMIT_REACHED`).
   - *Justification:* Required for granular observability and event streaming to the web GUI.
4. `api/schemas/tasks.py`:
   - *Modification:* Added `CANCELLED` and `LIMIT_REACHED` to `TaskStatus`; added `iterations`, `tool_calls_count`, and `termination_reason` to `TaskResponse`.
   - *Justification:* Enables reporting exact execution metrics and terminal states to API clients and the frontend.
5. `core/config.py`:
   - *Modification:* Added configurable execution budgets (`agent_max_iterations`, `agent_max_tool_calls`, `agent_tool_timeout_seconds`, `agent_task_timeout_seconds`) and validation.
   - *Justification:* Enforces bounded execution and prevents runaway resource consumption.
6. `core/task_service.py`:
   - *Modification:* Wired `AgentLoop` and `ToolDispatcher` into `submit_task()`, implemented active cancellation tokens, and broadcast real-time events.
   - *Justification:* Bridges HTTP task lifecycle requests to the multi-turn execution loop.
7. `api/routes/tasks.py`:
   - *Modification:* Updated SSE stream termination check to recognize `cancelled` and `limit_reached` statuses.
   - *Justification:* Ensures clients receive clean stream termination on all terminal states.
8. `core/__init__.py`:
   - *Modification:* Exported `AgentLoop`, `ToolDispatcher`, `AgentMessage`, etc.
   - *Justification:* Clean module exports for runtime consumers.
9. `frontend/src/types/api.ts`:
   - *Modification:* Synchronized `TaskStatusType`, `TaskResponse`, and `RuntimeEventType` with backend schemas.
   - *Justification:* Full type safety across the full stack.
10. `frontend/src/components/activity/ExecutionActivity.tsx`:
    - *Modification:* Supported `limit_reached` status.
    - *Justification:* UI rendering of budget limits.
11. `tests/test_smoke_live.py`:
    - *Modification:* Added `test_live_agent_loop_tool_calling_smoke`.
    - *Justification:* Live verification of tool calling loop against configured vLLM / Omniroute endpoint.

---

## 5. Files Deliberately Left Untouched

The following files were preserved without modification to protect earlier phase baselines:
- `core/tool_contracts.py` (Phase 2 frozen contracts)
- `core/tool_registry.py` (Phase 2 frozen registry)
- `tests/test_tool_contracts.py` (Phase 2 regression tests)
- `tests/test_tool_registry.py` (Phase 2 regression tests)
- `tests/test_tool_adversarial.py` (Phase 2 adversarial tests)
- `api/routes/health.py` (Phase 0 health routes)
- `tests/test_api_health.py` (Phase 0 tests)
- `tests/test_live_server.py` (Phase 0 server tests)
- `tests/test_logging.py` (Phase 0 logging tests)

---

## 6. Commands Executed & Verification Evidence

### A. Backend Pytest Suite
```bash
uv run pytest -v
```
**Result:**
- Total collected test items: **236**
- **Passed: 236 (100% pass rate in 19.81s)**
- **Failed: 0**
- **Skipped: 0**

Breakdown:
- `tests/test_agent_adversarial.py`: 7 passed
- `tests/test_agent_loop.py`: 17 passed
- `tests/test_api_health.py`: 15 passed
- `tests/test_api_tasks.py`: 12 passed
- `tests/test_config.py`: 20 passed
- `tests/test_live_server.py`: 1 passed
- `tests/test_logging.py`: 7 passed
- `tests/test_message_models.py`: 15 passed
- `tests/test_model_adapter.py`: 11 passed
- `tests/test_smoke_live.py`: 2 passed (live chat completion + live tool-calling loop)
- `tests/test_tool_adversarial.py`: 39 passed
- `tests/test_tool_contracts.py`: 56 passed
- `tests/test_tool_dispatcher.py`: 14 passed
- `tests/test_tool_registry.py`: 20 passed

### B. Live Model Tool-Calling Smoke Test
```bash
uv run pytest tests/test_smoke_live.py -v -s
```
**Result:**
```text
--- LIVE SMOKE TEST SUCCESS ---
Endpoint: https://wma-expensive-photos-authorized.trycloudflare.com/v1
Model ID: gemini-3.8-flash
Elapsed:  7.29s
Output:   Here is the most common and idiomatic way using slice notation...
Usage:    {'prompt_tokens': 88, 'completion_tokens': 60, 'total_tokens': 148}
-------------------------------
--- LIVE AGENT LOOP SMOKE TEST ---
Status:             completed
Iterations:         2
Tool calls count:   1
Final response:     The result of 17 + 25 is **42**.
Elapsed:            17.22s
----------------------------------
2 passed in 33.55s
```

### C. Frontend Build & Typecheck
```bash
npm --prefix frontend run build
```
**Result:**
- `tsc -b` (TypeScript strict typecheck): **0 errors**
- Vite build: **Built in 1.06s** (Assets bundled to `dist/`)
- Exit code: **0**

### D. Git Diff Hygiene
```bash
git diff --check
```
**Result:**
- Exit code: **0** (no whitespace errors or invalid line breaks).

---

## 7. Security Assessment

1. **Untrusted Model Output:**
   All tool calls proposed by the model are parsed through `json.loads` and validated with Pydantic/OpenAPI schemas before execution.
2. **Permission Boundary:**
   `DefaultPermissionPolicy` enforces deny-first evaluation. `COMMAND_EXECUTION` and `DESTRUCTIVE` tools are blocked by default unless explicitly allowlisted.
3. **Execution Sandboxing:**
   No arbitrary Python code execution (`eval`, `exec`) or host shell command execution exists in Phase 3. Only pre-registered, trusted Python callables can be invoked.
4. **Adversarial Resilience:**
   Verified against prototype pollution, command injection payloads in arguments, huge payloads (200KB), deeply nested JSON, prompt injection isolation, and cancellation races.
5. **Secret Redaction:**
   Error messages, logs, and events do not expose raw API keys, bearer tokens, or internal tracebacks to the client.

---

## 8. Observability Assessment

1. **Correlation:** Every lifecycle event records `event_id`, `task_id`, `event_type`, and ISO 8601 UTC `timestamp`.
2. **Granular Milestones:** The runtime emits `task_created`, `model_request_started`, `model_response_received`, `tool_requested`, `tool_execution_started`, `tool_completed` / `tool_execution_failed`, and `task_terminal`.
3. **Token Usage & Metrics:** Aggregated `prompt_tokens`, `completion_tokens`, and `total_tokens` are tracked across all turns and reported in `TaskResponse.usage`.
4. **Execution Summary:** Every task response includes `iterations`, `tool_calls_count`, and `termination_reason`.

---

## 9. Known Limitations & Phase 4+ Seams

1. **In-Memory Task Persistence:**
   Tasks and event histories are stored in memory (`_tasks`, `_events`). Process restart clears in-memory tasks. Persistent SQLite session storage is deferred to Phase 7 as planned in `docs/phases.md`.
2. **Sequential Tool Execution:**
   Multi-tool responses execute sequentially. Safe concurrent execution for read-only tools will be considered in future optimization passes after filesystem sandboxing is in place.
3. **Cancellation of Blocking C-Extensions:**
   Synchronous tools run in threads via `asyncio.to_thread`. While the agent loop ceases execution upon cancellation, terminating a blocking C-extension inside a thread requires process isolation (Phase 5).

---

## 10. Freeze Recommendation

### **`PASS`**

All acceptance criteria defined in `docs/phases.md` lines 281–322 and `sources/2604.14228v3.pdf` are fully satisfied, backed by 236 passing tests, verified live model execution, and clean frontend builds. Phase 3 is recommended for immediate freeze.
