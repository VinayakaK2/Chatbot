# Phase 3 Defect F-02 Change Impact Analysis

**Document Status:** PENDING ORCHESTRATOR APPROVAL  
**Defect Identifier:** F-02 (Race Condition in `TaskService.cancel_task` vs `_execute_with_loop`)  
**Target File:** `core/task_service.py`  
**Authority:** `sources/2604.14228v3.pdf`, `docs/phases.md`, `AGENTS.md`  
**Date:** 2026-11-14  

---

## 1. Defect Reproduction & Source-Level Explanation

### The Defect
In `core/task_service.py`, `_execute_with_loop()` and `cancel_task()` mutate the in-memory task dictionary (`self._tasks[task_id]`) and emit lifecycle events (`self._record_event()`) without synchronization:

```python
# In core/task_service.py:185 (_execute_with_loop)
loop_result = await self.agent_loop.run(exec_request)
...
self._tasks[task_id] = final_task  # Line 185: Unconditional write!
self._record_event(task_id, RuntimeEventType.TASK_TERMINAL, ...)

# In core/task_service.py:246 (cancel_task)
if task.status in (TaskStatus.PENDING, TaskStatus.EXECUTING):
    ...
    self._tasks[task_id] = cancelled_task  # Line 246
    self._record_event(task_id, RuntimeEventType.TASK_CANCELLED, ...)
    self._record_event(task_id, RuntimeEventType.TASK_TERMINAL, ...)
```

### The Race Condition Interleaving
1. `_execute_with_loop` awaits `self.agent_loop.run(exec_request)`.
2. When the agent loop completes its final iteration, `loop_result` is computed with `status = TaskStatus.COMPLETED`.
3. Before line 185 executes, an operator/user issues `POST /api/tasks/{task_id}/cancel`.
4. `cancel_task()` reads `task = self._tasks.get(task_id)`. At this instant, `task.status == TaskStatus.EXECUTING`.
5. `cancel_task()` passes the status check, creates `cancelled_task`, writes `self._tasks[task_id] = cancelled_task`, emits `TASK_CANCELLED` and `TASK_TERMINAL(cancelled)`, and returns HTTP 200 with status `"cancelled"`.
6. Control returns to `_execute_with_loop`. Line 185 executes unconditionally:
   `self._tasks[task_id] = final_task` (where `final_task.status == TaskStatus.COMPLETED`).
7. Line 187 emits a second `TASK_TERMINAL` event with status `"completed"`.
8. **Result:** The cancellation is silently erased. `GET /api/tasks/{task_id}` returns `"completed"`. The SSE stream broadcasts contradictory terminal events (`cancelled` followed by `completed`).

---

## 2. Affected Files & Responsibilities

1. **`core/task_service.py` (Primary Implementation):**
   - Coordinates task validation, submission, execution through `AgentLoop`, and event broadcasting.
   - Owns task state lifecycle (`_tasks`), event queues (`_event_queues`), and cancellation tokens (`_cancellation_tokens`).
2. **`api/schemas/tasks.py` (Reference / Contracts):**
   - Defines `TaskStatus` (`PENDING`, `EXECUTING`, `COMPLETED`, `FAILED`, `CANCELLED`, `LIMIT_REACHED`) and `TaskResponse`.
   - No schema changes required; all contracts preserved.
3. **`api/routes/tasks.py` (Consumer):**
   - Invokes `task_service.submit_task()`, `get_task()`, and `cancel_task()`.
   - Streams events over SSE.
4. **`tests/test_task_concurrency.py` (New Targeted Test Suite):**
   - Deterministic concurrency tests verifying atomic transitions, race prevention, and event consistency.

---

## 3. Import & Dependency Relationships

