"""Filesystem exploration and modification tools for workspace inspection and code editing."""

from datetime import datetime, timezone
import os
from pathlib import Path
import stat
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, ConfigDict, Field

from core.tool_contracts import (
    ToolDefinition,
    ToolRiskCategory,
)
from core.workspace import (
    BinaryFileError,
    EditConflictError,
    FileTooLargeError,
    InvalidPathError,
    PathOutsideWorkspaceError,
    WorkspaceContext,
    WorkspaceDirectoryNotFoundError,
    WorkspaceError,
    WorkspaceFileNotFoundError,
    WorkspaceIOError,
)

# Default patterns to ignore when listing or searching directories
DEFAULT_IGNORE_DIRS: frozenset[str] = frozenset(
    {
        ".git",
        ".venv",
        "node_modules",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        "dist",
        "build",
        ".tox",
    }
)


# ==============================================================================
# Input Schemas (Pydantic Models)
# ==============================================================================


class ListDirectoryInput(BaseModel):
    """Input parameters for listing entries within a workspace directory."""

    model_config = ConfigDict(extra="forbid")

    path: str = Field(
        default=".",
        description="Workspace-relative directory path to list (default: root '.')",
    )
    recursive: bool = Field(
        default=False,
        description="Whether to recursively traverse subdirectories",
    )
    max_entries: Optional[int] = Field(
        default=None,
        description="Optional override for maximum entries to return",
    )


class ReadFileInput(BaseModel):
    """Input parameters for reading the bounded text content of a workspace file."""

    model_config = ConfigDict(extra="forbid")

    path: str = Field(
        ...,
        description="Workspace-relative file path to read",
    )
    encoding: str = Field(
        default="utf-8",
        description="Text encoding to attempt (fallback cascade includes utf-8-sig and latin-1)",
    )
    max_bytes: Optional[int] = Field(
        default=None,
        description="Optional maximum bytes to read before truncating",
    )


class ReadFileRangeInput(BaseModel):
    """Input parameters for reading a specific line range from a workspace file."""

    model_config = ConfigDict(extra="forbid")

    path: str = Field(
        ...,
        description="Workspace-relative file path to read",
    )
    start_line: int = Field(
        ...,
        description="1-indexed starting line number (inclusive, >= 1)",
    )
    end_line: int = Field(
        ...,
        description="1-indexed ending line number (inclusive, >= start_line)",
    )
    encoding: str = Field(
        default="utf-8",
        description="Text encoding to attempt",
    )


class GetFileMetadataInput(BaseModel):
    """Input parameters for inspecting metadata attributes of a workspace path."""

    model_config = ConfigDict(extra="forbid")

    path: str = Field(
        ...,
        description="Workspace-relative path to inspect",
    )


class WriteFileInput(BaseModel):
    """Input parameters for creating or completely replacing a workspace file."""

    model_config = ConfigDict(extra="forbid")

    path: str = Field(
        ...,
        description="Workspace-relative file path to write",
    )
    content: str = Field(
        ...,
        description="Full text content to write into the file",
    )
    encoding: str = Field(
        default="utf-8",
        description="Text encoding to use when writing",
    )


class EditFileInput(BaseModel):
    """Input parameters for performing search-and-replace text modifications on a workspace file."""

    model_config = ConfigDict(extra="forbid")

    path: str = Field(
        ...,
        description="Workspace-relative file path to edit",
    )
    old_str: str = Field(
        ...,
        description="Exact existing text string to search for and replace",
    )
    new_str: str = Field(
        ...,
        description="Replacement text string to insert",
    )
    replace_all: bool = Field(
        default=False,
        description="Whether to replace all occurrences (True) or require a unique single match (False)",
    )


# ==============================================================================
# Tool Handler Factory
# ==============================================================================


