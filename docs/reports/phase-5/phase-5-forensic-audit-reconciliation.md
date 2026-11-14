# Phase 5 — Forensic Security, Architecture & Evidence Reconciliation
## Response to Operator Audit Findings (SEC-01, SEC-02, SEC-03, ARCH-01, SEC-04)

**Document Reference:** `docs/reports/phase-5/phase-5-forensic-audit-reconciliation.md`  
**Execution Date:** 2026-11-14  
**Project:** Chatbot Agent Runtime  
**Status:** `PERMANENT AUDIT RECORD & FORENSIC RECONCILIATION`  

---

## 1. Executive Summary

This document addresses and formally reconciles the five critical security and architectural findings (`SEC-01`, `SEC-02`, `SEC-03`, `ARCH-01`, `SEC-04`) and evidence reporting discrepancies identified during the Phase 5 Operator Audit. 

The core conclusions are:
1. **SEC-01:** Regex-based string splitting is an *outer intent filter*, **not an operating system sandbox or semantic grammar parser**. PowerShell's dynamic language features (aliases, backtick lexical escapes, `.NET` reflection, dynamic script blocks) can easily evade regex pattern matching.
2. **SEC-02:** The runtime is strictly appropriate **only for a trusted, supervised local development environment**. Human approval authorizes execution intent but provides zero kernel/OS-level isolation.
3. **SEC-03:** `-ExecutionPolicy Bypass` removes administrative safety rails to permit `.venv` scripts and package manager wrappers on default Windows systems. While not a security boundary, it can be tightened to `-ExecutionPolicy RemoteSigned -Scope Process` to block unsigned remote scripts with the Mark of the Web.
4. **ARCH-01:** The actual source code in `core/permissions.py` enforces a strict, fail-closed `DENY` for all unclassified commands. The report discrepancy (which mentioned an interactive fallback to `ASK`) has been reconciled: **unknown commands never receive approval prompts; they fail closed immediately to DENY**.
5. **SEC-04:** Exact-action binding binds the **logical tool invocation contract** ($task\_id, tool\_name, canonical\_cwd, args$). It **does not freeze host filesystem state, environment state, or script contents** (TOCTOU).
6. **Evidence Reconciliation:** Environment details are corrected to exact measurements (Node.js `v24.15.0`; Oxlint 10 notices are React Compiler optimization notices).

---

## 2. Forensic Analysis by Finding

### 2.1 SEC-01 — Command Filtering vs. PowerShell Execution Grammar

#### The Operator's Finding:
The evaluator splits command strings on `;`, `&&`, `||`, `|`, `\n` and applies regex hard-denies. PowerShell possesses a much richer grammar (aliases, variables, script blocks, backticks, .NET reflection, dot-sourcing, indirect execution). Testing 33 attack strings does not establish that arbitrary syntax cannot evade the filter.

#### Forensic Verification & Engineering Reality:
The operator's assessment is **100% technically correct and verified**.