- **Hierarchy:** `Frontend → API (`api/routes/tasks.py`) → Core (`core/task_service.py`) → Model (`model/contracts.py`)`
- **Dependencies of `core/task_service.py`:**
  - Standard library: `asyncio`, `threading`, `datetime`, `logging`, `typing`, `uuid`
  - Internal schemas: `api.schemas.events.RuntimeEvent`, `api.schemas.tasks.TaskResponse`, `TaskStatus`
  - Internal core: `core.agent_loop`, `core.config`, `core.tool_dispatcher`, `core.tool_registry`
- **Zero Circular Dependencies:** The remediation introduces only standard library synchronization primitives (`threading.Lock` / `threading.RLock`) within `core/task_service.py`. No new external packages or circular imports.

---

## 4. Callers & Consumers of Affected Methods

- **`submit_task(request)`:**
  - Called by `api/routes/tasks.py::submit_task` (`POST /api/tasks`).
  - Calls `_execute_with_loop(task_id, request, created_at)`.
- **`cancel_task(task_id)`:**
  - Called by `api/routes/tasks.py::cancel_task_by_id` (`POST /api/tasks/{task_id}/cancel`).
- **`get_task(task_id)`:**
  - Called by `api/routes/tasks.py::get_task_by_id` (`GET /api/tasks/{task_id}`) and SSE streamer.
- **Frontend Consumers:**
  - `frontend/src/services/api-client.ts` polls `GET /api/tasks/{task_id}` and posts to `/api/tasks/{task_id}/cancel`.
  - `frontend/src/components/activity/ExecutionActivity.tsx` consumes status updates and SSE events.

---

## 5. Existing Synchronization Mechanisms

Currently, `TaskService` has:
- `self._cancellation_tokens: Dict[str, asyncio.Event]`: An async event checked cooperatively by `AgentLoop.run()` to break multi-turn iterations.
- **Missing:** Any lock or atomic guard protecting mutations to `self._tasks[task_id]` or coordinating transitions between `EXECUTING`, `COMPLETED`, `CANCELLED`, and `FAILED`.
- `cancel_task` is synchronous, while `_execute_with_loop` is asynchronous. In FastAPI, synchronous endpoints can be run in a threadpool worker, creating concurrent thread-level access alongside event-loop coroutines.

---

## 6. Relevant State Transitions

The complete state-transition matrix is explicitly formalized:

| Current Status | Target Status | Permitted? | Semantics |
| :--- | :--- | :---: | :--- |
| `None` (New) | `PENDING` | YES | Initial task creation |
| `None` (New) | `FAILED` | YES | Immediate rejection (e.g. unconfigured model) |
| `PENDING` | `EXECUTING` | YES | Task execution commences |
| `PENDING` | `CANCELLED` | YES | Cancellation prior to execution start |
| `EXECUTING` | `COMPLETED` | YES | Successful completion from agent loop |
| `EXECUTING` | `CANCELLED` | YES | Operator cancellation during active execution |
| `EXECUTING` | `FAILED` | YES | Unhandled exception or model timeout |
| `EXECUTING` | `LIMIT_REACHED` | YES | Iteration or tool-call budget exhaustion |
| `COMPLETED` | `CANCELLED` | **NO** | Terminal state immutable; cannot cancel completed task |
| `COMPLETED` | `*` | **NO** | Terminal state immutable |
| `CANCELLED` | `COMPLETED` | **NO** | Terminal state immutable; late completion discarded |
| `CANCELLED` | `FAILED` | **NO** | Terminal state immutable; late failure discarded |
| `CANCELLED` | `LIMIT_REACHED` | **NO** | Terminal state immutable; late limit discarded |
| `CANCELLED` | `CANCELLED` | **NO** (Idempotent) | Already cancelled; duplicate cancellation is no-op |
| `FAILED` | `CANCELLED` | **NO** | Terminal state immutable; cannot cancel failed task |
| `FAILED` | `*` | **NO** | Terminal state immutable |
| `LIMIT_REACHED`| `CANCELLED` | **NO** | Terminal state immutable; cannot cancel finished task |
| `LIMIT_REACHED`| `*` | **NO** | Terminal state immutable |

