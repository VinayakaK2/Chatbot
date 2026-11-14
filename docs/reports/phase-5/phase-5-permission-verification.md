# Phase 5 — Permission Policy Verification Report
## Precedence Rules, Hard-Deny Catalogs & Fail-Closed Behavior

**Document Reference:** `docs/reports/phase-5/phase-5-permission-verification.md`  
**Phase:** Phase 5 — Shell Execution, Approvals, and Permission Policy  
**Target Environment:** Windows 11, Python 3.14.6  
**Applicable Research Foundation:** `2604.14228v3.pdf` (Section 5.1 & 5.2)  
**Execution Date:** 2026-11-14

---

## 1. Precedence Rule Verification (`DENY > ASK > ALLOW`)

In compliance with `2604.14228v3.pdf` Section 5.1, the permission engine evaluates requests through an immutable precedence hierarchy:

```mermaid
flowchart TD
    Req["Incoming Tool Request (Tool, Args, CWD)"] --> HardDeny{"Matches Hard-Deny Rule?"}
    HardDeny -- "YES" --> ResultDeny["DENY (Immediate Rejection)"]
    HardDeny -- "NO" --> CategoryCheck{"Risk Category Assessment"}
    
    CategoryCheck -- "READ_ONLY / SYSTEM_PROBE / WORKSPACE_WRITE" --> Allow["ALLOW (Auto-Permitted)"]
    CategoryCheck -- "DESTRUCTIVE" --> ResultDeny
    CategoryCheck -- "COMMAND_EXECUTION" --> DevMatcher{"Matches Recognized Dev Tool?"}

    DevMatcher -- "YES (pytest, npm, git, python)" --> Ask["ASK (Approval Gate)"]
    DevMatcher -- "NO (Unclassified / Unknown)" --> FailClosed["DENY (Fail-Closed Default)"]
```

### 1.1 Inviolable Hard-Deny Invariant
- **Rule:** A hard-deny decision is absolute. It cannot be overridden by an allow rule, an approval response from the UI, or a model prompt.
- **Verification:** Tested in `tests/test_shell.py::test_dispatcher_hard_denied_command_blocked_immediately`. When `format C:` was dispatched to `ToolDispatcher`, execution was blocked immediately with an error envelope (`permission_denied`), and zero approval requests were recorded in `ApprovalManager` (`assert len(approval_mgr._requests) == 0`).

---

## 2. Hard-Deny Pattern Catalog Verification (8 Categories)

The 8 prohibited categories defined in `core/permissions.py` were verified against 25 distinct attack payloads in `tests/test_permissions.py`:

| Catalog Name | Covered Attack Vectors | Test Count | Result |
|:---|:---|:---|:---|
| **1. Storage Destruction** | `format`, `Format-Volume`, `diskpart`, `fsutil`, `bcdedit` | 6 tests | 100% BLOCKED |
| **2. Root Filesystem Destruction**| `rmdir /s /q C:\`, `Remove-Item -Recurse -Force`, `del /f /s /q` | 5 tests | 100% BLOCKED |
| **3. Windows Registry Tampering** | `reg add`, `reg delete`, `regedit`, `Set-ItemProperty HKLM:` | 5 tests | 100% BLOCKED |
| **4. System Power & Reboot** | `shutdown`, `Restart-Computer`, `Stop-Computer` | 4 tests | 100% BLOCKED |
| **5. Security & Privilege Tampering**| `takeown`, `icacls`, `runas`, `vssadmin delete shadows` | 4 tests | 100% BLOCKED |
| **6. Credential Theft & Dumps** | `mimikatz`, `procdump lsass`, `Get-Credential`, `ntdsutil` | 5 tests | 100% BLOCKED |
| **7. Obfuscated & Indirect Exec**| `powershell -enc`, `FromBase64String`, `iex`, `certutil -urlcache` | 8 tests | 100% BLOCKED |
| **8. Network Exfiltration** | `curl ... \| iex`, `nc -l`, `ncat` | 4 tests | 100% BLOCKED |

---

## 3. Fail-Closed Default Policy Verification

Commands that do not match safe development patterns (`pytest`, `npm`, `git`, `python`, etc.) default to `DENY`:
- `unknown_utility.exe --flags` $\rightarrow$ `PermissionDecision.DENY`
- `some_script.bat` $\rightarrow$ `PermissionDecision.DENY`
- `curl -O https://example.com/file.zip` $\rightarrow$ `PermissionDecision.DENY`
- `powershell -WindowStyle Hidden` $\rightarrow$ `PermissionDecision.DENY`

Verified in `tests/test_permissions.py::TestCommandPermissionEvaluatorFailClosed`.

---

## 4. Tool-Level Category Evaluation

Verified in `tests/test_permissions.py::TestToolPermissionEvaluation`:
- `ToolRiskCategory.READ_ONLY`: Evaluates to `ALLOW` for safe file inspection.
- `ToolRiskCategory.WORKSPACE_WRITE`: Evaluates to `ALLOW` for contained workspace file modification.
- `ToolRiskCategory.DESTRUCTIVE`: Evaluates strictly to `DENY`.
- `ToolRiskCategory.COMMAND_EXECUTION`: Delegates to `evaluate_command`, ensuring shell command inspection occurs before execution.

---
**Permission Verification Verdict:** PASS — 100% of permission evaluation criteria verified.
