"""Typed tool contracts, data schemas, and error hierarchy for agent tools."""

from copy import deepcopy
from datetime import datetime, timezone
from enum import Enum
import json
import re
from typing import Any, Callable, Dict, List, Optional, Set, Type, Union
from pydantic import BaseModel, ConfigDict, Field, create_model, field_validator

# Maximum allowed character length for a stable tool identifier
TOOL_NAME_MAX_LENGTH: int = 64

# Maximum allowed character length for human-readable tool descriptions
TOOL_DESCRIPTION_MAX_LENGTH: int = 2048

# Strict identifier regex: alphanumeric characters, underscores, and hyphens only
TOOL_NAME_REGEX: re.Pattern = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")

# Prohibited tool names to prevent namespace collisions, prototype pollution, and command injection
RESERVED_TOOL_NAMES: frozenset[str] = frozenset(
    {
        "eval",
        "exec",
        "import",
        "system",
        "globals",
        "locals",
        "__init__",
        "__main__",
        "__proto__",
        "constructor",
    }
)


# ==============================================================================
# Error Hierarchy
# ==============================================================================


class ToolError(Exception):
    """Base exception for all tool contract, registration, and validation failures."""

    def __init__(self, message: str) -> None:
        """Initialize base tool error with human-readable diagnostic message."""
        super().__init__(message)
        self.message = message


class ToolRegistrationError(ToolError):
    """Raised when registering a tool definition fails due to schema or constraint violations."""


class DuplicateToolError(ToolRegistrationError):
    """Raised when attempting to register a tool with a name that is already in use."""

    def __init__(self, tool_name: str) -> None:
        """Initialize duplicate tool error with conflicting identifier."""
        super().__init__(f"Tool with name '{tool_name}' is already registered in registry.")
        self.tool_name = tool_name


class InvalidToolNameError(ToolRegistrationError):
    """Raised when a proposed tool name violates format constraints or matches a reserved identifier."""

    def __init__(self, tool_name: str, reason: str) -> None:
        """Initialize invalid name error with name and specific violation reason."""
        super().__init__(f"Invalid tool name '{tool_name}': {reason}")
        self.tool_name = tool_name
        self.reason = reason


class InvalidToolSchemaError(ToolRegistrationError):
    """Raised when a tool parameter schema is malformed or lacks required OpenAPI properties."""


class ToolNotFoundError(ToolError):
    """Raised when querying a tool name that does not exist in the active registry."""

    def __init__(self, tool_name: str) -> None:
        """Initialize tool not found error with requested identifier."""
        super().__init__(f"Tool '{tool_name}' is not registered in the active registry.")
        self.tool_name = tool_name


class ToolValidationError(ToolError):
    """Raised when input arguments fail validation against the tool's declared schema."""

    def __init__(
        self,
        message: str,
        tool_name: Optional[str] = None,
        errors: Optional[List[Dict[str, Any]]] = None,
    ) -> None:
        """Initialize validation error with detailed structured error descriptors."""
        super().__init__(message)
        self.tool_name = tool_name
        self.errors = errors or []


# ==============================================================================
# Tool Risk and Classification
# ==============================================================================


class ToolRiskCategory(str, Enum):
    """Granular risk categories for runtime permission enforcement and safety policies."""

    READ_ONLY = "read_only"
    WORKSPACE_WRITE = "workspace_write"
    COMMAND_EXECUTION = "command_execution"
    DESTRUCTIVE = "destructive"
    SYSTEM_PROBE = "system_probe"


# ==============================================================================
# Tool Result Envelope
# ==============================================================================


