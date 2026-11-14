"""Forensic unit and security tests for WorkspaceContext path confinement and file operations."""

import os
from pathlib import Path
import sys
import tempfile
import pytest

from core.workspace import (
    BinaryFileError,
    EditConflictError,
    FileTooLargeError,
    InvalidPathError,
    PathOutsideWorkspaceError,
    WorkspaceContext,
    WorkspaceDirectoryNotFoundError,
    WorkspaceFileNotFoundError,
)


@pytest.fixture
def temp_workspace(tmp_path: Path) -> WorkspaceContext:
    """Provide an isolated, temporary WorkspaceContext for testing."""
    ws_dir = tmp_path / "sandbox_workspace"
    ws_dir.mkdir(parents=True, exist_ok=True)
    return WorkspaceContext(root_path=ws_dir)


class TestWorkspacePathResolution:
    """Unit tests for path resolution and boundary containment."""

    def test_canonical_root_resolution(self, temp_workspace: WorkspaceContext) -> None:
        """WorkspaceContext exposes canonical, absolute root path."""
        assert temp_workspace.root_path.is_absolute()
        assert temp_workspace.root_path.exists()
        assert temp_workspace.get_relative_path(temp_workspace.root_path) == "."

    def test_resolve_relative_path(self, temp_workspace: WorkspaceContext) -> None:
        """Relative paths resolve inside workspace root."""
        resolved = temp_workspace.resolve_path("src/utils.py")
        expected = temp_workspace.root_path / "src" / "utils.py"
        assert resolved == expected
        assert temp_workspace.get_relative_path(resolved) == "src/utils.py"

    def test_resolve_with_leading_slash(self, temp_workspace: WorkspaceContext) -> None:
        """Leading slashes are treated as relative to workspace root, not drive root."""
        resolved_slash = temp_workspace.resolve_path("/src/index.ts")
        resolved_backslash = temp_workspace.resolve_path("\\src\\index.ts")
        expected = temp_workspace.root_path / "src" / "index.ts"
        assert resolved_slash == expected
        assert resolved_backslash == expected

    def test_resolve_nonexistent_workspace_root_fails(self, tmp_path: Path) -> None:
        """Initializing WorkspaceContext with non-existent root raises WorkspaceDirectoryNotFoundError."""
        non_existent = tmp_path / "does_not_exist_xyz"
        with pytest.raises(WorkspaceDirectoryNotFoundError):
            WorkspaceContext(root_path=non_existent)


class TestWorkspaceSecurityTraversal:
    """Security tests probing path traversal and workspace escape vectors."""

    def test_parent_traversal_dots_blocked(self, temp_workspace: WorkspaceContext) -> None:
        """Standard parent traversal attempts (../) are trapped and denied."""
        with pytest.raises(PathOutsideWorkspaceError):
            temp_workspace.resolve_path("../outside.txt")
        with pytest.raises(PathOutsideWorkspaceError):
            temp_workspace.resolve_path("sub/../../escape.txt")
        with pytest.raises(PathOutsideWorkspaceError):
            temp_workspace.resolve_path("..\\..\\windows\\system32\\cmd.exe")

    def test_absolute_path_escape_blocked(self, temp_workspace: WorkspaceContext, tmp_path: Path) -> None:
        """Absolute path pointing to an external directory outside workspace root is denied."""
        external_dir = tmp_path / "external_secret"
        external_dir.mkdir(parents=True, exist_ok=True)
        external_file = external_dir / "secret.env"
        external_file.write_text("SECRET=123", encoding="utf-8")

        with pytest.raises(PathOutsideWorkspaceError):
            temp_workspace.resolve_path(str(external_file))

    def test_null_byte_injection_blocked(self, temp_workspace: WorkspaceContext) -> None:
        """Embedded null bytes are rejected as invalid paths."""
        with pytest.raises(InvalidPathError):
            temp_workspace.resolve_path("safe.txt\x00../escape.txt")

    def test_unc_network_paths_blocked(self, temp_workspace: WorkspaceContext) -> None:
        """UNC paths are strictly rejected."""
        with pytest.raises(PathOutsideWorkspaceError):
            temp_workspace.resolve_path(r"\\192.168.1.1\c$\passwords.txt")
        with pytest.raises(PathOutsideWorkspaceError):
            temp_workspace.resolve_path("//server/share/file.txt")

    def test_alternate_data_streams_blocked(self, temp_workspace: WorkspaceContext) -> None:
        """NTFS Alternate Data Stream syntax (colons) is blocked."""
        with pytest.raises(InvalidPathError):
            temp_workspace.resolve_path("file.txt:stream")
        with pytest.raises(InvalidPathError):
            temp_workspace.resolve_path("file.txt::$DATA")

    def test_reserved_windows_device_names_blocked(self, temp_workspace: WorkspaceContext) -> None:
        """Reserved DOS/Windows device names are rejected."""
        reserved_examples = ["CON", "PRN", "AUX", "NUL", "COM1", "LPT1", "con.txt", "sub/nul/file.py"]
        for res_name in reserved_examples:
            with pytest.raises(InvalidPathError):
                temp_workspace.resolve_path(res_name)

    def test_symlink_escape_blocked(self, temp_workspace: WorkspaceContext, tmp_path: Path) -> None:
        """Symlink pointing outside workspace root is detected and denied upon resolution."""
        external_secret = tmp_path / "outside_dir" / "secret.txt"
        external_secret.parent.mkdir(parents=True, exist_ok=True)
        external_secret.write_text("HOST_SECRET", encoding="utf-8")

        symlink_target = temp_workspace.root_path / "leak_link.txt"
        try:
            symlink_target.symlink_to(external_secret)
        except (OSError, NotImplementedError):
            pytest.skip("Symlink creation requires administrative privileges on Windows")

        with pytest.raises(PathOutsideWorkspaceError):
            temp_workspace.resolve_path("leak_link.txt")


