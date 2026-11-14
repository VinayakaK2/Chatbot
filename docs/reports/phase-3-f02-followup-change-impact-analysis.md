# Phase 3 Defect F-02 Follow-up Change Impact Analysis — Lock Order, Thread Safety & Lifecycle Architecture

**Document Status:** PENDING ORCHESTRATOR REVIEW  
**Remediation Target:** Follow-up Concurrency Hardening for Phase 3 Defect F-02  
**Authorities:** `sources/2604.14228v3.pdf`, `docs/phases.md`, `AGENTS.md`  
**Date:** 2026-11-14  

---

## 1. Exact Deadlock Interleaving (Lock-Order Inversion Analysis)

### The Defect in Prior Implementation
In the initial F-02 fix, `cleanup_task()` attempted to acquire locks in the following sequence:
```python
# In cleanup_task():
with self._registry_lock:                     # 1. Acquire global registry lock
    lock = self._task_locks.get(task_id)
    with lock:                                 # 2. Acquire per-task lock WHILE holding registry lock!
        ...
```
Meanwhile, other operations execute with the per-task lock held:
```python
# In _transition_task_state(), cancel_task(), get_task():
lock = self._get_task_lock(task_id)            # 1. Acquire registry lock briefly, release, acquire task lock
with lock:                                     # 2. Hold task lock
    # If any internal helper ever touches _registry_lock or competes with cleanup
```

### Concrete Interleaving Triggering Deadlock / Global Head-of-Line Blocking
1. **Thread 1 (`cleanup_task` on Task A):** Acquires `self._registry_lock`.
2. **Thread 2 (Active execution or cancellation on Task A):** Holds Task A's `task_lock`.
3. **Thread 1** attempts `with lock:` on Task A. Because Thread 2 holds it, Thread 1 **blocks while holding `self._registry_lock`**.
4. **Thread 3 (Unrelated task, Task B):** Calls `submit_task`, `get_task`, or `cancel_task` for Task B. It calls `_get_task_lock("task-b")`, which attempts `with self._registry_lock:`.
5. **Impact:** Thread 3 is blocked on `_registry_lock` held by Thread 1. All task creation, lookup, and cancellation across the **entire application** stall indefinitely until Thread 2 releases Task A's lock.
6. If Thread 2 (or any tool/hook called by Thread 2) attempts any registry query that acquires `_registry_lock`, an immediate **circular deadlock** occurs:
   - Thread 1 holds `_registry_lock`, waits for Task A `task_lock`.
   - Thread 2 holds Task A `task_lock`, waits for `_registry_lock`.

### The Architectural Fix
- **Rule:** **Never hold `_registry_lock` while acquiring or holding any task lock.**
- **Lock Hierarchy:**
  - **Level 1 (Domain Lock):** `task_lock` (`threading.RLock`) per `task_id`.
  - **Level 2 (Leaf Registry Guard):** `self._registry_lock` (`threading.Lock`).
  - `_registry_lock` is acquired **only** for quick dictionary lookup/mutations ($< 1\mu s$) and is **never** held across any other lock acquisition or await point.
- **Cleanup Order:**
  1. Retrieve task lock reference under `_registry_lock`, release `_registry_lock` immediately.
  2. Acquire `with lock:`.
  3. Verify task is terminal. If not terminal, release lock and abort.
  4. While holding `lock`, acquire `with self._registry_lock:` to prune dictionary entries, release `_registry_lock`.
  5. Release `lock`.
  Because Level 2 is never held while waiting for Level 1, inversion is mathematically impossible.

---

## 2. Queue Ownership & Event-Loop Lifecycle Design

### The Problem
1. **Private Attribute Reliance:** Inspecting `getattr(q, "_loop", None)` relies on an undocumented, private CPython implementation detail that was deprecated in Python 3.10.
2. **Cross-Thread Queue Mutation:** Calling `q.put_nowait()` on an `asyncio.Queue` from a threadpool worker thread violates `asyncio.Queue` thread-safety contracts, leading to potential waiter corruption or lost wakeups for coroutines awaiting `queue.get()`.
3. **Closed Event Loops:** If a client disconnects and its event loop closes, writing to its queue can raise unhandled `RuntimeError: Event loop is closed`.

### The Solution: Explicit `EventSubscriber` Model
1. **Explicit Loop Association:**
   Define a typed dataclass for subscribers:
   ```python
   class EventSubscriber:
       queue: asyncio.Queue[Optional[RuntimeEvent]]
       loop: asyncio.AbstractEventLoop
   ```
2. **Registration Protocol:**
   `subscribe_events(task_id: str, queue: asyncio.Queue, loop: asyncio.AbstractEventLoop)` explicitly accepts the subscriber's owning event loop.
3. **Cross-Thread Dispatch Contract:**
   In `_broadcast_event()`:
   ```python
   for sub in list(self._event_subscribers[task_id]):
       if sub.loop.is_closed():
           dead_subscribers.append(sub)
           continue

       try:
           current_loop = asyncio.get_running_loop()
       except RuntimeError:
           current_loop = None

       if current_loop is sub.loop:
           # Same event loop: safe direct put
           self._safe_put_nowait(sub.queue, event)
       else:
           # Cross-thread or foreign loop: dispatch via call_soon_threadsafe
           sub.loop.call_soon_threadsafe(self._safe_put_nowait, sub.queue, event)
   ```
