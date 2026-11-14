"""Comprehensive tests for deterministic tool registry, discovery, scoping, and isolation."""

import concurrent.futures
import json
import pytest
from pydantic import BaseModel, Field

from core.tool_contracts import (
    DuplicateToolError,
    ToolDefinition,
    ToolNotFoundError,
    ToolRegistrationError,
    ToolRiskCategory,
    ToolValidationError,
)
from core.tool_registry import (
    ToolRegistry,
    get_global_registry,
    reset_global_registry,
)


# Sample parameter models for testing registry operations
class FileSearchInput(BaseModel):
    query: str = Field(..., description="Query string")
    max_results: int = Field(default=20, ge=1, le=100)


class FileDeleteInput(BaseModel):
    path: str = Field(..., description="Path to delete")
    confirm: bool = Field(default=False)


class HealthCheckInput(BaseModel):
    verbose: bool = Field(default=False)


def create_sample_tool(
    name: str,
    risk: ToolRiskCategory = ToolRiskCategory.READ_ONLY,
    description: str = "Test tool description",
) -> ToolDefinition:
    """Helper creating a test tool definition with standard schemas."""
    return ToolDefinition.from_pydantic(
        name=name,
        description=description,
        parameters_model=FileSearchInput,
        risk_category=risk,
    )


class TestToolRegistryBasicOperations:
    """Verify tool registration, lookup, duplicate rejection, and unregistration."""

    def setup_method(self) -> None:
        """Initialize a fresh isolated registry for each test."""
        self.registry = ToolRegistry()

    def test_successful_registration_and_retrieval(self) -> None:
        """Ensure valid tools can be registered and retrieved by name."""
        tool = create_sample_tool("search_files")
        self.registry.register(tool)

        assert len(self.registry) == 1
        assert self.registry.has("search_files") is True
        assert "search_files" in self.registry

        retrieved = self.registry.get("search_files")
        assert retrieved.name == "search_files"
        assert retrieved.risk_category == ToolRiskCategory.READ_ONLY

    def test_duplicate_registration_rejected(self) -> None:
        """Ensure registering an existing tool identifier raises DuplicateToolError."""
        tool1 = create_sample_tool("read_file")
        tool2 = create_sample_tool("read_file", description="Conflicting tool")

        self.registry.register(tool1)
        with pytest.raises(DuplicateToolError) as exc_info:
            self.registry.register(tool2)

        assert exc_info.value.tool_name == "read_file"
        assert len(self.registry) == 1
        # Original tool remains untouched
        assert self.registry.get("read_file").description == "Test tool description"

    def test_registering_invalid_object_type_rejected(self) -> None:
        """Ensure objects not inheriting from ToolDefinition raise ToolRegistrationError."""
        with pytest.raises(ToolRegistrationError):
            self.registry.register({"name": "dict_not_tool_def"})  # type: ignore

    def test_get_nonexistent_tool_raises_not_found_error(self) -> None:
        """Ensure querying an unregistered name raises ToolNotFoundError."""
        with pytest.raises(ToolNotFoundError) as exc_info:
            self.registry.get("nonexistent_tool")
        assert exc_info.value.tool_name == "nonexistent_tool"

    def test_unregistration(self) -> None:
        """Ensure registered tools can be cleanly removed from the registry."""
        tool = create_sample_tool("temp_tool")
        self.registry.register(tool)
        assert self.registry.has("temp_tool") is True

        removed = self.registry.unregister("temp_tool")
        assert removed is True
        assert self.registry.has("temp_tool") is False
        assert len(self.registry) == 0

        # Unregistering non-existent tool returns False without exception
        assert self.registry.unregister("never_existed") is False

    def test_clear_registry(self) -> None:
        """Ensure clearing registry empties all stored definitions."""
        self.registry.register(create_sample_tool("tool_a"))
        self.registry.register(create_sample_tool("tool_b"))
        assert len(self.registry) == 2

        self.registry.clear()
        assert len(self.registry) == 0
        assert self.registry.list_names() == []


