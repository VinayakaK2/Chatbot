# Tool Contracts and Registry Architecture (Phase 2)

This document specifies the typed tool contracts, validation rules, error taxonomy, and registry operations implemented in Phase 2 of the Agent Runtime.

---

## 1. Architectural Purpose

In accordance with `2604.14228v3.pdf` (Section 3.5 & Section 4) and `docs/phases.md` (Phase 2), tools are **deterministic application capabilities** that are advertised to the model through typed JSON schemas and dispatched through runtime code.

Key architectural boundaries:
- **Registry != Execution:** The registry holds definitions and argument validators. It does not run subprocesses, touch the filesystem, or interact with external services.
- **Model Output is Untrusted:** Argument payloads emitted by the model are parsed safely and strictly validated against registered schemas before any downstream action is permitted.
- **Zero Information Leakage:** Internal execution callables, handlers, and security tags are completely excluded from the exported OpenAI-compatible model schemas.

---

## 2. Core Components

```
                        ┌───────────────────────────────┐
                        │      Pydantic BaseModel       │
                        │    or Raw JSON Schema Dict    │
                        └───────────────┬───────────────┘
                                        │
                                        ▼
                        ┌───────────────────────────────┐
                        │        ToolDefinition         │
                        │  - name, description          │
                        │  - risk_category, strict      │
                        │  - parameters_schema          │
                        │  - handler (callable, opt)    │
                        └───────────────┬───────────────┘
                                        │
                 ┌──────────────────────┴──────────────────────┐
                 ▼                                             ▼
  ┌─────────────────────────────┐               ┌─────────────────────────────┐
  │   to_model_schema()         │               │     validate_input(args)    │
  │   - OpenAI function envelope│               │     - Parse JSON string     │
  │   - additionalProperties    │               │     - Validate types/bounds │
  │   - Zero handler leakage    │               │     - Reject extra fields   │
  └──────────────┬──────────────┘               └──────────────┬──────────────┘
                 │                                             │
                 ▼                                             ▼
  ┌─────────────────────────────┐               ┌─────────────────────────────┐
  │     OpenAI / vLLM Model     │               │   Validated Arguments Dict  │
  └─────────────────────────────┘               └─────────────────────────────┘
```

---

## 3. Tool Definition Examples

### Example A: Pydantic-First Tool Definition (Recommended)

```python
from pydantic import BaseModel, Field
from core.tool_contracts import ToolDefinition, ToolRiskCategory

class ReadFileParams(BaseModel):
    file_path: str = Field(..., description="Target file path within workspace")
    offset: int = Field(default=0, ge=0, description="Byte offset to begin reading")
    max_bytes: int = Field(default=4096, ge=1, le=65536, description="Maximum bytes to read")

# Define execution handler (executed in later phases)
def read_file_handler(file_path: str, offset: int = 0, max_bytes: int = 4096) -> str:
    with open(file_path, "rb") as f:
        f.seek(offset)
        return f.read(max_bytes).decode("utf-8", errors="replace")

tool = ToolDefinition.from_pydantic(
    name="read_file",
    description="Read file contents from the designated workspace root.",
    parameters_model=ReadFileParams,
    risk_category=ToolRiskCategory.READ_ONLY,
    handler=read_file_handler,
    tags=["filesystem", "read"],
    strict=True,
)
```

### Example B: Raw JSON Schema Tool Definition

```python
from core.tool_contracts import ToolDefinition, ToolRiskCategory

raw_schema = {
    "type": "object",
    "properties": {
        "pattern": {"type": "string", "minLength": 1, "description": "Glob pattern to match"},
        "recursive": {"type": "boolean", "default": True},
        "max_matches": {"type": "integer", "minimum": 1, "maximum": 500, "default": 50},
    },
    "required": ["pattern"],
    "additionalProperties": False,
}

tool = ToolDefinition.from_schema(
    name="find_files",
    description="Search for files matching a glob pattern.",
    parameters_schema=raw_schema,
    risk_category=ToolRiskCategory.READ_ONLY,
    tags=["search", "discovery"],
    strict=True,
)
```

---

## 4. Registry Usage and Examples

### 1. Registration
```python
from core.tool_registry import ToolRegistry

registry = ToolRegistry()
registry.register(tool)
```

### 2. Duplicate Registration Rejection
```python
from core.tool_contracts import DuplicateToolError

try:
    registry.register(tool)
except DuplicateToolError as e:
    print(f"Caught duplicate tool registration: {e.tool_name}")
```

### 3. Tool Lookup
```python
# Successful lookup
retrieved_tool = registry.get("read_file")
assert retrieved_tool.name == "read_file"

# Non-existent lookup
from core.tool_contracts import ToolNotFoundError

try:
    registry.get("unknown_tool")
except ToolNotFoundError as e:
    print(f"Tool not found: {e.tool_name}")
```

### 4. Input Validation
```python
# Valid JSON string input from model
raw_arguments = '{"file_path": "main.py", "offset": 0, "max_bytes": 1024}'
validated_dict = registry.validate_call("read_file", raw_arguments)
# Returns: {'file_path': 'main.py', 'offset': 0, 'max_bytes': 1024}

# Invalid input (missing required field)
from core.tool_contracts import ToolValidationError

try:
    registry.validate_call("read_file", '{"offset": 10}')
except ToolValidationError as e:
    print(f"Validation failed: {e.errors}")
```

### 5. Model-Facing Schema Export
```python
# Export OpenAI-compatible schemas for model completion request
schemas = registry.export_model_schemas()
# Returns:
# [
#   {
#     "type": "function",
#     "function": {
#       "name": "read_file",
#       "description": "Read file contents from the designated workspace root.",
#       "parameters": {
#         "type": "object",
#         "properties": { ... },
#         "required": ["file_path"],
#         "additionalProperties": False
#       }
#     }
#   }
# ]
```

---

## 5. Error Taxonomy

| Exception Class | Inherits From | Trigger Condition |
|---|---|---|
| `ToolError` | `Exception` | Base class for all tool subsystem exceptions |
| `ToolRegistrationError` | `ToolError` | General failure during tool registration |
| `DuplicateToolError` | `ToolRegistrationError` | Attempting to register an existing tool name |
| `InvalidToolNameError` | `ToolRegistrationError` | Proposed tool name violates regex, length, or reserved names |
| `InvalidToolSchemaError` | `ToolRegistrationError` | Schema is not `type: "object"` or has malformed properties |
| `ToolNotFoundError` | `ToolError` | Querying a tool name not present in the registry |
| `ToolValidationError` | `ToolError` | Arguments fail schema validation (missing, extra, mistyped) |

---

## 6. Security Invariants

1. **Deterministic Naming:** Names must match `^[a-zA-Z0-9_-]{1,64}$` and must not start with `__` or match reserved tokens (`eval`, `exec`, `system`, `__proto__`, etc.).
2. **Strict Mode Enforcement:** Extra undeclared properties are rejected (`extra_forbidden`) and type coercion (e.g. string for integer or bool for integer) is blocked.
3. **Immutability:** `ToolDefinition` is a frozen Pydantic model (`frozen=True`). Attributes cannot be mutated in-place after registration.
4. **Schema Isolation:** Exported schemas are deep-copied. Caller mutations to exported dictionaries have zero impact on registry state.
5. **No Code Execution in Phase 2:** The registry validates inputs and stores tool contracts; execution is handled by the dedicated Phase 3/4/5 dispatch pipeline.
