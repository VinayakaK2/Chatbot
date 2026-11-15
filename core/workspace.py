"""Core workspace confinement, canonical path security, and bounded filesystem context."""

from datetime import datetime, timezone
import difflib
import logging
import os
from pathlib import Path
import re
import stat
import tempfile
import threading
import time
from typing import Any, Dict, List, Optional, Set, Tuple, Union

from core.tool_contracts import ToolError

logger = logging.getLogger("agent_runtime.core.workspace")

# ==============================================================================
# Windows Reserved Names and Device Traps
# ==============================================================================

# Reserved Windows DOS device identifiers (case-insensitive, with or without extensions)
WINDOWS_RESERVED_NAMES: frozenset[str] = frozenset(
    {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        "COM1",
        "COM2",
        "COM3",
        "COM4",
        "COM5",
        "COM6",
        "COM7",
        "COM8",
        "COM9",
        "LPT1",
        "LPT2",
        "LPT3",
        "LPT4",
        "LPT5",
        "LPT6",
        "LPT7",
        "LPT8",
        "LPT9",
    }
)

WINDOWS_RESERVED_PATTERN: re.Pattern = re.compile(
    r"^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(\..*)?$",
    re.IGNORECASE,
)


# ==============================================================================
# Workspace Exceptions Hierarchy
# ==============================================================================


class WorkspaceError(ToolError):
    """Base exception for all workspace path, confinement, and filesystem failures."""

    def __init__(self, message: str, code: str = "WORKSPACE_ERROR") -> None:
        """Initialize workspace error with human-readable diagnostic message and code."""
        super().__init__(message)
        self.code = code


class PathOutsideWorkspaceError(WorkspaceError):
    """Raised when a resolved path escapes the canonical workspace root."""

    def __init__(self, message: str) -> None:
        """Initialize path outside workspace error."""
        super().__init__(message, code="PATH_OUTSIDE_WORKSPACE")


class InvalidPathError(WorkspaceError):
    """Raised when a path string contains illegal characters, reserved device names, or ADS."""

    def __init__(self, message: str) -> None:
        """Initialize invalid path error."""
        super().__init__(message, code="INVALID_PATH")


class WorkspaceFileNotFoundError(WorkspaceError):
    """Raised when a target file does not exist in the workspace."""

    def __init__(self, message: str) -> None:
        """Initialize file not found error."""
        super().__init__(message, code="FILE_NOT_FOUND")


class WorkspaceDirectoryNotFoundError(WorkspaceError):
    """Raised when a target directory does not exist in the workspace."""

    def __init__(self, message: str) -> None:
        """Initialize directory not found error."""
        super().__init__(message, code="DIRECTORY_NOT_FOUND")


class FileTooLargeError(WorkspaceError):
    """Raised when a file exceeds the configured maximum byte ceiling."""

    def __init__(self, message: str) -> None:
        """Initialize file too large error."""
        super().__init__(message, code="FILE_TOO_LARGE")


class BinaryFileError(WorkspaceError):
    """Raised when attempting to read a binary file as text."""

    def __init__(self, message: str) -> None:
        """Initialize binary file error."""
        super().__init__(message, code="BINARY_FILE")


class EditConflictError(WorkspaceError):
    """Raised when an edit operation encounters stale or mismatched file contents."""

    def __init__(self, message: str) -> None:
        """Initialize edit conflict error."""
        super().__init__(message, code="EDIT_CONFLICT")


class WorkspaceIOError(WorkspaceError):
    """Raised when an underlying filesystem I/O operation fails."""

    def __init__(self, message: str) -> None:
        """Initialize workspace I/O error."""
        super().__init__(message, code="IO_ERROR")


# ==============================================================================
# Workspace Context & Security Confinement
# ==============================================================================


