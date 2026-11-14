"""Tests for Phase 4 workspace filesystem and search tools."""

from pathlib import Path
import pytest

from core.workspace import (
    BinaryFileError,
    EditConflictError,
    FileTooLargeError,
    InvalidPathError,
    PathOutsideWorkspaceError,
    WorkspaceContext,
    WorkspaceFileNotFoundError,
)
from tools.filesystem import create_filesystem_tools
from tools.search import create_search_tools


@pytest.fixture
def workspace_with_files(tmp_path: Path) -> WorkspaceContext:
    """Create a temporary workspace populated with a structured directory tree and files."""
    ws_dir = tmp_path / "test_project"
    ws_dir.mkdir(parents=True, exist_ok=True)

    # Project tree
    src_dir = ws_dir / "src"
    src_dir.mkdir()
    (src_dir / "main.py").write_text("import os\n\ndef run():\n    return True\n", encoding="utf-8")
    (src_dir / "utils.py").write_text("def helper():\n    return 'help'\n", encoding="utf-8")

    docs_dir = ws_dir / "docs"
    docs_dir.mkdir()
    (docs_dir / "README.md").write_text("# Project Documentation\nWelcome to the project.\n", encoding="utf-8")

    # Binary file
    (ws_dir / "asset.bin").write_bytes(b"\x00\x01\x02\x03\xFF\xFE\x00")

    # Ignored directory
    git_dir = ws_dir / ".git"
    git_dir.mkdir()
    (git_dir / "config").write_text("[core]\n", encoding="utf-8")

    return WorkspaceContext(root_path=ws_dir)


class TestFilesystemTools:
    """Unit tests for the 6 filesystem exploration and modification tools."""

    def test_list_directory_non_recursive(self, workspace_with_files: WorkspaceContext) -> None:
        """list_directory lists root entries without descending into subdirectories."""
        tools = {t.name: t for t in create_filesystem_tools(workspace_with_files)}
        res = tools["list_directory"].handler(path=".", recursive=False)

        assert res["total_entries"] >= 3
        names = [e["name"] for e in res["entries"]]
        assert "src" in names
        assert "docs" in names
        assert "asset.bin" in names
        assert ".git" not in names  # Scandir doesn't recurse, but git was ignored

    def test_list_directory_recursive(self, workspace_with_files: WorkspaceContext) -> None:
        """list_directory recursively lists all nested files and directories, excluding .git."""
        tools = {t.name: t for t in create_filesystem_tools(workspace_with_files)}
        res = tools["list_directory"].handler(path=".", recursive=True)

        paths = [e["path"] for e in res["entries"]]
        assert "src/main.py" in paths
        assert "src/utils.py" in paths
        assert "docs/README.md" in paths
        assert not any(p.startswith(".git") for p in paths)

    def test_read_file_success(self, workspace_with_files: WorkspaceContext) -> None:
        """read_file returns full file content and metadata."""
        tools = {t.name: t for t in create_filesystem_tools(workspace_with_files)}
        res = tools["read_file"].handler(path="src/main.py")

        assert res["path"] == "src/main.py"
        assert "def run():" in res["content"]
        assert res["truncated"] is False
        assert "utf-8" in res["encoding"].lower()

    def test_read_file_binary_rejected(self, workspace_with_files: WorkspaceContext) -> None:
        """read_file raises BinaryFileError when targeting binary files."""
        tools = {t.name: t for t in create_filesystem_tools(workspace_with_files)}
        with pytest.raises(BinaryFileError):
            tools["read_file"].handler(path="asset.bin")

    def test_read_file_range(self, workspace_with_files: WorkspaceContext) -> None:
        """read_file_range returns specified line bounds."""
        tools = {t.name: t for t in create_filesystem_tools(workspace_with_files)}
        res = tools["read_file_range"].handler(path="src/main.py", start_line=1, end_line=2)

        assert res["start_line"] == 1
        assert res["end_line"] == 2
        assert res["lines_returned"] == 2
        assert res["content"].replace("\r\n", "\n") == "import os\n\n"

    def test_get_file_metadata(self, workspace_with_files: WorkspaceContext) -> None:
        """get_file_metadata returns detailed file properties."""
        tools = {t.name: t for t in create_filesystem_tools(workspace_with_files)}
        meta = tools["get_file_metadata"].handler(path="src/main.py")

        assert meta["name"] == "main.py"
        assert meta["is_file"] is True
        assert meta["is_dir"] is False
        assert meta["is_binary"] is False
        assert meta["size_bytes"] > 0
        assert meta["line_count"] == 4

    def test_write_file_atomic(self, workspace_with_files: WorkspaceContext) -> None:
        """write_file atomically creates file and parent directories."""
        tools = {t.name: t for t in create_filesystem_tools(workspace_with_files)}
        res = tools["write_file"].handler(
            path="tests/new_test.py",
            content="def test_sample():\n    assert 1 == 1\n",
        )

        assert res["path"] == "tests/new_test.py"
        assert res["bytes_written"] > 0
        assert res["lines_written"] == 2

        # Verify on disk
        target = workspace_with_files.root_path / "tests" / "new_test.py"
        assert target.exists()
        assert "test_sample" in target.read_text(encoding="utf-8")

    def test_edit_file_single_and_diff(self, workspace_with_files: WorkspaceContext) -> None:
        """edit_file replaces unique string and returns unified diff."""
        tools = {t.name: t for t in create_filesystem_tools(workspace_with_files)}
        res = tools["edit_file"].handler(
            path="src/utils.py",
            old_str="return 'help'",
            new_str="return 'super_help'",
        )

        assert res["replacements_made"] == 1
        assert "-    return 'help'" in res["diff"]
        assert "+    return 'super_help'" in res["diff"]

        # Verify on disk
        content = (workspace_with_files.root_path / "src" / "utils.py").read_text(encoding="utf-8")
        assert "super_help" in content

    def test_edit_file_conflict(self, workspace_with_files: WorkspaceContext) -> None:
        """edit_file raises EditConflictError if old_str is not found."""
        tools = {t.name: t for t in create_filesystem_tools(workspace_with_files)}
        with pytest.raises(EditConflictError):
            tools["edit_file"].handler(
                path="src/utils.py",
                old_str="non_existent_text_xyz",
                new_str="new_text",
            )


