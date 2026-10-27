# ADR 0001: Modular Runtime Architecture, FastAPI Backend, and OpenAI-Compatible Model Adapter

- **Status:** Accepted
- **Deciders:** Senior Staff Software Engineer, AI-Runtime Architect
- **Date:** 2026-10-27
- **Context:** Agent Runtime Project (Phase 0 & Phase 1)

---

## 1. Decision

1. **Layered Separation of Concerns:**
   We enforce a strict one-way dependency spine:
   `React + TypeScript GUI` → `FastAPI Route Layer` → `Task Service` → `Model Provider Contract` → `vLLM Adapter` → `Colab vLLM Server`.
   The web GUI is the primary user surface; the browser never speaks directly to the upstream model provider.

2. **Model Reasoning vs. Runtime Enforcement Boundary:**
   Aligned with research paper `2604.14228v3.pdf` (Sections 3.1, 4.1, 5.1), model proposals are treated as untrusted data. The deterministic harness validates prompts, enforces timeouts and retries, and normalizes output structures.

3. **Provider-Independent Model Contract:**
   The `ModelProvider` abstract class in `model/contracts.py` decouples the API and task execution layer from specific HTTP clients or server providers. The `VLLMAdapter` implements OpenAI-compatible chat completions with normalized error mapping (`ModelAuthError`, `ModelTimeoutError`, `ModelConnectionError`, `ModelRateLimitError`, `ModelProviderError`, `ModelResponseError`).

4. **Transient-Only Bounded Retries:**
   The adapter implements exponential backoff retries strictly for transient failures (HTTP 503, connection dropped, read timeout) up to a bounded limit (`max_retries <= 2`). Client errors (HTTP 401, 403, 422) and malformed responses fail fast without wasteful retries.

5. **Liveness vs. Model Connectivity Separation:**
   Application health (`/api/health`) reports backend process liveness without performing model generation. Model readiness (`/api/readiness`) performs a non-generating probe (`GET /models`) with a strict 5.0s timeout.

---

## 2. Evidence and Justification

- **Paper `2604.14228v3.pdf` (Pages 6, 8, 11):** Emphasizes that 98.4% of coding agent harnesses consist of operational infrastructure (context shaping, permission boundaries, retry logic) rather than raw AI generation.
- **Paper `2604.14228v3.pdf` (Section 5.1, Page 13):** Highlights that automated safety and deterministic enforcement must not depend on model compliance alone.
- **Specification `phases.md` (Sections 1, 2, Phase 0, Phase 1):** Explicitly mandates web-GUI-first product architecture, no direct browser-to-vLLM calls, and strict modular separation.

---

## 3. Alternatives Considered

1. **Direct Browser Calls to vLLM:**
   - *Rejected:* Exposes private tunnel URLs and auth tokens to client browsers; bypasses backend validation and logging; violates security boundaries.
2. **Monolithic `main.py` Application:**
   - *Rejected:* Mixing routing, configuration, logging, and model adapters in `main.py` creates circular dependencies and impedes independent unit testing.
3. **Heavy Multi-Provider Frameworks (LiteLLM / LangChain):**
   - *Rejected:* Introduces massive dependency trees and unpredictable abstraction layers. A lightweight, type-safe adapter built on `httpx` and `pydantic` gives complete control over timeout, retry, and error normalization.

---

## 4. Trade-offs

- **Pros:**
  - High testability: mock transports and dependency overrides allow 100% deterministic testing without requiring active GPU compute.
  - Zero credential leakage: credentials stay server-side; automated log formatter scrubs secrets.
  - Responsive web GUI: clear loading, success, and actionable failure states across desktop, tablet, and mobile.
- **Cons:**
  - Network hop: browser requests travel through the backend API before reaching vLLM (adds ~5-15ms local latency, negligible compared to model inference time).

---

## 5. Validation

- 30 automated unit and integration tests passing (`tests/test_config.py`, `tests/test_logging.py`, `tests/test_api_health.py`, `tests/test_api_tasks.py`, `tests/test_model_adapter.py`, `tests/test_live_server.py`).
- Frontend production build verified (`tsc -b && vite build` passing with zero errors; `oxlint` passing with zero warnings/errors).
- Secret scrub verified on bundle assets and log formatters.
