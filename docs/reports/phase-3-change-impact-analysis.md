# Phase 3 Change Impact Analysis: Agent Execution Loop

- **Project:** Chatbot — Agent Runtime
- **Target Phase:** Phase 3 — Agent Execution Loop
- **Status:** Pre-Implementation Analysis (Mandatory Gate)
- **Authorities:** `sources/2604.14228v3.pdf`, `docs/phases.md`, `AGENTS.md`
- **Date:** 2026-11-14

---

## 1. Executive Overview

Phase 3 transitions the Chatbot Agent Runtime from single-turn model invocation (Phase 1) and passive tool registration (Phase 2) into an active, multi-turn **Agent Execution Loop** (`Model → Tool Request → Validation → Permission → Dispatcher → Execution → Result → Model`).

In strict compliance with **Rule 5.1 (File-change impact analysis — BEFORE EVERY EDIT)**, this document provides a comprehensive dependency and blast-radius evaluation prior to modifying existing files or creating new components.

---

## 2. Existing Architecture & Call Graph

### Current Phase 1–2 Call Graph:
```
[User / Frontend UI]
        │  (HTTP POST /api/tasks)
        ▼
[api/routes/tasks.py]
        │
        ▼
[core/task_service.py: TaskService.submit_task]
        │
        ├─► [core/task_service.py: _record_event(TASK_CREATED)]
        │
        └─► [core/task_service.py: _execute_with_model]
                │
                ├─► [core/task_service.py: _record_event(MODEL_REQUEST_STARTED)]
                │
                ├─► [model/vllm_adapter.py: VLLMAdapter.generate(prompt)]
                │         │
                │         └─► [HTTP POST /chat/completions to vLLM]
                │
                └─► [core/task_service.py: _record_event(TASK_TERMINAL)]
                        │
                        ▼
                [Return single-turn TaskResponse]
```

### Limitations of Current Flow:
1. `TaskService` directly calls `model_provider.generate(prompt=request.prompt)` exactly once.
2. `VLLMAdapter.generate` hardcodes `messages = [{"role": "user", "content": prompt}]`. Multi-turn message sequences and assistant tool calls cannot be submitted to the model.
3. `ToolRegistry` contains registered tools, but there is no `ToolDispatcher` to safely parse arguments, check permissions, and invoke registered handlers.
4. No loop exists to feed tool execution results back to the model for subsequent reasoning or final response generation.
5. Task cancellation is purely state mutation in `_tasks`; it does not propagate an active abort signal into in-flight model or tool executions.

---

## 3. Proposed Phase 3 Target Architecture

```
[User / Frontend UI]
        │  (HTTP POST /api/tasks)
        ▼
[api/routes/tasks.py]
        │
        ▼
[core/task_service.py: TaskService.submit_task]
        │
        ▼
[core/agent_loop.py: AgentLoop.run]  ◄───────────────────────────────────────┐
        │                                                                   │
        ├──► 1. Check Cancellation Token (asyncio.Event)                   │
        ├──► 2. Check Execution Budgets (Iterations, Tool Calls, Timeout)   │
        ├──► 3. Context & Message Assembly (core/message_models.py)         │
        ├──► 4. Model Request (model/vllm_adapter.py with messages & tools) │
        │         │                                                         │
        │         ▼                                                         │
        ├──► 5. Receive ModelResponse                                       │
        │         │                                                         │
        │         ├─► [If text only, no tool calls]                         │
        │         │         │                                               │
        │         │         └─► Record Final Answer ──► Terminate (COMPLETED)
        │         │                                                         │
        │         └─► [If tool_calls present]                               │
        │                   │                                               │
        │                   ▼                                               │
        ├──► 6. Append Assistant Message with Tool Calls to History         │
        │                                                                   │
        ├──► 7. For each Tool Call (Sequential Deterministic Execution):    │
        │         │                                                         │
        │         ▼                                                         │
        │    [core/tool_dispatcher.py: ToolDispatcher.dispatch]             │
        │         ├─► Lookup in core/tool_registry.py                       │
        │         ├─► Safe JSON Argument Parsing                            │
        │         ├─► Validate Arguments (tool.validate_input)              │
        │         ├─► Permission Boundary Check (PermissionPolicy)          │
        │         ├─► Execute Trusted Handler (async native or to_thread)   │
        │         └─► Return ToolResultEnvelope                             │
        │                                                                   │
        ├──► 8. Append Tool Result Message to History                       │
        │                                                                   │
        └──► 9. Emit Runtime Events (Tool Execution, Status) ───────────────┘
```

---