4. **Closed Loop Handling:** Dead subscribers on closed loops are cleanly pruned without raising exceptions or leaking memory.

---

## 3. Cancellation-Token Signaling Design

### The Problem
`self._cancellation_tokens[task_id]` is an `asyncio.Event`. The Python documentation explicitly states:
> *"This class is not thread-safe."*
If `cancel_task()` is executed from an OS worker thread (such as FastAPI's threadpool), directly invoking `token.set()` can corrupt internal asyncio waiter linked lists, resulting in missed cancellation wakeups.

### The Solution: Event Loop-Bound Signal Dispatch
1. When a task starts execution in `_execute_with_loop()`, its owning event loop is recorded:
   ```python
   self._task_loops[task_id] = asyncio.get_running_loop()
   ```
2. In `cancel_task(task_id)`:
   ```python
   token = self._cancellation_tokens.get(task_id)
   task_loop = self._task_loops.get(task_id)
   if token is not None:
       try:
           current_loop = asyncio.get_running_loop()
       except RuntimeError:
           current_loop = None

       if task_loop and not task_loop.is_closed() and current_loop is not task_loop:
           # Thread-safe cross-thread signal
           task_loop.call_soon_threadsafe(token.set)
       else:
           # Direct same-loop signal
           token.set()
   ```
3. This guarantees that `token.set()` is **never** executed from a non-owning OS thread.

---

## 4. Cleanup & Task-Lock Lifecycle Design

### The Problem: Lock Deletion / Recreation Races
If Thread A cleans up Task 1 by popping `_task_locks["task-1"]`, and Thread B concurrently calls `get_task("task-1")` or `cancel_task("task-1")`:
If `_get_task_lock()` blindly instantiates a new lock for any queried string, Thread B creates an orphaned lock for a deleted task, leaking memory and introducing split lock identities.

### The Solution: Explicit Lock Creation Guard
1. **Creation Restriction:**
   `_get_task_lock(task_id: str, create: bool = False) -> Optional[threading.RLock]`:
   - `create=True` is passed **only** during task creation (`submit_task`).
   - For queries (`get_task`, `cancel_task`, `get_events`, `cleanup_task`), `create=False` is used. If the task does not exist or was cleaned up, `None` is returned, preventing ghost lock instantiation.
2. **Pruning Protocol:**
   `cleanup_task()`:
   - Fetches the existing lock without holding `_registry_lock`.
   - Acquires `with lock:`.
   - Verifies `task.status in TERMINAL_STATUSES`. If not, aborts.
   - Under `with self._registry_lock:`, deletes `_tasks[task_id]`, `_events[task_id]`, `_event_subscribers[task_id]`, `_cancellation_tokens[task_id]`, `_task_loops[task_id]`, and `_task_locks[task_id]`.
   - Any concurrent or subsequent query finds no task and no lock, returning `None` cleanly.

---

## 5. Affected Files, Callers, Tests & API Contracts

1. **`core/task_service.py`:**
   - Define `EventSubscriber` class.
   - Refactor `_get_task_lock()` to accept `create: bool = False`.
   - Refactor `cleanup_task()` to eliminate lock-order inversion.
   - Refactor `_broadcast_event()` to use `sub.loop.call_soon_threadsafe()`.
   - Refactor `cancel_task()` to signal `cancellation_token` via `task_loop.call_soon_threadsafe()`.
2. **`api/routes/tasks.py`:**
   - In `stream_task_events()`, obtain `loop = asyncio.get_running_loop()` and pass to `task_service.subscribe_events(task_id, queue, loop)`.
3. **`tests/test_task_concurrency.py`:**
   - Add targeted tests for:
     1. Cleanup racing with state transitions (zero deadlock).
     2. Cleanup racing with `get_task`/`get_events` (zero split lock identities).
     3. Cleanup on active tasks fails closed.
     4. Worker thread event publication while SSE coroutine awaits `queue.get()`.
     5. Closed-loop subscriber safe handling.
     6. Worker-thread cancellation token signaling cross-thread.
4. **API Contracts:** Unchanged. All REST payloads and SSE streaming behavior remain 100% backward compatible.

---

## 6. Alternatives Considered & Risk Assessment

- **Alternative 1: Global Asyncio Synchronization (`asyncio.Lock`)**
  - *Rejected:* Does not protect against multi-threaded access from FastAPI threadpool worker threads (`cancel_task_by_id`), and serializes unrelated tasks across the system.
- **Alternative 2: Deferring Cleanup to Garbage Collection Only**
  - *Rejected:* Leaves unbounded memory growth in long-running processes without a deterministic prune mechanism.
- **Risk Assessment:**
  - *Risk:* Calling `call_soon_threadsafe` on a loop that closes between the check and call.
  - *Mitigation:* Wrapped in `try/except RuntimeError` to safely swallow delivery attempts to shutting-down loops.

---
*End of Follow-up Change Impact Analysis*
