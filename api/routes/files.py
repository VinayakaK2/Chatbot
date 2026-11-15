"""Workspace file inspection and git diff endpoints for File & Diff Viewer."""

import logging
import os
from pathlib import Path
import re
import subprocess
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field

from core.workspace import PathOutsideWorkspaceError, WorkspaceContext

router = APIRouter(prefix="/api/files", tags=["Workspace Files"])
logger = logging.getLogger("agent_runtime.api.files")

# Supported language extensions
EXTENSION_LANGUAGE_MAP: Dict[str, str] = {
    ".md": "markdown",
    ".markdown": "markdown",
    ".py": "python",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".js": "javascript",
    ".jsx": "javascript",
    ".json": "json",
    ".html": "html",
    ".css": "css",
    ".sh": "bash",
    ".bat": "batch",
    ".ps1": "powershell",
    ".toml": "toml",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".txt": "text",
    ".log": "text",
}


class FileContentResponse(BaseModel):
    """File content and metadata for workspace file inspection."""

    path: str = Field(..., description="Workspace-relative path to the file")
    name: str = Field(..., description="File name with extension")
    content: str = Field(..., description="Full text content of the file")
    size_bytes: int = Field(..., description="File size in bytes")
    language: str = Field(..., description="Detected programming or markup language")
    is_markdown: bool = Field(..., description="Whether file is formatted markdown")


class FileDiffItem(BaseModel):
    """Structured diff representation for a single modified file."""

    path: str
    status: str
    insertions: int
    deletions: int
    patch: str


class GitDiffResponse(BaseModel):
    """Repository-wide diff summary and individual file changes."""

    files_changed_count: int
    insertions: int
    deletions: int
    files: List[FileDiffItem]
    raw_diff: str


class WorkspaceFileItem(BaseModel):
    """Listing metadata for workspace files."""

    path: str
    name: str
    is_dir: bool
    is_markdown: bool
    size_bytes: int


def _get_workspace() -> WorkspaceContext:
    """Retrieve workspace context bounded to active TaskService or current working directory."""
    try:
        from core.task_service import get_task_service
        return get_task_service().workspace_context
    except Exception:
        return WorkspaceContext(root_path=Path.cwd())



def _detect_language(file_path: Path) -> str:
    """Detect file language identifier from suffix."""
    suffix = file_path.suffix.lower()
    return EXTENSION_LANGUAGE_MAP.get(suffix, "text")


@router.get(
    "/content",
    response_model=FileContentResponse,
    status_code=status.HTTP_200_OK,
    summary="Fetch file text content from workspace",
)
def get_file_content(path: str = Query(..., description="Workspace-relative or absolute file path")) -> FileContentResponse:
    """Fetch text content of any allowed file within the workspace bounds."""
    workspace = _get_workspace()

    # Clean file:/// URLs or backslashes
    clean_path = path.strip()
    if clean_path.startswith("file:///"):
        clean_path = clean_path[8:]
        # On Windows, e.g. /H:/chatbot/... -> strip leading slash
        if len(clean_path) >= 3 and clean_path[0] == "/" and clean_path[2] == ":":
            clean_path = clean_path[1:]

    try:
        resolved_path = workspace.resolve_path(clean_path, must_exist=True)
    except PathOutsideWorkspaceError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Access denied: path is outside workspace: {str(exc)}",
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"File not found: {str(exc)}",
        ) from exc

    if not resolved_path.is_file():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Target path '{clean_path}' is a directory, not a file.",
        )

    # Read bounded content with fallback encodings
    encodings = ["utf-8", "utf-8-sig", "latin-1"]
    content: Optional[str] = None
    for enc in encodings:
        try:
            with open(resolved_path, "r", encoding=enc) as f:
                content = f.read(500_000)
            break
        except UnicodeDecodeError:
            continue
        except OSError as exc:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to read file '{clean_path}': {str(exc)}",
            ) from exc

    if content is None:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=f"File '{clean_path}' appears to be binary and cannot be rendered as text.",
        )

    rel_path = workspace.get_relative_path(resolved_path)
    lang = _detect_language(resolved_path)

    return FileContentResponse(
        path=rel_path,
        name=resolved_path.name,
        content=content,
        size_bytes=len(content.encode("utf-8")),
        language=lang,
        is_markdown=lang == "markdown",
    )