## 4. File-by-File Impact Analysis

| File Path | Current Responsibility | Proposed Modification | Why Necessary | Dependent Files | Regression Risk & Mitigation |
|---|---|---|---|---|---|
| `model/contracts.py` | Defines `ModelMessage`, `ModelRequest`, `ModelResponse`, error hierarchy, and `ModelProvider`. | 1. Update `ModelMessage`: allow `content` to be `Optional[str] = ""` or `None` when `tool_calls` are present.<br>2. Add `tool_calls: Optional[List[Dict[str, Any]]] = None` to `ModelMessage`. | In OpenAI format, assistant messages proposing tool calls often have null or empty content and a populated `tool_calls` array. | `model/vllm_adapter.py`, `tests/test_model_adapter.py`, `core/task_service.py` | **Low.** Existing usages specify `content="string"` and `role="user"`, which remain 100% valid. Existing tests pass unchanged. |
| `model/vllm_adapter.py` | Dispatches HTTP completion requests to OpenAI-compatible vLLM endpoints with retries. | In `generate()`: if `messages` is provided in `kwargs`, format them into OpenAI-compatible dictionaries instead of only sending `[{"role": "user", "content": prompt}]`. | Multi-turn agent loops require sending the entire conversation history (user prompt, assistant tool calls, tool results) to the model. | `tests/test_model_adapter.py`, `tests/test_smoke_live.py`, `core/agent_loop.py` | **Low.** If `messages` is not passed, falls back to existing single-prompt behavior. Fully backward-compatible. |
| `api/schemas/events.py` | Defines `RuntimeEventType` and `RuntimeEvent`. | Add new event types: `MODEL_RESPONSE_RECEIVED`, `TOOL_VALIDATED`, `TOOL_REJECTED`, `TOOL_EXECUTION_STARTED`, `TOOL_EXECUTION_FAILED`, `TASK_CANCELLED`, `TASK_LIMIT_REACHED`. | The execution loop emits fine-grained lifecycle events for observability and GUI streaming as specified in `phases.md` lines 297 and Table 1. | `core/task_service.py`, `core/agent_loop.py`, `api/routes/tasks.py`, `frontend/src/types/api.ts` | **Zero.** String enum additions do not break existing enum members. |
| `api/schemas/tasks.py` | Defines `TaskStatus`, `TaskCreateRequest`, `TaskResponse`. | 1. Add `CANCELLED = "cancelled"` and `LIMIT_REACHED = "limit_reached"` to `TaskStatus`.<br>2. Add optional fields `iterations`, `tool_calls_count`, `termination_reason` to `TaskResponse`. | Allows reporting exact terminal states and execution summary metrics to API clients and the frontend. | `core/task_service.py`, `core/agent_loop.py`, `api/routes/tasks.py`, `frontend/src/types/api.ts` | **Zero.** Optional fields default to `None`. Existing statuses (`pending`, `executing`, `completed`, `failed`) remain intact. |
| `core/config.py` | Application configuration loaded via Pydantic Settings. | Add configurable agent execution budgets:<br>- `agent_max_iterations: int = 20`<br>- `agent_max_tool_calls: int = 50`<br>- `agent_tool_timeout_seconds: float = 30.0`<br>- `agent_task_timeout_seconds: float = 300.0` | Prevents infinite agent loops and bounded resource consumption as required by `phases.md` line 295. | `core/agent_loop.py`, `core/task_service.py`, `tests/test_config.py` | **Zero.** Existing settings retain exact names and defaults. New settings have validated defaults. |
| `core/task_service.py` | In-memory task coordination service. | 1. Initialize `ToolRegistry` and `ToolDispatcher`.<br>2. Update `_execute_with_model` to invoke `AgentLoop.run()` with cancellation tokens and event broadcasting.<br>3. Update `cancel_task` to signal the task's cancellation token. | Connects HTTP task submissions to the multi-turn agent execution loop. | `api/routes/tasks.py`, `tests/test_api_tasks.py` | **Low.** Mock provider tests must pass with `AgentLoop`. We will ensure `TaskService` supports both `FakeModelProvider` and live adapters. |
| `core/__init__.py` | Top-level exports for `core`. | Export `AgentLoop`, `ToolDispatcher`, `AgentMessage`, `ToolCallRecord`, `PermissionPolicy`. | Clean public module interface for runtime components. | Downstream callers and test suites. | **Zero.** Additive exports only. |
| `frontend/src/types/api.ts` | Frontend TypeScript contract definitions. | Update `TaskStatusType` (`cancelled`, `limit_reached`) and `RuntimeEventType`. | Keeps frontend types in 100% sync with backend Pydantic schemas. | Frontend components, `api-client.ts`. | **Zero.** Additive union types. |

