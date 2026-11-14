# Phase 6 Token Budget & Estimation Forensic Verification

**Reference Standards:** `2604.14228v3.pdf` (§3.6, §7.1, §7.2) and `docs/phases.md` §6  
**Module Audited:** `core/context_manager.py`, `core/context_models.py`, `model/contracts.py`, `model/vllm_adapter.py`  
**Auditor:** Tokenization & Context Budget Specialist  
**Status:** **VERIFIED & REMEDIATED — MATHEMATICAL INVARIANTS & REJECTION HANDLING PROVEN**

---

## 1. Budgeting Formula & Headroom Reservation

The Phase 6 budgeting model is governed by:
$$B_{\text{input}} = M_{\text{ctx}} - M_{\text{out}} - M_{\text{margin}}$$
where:
- $M_{\text{ctx}}$ (`total_context_tokens`) = Total model context window (default: 32,768).
- $M_{\text{out}}$ (`reserved_output_tokens`) = Reserved completion headroom (default: 2,048).
- $M_{\text{margin}}$ (`safety_margin_tokens`) = Safety margin buffer (default: 512).
- $B_{\text{input}}$ (`available_input_tokens`) = $32,768 - 2,048 - 512 = 30,208$ tokens.

### Headroom Protection Verification
- `ContextBudgetConfig.validate_output_reserve` strictly rejects any configuration where $M_{\text{out}} \ge M_{\text{ctx}}$, raising `ValueError`.
- `assemble_context` calculates $B_{\text{input}}$ and enforces that $\sum \text{cost} \le B_{\text{input}}$.
- Output reservation is mathematically and programmatically protected from input consumption.

---

## 2. Token Estimation Heuristic: Implementation vs. Mathematical Specification (F6-01)

### A. The Formal Estimator Formula
`core/context_manager.py`:
$$E(n) = \left\lceil \left( \left\lceil \frac{n}{3.3} \right\rceil + 4 \right) \times 1.15 \right\rceil$$
for string length $n \ge 1$, and $E(0) = 0$ for empty strings or `None`.

Where:
- `ESTIMATOR_CHARS_PER_TOKEN = 3.3`
- `ESTIMATOR_BASE_OVERHEAD = 4`
- `ESTIMATOR_SAFETY_FACTOR = 1.15`

### B. Forensic Reconciliation of Table Discrepancy
The previous audit report table listed approximate token values that did not evaluate $E(n)$ on the exact character counts. The actual implementation in `core/context_manager.py` implements the formula with 100% mathematical fidelity.

| Characters ($n$) | Step 1: $\lceil n / 3.3 \rceil$ | Step 2: $+ 4$ | Step 3: $\times 1.15$ | Formula Expected $E(n)$ | Implementation Output `estimate_tokens()` | Verification Verdict |
| :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **0** | 0 | 0 | 0 | **0** | **0** | **MATCH** |
| **1** | 1 | 5 | 5.75 | **6** | **6** | **MATCH** |
| **129** | 40 | 44 | 50.60 | **51** | **51** | **MATCH** |
| **239** | 73 | 77 | 88.55 | **89** | **89** | **MATCH** |
| **524** | 159 | 163 | 187.45 | **188** | **188** | **MATCH** |
| **1,384** | 420 | 424 | 487.60 | **488** | **488** | **MATCH** |
| **2,833** | 859 | 863 | 992.45 | **993** | **993** | **MATCH** |
| **5,699** | 1727 | 1731 | 1990.65 | **1,991** | **1,991** | **MATCH** |

*Test Evidence:* Covered by `test_exact_formula_mathematical_table_verification` in `tests/test_context_manager.py`.

---

## 3. Tool Output Bounding: Inverse-Bound Math & Postcondition Guarantee (F6-01 & F6-12)

### A. Mathematical Inversion Formula
To guarantee $\text{estimate}(\text{bounded}) \le \text{max\_tokens}$, we invert the token estimator formula:
$$\left( \frac{\text{len}}{3.3} + 4 \right) \times 1.15 \le \text{max\_tokens}$$
$$\frac{\text{len}}{3.3} \le \frac{\text{max\_tokens}}{1.15} - 4$$
$$\text{safe\_token\_ceiling} = \frac{\text{max\_tokens}}{1.15} - 4$$
$$\text{target\_max\_chars} = \max\left(0, \left\lfloor \text{safe\_token\_ceiling} \times 3.3 \right\rfloor\right)$$

