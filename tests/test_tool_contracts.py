"""Comprehensive unit tests for typed tool contracts, schemas, and result envelopes."""

import json
import pytest
from pydantic import BaseModel, Field, ValidationError

from core.tool_contracts import (
    DuplicateToolError,
    InvalidToolNameError,
    InvalidToolSchemaError,
    ToolDefinition,
    ToolError,
    ToolNotFoundError,
    ToolRegistrationError,
    ToolResultEnvelope,
    ToolRiskCategory,
    ToolValidationError,
)


# Sample Pydantic argument schemas for testing
class FileReadInput(BaseModel):
    """Parameters for reading a file with optional pagination offset."""

    path: str = Field(..., description="Target file path")
    offset: int = Field(default=0, ge=0, description="Byte offset to begin reading")
    limit: int = Field(default=1024, ge=1, le=65536, description="Maximum bytes to read")


class AdvancedSearchInput(BaseModel):
    """Parameters with nested objects, arrays, and strict validation."""

    query: str = Field(..., min_length=1, description="Search query string")
    file_types: list[str] = Field(default_factory=list, description="List of file extensions")
    depth: int = Field(default=3, ge=1, le=10)


class TestToolNameValidation:
    """Verify tool name format constraints, length boundaries, and reserved name protection."""

    @pytest.mark.parametrize(
        "valid_name",
        [
            "read_file",
            "search-files",
            "get_file_metadata_v2",
            "git-status",
            "a",
            "tool_123_abc-xyz",
            "UPPERCASE_NAME",
        ],
    )
    def test_valid_tool_names_accepted(self, valid_name: str) -> None:
        """Ensure standard alphanumeric names with underscores and hyphens are accepted."""
        tool = ToolDefinition.from_pydantic(
            name=valid_name,
            description="A valid test tool",
            parameters_model=FileReadInput,
        )
        assert tool.name == valid_name

    @pytest.mark.parametrize(
        "invalid_name,expected_reason",
        [
            ("", "non-empty string"),
            ("   ", "leading or trailing whitespace"),
            (" read_file", "leading or trailing whitespace"),
            ("read_file ", "leading or trailing whitespace"),
            ("tool with spaces", "alphanumeric characters"),
            ("tool.with.dots", "alphanumeric characters"),
            ("tool/slash", "alphanumeric characters"),
            ("../path_traversal", "alphanumeric characters"),
            ("tool;command", "alphanumeric characters"),
            ("tool$var", "alphanumeric characters"),
            ("tool&exec", "alphanumeric characters"),
            ("tool\nbreak", "alphanumeric characters"),
            ("a" * 65, "exceeds maximum allowed"),
        ],
    )
    def test_malformed_tool_names_rejected(self, invalid_name: str, expected_reason: str) -> None:
        """Ensure whitespace, punctuation, path separators, and oversized names raise InvalidToolNameError."""
        with pytest.raises(InvalidToolNameError) as exc_info:
            ToolDefinition.from_pydantic(
                name=invalid_name,
                description="Valid description",
                parameters_model=FileReadInput,
            )
        assert expected_reason in str(exc_info.value)

    @pytest.mark.parametrize(
        "reserved_name",
        [
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
        ],
    )
    def test_reserved_names_rejected(self, reserved_name: str) -> None:
        """Ensure system keywords and prototype injection tokens cannot be used as tool names."""
        with pytest.raises(InvalidToolNameError) as exc_info:
            ToolDefinition.from_pydantic(
                name=reserved_name,
                description="Valid description",
                parameters_model=FileReadInput,
            )
        assert "reserved" in str(exc_info.value).lower() or "internals" in str(exc_info.value).lower()

    def test_double_underscore_prefix_rejected(self) -> None:
        """Reject tool names starting with double underscore to prevent internal namespace spoofing."""
        with pytest.raises(InvalidToolNameError) as exc_info:
            ToolDefinition.from_pydantic(
                name="__internal_hook",
                description="Valid description",
                parameters_model=FileReadInput,
            )
        assert "double underscores" in str(exc_info.value)