---

## 7. Race-Condition Interleavings

### Interleaving A: Cancellation vs Normal Completion
- Thread 1 (Cancellation): checks `status == EXECUTING` → acquires lock → sets status `CANCELLED` → emits `TASK_CANCELLED` and `TASK_TERMINAL` → releases lock.
- Thread 2 (Completion): acquires lock → inspects status → sees `CANCELLED` (terminal) → **rejects transition** → leaves `CANCELLED` intact → suppresses redundant terminal events.

### Interleaving B: Normal Completion vs Cancellation
- Thread 2 (Completion): acquires lock → sets status `COMPLETED` → emits `TASK_TERMINAL` → releases lock.
- Thread 1 (Cancellation): acquires lock → sees `COMPLETED` (terminal) → **rejects transition** → returns existing `COMPLETED` task → suppresses redundant cancellation events.

### Interleaving C: Concurrent Cancellation Requests
- Request 1: acquires lock → transitions `EXECUTING -> CANCELLED` → emits events → releases lock.
- Request 2: acquires lock → sees `CANCELLED` (terminal) → no-op → returns existing cancelled task → no duplicate events.

### Interleaving D: Cancellation vs Unexpected Crash
- Task cancelled during loop → `_execute_with_loop` raises or wraps exception → attempts transition to `FAILED` under lock → sees `CANCELLED` → transition rejected → task remains `CANCELLED`.

---

## 8. Proposed Implementation

We will implement an atomic transition mechanism inside `TaskService` using **per-task re-entrant locks (`threading.RLock`)**:

```python
class TaskService:
    def __init__(...):
        ...
        self._task_locks: Dict[str, threading.RLock] = {}
        self._registry_lock = threading.Lock()

    def _get_task_lock(self, task_id: str) -> threading.RLock:
        """Retrieve or initialize a per-task lock safely."""
        with self._registry_lock:
            if task_id not in self._task_locks:
                self._task_locks[task_id] = threading.RLock()
            return self._task_locks[task_id]

    def _transition_task_state(
        self,
        task_id: str,
        target_task: TaskResponse,
        terminal_event_payload: Optional[Dict[str, Any]] = None,
        intermediate_event: Optional[Tuple[RuntimeEventType, Dict[str, Any]]] = None,
    ) -> Tuple[bool, TaskResponse]:
        """Atomically validate and execute a task state transition under the task lock."""
        lock = self._get_task_lock(task_id)
        with lock:
            current = self._tasks.get(task_id)
            if current is None:
                # Initial registration
                self._tasks[task_id] = target_task
                return True, target_task

            # Terminal states are strictly immutable
            terminal_statuses = {
                TaskStatus.COMPLETED,
                TaskStatus.CANCELLED,
                TaskStatus.FAILED,
                TaskStatus.LIMIT_REACHED,
            }
            if current.status in terminal_statuses:
                logger.debug(
                    "Rejected transition for task %s: current=%s is terminal, target=%s",
                    task_id, current.status.value, target_task.status.value,
                )
                return False, current

            # Validate valid non-terminal transitions
            valid = False
            if current.status == TaskStatus.PENDING:
                valid = target_task.status in (TaskStatus.EXECUTING, TaskStatus.CANCELLED, TaskStatus.FAILED)
            elif current.status == TaskStatus.EXECUTING:
                valid = target_task.status in terminal_statuses

            if not valid:
                logger.warning(
                    "Invalid state transition requested for task %s: %s -> %s",
                    task_id, current.status.value, target_task.status.value,
                )
                return False, current

            # Apply state update
            self._tasks[task_id] = target_task

            # Emit events atomically with state change
            if intermediate_event:
                ev_type, ev_payload = intermediate_event
                self._record_event(task_id=task_id, event_type=ev_type, payload=ev_payload)

            if target_task.status in terminal_statuses:
                payload = terminal_event_payload or {
                    "status": target_task.status.value,
                    "termination_reason": target_task.termination_reason,
                    "error": target_task.error,
                }
                self._record_event(task_id=task_id, event_type=RuntimeEventType.TASK_TERMINAL, payload=payload)

            return True, target_task
```