class TestSearchTools:
    """Unit tests for find_files and search_files tools."""

    def test_find_files_glob(self, workspace_with_files: WorkspaceContext) -> None:
        """find_files finds all matching python files."""
        tools = {t.name: t for t in create_search_tools(workspace_with_files)}
        res = tools["find_files"].handler(pattern="*.py", directory=".")

        assert res["total_matches"] == 2
        assert "src/main.py" in res["matches"]
        assert "src/utils.py" in res["matches"]

    def test_find_files_skips_git(self, workspace_with_files: WorkspaceContext) -> None:
        """find_files ignores files inside .git directory."""
        tools = {t.name: t for t in create_search_tools(workspace_with_files)}
        res = tools["find_files"].handler(pattern="*config*", directory=".")

        assert res["total_matches"] == 0

    def test_search_files_substring(self, workspace_with_files: WorkspaceContext) -> None:
        """search_files locates text matches across workspace files."""
        tools = {t.name: t for t in create_search_tools(workspace_with_files)}
        res = tools["search_files"].handler(query="return", directory=".")

        assert res["total_matches"] == 2
        paths = [m["path"] for m in res["matches"]]
        assert "src/main.py" in paths
        assert "src/utils.py" in paths

    def test_search_files_regex(self, workspace_with_files: WorkspaceContext) -> None:
        """search_files supports regular expression patterns."""
        tools = {t.name: t for t in create_search_tools(workspace_with_files)}
        res = tools["search_files"].handler(query=r"def\s+\w+\(\):", directory=".", is_regex=True)

        assert res["total_matches"] == 2

    def test_search_files_invalid_regex(self, workspace_with_files: WorkspaceContext) -> None:
        """search_files raises InvalidPathError on malformed regex."""
        tools = {t.name: t for t in create_search_tools(workspace_with_files)}
        with pytest.raises(InvalidPathError):
            tools["search_files"].handler(query="[unclosed_bracket", is_regex=True)