class TestToolDescriptionValidation:
    """Verify tool description presence, non-emptiness, and length bounds."""

    def test_empty_description_rejected(self) -> None:
        """Reject empty description string."""
        with pytest.raises(InvalidToolSchemaError) as exc_info:
            ToolDefinition.from_pydantic(
                name="read_file",
                description="",
                parameters_model=FileReadInput,
            )
        assert "non-empty string" in str(exc_info.value)

    def test_whitespace_description_rejected(self) -> None:
        """Reject whitespace-only description string."""
        with pytest.raises(InvalidToolSchemaError) as exc_info:
            ToolDefinition.from_pydantic(
                name="read_file",
                description="     ",
                parameters_model=FileReadInput,
            )
        assert "whitespace-only" in str(exc_info.value)

    def test_oversized_description_rejected(self) -> None:
        """Reject descriptions exceeding the 2048 character limit."""
        with pytest.raises(InvalidToolSchemaError) as exc_info:
            ToolDefinition.from_pydantic(
                name="read_file",
                description="x" * 2049,
                parameters_model=FileReadInput,
            )
        assert "maximum allowed length" in str(exc_info.value)


class TestRawSchemaValidation:
    """Verify tool definition creation via raw JSON Schema dictionaries."""

    def test_valid_raw_schema_construction(self) -> None:
        """Ensure valid raw JSON Schema is accepted."""
        schema = {
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "description": "Path to file"},
                "verbose": {"type": "boolean", "default": False},
            },
            "required": ["file_path"],
        }
        tool = ToolDefinition.from_schema(
            name="custom_reader",
            description="Reads files with raw schema",
            parameters_schema=schema,
            risk_category=ToolRiskCategory.READ_ONLY,
        )
        assert tool.name == "custom_reader"
        assert tool.parameters_schema["type"] == "object"
        assert "file_path" in tool.parameters_schema["properties"]

    def test_non_object_schema_type_rejected(self) -> None:
        """Reject schemas where type is not 'object'."""
        with pytest.raises(InvalidToolSchemaError) as exc_info:
            ToolDefinition.from_schema(
                name="bad_schema_tool",
                description="Invalid schema",
                parameters_schema={"type": "array", "items": {"type": "string"}},
            )
        assert "must be 'object'" in str(exc_info.value)

    def test_non_dict_properties_rejected(self) -> None:
        """Reject schemas where properties is not a dictionary."""
        with pytest.raises(InvalidToolSchemaError) as exc_info:
            ToolDefinition.from_schema(
                name="bad_schema_tool",
                description="Invalid schema",
                parameters_schema={"type": "object", "properties": ["not", "a", "dict"]},
            )
        assert "'properties' must be a dictionary" in str(exc_info.value)

    def test_non_list_required_rejected(self) -> None:
        """Reject schemas where required is not a list of strings."""
        with pytest.raises(InvalidToolSchemaError) as exc_info:
            ToolDefinition.from_schema(
                name="bad_schema_tool",
                description="Invalid schema",
                parameters_schema={
                    "type": "object",
                    "properties": {"val": {"type": "string"}},
                    "required": "val",  # String instead of list
                },
            )
        assert "'required' must be a list of strings" in str(exc_info.value)


