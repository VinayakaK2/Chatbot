"""Workspace management, directory navigation, and Phase 4 bounds inspection API."""

import logging
import os
from pathlib import Path
import subprocess
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field

from core.task_service import get_task_service
from core.workspace import (
    InvalidPathError,
    PathOutsideWorkspaceError,
    WorkspaceContext,
    WorkspaceDirectoryNotFoundError,
)

router = APIRouter(prefix="/api/workspace", tags=["Workspace Management"])
logger = logging.getLogger("agent_runtime.api.workspace")

# In-memory recent workspaces cache
_recent_workspaces: List[str] = []

WORKSPACE_TOOLS_METADATA = [
    {
        "name": "list_directory",
        "risk": "read_only",
        "description": "Lists directory entries with file types, sizes, and timestamps bounded by max_entries.",
    },
    {
        "name": "read_file",
        "risk": "read_only",
        "description": "Bounded text file reading with fallback encoding inspection (UTF-8, Latin-1).",
    },
    {
        "name": "read_file_range",
        "risk": "read_only",
        "description": "Reads specified 1-indexed line ranges [start_line, end_line] within workspace.",
    },
    {
        "name": "get_file_metadata",
        "risk": "read_only",
        "description": "Inspects file size, modified time, line count, permissions, and binary status.",
    },
    {
        "name": "find_files",
        "risk": "read_only",
        "description": "Glob pattern search across workspace with automatic cache directory exclusion.",
    },
    {
        "name": "search_files",
        "risk": "read_only",
        "description": "Regex and substring content search returning matching file paths, lines, and text.",
    },
    {
        "name": "write_file",
        "risk": "workspace_write",
        "description": "Atomic file creation or overwrite with automatic parent directory creation.",
    },
    {
        "name": "edit_file",
        "risk": "workspace_write",
        "description": "Conflict-safe search-and-replace modification generating unified reviewable diffs.",
    },
]


class WorkspaceLimitsModel(BaseModel):
    """Operational bounds configured for Phase 4 workspace operations."""

    max_file_size_bytes: int
    max_read_bytes: int
    max_directory_entries: int
    max_search_results: int
    max_write_bytes: int
    max_edit_bytes: int


class WorkspaceToolInfo(BaseModel):
    """Metadata describing a registered workspace tool."""

    name: str
    risk: str
    description: str


class WorkspaceInfoResponse(BaseModel):
    """Comprehensive workspace state, path details, limits, and active tools."""

    root_path: str
    name: str
    exists: bool
    is_git_repo: bool
    current_branch: Optional[str] = None
    total_files: int
    limits: WorkspaceLimitsModel
    tools: List[WorkspaceToolInfo]


class WorkspaceSelectRequest(BaseModel):
    """Request payload to select or switch the active workspace directory."""

    path: str = Field(..., min_length=1, description="Absolute or relative folder path")


class DirectoryItem(BaseModel):
    """Metadata for browsable subdirectories."""

    name: str
    path: str
    is_dir: bool = True


class BrowseDirectoryResponse(BaseModel):
    """Directory navigation payload for folder picker UI."""

    current_path: str
    parent_path: Optional[str] = None
    directories: List[DirectoryItem]


def _get_git_branch(root_path: Path) -> Optional[str]:
    """Retrieve active git branch name if available."""
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=str(root_path),
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
        if proc.returncode == 0:
            branch = proc.stdout.strip()
            return branch if branch else None
    except Exception:
        pass
    return None


def _count_workspace_files(root_path: Path) -> int:
    """Bounded count of files in the workspace (ignoring build/dependency caches)."""
    count = 0
    ignored = {".git", "node_modules", "__pycache__", ".venv", ".pytest_cache", "dist", "build"}
    try:
        for dirpath, dirnames, filenames in os.walk(str(root_path)):
            dirnames[:] = [d for d in dirnames if d not in ignored and not d.startswith(".")]
            count += len(filenames)
            if count >= 1000:
                break
    except Exception:
        pass
    return count


