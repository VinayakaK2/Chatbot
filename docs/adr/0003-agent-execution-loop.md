# ADR 0003: Reactive Multi-Turn Agent Execution Loop, Tool Dispatcher, and Explicit State Machine

- **Status:** Accepted
- **Deciders:** Senior Staff Software Engineer, AI-Runtime Architect, Security Engineer
- **Date:** 2026-11-14
- **Context:** Agent Runtime Project (Phase 3 — Agent Execution Loop)

---

## 1. Decision

1. **Reactive ReAct Loop over Decision Scaffolding:**
   In adherence to `2604.14228v3.pdf` (Table 1, Sections 3.1 & 4.1), we implement a minimal-scaffolding, maximal-operational-harness reactive loop (`queryLoop` pattern). The model is given full latitude to reason and propose tool invocations. The runtime harness surrounds the model with deterministic validation, sequential tool dispatch, permission boundary enforcement, budget controls, and recovery mechanisms.

2. **Strict Perimeter Validation via `ToolDispatcher`:**
   Model-generated tool calls are treated as untrusted data:
   - Tool identifiers must resolve to a registered `ToolDefinition` in `ToolRegistry`; unknown tools fail safely with a structured error envelope.
   - Raw JSON argument strings are safely parsed with protection against non-dict payloads, prototype pollution, and malformed syntax.
   - Arguments are strictly validated against declared Pydantic / JSON schemas before any execution attempt.
   - A dedicated `PermissionPolicy` boundary checks tool risk categories and task parameters before execution.
   - Only pre-registered, trusted Python callables are invoked. Dynamic execution (`eval`, `exec`, arbitrary shell) is strictly forbidden.

3. **Sequential Deterministic Tool Execution:**
   In alignment with `docs/phases.md` line 296 and `2604.14228v3.pdf` Section 4.2, tool execution begins sequentially. While read-only parallel dispatch is considered for future optimization, state-modifying actions and multi-tool batches must execute in strict deterministic order to maintain consistent state and reproducible message histories.

4. **1-to-1 Tool Call and Result Message Pairing:**
   Every tool call requested by the assistant receives exactly one corresponding result message (`role="tool"`, `tool_call_id=<id>`). If a tool call fails, is unknown, or is denied by policy, an error `ToolResultEnvelope` is serialized into the result message. This maintains provider wire compliance and acts as a routing signal allowing the model to self-correct on subsequent turns.

5. **Explicit Finite State Machine & Bounded Execution:**
   The execution loop transitions through explicit, validated states (`PENDING`, `RUNNING`, `WAITING_FOR_MODEL`, `PROCESSING_TOOL_CALLS`, `EXECUTING_TOOLS`, `COMPLETED`, `FAILED`, `CANCELLED`, `LIMIT_REACHED`).
   Terminal states are immutable.
   The loop enforces four concrete runtime bounds:
   - `max_iterations`: caps conversation cycles (default: 20).
   - `max_tool_calls`: caps total tool executions (default: 50).
   - `tool_timeout_seconds`: per-tool execution limit (default: 30.0s).
   - `task_timeout_seconds`: overall wall-clock task limit (default: 300.0s).

6. **Async-Safe Execution of Synchronous Handlers:**
   Tool handlers may be native async coroutines or synchronous functions. Synchronous handlers are executed in worker threads via `asyncio.to_thread` with timeouts, ensuring that long-running tool execution never blocks the FastAPI / asyncio event loop.

7. **Observable Event Sink & SSE Streaming:**
   The loop records granular lifecycle events (`model_request_started`, `model_response_received`, `tool_requested`, `tool_completed`, etc.) through an injectable event interface, streaming real-time execution progress to the web GUI via Server-Sent Events (SSE).

---

## 2. Evidence and Justification

- **`sources/2604.14228v3.pdf` (Sections 3.1, 4.1, 4.2, 4.5, 5.1):**
  - Confirms that production coding agents operate as a reactive while-loop wrapped in deterministic operational infrastructure.
  - Demonstrates that only ~1.6% of agent code represents AI decision logic, while ~98.4% is operational infrastructure (loop, permissions, dispatch, recovery, events).
  - Emphasizes that permissions and argument validation must precede handler execution.
  - Establishes that tool denials and validation errors should feed back into the conversation as routing signals rather than halting the process abruptly.
- **`docs/phases.md` (Phase 3, Lines 281–322):**
  - Mandates multi-turn message history preservation, provider tool-call ID tracking, sequential batch execution, explicit stop conditions, and event recording.

---

## 3. Alternatives Considered

1. **State-Graph Workflow Frameworks (e.g. LangGraph, AutoGen):**
   - *Rejected:* Heavy scaffolding that constrains model reasoning into pre-compiled graph nodes. Overcomplicates debugging and introduces high architectural overhead. The ReAct while-loop provides superior flexibility, latency, and code maintainability.
2. **Eager Parallel Tool Execution:**
   - *Rejected for Phase 3:* Concurrent tool execution before filesystem tools (Phase 4) and sandboxing (Phase 5) are implemented introduces race conditions, non-deterministic error ordering, and complex cancellation rollbacks. Sequential execution is safe, deterministic, and verifiable.
3. **Failing the Entire Task on a Single Tool Error:**
   - *Rejected:* Halting the task on invalid arguments or tool failure prevents the model from recovering. Feeding the error envelope back to the model enables autonomous self-correction (e.g. fixing a typo in arguments).
4. **Direct Shell Execution in Tool Dispatcher:**
   - *Strictly Rejected:* Violates security boundaries. Shell execution belongs exclusively to Phase 5 sandboxed runners with explicit operator permissions. Phase 3 dispatches to registered Python callables only.

---

## 4. Consequences & Impact

- **Positive:**
  - Production-ready, resilient multi-turn agent execution loop.
  - Full compatibility with OpenAI vLLM and Qwen2.5-Coder tool calling.
  - Protection against infinite loops, recursive tool exhaustion, and unhandled exceptions.
  - Real-time observable lifecycle events streamed directly to the frontend GUI.
- **Trade-offs / Limitations:**
  - In-memory task state does not persist across backend process restarts (persistent session storage is deferred to Phase 7 as planned).
  - Synchronous tool cancellation depends on thread boundaries; terminating an active blocking C-extension in a thread cannot be guaranteed without process isolation (Phase 5).
