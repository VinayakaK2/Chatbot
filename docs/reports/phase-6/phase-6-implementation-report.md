# Phase 6 Implementation Report — Context Management & Budgeting

## 1. Executive Summary

Phase 6 introduces the **Context Management and Budgeting Subsystem** in the Chatbot Agent Runtime repository.
All implementations have been verified against the research reference `2604.14228v3.pdf` (§2, §3.6, §7.1, §7.2, §7.3, §12) and the Phase 6 specification in `docs/phases.md`.

- **Implementation Status:** COMPLETE
- **Test Evidence:**
  - Backend: 460 passed, 1 skipped, 1 warning (17.94s).
  - Frontend: 50 passed in 10 test files (5.74s).
  - Frontend Build: `tsc -b && vite build` succeeded in 602ms.
  - Frontend Lint: Oxlint 0 errors, 10 non-blocking compiler notices.
  - Phase 6 Test Suites: 21 dedicated tests passing (12 unit tests, 9 adversarial tests).
- **Zero Regressions:** All 439 pre-existing Phase 0–5 tests continue to pass without modification.

---

## 2. Module Implementations

### A. Data Models — `core/context_models.py`
Defines the core taxonomy, priority enum, data structures, and exception hierarchy.

1. **`ContextCategory` (Enum):**
   - Segregates 10 distinct context levels with strict priority values:
     `GLOBAL_INSTRUCTIONS` (1), `PROJECT_INSTRUCTIONS` (2), `CURRENT_USER_INTENT` (3), `TASK_STATE` (4), `SELECTED_AGENT_INSTRUCTIONS` (5), `VERIFICATION_EVIDENCE` (6), `RECENT_HISTORY` (7), `TOOL_OUTPUT` (8), `OLDER_HISTORY` (9), `DERIVED_SUMMARY` (10).
2. **`ContextItem` (Pydantic Model):**
   - Encapsulates an individual context payload with metadata:
     `id: str`, `category: ContextCategory`, `content: str`, `priority: int`, `estimated_tokens: int`, `is_mandatory: bool`, `source_message_id: Optional[str]`, `metadata: Dict[str, Any]`.
3. **`ContextBudgetConfig` (Pydantic Model):**
   - Configurable budget controls:
     - `total_context_tokens: int = 32768` (min 512)
     - `reserved_output_tokens: int = 2048` (min 128)
     - `safety_margin_tokens: int = 512` (min 0)
     - `max_tool_output_tokens: int = 1000` (min 64)
   - Property `available_input_tokens`: Computes $M_{\text{ctx}} - M_{\text{out}} - M_{\text{margin}}$.
4. **`DerivedSummary` (Pydantic Model):**
   - Strict provenance metadata for compacted summaries:
     `summary_id: str`, `source_message_ids: List[str]`, `summary_text: str`, `created_at: datetime`, `is_derived: bool = True`.
5. **`ContextAssemblyDiagnostics` & `ContextAssemblyResult`:**
   - Exposes comprehensive audit records: total estimated tokens, included categories, excluded categories, truncated items count, safety margin, and reserved headroom.
6. **Exception Taxonomy:**
   - `ContextError` (base)
   - `MandatoryContextOverflowError`
   - `InvalidBudgetConfigurationError`
   - `MalformedContextItemError`
   - `ContextAssemblyError`

### B. Core Context Manager — `core/context_manager.py`
Implements the core deterministic assembly, estimation, bounding, and redaction logic.

1. **Conservative Token Estimator (`estimate_tokens`):**
   - Implements $\lceil (\text{len}(s) / 3.3 + 4) \times 1.15 \rceil$.
   - Never claims character counts equal exact tokens.
2. **Tool Output Bounding (`bound_text_content`):**
   - Preserves 40% head and 60% tail of oversized outputs.
   - Embeds explicit truncation marker: `[Output truncated from X chars to Y chars; exit_code=Z]`.
3. **Secret Redactor (`redact_secrets`):**
   - Regex-based filtering of API keys, bearer tokens, passwords, and custom secrets.
4. **Deterministic Assembler (`assemble_context`):**
   - Assembles system instructions (global, project, agent, state).
   - Segregates conversation history into `RECENT_HISTORY` vs `OLDER_HISTORY`.
   - Incorporates derived summaries with provenance headers.
   - Enforces priority sorting during budget allocation.
   - Detects mandatory category overflow and raises `MandatoryContextOverflowError`.
   - Re-sorts admitted items back into **chronological wire order** (`System -> Summaries -> User -> Assistant -> Tool -> Assistant -> User`) to preserve conversational coherence.

### C. Agent Loop Integration — `core/agent_loop.py`
Integrates `ContextManager` into the multi-turn agent execution cycle.

1. **Constructor Updates:**
   - Accepts `context_manager: Optional[ContextManager] = None` and `budget_config: Optional[ContextBudgetConfig] = None`.
   - Defaults to standard `ContextManager()` and `ContextBudgetConfig()`.
2. **Execution Loop Updates:**
   - In `AgentLoop.run()`: Prior to `self.model_provider.generate()`, calls `self.context_manager.assemble_context()`.
   - Dispatches the assembled bounded messages to the model.
   - Catches `MandatoryContextOverflowError`, logs the failure, emits a `LIMIT_REACHED` event, and terminates the loop gracefully with `TaskStatus.LIMIT_REACHED`.
3. **Transcript Preservation:**
   - Appends raw, unmodified model responses and tool results directly to `self.history`.
   - Returns `AgentExecutionResult(history=self.history)` containing the 100% complete, uncompacted transcript.

### D. System Configuration & Task Service
1. **`core/config.py`:**
   - Added `context_window_tokens`, `context_reserve_output_tokens`, `context_safety_margin_tokens`, and `context_max_tool_output_tokens` to `Settings`.
   - Exposed all 4 context bounds in `get_sanitized_diagnostics()`.
2. **`core/task_service.py`:**
   - Instantiates `AgentLoop` with a `ContextBudgetConfig` populated from `self.settings`.
3. **`.env.example`:**
   - Documented all new Phase 4, Phase 5, and Phase 6 environment variables.
4. **`tools/shell.py`:**
   - Enhanced secret detection regex to match hyphenated OpenAI and Anthropic API keys (`sk-ant-...`, `sk-proj-...`).

---

## 3. Source Traceability Matrix

| Requirement from `docs/phases.md` §6 | Implementation File | Verification Test |
| :--- | :--- | :--- |
| Category Segregation (10 categories) | `core/context_models.py` | `test_category_priority_and_segregation` |
| Deterministic Priority Sorting | `core/context_manager.py` | `test_deterministic_ordering_and_priorities` |
| Conservative Token Estimation | `core/context_manager.py` | `test_conservative_token_estimation` |
| Output Bounding with Exit-Code Preservation | `core/context_manager.py` | `test_bound_text_content_head_and_tail` |
| Mandatory Overflow Protection | `core/context_manager.py` | `test_mandatory_overflow_raises_error` |
| Derived Summary Traceable Provenance | `core/context_models.py` | `test_summary_provenance_and_is_derived_flag` |
| Secret Redaction across all Categories | `core/context_manager.py` | `test_secret_redaction_across_all_categories` |
| Original Transcript Immutability | `core/agent_loop.py` | `test_original_transcript_preservation_in_history` |
| Session & Agent Context Isolation | `core/context_manager.py` | `test_concurrent_sessions_do_not_cross_contaminate` |
| Configuration & Diagnostics Exposure | `core/config.py` | `test_context_management_settings_default_and_custom` |