### Key Properties:
1. **Atomicity:** Reading current state, validating transition, writing new state, and broadcasting events occur inside the `with lock:` critical section.
2. **Zero Contention:** Locking is per-task (`task_id`). Task A never waits on Task B.
3. **No Lock Inversion or Deadlock:** Locks are acquired only for brief in-memory operations ($\le 10\mu s$); never across async `await`, network calls, or model generation.
4. **Thread & Coroutine Safety:** `threading.RLock` protects against simultaneous access from FastAPI threadpool worker threads (`cancel_task_by_id`) and the main event loop (`_execute_with_loop`).

---

## 9. Alternative Designs Considered

1. **Bare Conditional (`if self._tasks[task_id].status != TaskStatus.CANCELLED: ...`):**
   - *Rejected:* Without a synchronization primitive, a race between the check and assignment remains vulnerable to time-of-check-to-time-of-use (TOCTOU) interleaving.
2. **Global Asyncio Lock (`asyncio.Lock`):**
   - *Rejected:* `asyncio.Lock` is not thread-safe. Because `cancel_task_by_id` is a synchronous FastAPI route running in `starlette.concurrency.run_in_threadpool`, an `asyncio.Lock` would fail or cause event loop conflicts. Furthermore, a single global lock would serialize unrelated tasks.
3. **External State Machine / Database:**
   - *Rejected:* Disallowed by Phase 3 boundary rules. Persistence and database storage are slated for future phases.

---

## 10. Risks & Regression Scenarios

- **Deadlock Risk:** None. The lock is per-task, re-entrant (`RLock`), and only held for in-memory dictionary access.
- **Performance Impact:** Negligible. Dictionary lookup and lock acquisition take $< 1\mu s$.
- **Contract Compatibility:** 100% compatible. Method signatures and return types (`TaskResponse`, `Optional[TaskResponse]`) remain identical.
- **Frontend Compatibility:** Directly improves frontend stability by eliminating contradictory SSE `done` states and preventing status flip-flops in UI cards.

---

## 11. Exact Files Proposed for Modification

1. `core/task_service.py` — Add per-task synchronization and atomic `_transition_task_state`.
2. `tests/test_task_concurrency.py` — New test suite containing deterministic concurrency tests covering all 20 required scenarios across Groups A, B, C, and D.

---

## 12. Tests Required to Establish Correctness

### Group A: Cancellation & Completion Interleavings
1. Cancellation occurs immediately before completion.
2. Cancellation occurs while execution coroutine prepares final response.
3. Completion occurs immediately before cancellation.
4. Cancellation and completion attempt state updates simultaneously via concurrency barriers.
5. Repeated cancellation requests (idempotency).
6. Cancellation of a pending task.
7. Cancellation of an already completed task.

### Group B: Other Terminal States
8. Failure followed by cancellation.
9. Cancellation followed by a late failure result.
10. Limit reached followed by cancellation.
11. Late completion after cancellation.
12. Repeated attempts to transition an already terminal task.

### Group C: Concurrency & Independence
13. Two simultaneous cancellation requests for the same task.
14. Multiple independent tasks executing and cancelling concurrently without cross-blocking.
15. Concurrent status polling (`get_task`) during state transitions.
16. Non-blocking verification: Task A's lock acquisition does not block Task B.

### Group D: Event Consistency
17. Cancellation does not produce a contradictory final completion event.
18. SSE / event history contains exactly one `TASK_TERMINAL` event.
19. Event ordering is strictly preserved.
20. Frontend status mapping compatibility.

---
*End of Change Impact Analysis*
