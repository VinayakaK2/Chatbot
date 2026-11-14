"""Search tools for file discovery (globbing) and text pattern searching within workspace."""

import fnmatch
import os
from pathlib import Path
import re
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, ConfigDict, Field

from core.tool_contracts import (
    ToolDefinition,
    ToolRiskCategory,
)
from core.workspace import (
    InvalidPathError,
    WorkspaceContext,
    WorkspaceIOError,
)
from tools.filesystem import DEFAULT_IGNORE_DIRS


# ==============================================================================
# Input Schemas
# ==============================================================================


class FindFilesInput(BaseModel):
    """Input parameters for finding files matching a glob pattern in the workspace."""

    model_config = ConfigDict(extra="forbid")

    pattern: str = Field(
        default="*",
        description="Glob pattern to match file names against (e.g. '*.py', '**/*.ts', 'test_*.py')",
    )
    directory: str = Field(
        default=".",
        description="Workspace-relative directory to search within (default: root '.')",
    )
    max_results: Optional[int] = Field(
        default=None,
        description="Optional maximum number of matching file paths to return",
    )


class SearchFilesInput(BaseModel):
    """Input parameters for searching text or regex patterns across files in the workspace."""

    model_config = ConfigDict(extra="forbid")

    query: str = Field(
        ...,
        description="Text substring or regular expression to search for inside files",
    )
    directory: str = Field(
        default=".",
        description="Workspace-relative directory to search within (default: root '.')",
    )
    file_pattern: Optional[str] = Field(
        default=None,
        description="Optional glob pattern to filter which files are searched (e.g. '*.py')",
    )
    case_sensitive: bool = Field(
        default=False,
        description="Whether the search query is case-sensitive (default: False)",
    )
    is_regex: bool = Field(
        default=False,
        description="Whether to evaluate the query as a regular expression (default: False)",
    )
    max_results: Optional[int] = Field(
        default=None,
        description="Optional maximum number of matching lines to return",
    )


# ==============================================================================
# Search Tool Definitions Factory
# ==============================================================================


def create_search_tools(workspace: WorkspaceContext) -> List[ToolDefinition]:
    """Create and return search tool definitions bound to the specified WorkspaceContext."""

    def find_files(
        pattern: str = "*",
        directory: str = ".",
        max_results: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Find files matching a glob pattern within the workspace."""
        target_dir = workspace.resolve_path(directory, must_exist=True)
        if not target_dir.is_dir():
            raise InvalidPathError(f"Search target '{directory}' is not a directory.")

        limit = max_results or workspace.max_search_results
        matches: List[str] = []
        truncated = False

        for root, dirs, files in os.walk(target_dir):
            dirs[:] = [d for d in dirs if d not in DEFAULT_IGNORE_DIRS]

            for file_name in sorted(files):
                if fnmatch.fnmatch(file_name, pattern) or fnmatch.fnmatch(
                    os.path.join(root, file_name).replace("\\", "/"), pattern
                ):
                    full_path = Path(root) / file_name
                    matches.append(workspace.get_relative_path(full_path))
                    if len(matches) >= limit:
                        truncated = True
                        break
            if truncated:
                break

        return {
            "directory": workspace.get_relative_path(target_dir),
            "pattern": pattern,
            "matches": matches,
            "total_matches": len(matches),
            "truncated": truncated,
        }

    def search_files(
        query: str,
        directory: str = ".",
        file_pattern: Optional[str] = None,
        case_sensitive: bool = False,
        is_regex: bool = False,
        max_results: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Search text or regex pattern across workspace files."""
        if not query:
            raise InvalidPathError("Search query cannot be empty.")

        target_dir = workspace.resolve_path(directory, must_exist=True)
        if not target_dir.is_dir():
            raise InvalidPathError(f"Search directory '{directory}' is not a directory.")

        limit = max_results or workspace.max_search_results

        # Compile search regex
        flags = 0 if case_sensitive else re.IGNORECASE
        if is_regex:
            try:
                regex = re.compile(query, flags)
            except re.error as exc:
                raise InvalidPathError(f"Invalid regular expression '{query}': {str(exc)}")
        else:
            regex = re.compile(re.escape(query), flags)

        results: List[Dict[str, Any]] = []
        truncated = False

        for root, dirs, files in os.walk(target_dir):
            dirs[:] = [d for d in dirs if d not in DEFAULT_IGNORE_DIRS]

            for file_name in sorted(files):
                if file_pattern and not fnmatch.fnmatch(file_name, file_pattern):
                    continue

                full_path = Path(root) / file_name

                # Skip binary and oversized files
                try:
                    if workspace.is_binary_file(full_path):
                        continue
                    if full_path.stat().st_size > workspace.max_file_size_bytes:
                        continue
                except OSError:
                    continue

                # Read and scan lines
                try:
                    text, _, _ = workspace.read_text_bounded(full_path)
                except Exception:
                    continue

                rel_path = workspace.get_relative_path(full_path)
                for line_num, line in enumerate(text.splitlines(), start=1):
                    if regex.search(line):
                        results.append(
                            {
                                "path": rel_path,
                                "line_number": line_num,
                                "line_text": line[:200],  # Bound individual line length
                            }
                        )
                        if len(results) >= limit:
                            truncated = True
                            break
                if truncated:
                    break
            if truncated:
                break

        return {
            "directory": workspace.get_relative_path(target_dir),
            "query": query,
            "matches": results,
            "total_matches": len(results),
            "truncated": truncated,
        }

    return [
        ToolDefinition.from_pydantic(
            name="find_files",
            description="Find files in the workspace matching a glob pattern (e.g. '*.py', 'test_*.ts'), skipping common dependency caches.",
            parameters_model=FindFilesInput,
            risk_category=ToolRiskCategory.READ_ONLY,
            tags=["search", "discovery", "read_only"],
            handler=find_files,
        ),
        ToolDefinition.from_pydantic(
            name="search_files",
            description="Search text or regular expression patterns across files in the workspace, returning matching paths, line numbers, and line text.",
            parameters_model=SearchFilesInput,
            risk_category=ToolRiskCategory.READ_ONLY,
            tags=["search", "grep", "read_only"],
            handler=search_files,
        ),
    ]