@router.get(
    "/diff",
    response_model=GitDiffResponse,
    status_code=status.HTTP_200_OK,
    summary="Retrieve current git diff and changed files",
)
def get_git_diff() -> GitDiffResponse:
    """Retrieve working tree git diff or recent commit diff if working tree is clean."""
    workspace = _get_workspace()
    root = str(workspace.root_path)

    try:
        # 1. Try unstaged and staged diff
        diff_proc = subprocess.run(
            ["git", "diff", "HEAD"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        raw_diff = diff_proc.stdout or ""

        # If working tree has no uncommitted changes, fall back to recent commit diff
        if not raw_diff.strip():
            fallback_proc = subprocess.run(
                ["git", "diff", "HEAD~1"],
                cwd=root,
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            raw_diff = fallback_proc.stdout or ""

        # Parse per-file diffs
        files: List[FileDiffItem] = []
        total_insertions = 0
        total_deletions = 0

        # Split raw diff into file chunks
        file_chunks = re.split(r"(?=^diff --git )", raw_diff, flags=re.MULTILINE)
        for chunk in file_chunks:
            if not chunk.strip():
                continue
            match = re.search(r"^diff --git a/(.*?) b/(.*?)$", chunk, re.MULTILINE)
            if not match:
                continue
            file_path = match.group(2)
            ins = len(re.findall(r"^\+[^+]", chunk, re.MULTILINE))
            dels = len(re.findall(r"^-[^-]", chunk, re.MULTILINE))
            total_insertions += ins
            total_deletions += dels

            status_str = "modified"
            if "new file mode" in chunk:
                status_str = "added"
            elif "deleted file mode" in chunk:
                status_str = "deleted"

            files.append(
                FileDiffItem(
                    path=file_path,
                    status=status_str,
                    insertions=ins,
                    deletions=dels,
                    patch=chunk,
                )
            )

        return GitDiffResponse(
            files_changed_count=len(files),
            insertions=total_insertions,
            deletions=total_deletions,
            files=files,
            raw_diff=raw_diff,
        )
    except Exception as exc:
        logger.warning("Git diff query failed or git unavailable: %s", str(exc))
        return GitDiffResponse(
            files_changed_count=0,
            insertions=0,
            deletions=0,
            files=[],
            raw_diff="",
        )


@router.get(
    "/list",
    response_model=List[WorkspaceFileItem],
    status_code=status.HTTP_200_OK,
    summary="List available reports and key documents for quick opening",
)
def list_available_documents(
    all_files: bool = Query(False, description="Scan entire workspace"),
    search: Optional[str] = Query(None, description="Filter files by name or relative path"),
    limit: int = Query(200, ge=1, le=1000, description="Max files to return"),
) -> List[WorkspaceFileItem]:
    """List accessible files from project workspace for file picker and inspection."""
    workspace = _get_workspace()
    items: List[WorkspaceFileItem] = []
    seen_paths = set()
    ignored = {".git", "node_modules", "__pycache__", ".venv", ".pytest_cache", "dist", "build"}

    # If all_files requested or searching, scan full workspace bounded by limit
    if all_files or search:
        search_lower = search.lower().strip() if search else None
        try:
            for dirpath, dirnames, filenames in os.walk(str(workspace.root_path)):
                dirnames[:] = [d for d in dirnames if d not in ignored and not d.startswith(".")]
                for fname in filenames:
                    if fname.startswith("."):
                        continue
                    full_p = Path(dirpath) / fname
                    try:
                        rel = workspace.get_relative_path(full_p)
                    except Exception:
                        continue
                    if search_lower and search_lower not in rel.lower() and search_lower not in fname.lower():
                        continue
                    if rel not in seen_paths:
                        seen_paths.add(rel)
                        items.append(
                            WorkspaceFileItem(
                                path=rel,
                                name=fname,
                                is_dir=False,
                                is_markdown=full_p.suffix.lower() in {".md", ".markdown"},
                                size_bytes=full_p.stat().st_size if full_p.exists() else 0,
                            )
                        )
                        if len(items) >= limit:
                            break
                if len(items) >= limit:
                    break
        except Exception as exc:
            logger.warning("Error scanning workspace files: %s", str(exc))
        return items

    # Default: scan priority directories for reports and documentation
    target_dirs = [
        workspace.root_path / "docs" / "reports",
        workspace.root_path / "docs",
        workspace.root_path,
    ]

    for directory in target_dirs:
        if not directory.exists() or not directory.is_dir():
            continue
        try:
            for entry in sorted(directory.iterdir(), key=lambda p: p.name):
                if entry.is_file() and entry.suffix.lower() in {".md", ".markdown", ".json", ".log"}:
                    rel = workspace.get_relative_path(entry)
                    if rel not in seen_paths:
                        seen_paths.add(rel)
                        items.append(
                            WorkspaceFileItem(
                                path=rel,
                                name=entry.name,
                                is_dir=False,
                                is_markdown=entry.suffix.lower() in {".md", ".markdown"},
                                size_bytes=entry.stat().st_size,
                            )
                        )
        except OSError:
            continue

    return items