---

## 5. New Modules to be Created

| New File Path | Responsibility | Justification |
|---|---|---|
| `core/message_models.py` | Multi-turn conversation representation, `ToolCallRecord`, `AgentMessage`, and OpenAI wire format converters. | Decouples internal conversation representations from provider-specific payload schemas (`phases.md` line 289). |
| `core/tool_dispatcher.py` | Safe tool invocation engine: schema validation, permission checks, async/sync handler execution, timeout enforcement, result normalization. | Enforces execution safety boundary; ensures registry remains purely passive (`phases.md` line 293). |
| `core/agent_loop.py` | Core execution loop state machine: budget checks, turn management, tool dispatch coordination, terminal condition evaluation. | Primary Phase 3 deliverable (`phases.md` lines 281–305). |
| `tests/fakes.py` | Deterministic fake model provider (`FakeModelProvider`) capable of scripted multi-turn responses, tool calls, errors, and delays. | Enables hermetic unit testing without network or live GPU requirements (`phases.md` line 306). |
| `tests/test_message_models.py` | Tests for multi-turn history, tool-call formatting, and serialization integrity. | Validates message integrity requirements (`phases.md` line 315). |
| `tests/test_tool_dispatcher.py` | Tests for tool argument validation, missing tools, permission denial, handler timeouts, and exception handling. | Validates tool dispatch and safety boundaries (`phases.md` lines 311–314). |
| `tests/test_agent_loop.py` | Tests covering all 20 agent loop scenarios (no-tool answers, single tool, multi-tool, unknown tool, loops, budget exhaustion, cancellation). | Comprehensive execution loop verification (`phases.md` lines 308–322). |
| `tests/test_agent_adversarial.py` | Adversarial probes targeting JSON injection, infinite tool-call recursion, huge payloads, prototype pollution, cancellation races. | Verifies runtime resilience against malicious or misaligned model outputs. |

---

## 6. Files Deliberately Left Untouched

The following files belong to earlier frozen phases or orthogonal concerns and **MUST NOT** be modified in Phase 3:

1. `core/tool_contracts.py` — Phase 2 frozen contracts (`ToolDefinition`, `ToolResultEnvelope`, `ToolRiskCategory`).
2. `core/tool_registry.py` — Phase 2 frozen registry (`ToolRegistry`).
3. `tests/test_tool_contracts.py` — Phase 2 contract regression test suite.
4. `tests/test_tool_registry.py` — Phase 2 registry regression test suite.
5. `tests/test_tool_adversarial.py` — Phase 2 adversarial test suite.
6. `api/routes/health.py` — Health and diagnostic endpoint (already complete and verified).
7. `tests/test_api_health.py` — Health endpoint unit tests.
8. `tests/test_live_server.py` — Live server smoke tests.
9. `tests/test_logging.py` — Structured logging tests.
10. `sources/2604.14228v3.pdf` — Read-only research reference.

---

## 7. Regression Risk Analysis & Mitigation Plan

### Risk 1: Existing `test_model_adapter.py` Failures
- *Mechanism:* If `ModelMessage` or `VLLMAdapter.generate` changes break expectations of existing adapter tests.
- *Mitigation:* `VLLMAdapter.generate` will maintain exact parameter signatures (`prompt: str, max_tokens=None, temperature=None, **kwargs`). If `messages` is omitted in `kwargs`, it behaves identically to Phase 1.
- *Verification:* Execute `uv run pytest tests/test_model_adapter.py` immediately after adapter edits.

### Risk 2: Existing `test_api_tasks.py` Failures
- *Mechanism:* `test_api_tasks.py` submits prompts using mocked providers and tests task status and SSE event streams.
- *Mitigation:* Ensure `TaskService` maintains identical responses for single-turn completions and backward-compatible event types.
- *Verification:* Execute `uv run pytest tests/test_api_tasks.py` immediately after `task_service.py` edits.

### Risk 3: Breaking Frontend TypeScript Build
- *Mechanism:* Adding new union types or fields to frontend schemas might trigger type errors in existing React components.
- *Mitigation:* Run `npm --prefix frontend run build` (`tsc -b` + Vite) after frontend type updates.

---

## 8. Approval Gate

This change impact analysis establishes:
- Exact lines and symbols to be modified.
- Full preservation of Phase 0–2 architectural boundaries.
- Zero premature Phase 4+ scope.
- Comprehensive regression testing strategy.

Implementation may proceed in accordance with this analysis.
