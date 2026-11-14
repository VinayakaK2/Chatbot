"""Adversarial and security tests validating robustness against bypasses and attacks."""

import json
import pytest
from pydantic import BaseModel, Field, ValidationError

from core.tool_contracts import (
    DuplicateToolError,
    InvalidToolNameError,
    InvalidToolSchemaError,
    ToolDefinition,
    ToolNotFoundError,
    ToolRegistrationError,
    ToolRiskCategory,
    ToolValidationError,
)
from core.tool_registry import ToolRegistry


class NestedAddressModel(BaseModel):
    """Nested structure for testing deep schema validation."""

    city: str = Field(..., min_length=2)
    zip_code: int = Field(..., ge=10000, le=99999)


class ProfileInputModel(BaseModel):
    """Complex input model with nested objects, arrays, and strict validation."""

    username: str = Field(..., min_length=3)
    age: int = Field(..., ge=0, le=150)
    address: NestedAddressModel
    roles: list[str] = Field(default_factory=list)


class TestAdversarialSecurityScenarios:
    """Rigorous adversarial testing of tool contracts, schema boundaries, and registry."""

    def setup_method(self) -> None:
        """Initialize fresh registry for each attack scenario."""
        self.registry = ToolRegistry()

    # Scenario 1: Register two tools with the same name
    def test_scenario_01_duplicate_tool_name_attack(self) -> None:
        """Attack Scenario 1: Attempt to register multiple tools sharing identical identifier."""
        tool1 = ToolDefinition.from_pydantic(
            name="system_diagnostic",
            description="Benign diagnostic tool",
            parameters_model=ProfileInputModel,
            risk_category=ToolRiskCategory.READ_ONLY,
        )
        tool2 = ToolDefinition.from_pydantic(
            name="system_diagnostic",
            description="Malicious hijacked tool",
            parameters_model=ProfileInputModel,
            risk_category=ToolRiskCategory.DESTRUCTIVE,
        )

        self.registry.register(tool1)
        with pytest.raises(DuplicateToolError) as exc_info:
            self.registry.register(tool2)

        assert exc_info.value.tool_name == "system_diagnostic"
        # Ensure benign tool was not overwritten
        current_tool = self.registry.get("system_diagnostic")
        assert current_tool.risk_category == ToolRiskCategory.READ_ONLY
        assert current_tool.description == "Benign diagnostic tool"

    # Scenario 2: Register malformed schemas
    @pytest.mark.parametrize(
        "malformed_schema",
        [
            {"type": "string"},
            {"type": "number"},
            {"type": "array"},
            {"properties": {}},  # Missing type="object"
            {"type": "object", "properties": "not_a_dict"},
            {"type": "object", "required": "not_a_list"},
            {"type": "object", "required": [1, 2, 3]},  # List of ints instead of strings
        ],
    )
    def test_scenario_02_malformed_schemas_rejected(self, malformed_schema: dict) -> None:
        """Attack Scenario 2: Attempt to register tools with corrupt or non-object schemas."""
        with pytest.raises(InvalidToolSchemaError):
            ToolDefinition.from_schema(
                name="malformed_tool",
                description="Tool with invalid schema",
                parameters_schema=malformed_schema,
            )

    def test_scenario_02_non_base_model_class_rejected(self) -> None:
        """Attack Scenario 2b: Attempt to pass an arbitrary Python class instead of BaseModel."""
        class ArbitraryClass:
            pass

        with pytest.raises(InvalidToolSchemaError):
            ToolDefinition.from_pydantic(
                name="invalid_class_tool",
                description="Invalid class parameter",
                parameters_model=ArbitraryClass,  # type: ignore
            )

    # Scenario 3: Register tools with reserved or dangerous names
    @pytest.mark.parametrize(
        "dangerous_name",
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
            "../../etc/passwd",
            "tool;rm -rf /",
            "tool\x00nullbyte",
            "tool\nnewline",
            "tool\rreturn",
            "tool$env",
            "tool|pipe",
            "tool&amp",
            "tool>out",
            "a" * 65,  # Exceeds max length
            "   ",  # Blank
        ],
    )
    def test_scenario_03_dangerous_and_reserved_names_rejected(self, dangerous_name: str) -> None:
        """Attack Scenario 3: Attempt to register names with control chars, injection, or reserved tokens."""
        with pytest.raises(InvalidToolNameError):
            ToolDefinition.from_pydantic(
                name=dangerous_name,
                description="Tool with dangerous name",
                parameters_model=ProfileInputModel,
            )

    # Scenario 4: Look up nonexistent tools
    def test_scenario_04_lookup_nonexistent_tool(self) -> None:
        """Attack Scenario 4: Attempt to query tools that do not exist."""
        with pytest.raises(ToolNotFoundError) as exc_info:
            self.registry.get("ghost_tool")
        assert exc_info.value.tool_name == "ghost_tool"
        assert "ghost_tool" not in self.registry

    # Scenario 5: Supply unexpected arguments (extra fields when strict)
    def test_scenario_05_unexpected_arguments_rejected(self) -> None:
        """Attack Scenario 5: Supply unexpected parameters in strict mode."""
        tool = ToolDefinition.from_pydantic(
            name="user_profile",
            description="Manage user profile",
            parameters_model=ProfileInputModel,
            strict=True,
        )
        self.registry.register(tool)

        attack_payload = {
            "username": "alice",
            "age": 30,
            "address": {"city": "Seattle", "zip_code": 98101},
            "unauthorized_role_elevation": "superuser",  # Injected field
        }

        with pytest.raises(ToolValidationError) as exc_info:
            self.registry.validate_call("user_profile", attack_payload)
        assert "extra" in str(exc_info.value).lower()

    # Scenario 6: Supply nested objects with invalid fields
    def test_scenario_06_nested_invalid_fields_rejected(self) -> None:
        """Attack Scenario 6: Inject invalid types and missing fields into nested child objects."""
        tool = ToolDefinition.from_pydantic(
            name="user_profile",
            description="Manage user profile",
            parameters_model=ProfileInputModel,
            strict=True,
        )
        self.registry.register(tool)

        # Zip code below minimum (ge=10000)
        invalid_nested_zip = {
            "username": "bob",
            "age": 25,
            "address": {"city": "Portland", "zip_code": 999},
        }
        with pytest.raises(ToolValidationError):
            self.registry.validate_call("user_profile", invalid_nested_zip)

        # City missing in nested address
        missing_nested_city = {
            "username": "bob",
            "age": 25,
            "address": {"zip_code": 97201},
        }
        with pytest.raises(ToolValidationError):
            self.registry.validate_call("user_profile", missing_nested_city)

    # Scenario 7: Attempt type confusion and unexpected coercion
    def test_scenario_07_type_confusion_rejected(self) -> None:
        """Attack Scenario 7: Exploit type confusion by passing strings for ints or bools for ints."""
        tool = ToolDefinition.from_pydantic(
            name="user_profile",
            description="Manage user profile",
            parameters_model=ProfileInputModel,
            strict=True,
        )
        self.registry.register(tool)

        # String passed where int is required ('30' instead of 30)
        with pytest.raises(ToolValidationError):
            self.registry.validate_call(
                "user_profile",
                {
                    "username": "charlie",
                    "age": "30",
                    "address": {"city": "Austin", "zip_code": 78701},
                },
            )

        # Boolean True passed where int is required
        with pytest.raises(ToolValidationError):
            self.registry.validate_call(
                "user_profile",
                {
                    "username": "charlie",
                    "age": True,
                    "address": {"city": "Austin", "zip_code": 78701},
                },
            )

        # Array passed where object expected for address
        with pytest.raises(ToolValidationError):
            self.registry.validate_call(
                "user_profile",
                {
                    "username": "charlie",
                    "age": 30,
                    "address": ["Austin", 78701],
                },
            )

    # Scenario 8: Attempt to mutate a registered tool after registration
    def test_scenario_08_immutable_registered_definition(self) -> None:
        """Attack Scenario 8: Attempt to modify a registered tool's properties after registration."""
        tool = ToolDefinition.from_pydantic(
            name="immutable_tool",
            description="Cannot be mutated",
            parameters_model=ProfileInputModel,
            risk_category=ToolRiskCategory.READ_ONLY,
        )
        self.registry.register(tool)

        retrieved = self.registry.get("immutable_tool")
        with pytest.raises(ValidationError):
            # Attempt in-place attribute tampering
            retrieved.risk_category = ToolRiskCategory.DESTRUCTIVE  # type: ignore

        with pytest.raises(ValidationError):
            retrieved.name = "hijacked_tool"  # type: ignore

    # Scenario 9: Attempt to bypass validation through alternate code paths
    def test_scenario_09_alternate_validation_bypass_attempts(self) -> None:
        """Attack Scenario 9: Test alternative input paths (raw primitives, non-JSON strings, arrays)."""
        tool = ToolDefinition.from_pydantic(
            name="user_profile",
            description="Manage user profile",
            parameters_model=ProfileInputModel,
            strict=True,
        )
        self.registry.register(tool)

        # Primitive number as argument
        with pytest.raises(ToolValidationError):
            self.registry.validate_call("user_profile", 12345)  # type: ignore

        # List of items as argument
        with pytest.raises(ToolValidationError):
            self.registry.validate_call("user_profile", ["not", "dict"])  # type: ignore

        # Corrupt JSON string
        with pytest.raises(ToolValidationError):
            self.registry.validate_call("user_profile", "{\"username\": 'unterminated'")

        # String decoding to JSON primitive instead of object
        with pytest.raises(ToolValidationError):
            self.registry.validate_call("user_profile", '"a string, not an object"')

    # Scenario 10: Attempt to access internal handlers through model-facing definitions
    def test_scenario_10_no_handler_leakage_to_model_schema(self) -> None:
        """Attack Scenario 10: Verify exported model schemas contain zero execution references."""
        def sensitive_internal_handler(arg: str) -> str:
            return f"executed: {arg}"

        tool = ToolDefinition.from_pydantic(
            name="secure_tool",
            description="Secured internal tool",
            parameters_model=ProfileInputModel,
            handler=sensitive_internal_handler,
            tags=["confidential_tag"],
            risk_category=ToolRiskCategory.COMMAND_EXECUTION,
        )
        self.registry.register(tool)

        exported = self.registry.export_model_schemas()[0]
        exported_json_str = json.dumps(exported)

        # Assert no handler pointer or private field is present anywhere in exported JSON
        assert "sensitive_internal_handler" not in exported_json_str
        assert "handler" not in exported["function"]
        assert "confidential_tag" not in exported_json_str
        assert "COMMAND_EXECUTION" not in exported_json_str

    # Scenario 11: Attempt to register arbitrary executable code through untrusted input
    def test_scenario_11_code_injection_in_schema_fields_treated_as_literal_data(self) -> None:
        """Attack Scenario 11: Inject Python code strings into description and schemas."""
        malicious_code_payload = "__import__('os').system('echo hacked')"

        tool = ToolDefinition.from_pydantic(
            name="code_injection_test",
            description=f"Tool with code string: {malicious_code_payload}",
            parameters_model=ProfileInputModel,
        )
        self.registry.register(tool)

        # Tool definition stores string literally without evaluating
        assert malicious_code_payload in tool.description
        assert self.registry.get("code_injection_test").description == tool.description

    # Scenario 12: Attempt to make registry behavior depend on registration order unexpectedly
    def test_scenario_12_registration_order_independence(self) -> None:
        """Attack Scenario 12: Verify that registration order does not alter lookup or export order."""
        t1 = ToolDefinition.from_pydantic(name="alpha", description="A", parameters_model=ProfileInputModel)
        t2 = ToolDefinition.from_pydantic(name="beta", description="B", parameters_model=ProfileInputModel)
        t3 = ToolDefinition.from_pydantic(name="gamma", description="G", parameters_model=ProfileInputModel)

        # Order 1: gamma, alpha, beta
        reg1 = ToolRegistry()
        reg1.register(t3)
        reg1.register(t1)
        reg1.register(t2)

        # Order 2: beta, gamma, alpha
        reg2 = ToolRegistry()
        reg2.register(t2)
        reg2.register(t3)
        reg2.register(t1)

        # Both registries must produce identical deterministic sorted lists
        assert reg1.list_names() == ["alpha", "beta", "gamma"]
        assert reg2.list_names() == ["alpha", "beta", "gamma"]
        assert reg1.export_model_schemas() == reg2.export_model_schemas()
        # Direct lookup retrieves correct definitions irrespective of order
        assert reg1.get("alpha").name == reg2.get("alpha").name
        assert reg1.get("beta").name == reg2.get("beta").name
        assert reg1.get("gamma").name == reg2.get("gamma").name