def create_filesystem_tools(workspace: WorkspaceContext) -> List[ToolDefinition]:
    """Create and return all filesystem tool definitions bound to the specified WorkspaceContext."""

    def list_directory(
        path: str = ".",
        recursive: bool = False,
        max_entries: Optional[int] = None,
    ) -> Dict[str, Any]:
        """List contents of a directory in the workspace with metadata."""
        target_dir = workspace.resolve_path(path, must_exist=True)
        if not target_dir.is_dir():
            raise InvalidPathError(f"Path '{path}' is a file, not a directory. Use read_file to inspect files.")

        limit = max_entries or workspace.max_directory_entries
        entries: List[Dict[str, Any]] = []
        truncated = False

        if recursive:
            for root, dirs, files in os.walk(target_dir):
                # Filter out ignored directories in-place to prune walk
                dirs[:] = [d for d in dirs if d not in DEFAULT_IGNORE_DIRS]

                for name in sorted(dirs + files):
                    if len(entries) >= limit:
                        truncated = True
                        break
                    entry_path = Path(root) / name
                    rel_path = workspace.get_relative_path(entry_path)
                    is_directory = entry_path.is_dir()
                    try:
                        st = entry_path.stat()
                        size = st.st_size if not is_directory else 0
                        mtime = datetime.fromtimestamp(st.st_mtime, timezone.utc).isoformat()
                    except OSError:
                        size = 0
                        mtime = None

                    entries.append(
                        {
                            "name": name,
                            "path": rel_path,
                            "is_dir": is_directory,
                            "size_bytes": size,
                            "modified_at": mtime,
                        }
                    )
                if truncated:
                    break
        else:
            try:
                raw_entries = [
                    e
                    for e in sorted(os.scandir(target_dir), key=lambda e: e.name)
                    if e.name not in DEFAULT_IGNORE_DIRS
                ]
            except OSError as exc:
                raise WorkspaceIOError(f"Failed to scan directory '{path}': {str(exc)}")

            for item in raw_entries:
                if len(entries) >= limit:
                    truncated = True
                    break
                is_directory = item.is_dir()
                try:
                    st = item.stat()
                    size = st.st_size if not is_directory else 0
                    mtime = datetime.fromtimestamp(st.st_mtime, timezone.utc).isoformat()
                except OSError:
                    size = 0
                    mtime = None

                entries.append(
                    {
                        "name": item.name,
                        "path": workspace.get_relative_path(Path(item.path)),
                        "is_dir": is_directory,
                        "size_bytes": size,
                        "modified_at": mtime,
                    }
                )

        return {
            "path": workspace.get_relative_path(target_dir),
            "entries": entries,
            "total_entries": len(entries),
            "truncated": truncated,
        }

    def read_file(
        path: str,
        encoding: str = "utf-8",
        max_bytes: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Read bounded text content of a workspace file."""
        target_file = workspace.resolve_path(path, must_exist=True)
        content, truncated, used_encoding = workspace.read_text_bounded(
            target_file,
            encoding=encoding,
            max_bytes=max_bytes,
        )
        return {
            "path": workspace.get_relative_path(target_file),
            "content": content,
            "bytes_read": len(content.encode(used_encoding, errors="replace")),
            "encoding": used_encoding,
            "truncated": truncated,
        }

    def read_file_range(
        path: str,
        start_line: int,
        end_line: int,
        encoding: str = "utf-8",
    ) -> Dict[str, Any]:
        """Read a specific line range [start_line, end_line] from a workspace file."""
        target_file = workspace.resolve_path(path, must_exist=True)
        content, total_lines, returned_lines = workspace.read_line_range(
            target_file,
            start_line=start_line,
            end_line=end_line,
            encoding=encoding,
        )
        return {
            "path": workspace.get_relative_path(target_file),
            "content": content,
            "start_line": start_line,
            "end_line": end_line,
            "total_lines": total_lines,
            "lines_returned": returned_lines,
        }

    def get_file_metadata(path: str) -> Dict[str, Any]:
        """Inspect metadata properties of a workspace file or directory."""
        target_path = workspace.resolve_path(path, must_exist=True)
        try:
            st = target_path.stat()
        except OSError as exc:
            raise WorkspaceIOError(f"Failed to stat path '{path}': {str(exc)}")

        is_directory = target_path.is_dir()
        is_binary = False
        line_count: Optional[int] = None

        if not is_directory:
            is_binary = workspace.is_binary_file(target_path)
            if not is_binary:
                try:
                    text, _, _ = workspace.read_text_bounded(target_path)
                    line_count = len(text.splitlines())
                except WorkspaceError:
                    line_count = None

        return {
            "name": target_path.name,
            "path": workspace.get_relative_path(target_path),
            "is_file": target_path.is_file(),
            "is_dir": is_directory,
            "is_symlink": target_path.is_symlink(),
            "is_binary": is_binary,
            "size_bytes": st.st_size,
            "line_count": line_count,
            "created_at": datetime.fromtimestamp(st.st_ctime, timezone.utc).isoformat(),
            "modified_at": datetime.fromtimestamp(st.st_mtime, timezone.utc).isoformat(),
            "permissions": oct(stat.S_IMODE(st.st_mode)),
        }

    def write_file(
        path: str,
        content: str,
        encoding: str = "utf-8",
    ) -> Dict[str, Any]:
        """Create or overwrite a file in the workspace atomically."""
        target_file = workspace.resolve_path(path, must_exist=False)
        bytes_written, lines_written = workspace.atomic_write_text(
            target_file,
            content=content,
            encoding=encoding,
        )
        return {
            "path": workspace.get_relative_path(target_file),
            "bytes_written": bytes_written,
            "lines_written": lines_written,
            "encoding": encoding,
            "created": not target_file.exists(),
        }

    def edit_file(
        path: str,
        old_str: str,
        new_str: str,
        replace_all: bool = False,
    ) -> Dict[str, Any]:
        """Search and replace text in a workspace file with conflict checking and diff output."""
        target_file = workspace.resolve_path(path, must_exist=True)
        diff, count = workspace.edit_file_text(
            target_file,
            old_str=old_str,
            new_str=new_str,
            replace_all=replace_all,
        )
        return {
            "path": workspace.get_relative_path(target_file),
            "replacements_made": count,
            "diff": diff,
        }

    # Build typed ToolDefinitions
    return [
        ToolDefinition.from_pydantic(
            name="list_directory",
            description="List files and directories in a workspace directory with metadata (size, modification time).",
            parameters_model=ListDirectoryInput,
            risk_category=ToolRiskCategory.READ_ONLY,
            tags=["filesystem", "discovery", "read_only"],
            handler=list_directory,
        ),
        ToolDefinition.from_pydantic(
            name="read_file",
            description="Read the text content of a file within the workspace with size limits and encoding detection.",
            parameters_model=ReadFileInput,
            risk_category=ToolRiskCategory.READ_ONLY,
            tags=["filesystem", "read_only"],
            handler=read_file,
        ),
        ToolDefinition.from_pydantic(
            name="read_file_range",
            description="Read a specific 1-indexed line range [start_line, end_line] from a workspace text file.",
            parameters_model=ReadFileRangeInput,
            risk_category=ToolRiskCategory.READ_ONLY,
            tags=["filesystem", "read_only"],
            handler=read_file_range,
        ),
        ToolDefinition.from_pydantic(
            name="get_file_metadata",
            description="Inspect detailed metadata (size, lines, timestamps, permissions, binary status) of a workspace path.",
            parameters_model=GetFileMetadataInput,
            risk_category=ToolRiskCategory.READ_ONLY,
            tags=["filesystem", "metadata", "read_only"],
            handler=get_file_metadata,
        ),
        ToolDefinition.from_pydantic(
            name="write_file",
            description="Atomically create or overwrite a file in the workspace with text content.",
            parameters_model=WriteFileInput,
            risk_category=ToolRiskCategory.WORKSPACE_WRITE,
            tags=["filesystem", "workspace_write"],
            handler=write_file,
        ),
        ToolDefinition.from_pydantic(
            name="edit_file",
            description="Perform a precise search-and-replace modification on a file with conflict checking and diff generation.",
            parameters_model=EditFileInput,
            risk_category=ToolRiskCategory.WORKSPACE_WRITE,
            tags=["filesystem", "workspace_write"],
            handler=edit_file,
        ),
    ]
