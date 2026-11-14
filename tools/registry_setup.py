"""Setup and registration utilities for workspace and filesystem tools."""

import logging
from typing import List, Optional

from core.tool_contracts import ToolDefinition
from core.tool_registry import ToolRegistry, get_global_registry
from core.workspace import WorkspaceContext
from tools.filesystem import create_filesystem_tools
from tools.search import create_search_tools

logger = logging.getLogger("agent_runtime.tools.registry_setup")


def get_all_workspace_tools(workspace: WorkspaceContext) -> List[ToolDefinition]:
    """Assemble and return all 8 Phase 4 workspace exploration and modification tool definitions."""
    tools: List[ToolDefinition] = []
    tools.extend(create_filesystem_tools(workspace))
    tools.extend(create_search_tools(workspace))
    return tools


def register_workspace_tools(
    registry: Optional[ToolRegistry] = None,
    workspace: Optional[WorkspaceContext] = None,
) -> List[ToolDefinition]:
    """Register all Phase 4 workspace tools into the target tool registry.

    If registry is omitted, registers into the global registry.
    If workspace is omitted, raises ValueError requiring an explicit WorkspaceContext.
    """
    if workspace is None:
        raise ValueError("register_workspace_tools requires an explicit WorkspaceContext instance.")

    target_registry = registry if registry is not None else get_global_registry()
    tools = get_all_workspace_tools(workspace)

    for tool in tools:
        try:
            target_registry.register(tool)
        except Exception as exc:
            logger.debug("Tool '%s' already registered or registration skipped: %s", tool.name, str(exc))

    logger.info("Successfully registered %d workspace tools into registry", len(tools))
    return tools
