"""Forensic unit and security tests for WorkspaceContext path confinement and file operations."""

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
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

    def test_reparse_point_junction_escape_blocked(self, temp_workspace: WorkspaceContext, tmp_path: Path) -> None:
        """Directory junction or reparse point pointing outside workspace root is detected and blocked."""
        external_dir = tmp_path / "external_vault"
        external_dir.mkdir(parents=True, exist_ok=True)
        secret_file = external_dir / "secret.txt"
        secret_file.write_text("SUPER_SECRET_PAYLOAD", encoding="utf-8")

        junction_link = temp_workspace.root_path / "external_junction"
        if sys.platform == "win32":
            res = subprocess.run(
                ["cmd.exe", "/c", "mklink", "/J", str(junction_link), str(external_dir)],
                capture_output=True,
                text=True,
            )
            if res.returncode != 0:
                pytest.skip(f"mklink /J failed: {res.stderr}")
        else:
            try:
                os.symlink(str(external_dir), str(junction_link), target_is_directory=True)
            except OSError:
                pytest.skip("Symlink requires permissions")

        with pytest.raises(PathOutsideWorkspaceError):
            temp_workspace.resolve_path("external_junction/secret.txt", must_exist=True)

    def test_host_path_sanitization_in_errors(self, temp_workspace: WorkspaceContext) -> None:
        """Exceptions must not disclose full host workspace root or host drive structures."""
        # Outside workspace error
        with pytest.raises(PathOutsideWorkspaceError) as exc_info:
            temp_workspace.resolve_path("../../etc/passwd")
        err_msg = str(exc_info.value)
        assert str(temp_workspace.root_path) not in err_msg
        assert "<workspace>" in err_msg or "outside workspace boundary" in err_msg


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

    def test_binary_misclassification_defense_large_buffer(self, temp_workspace: WorkspaceContext) -> None:
        """A file with >4096 bytes of ASCII text followed by binary bytes must be rejected as binary, not decoded as Latin-1."""
        target_path = temp_workspace.resolve_path("large_binary_payload.bin")
        payload = (b"HEADER_LINE_A\n" * 400) + (b"\x00\x01\x02\x03\xff\xfe" * 50)
        assert len(payload) > 5000

        with open(target_path, "wb") as f:
            f.write(payload)

        with pytest.raises(BinaryFileError):
            temp_workspace.read_text_bounded(target_path)

    def test_concurrent_edits_conflict_detection(self, temp_workspace: WorkspaceContext) -> None:
        """Concurrent edits to the same file serialize via path lock; exactly one succeeds and others raise EditConflictError."""
        target_path = temp_workspace.resolve_path("counter.py")
        temp_workspace.atomic_write_text(target_path, "COUNTER = 0\n")

        successes = 0
        conflicts = 0
        errors = []

        def worker(idx: int) -> None:
            nonlocal successes, conflicts
            try:
                _, count = temp_workspace.edit_file_text(
                    target_path,
                    old_str="COUNTER = 0",
                    new_str=f"COUNTER = {idx + 1}",
                )
                if count == 1:
                    successes += 1
            except EditConflictError:
                conflicts += 1
            except Exception as e:
                errors.append(e)

        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(worker, i) for i in range(10)]
            for f in futures:
                f.result()

        assert len(errors) == 0
        assert successes == 1
        assert conflicts == 9

        final_content, _, _ = temp_workspace.read_text_bounded(target_path)
        assert "COUNTER = 0" not in final_content
        assert final_content.startswith("COUNTER = ")

    def test_concurrent_writes_no_handle_contention(self, temp_workspace: WorkspaceContext) -> None:
        """Concurrent writes to the exact same file must not fail with [WinError 5] Access is denied."""
        target_path = temp_workspace.resolve_path("shared_write.txt")
        errors = []

        def write_worker(idx: int) -> None:
            try:
                temp_workspace.atomic_write_text(target_path, f"Thread {idx} content\n")
            except Exception as e:
                errors.append(e)

        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(write_worker, i) for i in range(10)]
            for f in futures:
                f.result()

        assert len(errors) == 0
        final_text, _, _ = temp_workspace.read_text_bounded(target_path)
        assert final_text.startswith("Thread ")

    def test_temp_file_cleanup_on_interruption(self, temp_workspace: WorkspaceContext, monkeypatch: pytest.MonkeyPatch) -> None:
        """Temporary files created during atomic writes must be cleaned up if an interruption occurs."""
        target_path = temp_workspace.resolve_path("interrupted.txt")

        # Simulate interruption during os.replace
        def mock_replace(src: str, dst: str) -> None:
            raise KeyboardInterrupt("Simulated task cancellation / interruption")

        monkeypatch.setattr(os, "replace", mock_replace)

        with pytest.raises(KeyboardInterrupt):
            temp_workspace.atomic_write_text(target_path, "Some content")

        # Verify no .tmp_write_* files left in directory
        tmp_files = list(temp_workspace.root_path.glob(".tmp_write_*"))
        assert len(tmp_files) == 0
