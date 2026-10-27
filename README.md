# Coding Agent Runtime

A modular, web-GUI-first coding agent runtime architecture separating model decisions from deterministic runtime enforcement.

- **Architecture Reference:** `2604.14228v3.pdf` ("Dive into Claude Code: The Design Space of Today's and Future AI Agent Systems")
- **Implementation Plan:** `docs/phases.md`
- **Target Model:** `Qwen/Qwen2.5-Coder-14B-Instruct-AWQ`
- **Model Serving Pipeline:** Google Colab → vLLM → OpenAI-compatible API → Python Backend → React GUI

---

## Architecture Overview

```text
React + TypeScript GUI (Port 5173)
          |  (HTTP API / Local Proxy)
          v
FastAPI Routes (/api/health, /api/readiness, /api/tasks)
          |
          v
     Task Service
          |
          v
Model Provider Interface (core/model_provider.py)
          |
          v
OpenAI-Compatible Adapter (core/vllm_adapter.py)
          |
          v
   vLLM Endpoint (Colab Tunnel)
```

**Security Boundaries:**
- The model proposes actions; the deterministic runtime validates and enforces all limits.
- The browser GUI never communicates directly with the external model endpoint.
- Model credentials and URLs remain on the server and are scrubbed from diagnostic outputs and logs.

---

## Prerequisites

- **Python:** 3.11+ (installed via Python launcher or virtualenv)
- **Node.js:** 20+ (Node v24.15.0 and npm 11.12.1 tested)
- **Package Managers:** `uv` for Python virtual environment; `npm` for frontend

---

## Quick Start (Windows / PowerShell)

### 1. Environment & Backend Setup

From repository root (`H:\chatbot`):

```powershell
# Install backend dependencies
uv pip install -e .

# Run automated backend test suite
uv run pytest
```

### 2. Frontend Installation & Build

```powershell
# Install frontend npm dependencies
npm --prefix frontend install

# Build frontend production bundle
npm --prefix frontend run build
```

### 3. Launching Services

**Terminal 1 — Backend API Server:**
```powershell
uv run python main.py
# Server binds to http://127.0.0.1:8000
```

**Terminal 2 — Frontend GUI Development Server:**
```powershell
npm --prefix frontend run dev
# Vite runs at http://localhost:5173 with proxy to backend
```

---

## Configuration

Copy `.env.example` to `.env` (or configure PowerShell environment variables):

```powershell
$env:HOST = "127.0.0.1"
$env:PORT = "8000"
$env:AGENT_MODEL_BASE_URL = "https://your-tunnel.trycloudflare.com/v1"
$env:AGENT_MODEL_API_KEY = "sk-agent-runtime-colab-sec"
$env:AGENT_MODEL_NAME = "Qwen/Qwen2.5-Coder-14B-Instruct-AWQ"
```

If `AGENT_MODEL_BASE_URL` is omitted, the runtime operates safely in unconfigured/offline mode without failing startup.

---

## Verification Commands

| Component | Command | Expected Result |
|---|---|---|
| Backend Tests | `uv run pytest` | All 18+ tests pass |
| Frontend Build | `npm --prefix frontend run build` | Zero TypeScript/build errors |
| Health Probe | `Invoke-RestMethod http://127.0.0.1:8000/api/health` | HTTP 200 with health schema |
| Readiness Probe | `Invoke-RestMethod http://127.0.0.1:8000/api/readiness` | HTTP 200 with connectivity state |
| Task Submission | `Invoke-RestMethod http://127.0.0.1:8000/api/tasks -Method Post -Body '{"prompt":"Hello"}' -ContentType "application/json"` | HTTP 201 with TaskResponse |
