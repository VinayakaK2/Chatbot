# Phase 4 Security Verification Matrix

**Target Phase:** Phase 4 — Workspace and Filesystem Tools  
**Commit SHA:** `3b620e646637468702efdbee2b61d9ae031dd6d5`  
**Evaluator:** Agent Runtime Security Auditor & Adversarial Challenger  
**Date:** 2026-11-14  

---

## 1. Filesystem Containment & Security Probes

| Criterion / Probe | Threat Mechanism | Implemented Defense Mechanism | Test Command / Reproduction | Result | Severity if Failed |
|:---|:---|:---|:---|:---:|:---:|
| **Relative Traversal** | `../../../../Windows/System32` | `Path.resolve()` + `os.path.commonpath()` validation against `canonical_root` | `pytest tests/test_workspace_security.py -k test_relative_traversal_rejected` | **DEFENDED** | Critical |
| **Absolute POSIX Escape** | `/etc/shadow` or `/var/log` | Strips leading slashes; resolves relative to workspace root | `pytest tests/test_workspace_security.py -k test_leading_slashes_treated_relative` | **DEFENDED** | Critical |
| **Windows Drive Escape** | `C:\Windows\System32` | Resolves canonical target; `commonpath` detects distinct drive/root | `pytest tests/test_workspace_security.py -k test_absolute_path_escape_rejected` | **DEFENDED** | Critical |
| **Drive-Relative Escape** | `C:foo.txt` (no slash) | Windows drive-relative resolution evaluates outside workspace | `python -c "ws.resolve_path('C:foo.txt')"` | **DEFENDED** | High |
| **UNC / Network Shares** | `\\server\share\payload` or `//server/share` | Pre-resolution syntax regex check rejects `\\` and `//` prefixes | `pytest tests/test_workspace_security.py -k test_unc_paths_rejected` | **DEFENDED** | Critical |
| **Alternate Data Streams** | `file.txt:hidden_stream` | Rejects colon (`:`) characters after drive letter specification | `pytest tests/test_workspace_security.py -k test_alternate_data_streams_rejected` | **DEFENDED** | High |
| **Windows Reserved Devices** | `CON`, `PRN`, `AUX`, `NUL`, `COM1-9`, `LPT1-9` | Regex pattern matching against reserved device names across all segments | `pytest tests/test_workspace_security.py -k test_reserved_windows_device_names_rejected` | **DEFENDED** | High |
| **Directory Junctions** | Windows NTFS junction (`mklink /J`) pointing outside | `os.path.realpath()` canonical dereferencing detects junction boundary escape | Executable junction script via `cmd /c mklink /J` | **DEFENDED** | Critical |
| **Symlink Loops** | Symlink pointing to ancestor directory | `os.walk(followlinks=False)` default prevents recursive directory loops | Python recursive walk on recursive junction | **DEFENDED** | Medium |
| **Binary File Injection** | Binary file read into prompt context | `is_binary_file()` checks NULL byte (`\x00`) and control char ratio | `pytest tests/test_workspace_security.py -k test_binary_file_rejection` | **DEFENDED (with caveat F-04)** | Medium |
| **Partial Write Corruption** | Write fails mid-stream or crashes | Writes to `.tmp_write_*` followed by atomic `os.replace()` | `pytest tests/test_workspace_security.py -k test_atomic_write_preserves_original_on_failure` | **DEFENDED** | High |
| **Deep Path Traversal** | Deeply nested directory structure | `Path.resolve()` canonicalization + `commonpath` | `pytest tests/test_workspace_security.py -k test_deeply_nested_paths_within_workspace` | **DEFENDED** | Low |

---

## 2. Security Exceptions & Adversarial Edge Cases

### 2.1 Binary Detection Boundary (Probe F-04)
- **Status:** Potential Misclassification.
- **Details:** `is_binary_file()` samples 4096 bytes. If a file begins with 4096 bytes of plain text and contains binary bytes at byte 4097+, the initial binary check returns `False`. UTF-8 decoding fails, but fallback `latin-1` decoding succeeds, loading raw binary bytes as string text.
- **Remediation Recommendation:** Inspect `raw_bytes` for `\x00` prior to falling back to `latin-1`.

### 2.2 Event Stream Path & Payload Disclosures (Probe F-07)
- **Status:** Confirmed Exposure.
- **Details:** File read contents and write contents are published verbatim to `TOOL_COMPLETED` and `TOOL_REQUESTED` SSE events. In addition, `PathOutsideWorkspaceError` discloses `self._root_path` on the host machine.
- **Remediation Recommendation:** Redact sensitive files and sanitize host paths in client-facing event envelopes.