class WorkspaceContext:
    """Manages workspace boundary confinement, path resolution, limits, and secure file I/O."""

    def __init__(
        self,
        root_path: Union[str, Path],
        max_file_size_bytes: int = 10 * 1024 * 1024,      # 10 MB
        max_read_bytes: int = 256 * 1024,                  # 256 KB
        max_directory_entries: int = 1000,
        max_search_results: int = 200,
        max_write_bytes: int = 5 * 1024 * 1024,            # 5 MB
        max_edit_bytes: int = 1024 * 1024,                 # 1 MB
    ) -> None:
        """Initialize and validate workspace root boundary with operational limits."""
        raw_path = Path(root_path).expanduser()
        if not raw_path.exists():
            raise WorkspaceDirectoryNotFoundError(
                f"Configured workspace root '{root_path}' does not exist."
            )
        if not raw_path.is_dir():
            raise InvalidPathError(
                f"Configured workspace root '{root_path}' is not a directory."
            )

        # Canonicalize workspace root
        self._root_path: Path = Path(os.path.realpath(raw_path)).resolve()
        self.max_file_size_bytes = max_file_size_bytes
        self.max_read_bytes = max_read_bytes
        self.max_directory_entries = max_directory_entries
        self.max_search_results = max_search_results
        self.max_write_bytes = max_write_bytes
        self.max_edit_bytes = max_edit_bytes

        # Thread synchronization locks per canonical file path (CONC-01, CONC-02)
        self._path_locks: Dict[str, threading.RLock] = {}
        self._path_locks_mutex: threading.Lock = threading.Lock()

        logger.info("Initialized WorkspaceContext at canonical root: %s", self._root_path)

    def _get_path_lock(self, canonical_target: Path) -> threading.RLock:
        """Retrieve or initialize an isolated re-entrant lock for a canonical file path."""
        key = str(canonical_target)
        with self._path_locks_mutex:
            if key not in self._path_locks:
                self._path_locks[key] = threading.RLock()
            return self._path_locks[key]

    @property
    def root_path(self) -> Path:
        """Return the canonical workspace root path."""
        return self._root_path

    def get_relative_path(self, target: Path) -> str:
        """Convert a canonical path into a normalized forward-slash workspace-relative string."""
        try:
            rel = target.relative_to(self._root_path)
            rel_str = str(rel).replace("\\", "/")
            return "." if rel_str == "" else rel_str
        except ValueError:
            return target.name

    def validate_path_syntax(self, path_str: str) -> None:
        """Perform pre-resolution syntax checks for null bytes, ADS, UNC, and DOS device names."""
        if not path_str or not isinstance(path_str, str):
            raise InvalidPathError("Path must be a non-empty string.")

        stripped = path_str.strip()
        if not stripped:
            raise InvalidPathError("Path must not be empty or whitespace-only.")

        # 1. Null byte injection check
        if "\x00" in path_str:
            raise InvalidPathError("Path contains prohibited null byte character.")

        # 2. UNC path check (\\server\share or //server/share)
        if path_str.startswith(("\\\\", "//")):
            raise PathOutsideWorkspaceError("UNC network paths are prohibited outside local workspace.")

        # 3. Alternate Data Stream (ADS) check: colons after drive letter
        # Allowed drive letter pattern: 'C:' or 'c:' as the first 2 characters
        check_ads = path_str[2:] if (len(path_str) >= 2 and path_str[1] == ":" and path_str[0].isalpha()) else path_str
        if ":" in check_ads:
            raise InvalidPathError("Alternate Data Streams (ADS) syntax (':') is prohibited.")

        # 4. Windows reserved device names check across all segments
        normalized_parts = re.split(r"[/\\]+", stripped)
        for part in normalized_parts:
            if not part:
                continue
            # Remove trailing spaces/dots that Windows strips
            clean_part = part.rstrip(". ")
            if WINDOW_match := WINDOWS_RESERVED_PATTERN.match(clean_part):
                raise InvalidPathError(
                    f"Path segment '{part}' matches reserved Windows device name '{WINDOW_match.group(1)}'."
                )

    def resolve_path(self, path_str: str, must_exist: bool = False) -> Path:
        """Resolve a path relative to workspace root with strict canonical containment checks."""
        self.validate_path_syntax(path_str)
        stripped = path_str.strip()

        # Handle drive root / leading slashes: treat leading slashes as workspace-relative
        if stripped.startswith(("/", "\\")):
            clean_rel = stripped.lstrip("/\\")
            candidate = self._root_path / clean_rel
        elif len(stripped) >= 2 and stripped[1] == ":" and stripped[0].isalpha():
            # Absolute path with drive letter
            candidate = Path(stripped)
        else:
            candidate = self._root_path / stripped

        # Canonicalize the candidate path
        if candidate.exists():
            resolved = Path(os.path.realpath(candidate)).resolve()
        else:
            # For non-existent paths, resolve deepest existing parent to trace symlink escapes
            cur = candidate
            unresolved_parts: List[str] = []
            while not cur.exists() and cur != cur.parent:
                unresolved_parts.append(cur.name)
                cur = cur.parent

            resolved_parent = Path(os.path.realpath(cur)).resolve()
            for part in reversed(unresolved_parts):
                resolved_parent = resolved_parent / part
            resolved = resolved_parent

        # Enforce canonical containment
        try:
            common = os.path.commonpath([str(self._root_path), str(resolved)])
        except ValueError:
            # Different drives on Windows
            raise PathOutsideWorkspaceError(
                f"Path '{path_str}' is on a different drive than the workspace root."
            )

        if common != str(self._root_path):
            raise PathOutsideWorkspaceError(
                f"Path '{path_str}' resolves outside workspace boundary."
            )

        if must_exist and not resolved.exists():
            raise WorkspaceFileNotFoundError(f"File or directory does not exist: '{path_str}'")

        return resolved

    def is_binary_file(self, target: Path, sample_size: int = 4096) -> bool:
        """Detect whether a file contains binary / non-text content via byte sampling."""
        if not target.is_file():
            return False
        try:
            with open(target, "rb") as f:
                chunk = f.read(sample_size)
                if not chunk:
                    return False
                # Null byte is a definitive binary indicator
                if b"\x00" in chunk:
                    return True
                # Check ratio of non-printable control characters
                text_characters = bytes(range(32, 127)) + b"\n\r\t\b"
                non_text = chunk.translate(None, text_characters)
                return len(non_text) / len(chunk) > 0.30
        except OSError:
            return False

    def read_text_bounded(
        self,
        target: Path,
        encoding: str = "utf-8",
        max_bytes: Optional[int] = None,
    ) -> Tuple[str, bool, str]:
        """Read a text file with multi-encoding fallback and size truncation boundaries."""
        if not target.exists():
            raise WorkspaceFileNotFoundError(f"File not found: {self.get_relative_path(target)}")
        if not target.is_file():
            raise InvalidPathError(f"Target is not a regular file: {self.get_relative_path(target)}")

        file_size = target.stat().st_size
        if file_size > self.max_file_size_bytes:
            raise FileTooLargeError(
                f"File size ({file_size} bytes) exceeds maximum permitted ({self.max_file_size_bytes} bytes)."
            )

        if self.is_binary_file(target):
            raise BinaryFileError(
                f"File '{self.get_relative_path(target)}' appears to be binary and cannot be read as text."
            )

        limit_bytes = max_bytes or self.max_read_bytes
        truncated = file_size > limit_bytes

        # Read bounded raw bytes
        try:
            with open(target, "rb") as f:
                raw_bytes = f.read(limit_bytes)
        except OSError as exc:
            raise WorkspaceIOError(f"Failed to read file '{self.get_relative_path(target)}': {str(exc)}")

        # Multi-encoding decode cascade
        candidate_encodings = [encoding, "utf-8-sig", "utf-8"]
        decoded_text: Optional[str] = None
        used_encoding: Optional[str] = None

        for enc in candidate_encodings:
            try:
                decoded_text = raw_bytes.decode(enc)
                used_encoding = enc
                break
            except (UnicodeDecodeError, LookupError):
                continue

        # If standard UTF-8 encodings fail, verify raw_bytes before attempting universal latin-1 fallback (SEC-03)
        if decoded_text is None:
            if b"\x00" in raw_bytes:
                raise BinaryFileError(
                    f"File '{self.get_relative_path(target)}' contains null bytes and cannot be read as text."
                )
            text_characters = bytes(range(32, 127)) + b"\n\r\t\b"
            non_text = raw_bytes.translate(None, text_characters)
            if len(raw_bytes) > 0 and len(non_text) / len(raw_bytes) > 0.30:
                raise BinaryFileError(
                    f"File '{self.get_relative_path(target)}' appears to be binary and cannot be read as text."
                )
            try:
                decoded_text = raw_bytes.decode("latin-1")
                used_encoding = "latin-1"
            except (UnicodeDecodeError, LookupError):
                pass

        if decoded_text is None:
            raise WorkspaceError(
                f"Failed to decode file '{self.get_relative_path(target)}' with supported encodings.",
                code="UNSUPPORTED_ENCODING",
            )

        return decoded_text, truncated, used_encoding

    def read_line_range(
        self,
        target: Path,
        start_line: int,
        end_line: int,
        encoding: str = "utf-8",
    ) -> Tuple[str, int, int]:
        """Read a 1-indexed line range [start_line, end_line] from a text file."""
        if start_line < 1:
            raise InvalidPathError(f"start_line must be >= 1, got {start_line}")
        if end_line < start_line:
            raise InvalidPathError(f"end_line ({end_line}) cannot be less than start_line ({start_line})")

        full_text, _, _ = self.read_text_bounded(target, encoding=encoding)
        lines = full_text.splitlines(keepends=True)
        total_lines = len(lines)

        if total_lines == 0:
            return "", 0, 0

        # Slice 1-indexed lines
        sliced_lines = lines[start_line - 1 : end_line]
        return "".join(sliced_lines), total_lines, len(sliced_lines)

    def atomic_write_text(
        self,
        target: Path,
        content: str,
        encoding: str = "utf-8",
    ) -> Tuple[int, int]:
        """Atomically write text content using a temporary file in the target directory."""
        if not isinstance(content, str):
            raise InvalidPathError("Content must be a string.")

        encoded_bytes = content.encode(encoding)
        if len(encoded_bytes) > self.max_write_bytes:
            raise FileTooLargeError(
                f"Write payload size ({len(encoded_bytes)} bytes) exceeds maximum limit ({self.max_write_bytes} bytes)."
            )

        target_dir = target.parent
        # Create parent directory safely within workspace
        try:
            target_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise WorkspaceIOError(f"Failed to create parent directory '{self.get_relative_path(target_dir)}': {str(exc)}")

        # Verify parent directory is also contained in workspace
        try:
            parent_common = os.path.commonpath([str(self._root_path), str(target_dir.resolve())])
            if parent_common != str(self._root_path):
                raise PathOutsideWorkspaceError("Parent directory resolves outside workspace boundary.")
        except ValueError:
            raise PathOutsideWorkspaceError("Parent directory is on a different drive than the workspace root.")

        # Serialize writes per canonical file target (CONC-01, CONC-02)
        lock = self._get_path_lock(self.resolve_path(self.get_relative_path(target)))
        with lock:
            # Write to temp file in same directory for atomic os.replace support
            temp_fd, temp_path_str = tempfile.mkstemp(
                prefix=".tmp_write_",
                dir=str(target_dir),
                text=False,
            )
            temp_path = Path(temp_path_str)

            try:
                with os.fdopen(temp_fd, "wb") as f:
                    f.write(encoded_bytes)
                    f.flush()
                    os.fsync(f.fileno())

                # Atomic swap with brief retry on Windows for transient sharing violations
                max_attempts = 5 if os.name == "nt" else 1
                for attempt in range(max_attempts):
                    try:
                        os.replace(temp_path, target)
                        break
                    except (PermissionError, OSError) as replace_err:
                        if attempt == max_attempts - 1:
                            raise
                        time.sleep(0.01 * (attempt + 1))
                lines_count = len(content.splitlines())
                return len(encoded_bytes), lines_count
            except OSError as exc:
                sanitized_exc = str(exc).replace(str(self._root_path), "<workspace>")
                raise WorkspaceIOError(f"Atomic write to '{self.get_relative_path(target)}' failed: {sanitized_exc}")
            finally:
                # Guarantee temp file cleanup on any error or cancellation (CONC-03)
                if temp_path.exists():
                    try:
                        temp_path.unlink()
                    except OSError:
                        pass

    def edit_file_text(
        self,
        target: Path,
        old_str: str,
        new_str: str,
        replace_all: bool = False,
        encoding: str = "utf-8",
    ) -> Tuple[str, int]:
        """Perform search-and-replace modification with conflict detection and unified diff generation."""
        if not old_str:
            raise InvalidPathError("old_str cannot be empty.")

        # Serialize entire Read -> Conflict Check -> Replace -> Write critical section (CONC-01)
        lock = self._get_path_lock(self.resolve_path(self.get_relative_path(target)))
        with lock:
            current_content, _, detected_enc = self.read_text_bounded(target, encoding=encoding)

            # Check for conflict: old_str must exist in the file
            occurrences = current_content.count(old_str)
            if occurrences == 0:
                raise EditConflictError(
                    f"old_str was not found in '{self.get_relative_path(target)}'. The file may have changed."
                )
            if not replace_all and occurrences > 1:
                raise EditConflictError(
                    f"old_str matched {occurrences} occurrences in '{self.get_relative_path(target)}'. Specify replace_all=True or a more unique string."
                )

            # Perform replacement
            if replace_all:
                updated_content = current_content.replace(old_str, new_str)
                replacements_made = occurrences
            else:
                updated_content = current_content.replace(old_str, new_str, 1)
                replacements_made = 1

            # Generate unified diff for auditability and UI rendering
            old_lines = current_content.splitlines(keepends=True)
            new_lines = updated_content.splitlines(keepends=True)
            rel_path = self.get_relative_path(target)
            diff_lines = list(
                difflib.unified_diff(
                    old_lines,
                    new_lines,
                    fromfile=f"a/{rel_path}",
                    tofile=f"b/{rel_path}",
                    lineterm="",
                )
            )
            unified_diff = "\n".join(diff_lines)

            # Atomically write updated content under the same file lock
            self.atomic_write_text(target, updated_content, encoding=detected_enc)
            return unified_diff, replacements_made