class TestToolRegistryListingAndDeterministicOrdering:
    """Verify tool listing and deterministic alphabetical ordering."""

    def setup_method(self) -> None:
        """Initialize registry and register unordered tools."""
        self.registry = ToolRegistry()
        # Register in deliberately reverse-alphabetical order
        self.registry.register(create_sample_tool("write_file", ToolRiskCategory.WORKSPACE_WRITE))
        self.registry.register(create_sample_tool("apply_patch", ToolRiskCategory.WORKSPACE_WRITE))
        self.registry.register(create_sample_tool("read_file", ToolRiskCategory.READ_ONLY))
        self.registry.register(create_sample_tool("delete_file", ToolRiskCategory.DESTRUCTIVE))
        self.registry.register(create_sample_tool("check_health", ToolRiskCategory.SYSTEM_PROBE))

    def test_list_tools_is_deterministically_sorted(self) -> None:
        """Ensure list_tools() returns definitions sorted alphabetically by tool name."""
        tools = self.registry.list_tools()
        names = [t.name for t in tools]
        expected_sorted = ["apply_patch", "check_health", "delete_file", "read_file", "write_file"]
        assert names == expected_sorted

    def test_list_names_is_deterministically_sorted(self) -> None:
        """Ensure list_names() returns sorted identifier strings."""
        assert self.registry.list_names() == [
            "apply_patch",
            "check_health",
            "delete_file",
            "read_file",
            "write_file",
        ]

    def test_filtering_by_risk_category(self) -> None:
        """Ensure filtering by ToolRiskCategory returns only matching tools in sorted order."""
        write_tools = self.registry.list_tools(filter_category=ToolRiskCategory.WORKSPACE_WRITE)
        assert [t.name for t in write_tools] == ["apply_patch", "write_file"]

        destructive = self.registry.list_tools(filter_category=ToolRiskCategory.DESTRUCTIVE)
        assert [t.name for t in destructive] == ["delete_file"]

        read_only = self.registry.list_tools(filter_category=ToolRiskCategory.READ_ONLY)
        assert [t.name for t in read_only] == ["read_file"]


class TestModelSchemaExport:
    """Verify conversion of registered tools to model-facing OpenAI function definitions."""

    def setup_method(self) -> None:
        """Initialize registry with diverse tools."""
        self.registry = ToolRegistry()
        self.registry.register(create_sample_tool("search_code", ToolRiskCategory.READ_ONLY))
        self.registry.register(create_sample_tool("execute_bash", ToolRiskCategory.COMMAND_EXECUTION))
        self.registry.register(create_sample_tool("inspect_ast", ToolRiskCategory.READ_ONLY))

    def test_export_all_model_schemas(self) -> None:
        """Ensure all registered tools export valid OpenAI function schemas in sorted order."""
        schemas = self.registry.export_model_schemas()
        assert len(schemas) == 3
        # Sorted order: execute_bash, inspect_ast, search_code
        assert [s["function"]["name"] for s in schemas] == [
            "execute_bash",
            "inspect_ast",
            "search_code",
        ]
        for s in schemas:
            assert s["type"] == "function"
            assert "name" in s["function"]
            assert "description" in s["function"]
            assert "parameters" in s["function"]

    def test_export_with_risk_category_filter(self) -> None:
        """Ensure export can be scoped to specific risk categories (e.g. read_only)."""
        read_schemas = self.registry.export_model_schemas(
            filter_category=ToolRiskCategory.READ_ONLY
        )
        assert len(read_schemas) == 2
        assert [s["function"]["name"] for s in read_schemas] == ["inspect_ast", "search_code"]

    def test_export_with_allowed_names_scoping(self) -> None:
        """Ensure export can be scoped to a task-specific allowlist of tool names."""
        scoped_schemas = self.registry.export_model_schemas(
            allowed_names={"search_code", "execute_bash"}
        )
        assert len(scoped_schemas) == 2
        assert [s["function"]["name"] for s in scoped_schemas] == ["execute_bash", "search_code"]

    def test_exported_schemas_are_isolated_copies(self) -> None:
        """Ensure modifying exported schemas does not mutate registry state."""
        schemas = self.registry.export_model_schemas()
        schemas[0]["function"]["name"] = "tampered_name"
        schemas[0]["function"]["parameters"]["injected"] = "malicious"

        # Fresh export must remain unaltered
        fresh = self.registry.export_model_schemas()
        assert fresh[0]["function"]["name"] != "tampered_name"
        assert "injected" not in fresh[0]["function"]["parameters"]


