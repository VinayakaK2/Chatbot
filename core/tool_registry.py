"""Thread-safe, deterministic tool registry for registration, discovery, and schema export."""

from copy import deepcopy
import logging
from threading import RLock
from typing import Any, Dict, List, Optional, Set, Union

from core.tool_contracts import (
    DuplicateToolError,
    ToolDefinition,
    ToolNotFoundError,
    ToolRegistrationError,
    ToolRiskCategory,
    ToolValidationError,
)

logger = logging.getLogger("agent_runtime.core.tool_registry")


class ToolRegistry:
    """Thread-safe, deterministic registry managing tool contracts, discovery, and validation."""

    def __init__(self) -> None:
        """Initialize a fresh, isolated tool registry instance with synchronization locks."""
        self._tools: Dict[str, ToolDefinition] = {}
        self._lock: RLock = RLock()

    def register(self, tool: ToolDefinition) -> None:
        """Register a validated tool definition, enforcing unique naming and type safety.

        Raises DuplicateToolError if a tool with the same identifier is already registered.
        Raises ToolRegistrationError if the supplied object is not a ToolDefinition instance.
        """
        if not isinstance(tool, ToolDefinition):
            raise ToolRegistrationError(
                f"Expected instance of ToolDefinition, got {type(tool).__name__}."
            )

        with self._lock:
            # Enforce unique tool names
            if tool.name in self._tools:
                logger.warning("Duplicate registration rejected for tool '%s'", tool.name)
                raise DuplicateToolError(tool.name)

            self._tools[tool.name] = tool
            logger.info("Registered tool '%s' [risk=%s]", tool.name, tool.risk_category.value)

    def unregister(self, name: str) -> bool:
        """Remove a tool from the registry by its identifier.

        Returns True if the tool was present and removed, False otherwise.
        """
        if not isinstance(name, str):
            return False

        with self._lock:
            if name in self._tools:
                del self._tools[name]
                logger.info("Unregistered tool '%s'", name)
                return True
            return False

    def get(self, name: str) -> ToolDefinition:
        """Retrieve a registered tool definition by its unique identifier.

        Raises ToolNotFoundError if the tool identifier is not registered.
        """
        if not isinstance(name, str):
            raise ToolNotFoundError(str(name))

        with self._lock:
            tool = self._tools.get(name)
            if tool is None:
                raise ToolNotFoundError(name)
            return tool

    def has(self, name: str) -> bool:
        """Check whether a tool identifier is registered in this registry instance."""
        if not isinstance(name, str):
            return False

        with self._lock:
            return name in self._tools

    def list_tools(
        self,
        filter_category: Optional[ToolRiskCategory] = None,
    ) -> List[ToolDefinition]:
        """Return a deterministically sorted list of all registered tool definitions.

        Tools are sorted alphabetically by their unique name for stable model context assembly.
        Optionally filters results by ToolRiskCategory.
        """
        with self._lock:
            tools = list(self._tools.values())
            if filter_category is not None:
                tools = [t for t in tools if t.risk_category == filter_category]
            # Deterministic ordering by tool name
            return sorted(tools, key=lambda t: t.name)

    def list_names(
        self,
        filter_category: Optional[ToolRiskCategory] = None,
    ) -> List[str]:
        """Return a deterministically sorted list of registered tool names."""
        tools = self.list_tools(filter_category=filter_category)
        return [t.name for t in tools]

    def export_model_schemas(
        self,
        filter_category: Optional[ToolRiskCategory] = None,
        allowed_names: Optional[Set[str]] = None,
    ) -> List[Dict[str, Any]]:
        """Export OpenAI-compatible function calling schemas for registered tools.

        Applies deterministic sorting, optional category filtering, and allowlist scoping.
        Guarantees zero internal handler leakage and produces isolated deep copies.
        """
        tools = self.list_tools(filter_category=filter_category)
        if allowed_names is not None:
            tools = [t for t in tools if t.name in allowed_names]

        return [t.to_model_schema() for t in tools]

    def validate_call(
        self,
        tool_name: str,
        arguments: Union[Dict[str, Any], str, None],
    ) -> Dict[str, Any]:
        """Validate proposed arguments against the declared schema of a registered tool.

        Raises ToolNotFoundError if tool_name is not registered.
        Raises ToolValidationError if arguments are malformed or fail schema rules.
        """
        tool = self.get(tool_name)
        return tool.validate_input(arguments)

    def clear(self) -> None:
        """Remove all registered tools from this registry instance."""
        with self._lock:
            self._tools.clear()
            logger.debug("Cleared tool registry.")

    def __len__(self) -> int:
        """Return the count of registered tools."""
        with self._lock:
            return len(self._tools)

    def __contains__(self, name: str) -> bool:
        """Support standard 'in' membership testing."""
        return self.has(name)

    def __bool__(self) -> bool:
        """A ToolRegistry instance is always considered truthy regardless of registered tool count."""
        return True


# ==============================================================================
# Global Registry Singleton & Factory
# ==============================================================================

_global_registry: Optional[ToolRegistry] = None


def get_global_registry() -> ToolRegistry:
    """Retrieve or initialize the global singleton tool registry."""
    global _global_registry
    if _global_registry is None:
        _global_registry = ToolRegistry()
    return _global_registry


def reset_global_registry() -> None:
    """Reset the global tool registry (primarily used for test isolation)."""
    global _global_registry
    if _global_registry is not None:
        _global_registry.clear()
    _global_registry = None