class TestWorkspaceFileOperations:
    """Unit tests for bounded file reading, metadata inspection, and atomic writes."""

    def test_atomic_write_and_read(self, temp_workspace: WorkspaceContext) -> None:
        """Writing text atomically writes file and allows bounded reading."""
        target_path = temp_workspace.resolve_path("docs/notes.txt")
        bytes_written, lines = temp_workspace.atomic_write_text(target_path, "Line 1\nLine 2\nLine 3\n")
        assert bytes_written > 0
        assert lines == 3

        text, truncated, enc = temp_workspace.read_text_bounded(target_path)
        assert text == "Line 1\nLine 2\nLine 3\n"
        assert truncated is False
        assert "utf-8" in enc.lower()

    def test_read_nonexistent_file_raises_error(self, temp_workspace: WorkspaceContext) -> None:
        """Reading a non-existent file raises WorkspaceFileNotFoundError."""
        target_path = temp_workspace.resolve_path("missing.txt")
        with pytest.raises(WorkspaceFileNotFoundError):
            temp_workspace.read_text_bounded(target_path)

    def test_read_line_range(self, temp_workspace: WorkspaceContext) -> None:
        """Reading a line range returns only requested 1-indexed lines."""
        target_path = temp_workspace.resolve_path("sample.txt")
        content = "\n".join(f"Line {i}" for i in range(1, 11)) + "\n"
        temp_workspace.atomic_write_text(target_path, content)

        sliced_text, total_lines, count = temp_workspace.read_line_range(target_path, start_line=3, end_line=5)
        assert count == 3
        assert total_lines == 10
        assert sliced_text == "Line 3\nLine 4\nLine 5\n"

    def test_read_line_range_invalid_indices(self, temp_workspace: WorkspaceContext) -> None:
        """Invalid line indices raise InvalidPathError."""
        target_path = temp_workspace.resolve_path("sample2.txt")
        temp_workspace.atomic_write_text(target_path, "Hello\nWorld\n")

        with pytest.raises(InvalidPathError):
            temp_workspace.read_line_range(target_path, start_line=0, end_line=2)
        with pytest.raises(InvalidPathError):
            temp_workspace.read_line_range(target_path, start_line=5, end_line=2)

    def test_binary_file_detection(self, temp_workspace: WorkspaceContext) -> None:
        """Binary files containing null bytes are rejected by read_text_bounded."""
        bin_path = temp_workspace.root_path / "binary.bin"
        bin_path.write_bytes(b"\x00\x01\x02\xFF\xFE\x00\x12")

        assert temp_workspace.is_binary_file(bin_path) is True
        with pytest.raises(BinaryFileError):
            temp_workspace.read_text_bounded(bin_path)

    def test_edit_file_text_success_and_diff(self, temp_workspace: WorkspaceContext) -> None:
        """Editing file replaces target text and returns a valid unified diff."""
        target_path = temp_workspace.resolve_path("code.py")
        temp_workspace.atomic_write_text(target_path, "def hello():\n    return 'old'\n")

        diff, count = temp_workspace.edit_file_text(
            target_path,
            old_str="return 'old'",
            new_str="return 'new'",
        )
        assert count == 1
        assert "-    return 'old'" in diff
        assert "+    return 'new'" in diff

        updated_text, _, _ = temp_workspace.read_text_bounded(target_path)
        assert "return 'new'" in updated_text

    def test_edit_file_text_conflict_detection(self, temp_workspace: WorkspaceContext) -> None:
        """Editing fails with EditConflictError when old_str is missing or ambiguous."""
        target_path = temp_workspace.resolve_path("conflict.py")
        temp_workspace.atomic_write_text(target_path, "val = 10\nval = 10\n")

        # Missing text
        with pytest.raises(EditConflictError):
            temp_workspace.edit_file_text(target_path, old_str="val = 99", new_str="val = 100")

        # Multiple matches without replace_all
        with pytest.raises(EditConflictError):
            temp_workspace.edit_file_text(target_path, old_str="val = 10", new_str="val = 20", replace_all=False)

        # Multiple matches with replace_all works
        diff, count = temp_workspace.edit_file_text(target_path, old_str="val = 10", new_str="val = 20", replace_all=True)
        assert count == 2