class ToolResultEnvelope(BaseModel):
    """Normalized output envelope returned by all tool executions across the runtime."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    call_id: Optional[str] = Field(
        default=None,
        description="Optional upstream tool call ID matching provider request",
    )
    tool_name: str = Field(..., description="Stable identifier of the executed tool")
    success: bool = Field(..., description="Whether the tool execution completed successfully")
    output: Optional[Any] = Field(
        default=None,
        description="Structured output payload or text returned on success",
    )
    error: Optional[str] = Field(
        default=None,
        description="Human-readable error message if tool execution failed",
    )
    error_category: Optional[str] = Field(
        default=None,
        description="Categorical failure classifier (e.g., validation_error, execution_error, timeout)",
    )
    metadata: Dict[str, Any] = Field(
        default_factory=dict,
        description="Execution metadata including latency, output size, and truncation status",
    )
    timestamp: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(),
        description="ISO 8601 UTC timestamp of execution completion",
    )

    @classmethod
    def create_success(
        cls,
        tool_name: str,
        output: Any,
        call_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> "ToolResultEnvelope":
        """Construct a successful tool execution envelope."""
        return cls(
            call_id=call_id,
            tool_name=tool_name,
            success=True,
            output=output,
            error=None,
            error_category=None,
            metadata=metadata or {},
        )

    @classmethod
    def create_error(
        cls,
        tool_name: str,
        error: str,
        error_category: str = "execution_error",
        call_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> "ToolResultEnvelope":
        """Construct a failed tool execution envelope with error classification."""
        return cls(
            call_id=call_id,
            tool_name=tool_name,
            success=False,
            output=None,
            error=error,
            error_category=error_category,
            metadata=metadata or {},
        )


# ==============================================================================
# Schema Validation Helper for Raw JSON Schema
# ==============================================================================


def _validate_raw_schema_value(
    val: Any,
    expected_type: str,
    field_name: str,
    path: str,
) -> None:
    """Validate a single field value against its declared JSON schema primitive type."""
    # Ensure booleans are not treated as integers or numbers
    if expected_type == "string":
        if not (isinstance(val, str) and not isinstance(val, bool)):
            raise ToolValidationError(
                f"Field '{path}' expected string, got {type(val).__name__}",
                errors=[{"loc": [path], "msg": "Expected string", "type": "type_error.string"}],
            )
    elif expected_type == "integer":
        if not (isinstance(val, int) and not isinstance(val, bool)):
            raise ToolValidationError(
                f"Field '{path}' expected integer, got {type(val).__name__}",
                errors=[{"loc": [path], "msg": "Expected integer", "type": "type_error.integer"}],
            )
    elif expected_type == "number":
        if not (isinstance(val, (int, float)) and not isinstance(val, bool)):
            raise ToolValidationError(
                f"Field '{path}' expected number, got {type(val).__name__}",
                errors=[{"loc": [path], "msg": "Expected number", "type": "type_error.number"}],
            )
    elif expected_type == "boolean":
        if not isinstance(val, bool):
            raise ToolValidationError(
                f"Field '{path}' expected boolean, got {type(val).__name__}",
                errors=[{"loc": [path], "msg": "Expected boolean", "type": "type_error.boolean"}],
            )
    elif expected_type == "array":
        if not isinstance(val, list):
            raise ToolValidationError(
                f"Field '{path}' expected array, got {type(val).__name__}",
                errors=[{"loc": [path], "msg": "Expected array", "type": "type_error.array"}],
            )
    elif expected_type == "object":
        if not isinstance(val, dict):
            raise ToolValidationError(
                f"Field '{path}' expected object, got {type(val).__name__}",
                errors=[{"loc": [path], "msg": "Expected object", "type": "type_error.object"}],
            )


def _validate_dict_against_json_schema(
    data: Dict[str, Any],
    schema: Dict[str, Any],
    strict: bool = True,
    path_prefix: str = "",
) -> None:
    """Recursively validate a dictionary against a JSON Schema object specification."""
    properties: Dict[str, Any] = schema.get("properties", {})
    required_fields: List[str] = schema.get("required", [])

    # Step 1: Verify all required properties are present
    for req in required_fields:
        if req not in data:
            field_path = f"{path_prefix}.{req}" if path_prefix else req
            raise ToolValidationError(
                f"Missing required parameter '{field_path}'",
                errors=[{"loc": [field_path], "msg": "Field required", "type": "value_error.missing"}],
            )

    # Step 2: In strict mode, forbid undeclared extra properties
    if strict:
        for key in data:
            if key not in properties:
                field_path = f"{path_prefix}.{key}" if path_prefix else key
                raise ToolValidationError(
                    f"Extra parameter '{field_path}' is not permitted",
                    errors=[{"loc": [field_path], "msg": "Extra field forbidden", "type": "extra_forbidden"}],
                )

    # Step 3: Check types and recursively check nested objects/arrays
    for key, val in data.items():
        if key not in properties:
            continue
        field_path = f"{path_prefix}.{key}" if path_prefix else key
        prop_spec = properties[key]
        expected_type = prop_spec.get("type")

        if val is None:
            # Null is only valid if explicitly permitted
            if expected_type != "null" and not prop_spec.get("nullable", False):
                raise ToolValidationError(
                    f"Field '{field_path}' cannot be null",
                    errors=[{"loc": [field_path], "msg": "Value cannot be null", "type": "type_error.none"}],
                )
            continue

        if expected_type:
            _validate_raw_schema_value(val, expected_type, key, field_path)

        # Enforce enum constraints if declared
        if "enum" in prop_spec and val not in prop_spec["enum"]:
            raise ToolValidationError(
                f"Field '{field_path}' value '{val}' not in permitted enum choices {prop_spec['enum']}",
                errors=[{"loc": [field_path], "msg": f"Value not in enum {prop_spec['enum']}", "type": "value_error.enum"}],
            )

        # Enforce numeric boundary constraints if declared
        if isinstance(val, (int, float)) and not isinstance(val, bool):
            if "minimum" in prop_spec and val < prop_spec["minimum"]:
                raise ToolValidationError(
                    f"Field '{field_path}' value {val} is below minimum {prop_spec['minimum']}",
                    errors=[{"loc": [field_path], "msg": f"Below minimum {prop_spec['minimum']}", "type": "value_error.number.not_ge"}],
                )
            if "maximum" in prop_spec and val > prop_spec["maximum"]:
                raise ToolValidationError(
                    f"Field '{field_path}' value {val} exceeds maximum {prop_spec['maximum']}",
                    errors=[{"loc": [field_path], "msg": f"Exceeds maximum {prop_spec['maximum']}", "type": "value_error.number.not_le"}],
                )

        # Enforce string length bounds if declared
        if isinstance(val, str):
            if "minLength" in prop_spec and len(val) < prop_spec["minLength"]:
                raise ToolValidationError(
                    f"Field '{field_path}' length {len(val)} is below minLength {prop_spec['minLength']}",
                    errors=[{"loc": [field_path], "msg": f"String too short (< {prop_spec['minLength']})", "type": "value_error.string.too_short"}],
                )
            if "maxLength" in prop_spec and len(val) > prop_spec["maxLength"]:
                raise ToolValidationError(
                    f"Field '{field_path}' length {len(val)} exceeds maxLength {prop_spec['maxLength']}",
                    errors=[{"loc": [field_path], "msg": f"String too long (> {prop_spec['maxLength']})", "type": "value_error.string.too_long"}],
                )

        # Recursively validate nested object
        if expected_type == "object" and isinstance(val, dict) and "properties" in prop_spec:
            _validate_dict_against_json_schema(val, prop_spec, strict=strict, path_prefix=field_path)

        # Validate array item types if declared
        if expected_type == "array" and isinstance(val, list) and "items" in prop_spec:
            item_spec = prop_spec["items"]
            item_type = item_spec.get("type")
            for idx, item in enumerate(val):
                item_path = f"{field_path}[{idx}]"
                if item_type:
                    _validate_raw_schema_value(item, item_type, str(idx), item_path)
                if item_type == "object" and isinstance(item, dict) and "properties" in item_spec:
                    _validate_dict_against_json_schema(item, item_spec, strict=strict, path_prefix=item_path)


# ==============================================================================
# Tool Definition Contract
# ==============================================================================


class ToolDefinition(BaseModel):
    """Immutable, typed specification of a runtime tool capability and argument contract."""

    model_config = ConfigDict(
        frozen=True,
        arbitrary_types_allowed=True,
        extra="forbid",
    )

    name: str = Field(..., description="Stable, unique tool identifier")
    description: str = Field(..., description="Human-readable description exposed to model")
    parameters_schema: Dict[str, Any] = Field(
        ...,
        description="Standard OpenAPI/JSON Schema dictionary describing accepted arguments",
    )
    parameters_model: Optional[Type[BaseModel]] = Field(
        default=None,
        description="Optional strongly-typed Pydantic model for argument validation",
    )
    risk_category: ToolRiskCategory = Field(
        default=ToolRiskCategory.READ_ONLY,
        description="Risk level governing permission policy and safety gates",
    )
    strict: bool = Field(
        default=True,
        description="Whether to forbid undeclared parameters and enforce strict types",
    )
    tags: List[str] = Field(
        default_factory=list,
        description="Semantic tags categorizing capability (e.g. filesystem, git, test)",
    )
    handler: Optional[Callable[..., Any]] = Field(
        default=None,
        exclude=True,
        description="Optional execution callable preserved for future dispatch (never exposed to model)",
    )

    @field_validator("name")
    @classmethod
    def validate_tool_name(cls, v: str) -> str:
        """Enforce strict naming conventions and reject reserved or dangerous names."""
        if not v or not isinstance(v, str):
            raise InvalidToolNameError(str(v), "Tool name must be a non-empty string.")

        stripped = v.strip()
        if stripped != v:
            raise InvalidToolNameError(v, "Tool name must not contain leading or trailing whitespace.")

        if len(stripped) > TOOL_NAME_MAX_LENGTH:
            raise InvalidToolNameError(
                v,
                f"Tool name length ({len(stripped)}) exceeds maximum allowed ({TOOL_NAME_MAX_LENGTH}).",
            )

        if not TOOL_NAME_REGEX.match(stripped):
            raise InvalidToolNameError(
                v,
                "Tool name must only contain alphanumeric characters, underscores, and hyphens.",
            )

        if stripped.startswith("__"):
            raise InvalidToolNameError(
                v,
                "Tool names must not start with double underscores (reserved for internals).",
            )

        if stripped.lower() in RESERVED_TOOL_NAMES:
            raise InvalidToolNameError(
                v,
                f"Tool name '{v}' is a reserved identifier and cannot be registered.",
            )

        return stripped

    @field_validator("description")
    @classmethod
    def validate_tool_description(cls, v: str) -> str:
        """Ensure tool description is present, non-empty, and bounded in length."""
        if not v or not isinstance(v, str):
            raise InvalidToolSchemaError("Tool description must be a non-empty string.")

        stripped = v.strip()
        if not stripped:
            raise InvalidToolSchemaError("Tool description must not be whitespace-only.")

        if len(stripped) > TOOL_DESCRIPTION_MAX_LENGTH:
            raise InvalidToolSchemaError(
                f"Tool description exceeds maximum allowed length of {TOOL_DESCRIPTION_MAX_LENGTH} characters."
            )

        return stripped

    @field_validator("parameters_schema")
    @classmethod
    def validate_parameters_schema(cls, v: Dict[str, Any]) -> Dict[str, Any]:
        """Validate that the parameter schema conforms to minimal OpenAPI object format."""
        if not isinstance(v, dict):
            raise InvalidToolSchemaError(f"parameters_schema must be a dictionary, got {type(v).__name__}.")

        schema_type = v.get("type")
        if schema_type != "object":
            raise InvalidToolSchemaError(
                f"parameters_schema 'type' must be 'object', got '{schema_type}'."
            )

        properties = v.get("properties")
        if properties is not None and not isinstance(properties, dict):
            raise InvalidToolSchemaError("parameters_schema 'properties' must be a dictionary if present.")

        required = v.get("required")
        if required is not None:
            if not isinstance(required, list) or not all(isinstance(item, str) for item in required):
                raise InvalidToolSchemaError("parameters_schema 'required' must be a list of strings if present.")

        return v

    def to_model_schema(self) -> Dict[str, Any]:
        """Export tool definition in standardized OpenAI function calling format.

        Produces an isolated deepcopy ensuring no caller mutations affect internal registry state
        and zero internal implementation details (e.g. callable handlers, runtime tags) are leaked.
        """
        # Deepcopy parameters schema to prevent external mutation of registered schema
        clean_parameters = deepcopy(self.parameters_schema)

        # Enforce additionalProperties: false in model schema when strict validation is enabled
        if self.strict and "additionalProperties" not in clean_parameters:
            clean_parameters["additionalProperties"] = False

        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": clean_parameters,
            },
        }

    def validate_input(self, arguments: Union[Dict[str, Any], str, None]) -> Dict[str, Any]:
        """Validate input arguments against declared schema, returning normalized dictionary.

        Supports both raw dictionaries and JSON-encoded argument strings emitted by LLMs.
        Rejects malformed JSON, type mismatch, missing required fields, and unexpected extra fields.
        """
        # Step 1: Normalize None to empty dictionary
        if arguments is None:
            raw_args: Dict[str, Any] = {}
        elif isinstance(arguments, str):
            # Parse model-emitted JSON string safely
            stripped = arguments.strip()
            if not stripped:
                raw_args = {}
            else:
                try:
                    parsed = json.loads(stripped)
                except Exception as exc:
                    raise ToolValidationError(
                        f"Malformed JSON in tool arguments for tool '{self.name}': {exc}",
                        tool_name=self.name,
                        errors=[{"msg": "Malformed JSON", "type": "json_invalid"}],
                    ) from exc
                if not isinstance(parsed, dict):
                    raise ToolValidationError(
                        f"Tool arguments for '{self.name}' must decode to a dictionary, got {type(parsed).__name__}",
                        tool_name=self.name,
                        errors=[{"msg": "Arguments must be a JSON object", "type": "type_error.object"}],
                    )
                raw_args = parsed
        elif isinstance(arguments, dict):
            raw_args = arguments
        else:
            raise ToolValidationError(
                f"Arguments for tool '{self.name}' must be dict or JSON string, got {type(arguments).__name__}",
                tool_name=self.name,
                errors=[{"msg": "Invalid argument format", "type": "type_error.format"}],
            )

        # Step 2: Validate through strongly-typed Pydantic model if available
        if self.parameters_model is not None:
            try:
                # Wrap model to strictly enforce extra='forbid' and strict=True when requested
                if self.strict:
                    strict_model_class = create_model(
                        f"Strict{self.parameters_model.__name__}",
                        __base__=self.parameters_model,
                        __config__=ConfigDict(extra="forbid", strict=True),
                    )
                    validated_obj = strict_model_class.model_validate(raw_args)
                else:
                    validated_obj = self.parameters_model.model_validate(raw_args)

                return validated_obj.model_dump()
            except Exception as exc:
                from pydantic import ValidationError as PydanticValidationError

                if isinstance(exc, PydanticValidationError):
                    raise ToolValidationError(
                        f"Argument validation failed for tool '{self.name}': {exc.errors()}",
                        tool_name=self.name,
                        errors=exc.errors(),
                    ) from exc
                raise ToolValidationError(
                    f"Unexpected validation error for tool '{self.name}': {exc}",
                    tool_name=self.name,
                ) from exc

        # Step 3: Validate through manual JSON Schema validator
        _validate_dict_against_json_schema(raw_args, self.parameters_schema, strict=self.strict)
        return deepcopy(raw_args)

    @classmethod
    def from_pydantic(
        cls,
        name: str,
        description: str,
        parameters_model: Type[BaseModel],
        risk_category: ToolRiskCategory = ToolRiskCategory.READ_ONLY,
        strict: bool = True,
        tags: Optional[List[str]] = None,
        handler: Optional[Callable[..., Any]] = None,
    ) -> "ToolDefinition":
        """Factory creating a ToolDefinition derived directly from a Pydantic argument model."""
        if not (isinstance(parameters_model, type) and issubclass(parameters_model, BaseModel)):
            raise InvalidToolSchemaError(
                f"parameters_model must be a subclass of pydantic.BaseModel, got {parameters_model}"
            )

        # Extract standard OpenAPI JSON schema from Pydantic model
        schema = parameters_model.model_json_schema()

        # Sanitize title and metadata for clean model export
        clean_schema: Dict[str, Any] = {
            "type": "object",
            "properties": schema.get("properties", {}),
        }
        if "required" in schema and schema["required"]:
            clean_schema["required"] = schema["required"]
        if strict:
            clean_schema["additionalProperties"] = False

        return cls(
            name=name,
            description=description,
            parameters_schema=clean_schema,
            parameters_model=parameters_model,
            risk_category=risk_category,
            strict=strict,
            tags=tags or [],
            handler=handler,
        )

    @classmethod
    def from_schema(
        cls,
        name: str,
        description: str,
        parameters_schema: Dict[str, Any],
        risk_category: ToolRiskCategory = ToolRiskCategory.READ_ONLY,
        strict: bool = True,
        tags: Optional[List[str]] = None,
        handler: Optional[Callable[..., Any]] = None,
    ) -> "ToolDefinition":
        """Factory creating a ToolDefinition from an explicit JSON Schema dictionary."""
        return cls(
            name=name,
            description=description,
            parameters_schema=parameters_schema,
            parameters_model=None,
            risk_category=risk_category,
            strict=strict,
            tags=tags or [],
            handler=handler,
        )
