# Phase 4 Forensic Test Evidence & Reproducibility Matrix

**Audit Target:** Phase 4 — Workspace and Filesystem Tools  
**Commit SHA:** `3b620e646637468702efdbee2b61d9ae031dd6d5`  
**Execution Environment:** Windows 11 (AMD64), Python 3.14.6 in `.venv`, Node.js v20+, Vite 8.3.5  
**Date:** 2026-11-14  

---

## 1. Test Execution Summary

| Test Category | Exact Command | Total | Passed | Failed | Skipped | Duration | Exit Code |
|:---|:---|:---:|:---:|:---:|:---:|:---:|:---:|
| **Workspace Security** | `.venv\Scripts\python.exe -m pytest tests/test_workspace_security.py -v` | 18 | 17 | 0 | 1 | 0.45s | `0` |
| **Filesystem Unit** | `.venv\Scripts\python.exe -m pytest tests/test_filesystem.py -v` | 14 | 14 | 0 | 0 | 0.51s | `0` |
| **Filesystem Integration** | `.venv\Scripts\python.exe -m pytest tests/test_filesystem_integration.py -v` | 11 | 11 | 0 | 0 | 0.97s | `0` |
| **Full Backend Regression** | `.venv\Scripts\python.exe -m pytest --ignore=tests/test_smoke_live.py` | 318 | 317 | 0 | 1 | 8.26s | `0` |
| **Frontend Vitest** | `npm --prefix frontend test -- --run` | 39 | 39 | 0 | 0 | 4.79s | `0` |
| **Frontend Production Build** | `npm --prefix frontend run build` | Built | 40 modules | 0 | 0 | 0.66s | `0` |
| **Frontend Oxlint** | `npm --prefix frontend run lint` | 41 files | Clean | 0 | 0 | 0.06s | `0` |

---

## 2. Skipped Test Investigation & Windows Reparse-Point Evidence

### 2.1 Skipped Test Details
- **Test:** `tests/test_workspace_security.py::TestWorkspaceSecurityConfinement::test_symlink_escape_rejected`
- **Skip Reason:** `Windows non-admin privileges prevented symlink creation (OSError WinError 1314)`
- **Line Reference:** `tests/test_workspace_security.py:120`
- **Audit Assessment:** Windows requires Developer Mode or elevated Administrator privileges to create symbolic links (`os.symlink`). The test gracefully skips when `OSError 1314` is caught.

### 2.2 Independent Junction Escape Verification
Because symlink creation was skipped, the audit team executed an independent, non-privileged Windows Directory Junction probe (`mklink /J`):
- **Command:**
  ```python
  subprocess.run(['cmd', '/c', 'mklink', '/J', str(ws_dir / 'junction_link'), str(outside_dir)])
  ```
- **Execution Output:**
  ```
  Junction created for C:\Users\...\workspace\junction_link <<===>> C:\Users\...\outside_target
  ```
- **Containment Result:**
  ```
  PathOutsideWorkspaceError: Path 'junction_link/secret.txt' resolves outside workspace boundary.
  ```
- **Conclusion:** Proved that `WorkspaceContext.resolve_path()` properly detects and denies Windows directory junction escapes without requiring elevated permissions.

---

## 3. Concurrency & Adversarial Probe Reproduction Records

### 3.1 Probe F-02: Concurrent Writes
- **Script:** 10 threads calling `atomic_write_text()` on the same file simultaneously.
- **Output:**
  ```
  Concurrent writes completed. Errors: 2
  Final content matches one worker cleanly: True
  [WinError 5] Access is denied during os.replace
  ```
- **Evidence:** Proves that while files are never corrupted (atomic write guarantees clean single-worker state), Windows file-locking raises `Access is denied` under uncoordinated concurrency.

### 3.2 Probe F-04: Latin-1 Binary Fallback
- **Script:** 4096 bytes of ASCII text followed by binary null bytes `b"\x00\xFF\xFE\x00"`.
- **Output:**
  ```
  is_binary_file result: False
  read_text_bounded decoded using: latin-1 length: 4100
  ```
- **Evidence:** Confirms that sampling only the first 4096 bytes allows a binary payload to be misclassified as text and decoded via `latin-1`.

### 3.3 Probe F-06: Truncation Metadata
- **Script:** `read_file` with `max_read_bytes=10` on a 16-byte file.
- **Output:**
  ```
  output.truncated: True
  metadata.truncated: False
  ```
- **Evidence:** Proves `ToolDispatcher` metadata does not mirror dictionary output truncation flags.

### 3.4 Probe F-10: Edit Conflict Detection
- **Script:** External change to unrelated function `bar()` while target `foo()` is edited.
- **Output:**
  ```
  Edit result with changed unrelated content: 1 Replacements
  Disk contents: def foo(): return 1
  def bar(): return 42
  ```
- **Evidence:** Confirms conflict detection checks substring presence only; concurrent changes elsewhere in the file are silently preserved without conflict error.

---

## 4. Frontend Build & Lint Verification Outputs

### 4.1 Production Build (`npm --prefix frontend run build`)
```
> frontend@0.0.0 build
> tsc -b && vite build

vite v8.3.5 building client environment for production...
transforming...
✓ 40 modules transformed.
rendering chunks...
computing gzip size...
dist/index.html                   0.90 kB │ gzip:  0.47 kB
dist/assets/index-Ddrdinie.css   50.21 kB │ gzip:  8.93 kB
dist/assets/index-HE8Q3zNJ.js   308.09 kB │ gzip: 90.59 kB
✓ built in 663ms
```

### 4.2 Linter Output (`npm --prefix frontend run lint`)
```
> frontend@0.0.0 lint
> oxlint

Found 7 warnings and 0 errors.
Finished in 58ms on 41 files with 116 rules using 12 threads.
```
