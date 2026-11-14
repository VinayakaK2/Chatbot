# ADR 0002: Deterministic Typed Tool Contracts, Validation, and Thread-Safe Tool Registry

- **Status:** Accepted
- **Deciders:** Senior Staff Software Engineer, AI-Runtime Architect
- **Date:** 2026-11-13
- **Context:** Agent Runtime Project (Phase 2 — Typed Tool Contracts & Registry)

---

## 1. Decision

1. **Deterministic Application Infrastructure Over LLM Control:**
   Tools are registered, discovered, validated, and exported through deterministic runtime code. The LLM does not define tools, grant permissions, or alter schema contracts.

2. **Decoupled Definition from Execution:**
   `ToolDefinition` defines the tool's stable name, human-readable description, input parameter schema, risk category, and immutability settings. Actual tool execution is strictly separated and deferred to Phase 3 (Execution Loop) and Phase 4/5 (Filesystem & Sandbox tools). The registry holds tool definitions and optional handler references without executing them.

3. **Pydantic-First Schema Validation with Raw JSON Schema Fallback:**
   - Tools can be registered directly from a Pydantic `BaseModel` via `ToolDefinition.from_pydantic(...)`, producing compliant OpenAPI/JSON Schema contracts.
   - Alternatively, tools can be registered from validated raw JSON Schema dictionaries via `ToolDefinition.from_schema(...)`.
   - In both modes, argument validation rejects malformed JSON, type mismatch (including bool-to-int coercion), missing required fields, and unexpected extra fields when strict mode is active.

4. **Thread-Safe, Deterministic Tool Registry:**
   - `ToolRegistry` enforces unique tool names, rejecting duplicate registrations with `DuplicateToolError`.
   - Reserved identifiers (`eval`, `exec`, `system`, `__proto__`, etc.) and dangerous formats (whitespace, special characters, path traversal) are rejected at registration time with `InvalidToolNameError`.
   - Tool lookups and model schema exports are deterministically sorted alphabetically by tool name, preventing nondeterministic prompt caching degradation across agent runs.
   - Instance isolation is maintained: distinct `ToolRegistry` instances operate independently with zero shared mutable state. Thread-safety is enforced via `threading.RLock`.

5. **Model-Facing Schema Isolation:**
   `ToolDefinition.to_model_schema()` returns a clean, deep-copied OpenAI function specification (`{"type": "function", "function": {"name": ..., "description": ..., "parameters": ...}}`). Zero internal implementation details (e.g. Python function pointers, callable handlers, internal tags, or risk metadata) are exposed to the model.

6. **Normalized Tool Result Envelope:**
   `ToolResultEnvelope` standardizes tool outputs into a structured record with `tool_name`, `call_id`, `success`, `output`, `error`, `error_category`, `metadata`, and ISO 8601 UTC `timestamp`, ready for Phase 3 message assembly.

---

## 2. Evidence and Justification

- **Paper `2604.14228v3.pdf` (Section 3.5, Section 4.1, Pages 8–12):**
  - Demonstrates that coding agents require structured tool definitions exported to the model, while actual tool dispatch and permission checks must occur in the surrounding runtime harness.
  - Highlights that model proposals are untrusted inputs requiring deterministic schema validation before any side-effecting code is executed.
- **Paper `2604.14228v3.pdf` (Section 5, Figure 4, Pages 13–15):**
  - Emphasizes granular risk categorization (`READ_ONLY`, `WORKSPACE_WRITE`, `COMMAND_EXECUTION`, `DESTRUCTIVE`) to enable automated permission policies.
- **Specification `docs/phases.md` (Phase 2, Lines 243–278, Lines 434–458):**
  - Explicitly mandates: typed tool contracts, Pydantic schema validation, duplicate rejection, export in model API format, normalized result envelope, and rejection of invalid arguments before side effects.

---

## 3. Alternatives Considered

1. **Ad-Hoc Python Function Introspection (`inspect.signature`):**
   - *Rejected:* Raw Python docstrings and inspect signatures do not provide strong JSON Schema type bounds, numeric range validation, or regex matching needed by OpenAI-compatible models.
2. **Third-Party Agent Tool Frameworks (LangChain Tools / CrewAI):**
   - *Rejected:* Introduces massive dependency bloat, opinionated execution loops, and leaky abstractions. Using pure Pydantic 2.x and standard Python libraries achieves complete type safety with zero external framework dependencies.
3. **Database-Backed Persistent Tool Registry:**
   - *Rejected:* Unjustified complexity for Phase 2. The roadmap specifies in-memory discovery and registration for built-in capabilities; persistence is only needed for sessions and tasks in Phase 7.
4. **Permissive Type Coercion:**
   - *Rejected:* Silently coercing `"123"` to `123` or `True` to `1` can cause unexpected bugs in file offsets, port numbers, or command arguments. Strict validation ensures model intent aligns with tool specifications.

---

## 4. Trade-offs

- **Strict Validation Overhead:** Requiring Pydantic models or validated JSON Schemas adds slight boilerplate when defining tools, but completely eliminates runtime type confusion, path injection via names, and malformed parameter bugs.
- **Memory Footprint:** In-memory registry with deep-copied schema exports incurs negligible CPU/memory cost while guaranteeing mutation immunity.