def _build_workspace_info(ws: WorkspaceContext) -> WorkspaceInfoResponse:
    """Construct structured response from active WorkspaceContext."""
    root = ws.root_path
    is_git = (root / ".git").is_dir()
    branch = _get_git_branch(root) if is_git else None
    file_count = _count_workspace_files(root)

    # Maintain recent workspaces cache
    path_str = str(root)
    if path_str not in _recent_workspaces:
        _recent_workspaces.insert(0, path_str)
        if len(_recent_workspaces) > 10:
            _recent_workspaces.pop()

    return WorkspaceInfoResponse(
        root_path=path_str,
        name=root.name or path_str,
        exists=root.exists(),
        is_git_repo=is_git,
        current_branch=branch,
        total_files=file_count,
        limits=WorkspaceLimitsModel(
            max_file_size_bytes=ws.max_file_size_bytes,
            max_read_bytes=ws.max_read_bytes,
            max_directory_entries=ws.max_directory_entries,
            max_search_results=ws.max_search_results,
            max_write_bytes=ws.max_write_bytes,
            max_edit_bytes=ws.max_edit_bytes,
        ),
        tools=[WorkspaceToolInfo(**t) for t in WORKSPACE_TOOLS_METADATA],
    )


@router.get(
    "",
    response_model=WorkspaceInfoResponse,
    status_code=status.HTTP_200_OK,
    summary="Retrieve current active workspace details and operational limits",
)
def get_workspace_info() -> WorkspaceInfoResponse:
    """Return active workspace boundary, canonical path, and registered Phase 4 tools."""
    task_service = get_task_service()
    return _build_workspace_info(task_service.workspace_context)


@router.post(
    "/select",
    response_model=WorkspaceInfoResponse,
    status_code=status.HTTP_200_OK,
    summary="Switch or select active workspace root folder",
)
def select_workspace(request: WorkspaceSelectRequest) -> WorkspaceInfoResponse:
    """Validate and atomically switch the active workspace folder."""
    raw_path = request.path.strip()

    # Clean file:/// URL prefixes
    if raw_path.startswith("file:///"):
        raw_path = raw_path[8:]
        if len(raw_path) >= 3 and raw_path[0] == "/" and raw_path[2] == ":":
            raw_path = raw_path[1:]

    target = Path(raw_path).expanduser()
    if not target.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Selected workspace folder does not exist: '{request.path}'",
        )

    if not target.is_dir():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Selected path is not a directory: '{request.path}'",
        )

    try:
        task_service = get_task_service()
        new_ws = task_service.set_workspace_root(target)
        return _build_workspace_info(new_ws)
    except (InvalidPathError, PathOutsideWorkspaceError, WorkspaceDirectoryNotFoundError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Workspace path rejected: {str(exc)}",
        ) from exc
    except Exception as exc:
        logger.error("Failed to select workspace root: %s", str(exc), exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to switch workspace: {str(exc)}",
        ) from exc


@router.get(
    "/directories",
    response_model=BrowseDirectoryResponse,
    status_code=status.HTTP_200_OK,
    summary="List subdirectories for folder navigation and picker UI",
)
def browse_directories(
    base_path: Optional[str] = Query(None, description="Directory path to inspect"),
) -> BrowseDirectoryResponse:
    """Browse subdirectories for the workspace folder selection dialog."""
    task_service = get_task_service()
    current_root = task_service.workspace_context.root_path

    if base_path:
        clean = base_path.strip()
        if clean.startswith("file:///"):
            clean = clean[8:]
            if len(clean) >= 3 and clean[0] == "/" and clean[2] == ":":
                clean = clean[1:]
        target = Path(clean).expanduser()
    else:
        target = current_root

    if not target.exists() or not target.is_dir():
        target = current_root

    resolved_target = Path(os.path.realpath(target)).resolve()

    # Determine parent directory
    parent_path: Optional[str] = None
    if resolved_target.parent and resolved_target.parent != resolved_target:
        parent_path = str(resolved_target.parent)

    ignored = {".git", "node_modules", "__pycache__", ".venv", ".pytest_cache", "dist", "build"}
    dirs: List[DirectoryItem] = []

    try:
        with os.scandir(str(resolved_target)) as entries:
            for entry in sorted(entries, key=lambda e: e.name.lower()):
                if entry.is_dir() and entry.name not in ignored and not entry.name.startswith("."):
                    dirs.append(
                        DirectoryItem(
                            name=entry.name,
                            path=entry.path,
                            is_dir=True,
                        )
                    )
    except OSError as exc:
        logger.warning("Directory inspection error for '%s': %s", str(resolved_target), str(exc))

    return BrowseDirectoryResponse(
        current_path=str(resolved_target),
        parent_path=parent_path,
        directories=dirs,
    )


@router.get(
    "/recent",
    response_model=List[str],
    status_code=status.HTTP_200_OK,
    summary="Retrieve recently opened workspace paths",
)
def get_recent_workspaces() -> List[str]:
    """Retrieve list of recently accessed workspace roots."""
    task_service = get_task_service()
    current = str(task_service.workspace_context.root_path)
    if current not in _recent_workspaces:
        _recent_workspaces.insert(0, current)
    return _recent_workspaces