class TestToolInputValidation:
    """Verify input validation for dicts, JSON strings, strict typing, and boundary enforcement."""

    def setup_method(self) -> None:
        """Create reference tool definitions for testing."""
        self.pydantic_tool = ToolDefinition.from_pydantic(
            name="read_file",
            description="Reads file contents",
            parameters_model=FileReadInput,
            strict=True,
        )
        self.raw_schema_tool = ToolDefinition.from_schema(
            name="set_status",
            description="Sets status with enum and numeric bounds",
            parameters_schema={
                "type": "object",
                "properties": {
                    "status": {"type": "string", "enum": ["active", "paused", "stopped"]},
                    "priority": {"type": "integer", "minimum": 1, "maximum": 5},
                    "tags": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["status", "priority"],
            },
            strict=True,
        )

    def test_valid_dict_input(self) -> None:
        """Ensure valid dict arguments validate successfully."""
        args = {"path": "src/main.py", "offset": 0, "limit": 500}
        validated = self.pydantic_tool.validate_input(args)
        assert validated["path"] == "src/main.py"
        assert validated["offset"] == 0
        assert validated["limit"] == 500

    def test_valid_json_string_input(self) -> None:
        """Ensure valid JSON string arguments decode and validate successfully."""
        json_str = json.dumps({"path": "docs/readme.md", "offset": 100})
        validated = self.pydantic_tool.validate_input(json_str)
        assert validated["path"] == "docs/readme.md"
        assert validated["offset"] == 100
        # Default value applied
        assert validated["limit"] == 1024

    def test_none_input_normalized_to_empty_dict(self) -> None:
        """Ensure None arguments are treated as an empty dict."""
        # Tool with all optional fields
        class OptionalArgs(BaseModel):
            flag: bool = False

        tool = ToolDefinition.from_pydantic(
            name="optional_tool",
            description="Tool with defaults",
            parameters_model=OptionalArgs,
        )
        validated = tool.validate_input(None)
        assert validated == {"flag": False}

    def test_missing_required_parameter_raises_validation_error(self) -> None:
        """Ensure omitting required parameter raises ToolValidationError with structured loc."""
        with pytest.raises(ToolValidationError) as exc_info:
            self.pydantic_tool.validate_input({"offset": 10})
        assert "path" in str(exc_info.value).lower()
        assert exc_info.value.tool_name == "read_file"

    def test_extra_forbidden_in_strict_mode(self) -> None:
        """Ensure extra undeclared arguments are rejected in strict mode."""
        with pytest.raises(ToolValidationError) as exc_info:
            self.pydantic_tool.validate_input(
                {"path": "file.txt", "unauthorized_extra_field": "injected"}
            )
        assert "extra" in str(exc_info.value).lower()

    def test_type_coercion_rejected_in_strict_mode(self) -> None:
        """Ensure string representations of numbers are not silently coerced to ints in strict mode."""
        with pytest.raises(ToolValidationError):
            # 'offset' expects int, string '100' must be rejected
            self.pydantic_tool.validate_input({"path": "file.txt", "offset": "100"})

    def test_boolean_for_integer_rejected(self) -> None:
        """Ensure Python bool (subclass of int) is rejected when integer is expected."""
        with pytest.raises(ToolValidationError):
            self.pydantic_tool.validate_input({"path": "file.txt", "offset": True})

    def test_malformed_json_string_rejected(self) -> None:
        """Ensure unparseable JSON strings produce a clear ToolValidationError."""
        with pytest.raises(ToolValidationError) as exc_info:
            self.pydantic_tool.validate_input("{malformed json:")
        assert "malformed json" in str(exc_info.value).lower()

    def test_non_dict_json_payload_rejected(self) -> None:
        """Ensure JSON array or primitive string payload is rejected."""
        with pytest.raises(ToolValidationError) as exc_info:
            self.pydantic_tool.validate_input('["array", "of", "items"]')
        assert "dictionary" in str(exc_info.value).lower() or "object" in str(exc_info.value).lower()

    def test_raw_schema_enum_validation(self) -> None:
        """Ensure raw schema validates enum choices."""
        valid_input = {"status": "active", "priority": 3}
        validated = self.raw_schema_tool.validate_input(valid_input)
        assert validated["status"] == "active"

        invalid_input = {"status": "unauthorized_status", "priority": 3}
        with pytest.raises(ToolValidationError) as exc_info:
            self.raw_schema_tool.validate_input(invalid_input)
        assert "enum" in str(exc_info.value).lower()

    def test_raw_schema_numeric_bounds_validation(self) -> None:
        """Ensure raw schema enforces minimum and maximum boundaries."""
        # Below minimum (1)
        with pytest.raises(ToolValidationError) as exc_info:
            self.raw_schema_tool.validate_input({"status": "active", "priority": 0})
        assert "minimum" in str(exc_info.value).lower()

        # Above maximum (5)
        with pytest.raises(ToolValidationError) as exc_info:
            self.raw_schema_tool.validate_input({"status": "active", "priority": 10})
        assert "maximum" in str(exc_info.value).lower()

    def test_raw_schema_array_items_validation(self) -> None:
        """Ensure raw schema validates item types within arrays."""
        valid_args = {"status": "active", "priority": 2, "tags": ["prod", "web"]}
        assert self.raw_schema_tool.validate_input(valid_args)["tags"] == ["prod", "web"]

        # Item type mismatch (int inside string array)
        invalid_args = {"status": "active", "priority": 2, "tags": ["prod", 999]}
        with pytest.raises(ToolValidationError) as exc_info:
            self.raw_schema_tool.validate_input(invalid_args)
        assert "string" in str(exc_info.value).lower()


class TestModelFacingSchemaConversion:
    """Verify tool schema export to OpenAI function calling format."""

    def test_to_model_schema_structure(self) -> None:
        """Ensure export conforms to standard function calling envelope."""
        tool = ToolDefinition.from_pydantic(
            name="read_file",
            description="Read file from workspace.",
            parameters_model=FileReadInput,
            strict=True,
        )
        schema = tool.to_model_schema()

        assert schema["type"] == "function"
        fn = schema["function"]
        assert fn["name"] == "read_file"
        assert fn["description"] == "Read file from workspace."
        assert fn["parameters"]["type"] == "object"
        assert "path" in fn["parameters"]["properties"]
        assert "offset" in fn["parameters"]["properties"]
        assert fn["parameters"]["required"] == ["path"]
        assert fn["parameters"]["additionalProperties"] is False

    def test_no_internal_handler_leakage(self) -> None:
        """Ensure internal callable handler is never included in model-facing schema."""
        def dummy_handler(path: str) -> str:
            return "contents"

        tool = ToolDefinition.from_pydantic(
            name="read_file",
            description="Read file from workspace.",
            parameters_model=FileReadInput,
            handler=dummy_handler,
            tags=["fs", "internal"],
        )
        schema = tool.to_model_schema()
        fn = schema["function"]

        assert "handler" not in fn
        assert "tags" not in fn
        assert "risk_category" not in fn
        assert "parameters_model" not in fn

    def test_model_schema_export_mutation_isolated(self) -> None:
        """Ensure modifying exported schema dictionary does not alter registered definition."""
        tool = ToolDefinition.from_pydantic(
            name="read_file",
            description="Original description",
            parameters_model=FileReadInput,
        )
        schema = tool.to_model_schema()
        # Malicious caller attempts to mutate returned dict
        schema["function"]["name"] = "mutated_name"
        schema["function"]["parameters"]["properties"]["injected"] = {"type": "string"}

        # Original definition must be completely unchanged
        fresh_schema = tool.to_model_schema()
        assert fresh_schema["function"]["name"] == "read_file"
        assert "injected" not in fresh_schema["function"]["parameters"]["properties"]

    def test_tool_definition_is_immutable(self) -> None:
        """Ensure ToolDefinition instances cannot be modified in-place."""
        tool = ToolDefinition.from_pydantic(
            name="read_file",
            description="Immutable tool",
            parameters_model=FileReadInput,
        )
        with pytest.raises(ValidationError):
            # Frozen model rejects attribute mutation
            tool.name = "new_name"


class TestToolResultEnvelope:
    """Verify tool execution result envelope construction and serialization."""

    def test_success_envelope_construction(self) -> None:
        """Ensure success envelope encapsulates output, call_id, and metadata."""
        envelope = ToolResultEnvelope.create_success(
            tool_name="read_file",
            output={"lines": 42, "content": "hello world"},
            call_id="call_abc_123",
            metadata={"execution_time_ms": 12.5},
        )
        assert envelope.success is True
        assert envelope.tool_name == "read_file"
        assert envelope.call_id == "call_abc_123"
        assert envelope.output == {"lines": 42, "content": "hello world"}
        assert envelope.error is None
        assert envelope.error_category is None
        assert envelope.metadata["execution_time_ms"] == 12.5
        assert envelope.timestamp is not None

    def test_error_envelope_construction(self) -> None:
        """Ensure error envelope records failure description and classification category."""
        envelope = ToolResultEnvelope.create_error(
            tool_name="read_file",
            error="File not found: missing.txt",
            error_category="not_found",
            call_id="call_xyz_789",
        )
        assert envelope.success is False
        assert envelope.tool_name == "read_file"
        assert envelope.output is None
        assert envelope.error == "File not found: missing.txt"
        assert envelope.error_category == "not_found"
        assert envelope.call_id == "call_xyz_789"
