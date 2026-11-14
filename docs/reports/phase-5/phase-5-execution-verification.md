# Phase 5 — Subprocess Execution & Process Lifecycle Report
## PowerShell Runner Controls, Process Tree Termination & Output Bounding

**Document Reference:** `docs/reports/phase-5/phase-5-execution-verification.md`  
**Phase:** Phase 5 — Shell Execution, Approvals, and Permission Policy  
**Target Environment:** Windows 11 (NTFS), Python 3.14.6, PowerShell 5.1 / 7+  
**Authority:** `2604.14228v3.pdf` (Section 5.4 & 13.1) and `docs/phases.md`  
**Execution Date:** 2026-11-14

---

## 1. PowerShell Runner Architecture & Invocation

The runner (`PowerShellRunner` in `tools/shell.py`) launches subprocesses using the exact command line:
```powershell
powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -Command "<command>"
```
- `-NoProfile`: Completely ignores host PowerShell profile scripts, preventing alias tampering.
- `-NonInteractive`: Prevents hanging on commands requiring interactive console input (e.g. `Read-Host`).
- `-ExecutionPolicy Bypass`: Allows execution of legitimate project build and test scripts regardless of host script-signing policy.

---

## 2. Working Directory Containment Evidence

Working directory validation uses Python canonical realpaths and commonpath containment:
$$\text{commonpath}([\text{workspace.root\_path}, \text{realpath}(\text{cwd})]) == \text{workspace.root\_path}$$

### Test Evidence (`tests/test_shell.py`):
1. **Valid Subfolder:** `sub_project` executes with exit code 0 and location matching the workspace subfolder (`test_cwd_containment_inside_workspace`).
2. **Directory Escape (`..`):** Attempting to escape the workspace raises `ValueError: Target working directory '..' resolves to '...', which is outside the workspace root` (`test_cwd_traversal_outside_workspace_rejected`).
3. **Absolute Escape (`C:\Windows`):** Target directory outside workspace raises `ValueError` (`test_cwd_traversal_outside_workspace_rejected`).
4. **Nonexistent Directory:** Target folder that does not exist raises `ValueError: Target working directory '...' does not exist on disk` (`test_cwd_nonexistent_rejected`).

---

## 3. Environment Sanitization Allowlist

Host environment variables are filtered against `SAFE_ENV_VARS`:
`SYSTEMROOT`, `SYSTEMDRIVE`, `COMSPEC`, `PATH`, `PATHEXT`, `TEMP`, `TMP`, `USERPROFILE`, `LOCALAPPDATA`, `APPDATA`, `ALLUSERSPROFILE`, `PROGRAMDATA`.

- All API keys, secrets, tokens, and sensitive host credentials are excluded from the subprocess environment.
- If a project virtualenv exists (`.venv\Scripts`), it is safely prepended to `PATH` and `VIRTUAL_ENV` is set, ensuring tests run against the project virtual environment.

---

## 4. Exit Code & Output Stream Preservation

- **Success Returncode:** `exit 0` returns integer return code `0` (`test_simple_echo_success`).
- **Failure Returncode:** `exit 42` returns integer return code `42` (`test_nonzero_exit_code_preserved`).
- **Stderr Stream:** Captured independently from stdout (`test_stderr_captured`).
- **Output Bounding & Truncation:** Commands producing output beyond `max_output_chars` (default 65,536) are truncated cleanly with `[Output truncated to 65536 chars]`, setting `truncated = True` (`test_output_truncation_when_exceeding_limit`).

---

## 5. Timeout & Process Tree Termination (`taskkill /F /T /PID`)

On Windows, child processes spawned by PowerShell (e.g. `python -m pytest` or worker scripts) are not killed when only the parent `powershell.exe` process is terminated.

### Implementation:
```python
await asyncio.create_subprocess_exec("taskkill.exe", "/F", "/T", "/PID", str(pid))
```
- `/F`: Forcibly terminates the process.
- `/T`: Recursively terminates all child processes in the process tree.
- `/PID`: Specifies the target process ID.

### Test Evidence (`tests/test_shell.py::test_timeout_and_process_tree_termination`):
- A command running `Start-Sleep -Seconds 10` with a timeout of `0.5s` timed out cleanly.
- `taskkill` terminated the process tree.
- The runner returned `timed_out = True` and exit code `-1`.

---

## 6. Secret Redaction Evidence

The secret redactor (`redact_secrets`) scans stdout and stderr:
1. Replaces explicit configured secrets (`extra_secrets`, such as `AGENT_MODEL_API_KEY`).
2. Replaces standard regex patterns (Bearer tokens, API key headers).
- **Test Evidence:** Input `"Config value is SECRET_API_TOKEN_9999"` returned `"[REDACTED_SECRET]"` with zero literal secret leakage (`test_runner_redacts_configured_secrets`).

---
**Execution Verification Verdict:** PASS — Subprocess runner controls, process-tree termination, and output bounding verified.