class TestValidateCallDispatch:
    """Verify argument validation dispatch through the registry."""

    def setup_method(self) -> None:
        """Initialize registry with test tool."""
        self.registry = ToolRegistry()
        self.registry.register(
            ToolDefinition.from_pydantic(
                name="search_files",
                description="Search files in project",
                parameters_model=FileSearchInput,
                strict=True,
            )
        )

    def test_validate_call_success_with_dict(self) -> None:
        """Ensure validate_call succeeds with valid dict arguments."""
        result = self.registry.validate_call("search_files", {"query": "auth", "max_results": 10})
        assert result["query"] == "auth"
        assert result["max_results"] == 10

    def test_validate_call_success_with_json_string(self) -> None:
        """Ensure validate_call parses and validates JSON string arguments."""
        json_str = json.dumps({"query": "database"})
        result = self.registry.validate_call("search_files", json_str)
        assert result["query"] == "database"
        assert result["max_results"] == 20  # default applied

    def test_validate_call_fails_on_missing_required_arguments(self) -> None:
        """Ensure validate_call raises ToolValidationError when required fields are missing."""
        with pytest.raises(ToolValidationError):
            self.registry.validate_call("search_files", {"max_results": 10})

    def test_validate_call_fails_on_unknown_tool(self) -> None:
        """Ensure validate_call raises ToolNotFoundError when tool does not exist."""
        with pytest.raises(ToolNotFoundError):
            self.registry.validate_call("unregistered_tool", {"query": "test"})


class TestRegistryIsolationAndConcurrency:
    """Verify instance isolation and thread-safe operations."""

    def test_registry_instances_are_isolated(self) -> None:
        """Ensure different ToolRegistry instances maintain independent namespaces."""
        reg1 = ToolRegistry()
        reg2 = ToolRegistry()

        reg1.register(create_sample_tool("tool_in_reg1"))
        assert reg1.has("tool_in_reg1") is True
        assert reg2.has("tool_in_reg1") is False
        assert len(reg2) == 0

    def test_global_registry_helpers(self) -> None:
        """Ensure global singleton access and reset behave correctly."""
        reset_global_registry()
        g1 = get_global_registry()
        g2 = get_global_registry()
        assert g1 is g2

        g1.register(create_sample_tool("global_tool"))
        assert g2.has("global_tool") is True

        reset_global_registry()
        g3 = get_global_registry()
        assert g3 is not g1
        assert len(g3) == 0

    def test_concurrent_registrations_and_lookups(self) -> None:
        """Verify thread-safety when registering and reading tools concurrently."""
        registry = ToolRegistry()
        tool_count = 100

        def register_worker(index: int) -> None:
            tool = create_sample_tool(f"worker_tool_{index:03d}")
            registry.register(tool)

        def read_worker(index: int) -> int:
            return len(registry)

        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
            # Concurrently register 100 distinct tools
            reg_futures = [executor.submit(register_worker, i) for i in range(tool_count)]
            read_futures = [executor.submit(read_worker, i) for i in range(tool_count)]
            concurrent.futures.wait(reg_futures + read_futures)

        assert len(registry) == tool_count
        assert len(registry.list_names()) == tool_count
        assert registry.list_names() == sorted(registry.list_names())
