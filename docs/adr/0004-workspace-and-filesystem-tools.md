# ADR 0004: Bounded Workspace Confinement, Path Security, and Atomic Filesystem Tools

- **Status:** Accepted
- **Deciders:** Senior Staff Software Engineer, Runtime Architect, Windows Filesystem Specialist, Security Engineer
- **Date:** 2026-11-14
- **Context:** Agent Runtime Project (Phase 4 — Workspace and Filesystem Tools)
- **Authority:** `sources/2604.14228v3.pdf` (§3.5, §5.1, §5.4), `docs/phases.md` (§Phase 4, Lines 350–390), `AGENTS.md`

---

## 1. Decision

1. **Explicit Configured Workspace Boundary (`WorkspaceContext`):**
   In adherence to `docs/phases.md` §7, the runtime operates against a single configured, canonical workspace root. The current process working directory is never assumed to be the workspace. All path operations are anchored to this explicit root.

2. **Defense-in-Depth Path Security & Windows Confinement:**
   A path that lexically appears within the workspace can escape via symbolic links, directory junctions, alternate data streams, or Windows device names. Therefore:
   - **Canonical Resolution:** All paths are resolved using strict canonical resolution (`Path.resolve()` / `os.path.realpath`) after combining with the workspace root.
   - **Containment Check:** A resolved target path $R$ is permitted if and only if $R$ is equal to the canonical workspace root $W$ or $R$ is relative to $W$ (`os.path.commonpath([W, R]) == str(W)`).
   - **Windows Device Name Defense:** Reserved DOS/Windows device names (`CON`, `PRN`, `AUX`, `NUL`, `COM1`–`COM9`, `LPT1`–`LPT9` with or without file extensions) are strictly rejected before filesystem touch.
   - **Alternate Data Stream (ADS) Defense:** Colons in file paths (`file.txt:stream` or `::$DATA`) are rejected to prevent hidden stream execution or NTFS attribute smuggling.
   - **UNC & Drive Escapes:** UNC paths (`\\server\share`) and paths attempting to span different Windows drive letters are trapped and denied.
   - **Symlink & Junction Traversal:** Symlinks and directory junctions pointing outside the workspace boundary are caught during canonical resolution and denied with `path_outside_workspace`.

3. **Eight Standardized Workspace Capabilities:**
   The action surface is partitioned into 6 read-only discovery tools and 2 controlled code modification tools:
   - **Discovery (Risk: `READ_ONLY`):**
     - `list_directory`: Lists directory entries with file type, size, modification timestamp, bounded by `max_entries`.
     - `read_file`: Bounded text reading with multi-encoding fallback (UTF-8, UTF-8-BOM, Latin-1) and binary inspection.
     - `read_file_range`: Reads specified 1-indexed line ranges `[start_line, end_line]`.
     - `get_file_metadata`: Reports file size, modified time, line count, permissions, and binary status.
     - `find_files`: Glob-based file discovery with automatic exclusion of build and dependency caches (`.git`, `node_modules`, `__pycache__`, `.venv`).
     - `search_files`: Regex and substring content search returning matching file paths, line numbers, and line text, bounded by `max_results`.
   - **Code Changes (Risk: `WORKSPACE_WRITE`):**
     - `write_file`: Atomic file creation / overwrite with automatic parent directory creation inside the workspace.
     - `edit_file`: Precise search-and-replace modification (`old_str` $\rightarrow$ `new_str`) with conflict detection against stale file contents and reviewable unified diff generation.

4. **Atomic Write Safety & Failure Invariants:**
   File writes must never leave a partially written or corrupted file on disk if an error (disk full, encoding failure, permission failure, cancellation) occurs.
   - Writes write first to a temporary file (`.tmp_*`) in the target directory (same filesystem/drive) and use atomic replacement (`os.replace`) to swap the target.
   - If an error occurs prior to swap, the temporary file is unlinked and the original file remains unmutated.

5. **Conflict-Safe Editing with Reviewable Diffs:**
   `edit_file` enforces optimistic concurrency / content matching. If `old_str` is not present in the current file on disk (indicating that the file was modified concurrently or the model reasoned over stale contents), the edit is rejected with `edit_conflict` without mutating the file. Every successful edit produces a standard unified diff in the `ToolResultEnvelope.metadata` for auditability and UI rendering.

6. **Bounded Resource Limits:**
   To protect context budgets and memory, all operations enforce strict, configurable limits:
   - File size ceiling (default: 10 MB)
   - Maximum read payload (default: 256 KB)
   - Maximum directory listing entries (default: 1,000)
   - Maximum search results (default: 200)
   - Maximum write payload (default: 5 MB)

---

## 2. Alternatives Considered

1. **Using Operating-System Subprocesses (`dir`, `grep`, `Get-ChildItem`):**
   *Rejected:* Violates Phase 4 boundary. Shell execution belongs in Phase 5 and introduces platform-dependent shell injection, encoding ambiguities, and escape risks. Python native standard library I/O provides deterministic, cross-platform, testable behavior.
2. **String Prefix Path Containment:**
   *Rejected:* `str(path).startswith(str(workspace_root))` is vulnerable to prefix confusion (`C:\workspace` vs `C:\workspace_secret`) and completely blind to symlinks, junctions, and `..` segments. Canonical path resolution with `commonpath` is mandatory.
3. **In-Place File Overwriting without Temp Files:**
   *Rejected:* An I/O error or abrupt process kill halfway through `f.write()` permanently destroys the original file content. Atomic replacement guarantees all-or-nothing writes.

---

## 3. Verification & Compliance

- Satisfies all Phase 4 acceptance criteria in `docs/phases.md`.
- Conforms to the layered harness philosophy of `sources/2604.14228v3.pdf`.
- All tools return standardized `ToolResultEnvelope` instances with structured error categories and metadata.