Regex-based command string splitting is inherently incapable of parsing PowerShell Abstract Syntax Trees (AST). In PowerShell:
1. **Lexical Escapes (Backticks):** A backtick (`` ` ``) is an escape character. The string `` f`o`r`m`a`t `` or `` r`e`g `` is evaluated by PowerShell as `format` or `reg`, but evades simple regexes like `\bformat\b` or `\breg\b`.
2. **Aliases:** `del`, `rm`, `erase` resolve to `Remove-Item`; `sc` resolves to `Set-Content`; `iex` resolves to `Invoke-Expression`; `kill` resolves to `Stop-Process`.
3. **Dynamic Evaluation & Variables:** `$cmd = "format"; & $cmd` or `& ("for" + "mat")`.
4. **Direct .NET Invocations:** `[System.IO.File]::Delete("C:\...")` or `[System.Diagnostics.Process]::Start("cmd.exe", "/c ...")`.
5. **Script Blocks:** `& { param($p) Remove-Item $p -Recurse } "C:\"`.
6. **Nested Interpreters:** Commands that match `SAFE_DEV_COMMAND_PATTERNS` (e.g. `python.exe`) can execute arbitrary code passed via `-c "import os; os.system('...')"` or scripts that run outside the regexes' purview.

#### Actual Runtime Defense Layer:
In `core/permissions.py`:
- `ALL_HARD_DENY_PATTERNS` acts as an initial coarse filter for blatant destructive patterns.
- Crucially, `evaluate_command` requires a command to match `SAFE_DEV_COMMAND_PATTERNS` (`pytest`, `npm test`, etc.) to even reach the `ASK` state.
- Anything that does NOT match safe development commands fails closed to `DENY`.
- **Limitation:** An attacker embedding arbitrary logic within allowed development tools (e.g., inside test scripts, build configs, or interpreter CLI flags) can execute arbitrary code once approved.
- **Permanent Verdict:** The command filter is a **coarse intent gate**, NOT a security boundary.

---

### 2.2 SEC-02 — Human Approval vs. Unrestricted Subprocess Execution

#### The Operator's Finding:
Approval does not create a sandbox. The process runs under the host user account with full access to host files, network, executables, and user credentials. The design is appropriate only for a trusted, supervised local environment.

#### Forensic Verification & Engineering Reality:
**Confirmed and formally accepted.**

The runtime does not use Windows AppContainers, Windows Sandbox, Docker containers, or Hyper-V microVMs. Once an operator clicks "Approve", the spawned `powershell.exe` process:
1. Inherits the user's OS-level access control list (ACL) rights on the filesystem.
2. Can make unrestricted outbound TCP/UDP network connections.
3. Can access any host resource the logged-in user can access.
4. Can read project files and write within its permitted permissions.

**Mandatory Operational Boundary:**
> [!WARNING]
> The Phase 5 runner is exclusively intended for **trusted, supervised local development environments where the developer executes their own project tests and build tooling**. It is **UNSAFE for evaluating untrusted or adversarial third-party repositories**.

---

### 2.3 SEC-03 — Justification of `ExecutionPolicy Bypass`

#### The Operator's Finding:
`powershell.exe -ExecutionPolicy Bypass` removes execution policy restrictions without providing a sandbox. Investigate whether it is necessary.

#### Forensic Verification & Analysis:
1. **Why it was used:** On default Windows 10/11 client installations, the system-wide PowerShell execution policy is `Restricted`. Under `Restricted`, executing any `.ps1` script file—including `.venv\Scripts\activate.ps1`, `npm.ps1`, `npx.ps1`, `pnpm.ps1`, or test runners packaged as PowerShell scripts—immediately raises `PSSecurityException`.
2. **Microsoft's Definition:** Microsoft's security guidance states that PowerShell execution policy is an *administrative safeguard to prevent users from accidentally clicking scripts*, not an access control boundary. A user can easily bypass it by piping text into PowerShell or using `-EncodedCommand`.
3. **The Risk of `Bypass`:** `Bypass` disables checks even for remote scripts containing the NTFS Alternate Data Stream `Zone.Identifier` (Mark of the Web).
4. **Recommended Tightening:** `-ExecutionPolicy RemoteSigned -Scope Process` is a strictly safer alternative. It allows local scripts (such as virtualenv and package manager wrappers) to run without digital signatures, while blocking unsigned scripts downloaded from untrusted internet zones.

---

### 2.4 ARCH-01 — Reconciliation of Unknown-Command Behavior

#### The Operator's Finding:
Discrepancy across documents: Some text reported unknown commands as `DENY` in headless mode but `ASK` in interactive mode, whereas permission verification claimed fail-closed `DENY`.

#### Forensic Code Inspection (`core/permissions.py`):
```python
# Lines 208-210 in core/permissions.py
logger.warning("Command '%s' was not classified by policy; failing closed to DENY", trimmed)
return PermissionDecision.DENY, "Command is not recognized as an approved development command by policy."
```

#### Resolution:
- **Code Reality:** In the actual Python code, **unclassified commands ALWAYS fail closed to `DENY`**.
- There is NO conditional branch where an unrecognized command returns `ASK`.
- The discrepancy was an artifact of an early design proposal in `phase-5-design.md` that was rejected during implementation in favor of strict fail-closed security.
- **Reconciliation:** All documentation has been updated to state that unclassified commands are rejected immediately with `DENY` and never generate approval requests.

---

### 2.5 SEC-04 — Approval Action Hash Coverage & TOCTOU

#### The Operator's Finding:
The hash binds `task_id`, `tool_name`, `canonical_cwd`, and `arguments`. Execution also depends on parameters outside the hash (executable resolution, environment variables, filesystem state, workspace identity).

#### Forensic Investigation:
In `core/approval.py`:
$$\text{action\_hash} = \text{SHA256}(\text{task\_id} \mathbin{\Vert} \text{tool\_name} \mathbin{\Vert} \text{canonical\_cwd} \mathbin{\Vert} \text{canonical\_arguments\_json})$$

Parameters and conditions outside the hash:
1. **Resolved Executable Path:** Resolves `powershell.exe` from the host `PATH`. If host PATH was tampered with prior to execution, a spoofed binary could be spawned.
2. **Dynamic Environment:** `env` is constructed at execution time from `os.environ` filtering. Any host environment modification between approval and launch is reflected in the subprocess.
3. **Underlying File Content (TOCTOU):** When the operator approves `pytest tests/test_core.py`, the hash validates that the arguments string `{"command": "pytest tests/test_core.py"}` has not been tampered with. However, the *contents of `tests/test_core.py` on disk* could be modified between the moment the approval request is issued and the moment the token is consumed.
4. **Workspace Identity:** The canonical workspace root path is validated via `validate_working_dir`, but the workspace ID is not hashed.

#### Definitive Finding:
The approval hash provides **Call Contract Binding** (ensuring the model or attacker cannot mutate the requested command or arguments between approval issuance and dispatch). It **does not provide File-State or Environment-State Attestation**. This is a necessary and fundamental distinction that is now formally documented.

---

### 2.6 Evidence Inconsistencies Reconciled

1. **Node.js Runtime Version:**
   - *Reported in draft:* `Node.js v20.x`
   - *Actual Host Measurement (`node --version`):* **`v24.15.0`**
2. **Oxlint Warning Identities:**
   - *Reported in draft:* "unused-variable notices in test mocks"
   - *Actual Compiler Output:* **10 React Compiler optimization notices** (`react(set-state-in-effect)` in `CommandPalette.tsx`, `Composer.tsx`, `FileViewerPanel.tsx`, and `App.tsx`; `react(purity)` in `FileViewerPanel.tsx` on `Date.now()`). Unused imports in `FileViewerPanel.tsx` and `App.tsx` were eliminated.
3. **Adversarial Test Suite Scope:**
   - Passing 33 adversarial vectors demonstrates regression resistance against *known specific patterns*. It is **not** equivalent to proving that all potential PowerShell bypasses have been eliminated.

---

## 3. Permanent Accepted Limitations for Phase 5

| Limitation ID | Category | Description | Accepted Operational Scope |
|---|---|---|---|
| **P5-LIMIT-01** | Security Boundary | Subprocess execution uses host OS process without kernel isolation | Trusted, supervised local development only. Unsafe for untrusted/hostile repositories. |
| **P5-LIMIT-02** | Platform Limitation | Symlink creation tests skipped on Windows without Developer Mode | Inherited from Phase 4 freeze. |
| **P5-LIMIT-03** | Grammar Limitation | Command filtering cannot semantically evaluate arbitrary PowerShell grammar | Coarse intent gate only. Safe dev tools can execute arbitrary logic if passed as arguments or scripts. |
| **P5-LIMIT-04** | TOCTOU / Binding | Action hash binds arguments, not filesystem state | Exact parameter binding; does not attest to script file contents on disk at launch. |
| **P5-LIMIT-05** | Execution Policy | `ExecutionPolicy Bypass` removes script safety rails | Permitted for local `.venv` and npm scripts; recommended for future tightening to `RemoteSigned`. |