Subtracting the maximum truncation marker length (~85 characters plus metadata length):
$$\text{char\_budget} = \max(0, \text{target\_max\_chars} - 85 - \text{len}(\text{meta\_str}))$$

### B. Fail-Closed Boundary Handling & Progressive Trimming
1. **Pre-condition check:** If `safe_token_ceiling <= 0` (e.g. `max_tokens < 5`), raises `OutputBudgetExceededError` fail-closed.
2. **Progressive Trimming:** While `estimate_tokens(bounded) > max_tokens`, tail is progressively trimmed down to 0; if still oversized, head is trimmed down to 0.
3. **Post-condition check:** If the bare truncation marker alone exceeds `max_tokens`, raises `OutputBudgetExceededError` rather than silently returning oversized content.

### C. Empirical Tool Bounding Output vs Configured Cap

Tested across caps [63, 64, 65, 99, 100, 101, 199, 200, 201, 500, 1000, 2000] with 500,000 character inputs and heavy metadata (`exit_code=137`):

| Configured Cap (`max_tokens`) | Original Chars | Bounded Chars | Estimated Tokens | Postcondition Satisfied? |
| :---: | :---: | :---: | :---: | :---: |
| **64** | 500,000 | 146 | **57** | **YES ($\le 64$)** |
| **100** | 500,000 | 250 | **92** | **YES ($\le 100$)** |
| **200** | 500,000 | 537 | **193** | **YES ($\le 200$)** |
| **500** | 500,000 | 1,399 | **493** | **YES ($\le 500$)** |
| **1,000** | 500,000 | 2,834 | **993** | **YES ($\le 1000$)** |
| **2,000** | 500,000 | 5,703 | **1,993** | **YES ($\le 2000$)** |
| **4** | 500,000 | N/A | N/A | **RAISES `OutputBudgetExceededError`** |

*Test Evidence:* Covered by `test_bounded_output_never_exceeds_max_tokens_budget`, `test_boundary_one_unit_below_and_above_cap`, `test_heavy_metadata_and_progressive_trimming`, and `test_impossibly_small_budget_raises_error`.

---

## 4. F6-02: Separation of Application Budgeting, Provider Tokenization, and Rejection Handling

Three concepts must remain strictly separated:

### Concept A: Application Heuristic Budget
- Implemented in `core/context_manager.py`.
- Fast, zero-dependency, character-based upper-bound estimation.
- Purpose: Deterministic context admission, tool bounding, and priority sorting before network dispatch.
- Overestimates English prose and programming languages by $16\%$ to $47\%$.
- **Explicit Limitation:** The 512-token safety margin ($M_{\text{margin}}$) is an application buffer for standard code and English prose; **it is NOT a universal multilingual safety guarantee**. On CJK, Arabic, or heavy emoji sequences, character count underestimates BPE tokens.

### Concept B: Upstream Provider Tokenizer & Context Window
- Implemented in the model server (vLLM / Qwen2.5-Coder BPE tokenizer).
- Counts exact BPE subwords and enforces hard attention-window boundaries (e.g. 32,768 tokens).
- The application cannot determine exact BPE counts without downloading provider-specific tokenizer binaries or executing remote tokenize endpoints.

### Concept C: Provider Rejection Handling & Error Normalization
- Implemented in `model/contracts.py` and `model/vllm_adapter.py`.
- When an upstream provider rejects an oversized request (HTTP 400 or HTTP 413):
  1. `VLLMAdapter` inspects the response body for context-length error phrases (`"maximum context length"`, `"context length exceeded"`).
  2. Maps the error to `ModelContextLengthExceededError(ModelError)`.
  3. Fails immediately without executing wasteful retries (retrying an oversized payload will never succeed).
  4. `AgentLoop` catches `ModelError` and terminates gracefully with `TaskStatus.FAILED` and a clear diagnostic termination reason (`"model_error"`), emitting a `TASK_LIMIT_REACHED` event.
- *Test Evidence:* Verified via `test_context_length_exceeded_raises_specific_error_without_retry` and `test_http_413_payload_too_large_raises_context_length_error` in `tests/test_model_adapter.py`.
