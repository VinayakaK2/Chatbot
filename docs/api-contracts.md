# API Contracts & Schemas Documentation

This document describes the HTTP contracts implemented for Phase 0 and Phase 1 of the Coding Agent Runtime.

All endpoints adhere to standardized status codes and Pydantic validation schemas.

---

## 1. System Health & Readiness

### GET `/api/health`
Checks backend service liveness and reports whether model endpoint configuration is present.
Does not expose internal endpoint URLs or infrastructure details to unauthenticated callers.

- **Status Code:** `200 OK`
- **Response Schema (`HealthResponse`):**
```json
{
  "status": "healthy",
  "version": "0.1.0",
  "timestamp": "2026-10-27T11:50:00.000000Z",
  "model_configured": true,
  "model_id": "Qwen/Qwen2.5-Coder-14B-Instruct-AWQ"
}
```

---

### GET `/api/readiness`
Probes the upstream model server (`GET /models`) using a non-generating 5.0-second probe.

- **Status Code:** `200 OK`
- **Response Schema (`ModelReadinessResponse`):**
```json
{
  "status": "ready",
  "model_name": "Qwen/Qwen2.5-Coder-14B-Instruct-AWQ",
  "endpoint": "https://your-tunnel.trycloudflare.com/v1",
  "message": "Successfully connected to vLLM OpenAI-compatible endpoint.",
  "checked_at": "2026-10-27T11:50:01.000000Z"
}
```

**Status States:**
- `ready`: Upstream `/models` returned HTTP 200.
- `unconfigured`: `AGENT_MODEL_BASE_URL` is unset or empty.
- `unreachable`: TCP connection failed, DNS failed, or probe timed out.
- `error`: Unexpected upstream failure.

**Security & Sanitization Policy:**
- The `endpoint` attribute returned by `/api/readiness` is sanitized via `sanitize_endpoint_url()`.
- Embedded credentials (userinfo), query parameters, and URL fragments are stripped.
- If the endpoint string is malformed, lacks an `http://` or `https://` scheme, or has no valid hostname, `[INVALID_URL]` is returned rather than a pseudo-URL.
- URL fragments (`#...`) are strictly prohibited in `AGENT_MODEL_BASE_URL` and rejected during startup validation.

---

## 2. Task Management

### POST `/api/tasks`
Submits a user prompt for execution through the backend model adapter pipeline.

- **Status Code:** `201 Created`
- **Request Schema (`TaskCreateRequest`):**
```json
{
  "prompt": "Write a Python function to compute Fibonacci numbers.",
  "max_tokens": 2048,
  "temperature": 0.2
}
```

**Request Validation:**
- `prompt`: String, non-empty, non-whitespace, 1 to 32,768 characters.
- `max_tokens`: Integer between 1 and 8,192 (optional, default 2048).
- `temperature`: Float between 0.0 and 2.0 (optional, default 0.2).

- **Success Response Schema (`TaskResponse`):**
```json
{
  "task_id": "9b1deb4d-3b7d-4bad-9bdd-2b0d7b3dcb6d",
  "prompt": "Write a Python function to compute Fibonacci numbers.",
  "status": "completed",
  "response_text": "def fibonacci(n):\n    if n <= 1:\n        return n\n    return fibonacci(n-1) + fibonacci(n-2)",
  "finish_reason": "stop",
  "model": "Qwen/Qwen2.5-Coder-14B-Instruct-AWQ",
  "usage": {
    "prompt_tokens": 14,
    "completion_tokens": 36,
    "total_tokens": 50
  },
  "error": null,
  "created_at": "2026-10-27T11:50:00.000000Z",
  "completed_at": "2026-10-27T11:50:03.500000Z"
}
```

- **Unconfigured / Upstream Error Response Schema (`TaskResponse`):**
```json
{
  "task_id": "9b1deb4d-3b7d-4bad-9bdd-2b0d7b3dcb6d",
  "prompt": "Write a Python function...",
  "status": "failed",
  "response_text": null,
  "finish_reason": null,
  "model": null,
  "usage": null,
  "error": "Model endpoint is unconfigured. Set AGENT_MODEL_BASE_URL to connect to vLLM.",
  "created_at": "2026-10-27T11:50:00.000000Z",
  "completed_at": "2026-10-27T11:50:00.005000Z"
}
```

---

### GET `/api/tasks/{task_id}`
Retrieves a previously submitted task by its UUID.

- **Status Code:** `200 OK` (or `404 Not Found`)
- **Response Schema (`TaskResponse`):** Identical to `POST /api/tasks` response.

---

## 3. Error Schemas

### Validation Failure (`422 Unprocessable Content`)
```json
{
  "error": "Validation Error",
  "message": "The request payload failed schema validation.",
  "details": [
    {
      "field": "body -> prompt",
      "message": "Prompt cannot be empty or contain only whitespace characters"
    }
  ]
}
```

### Resource Not Found (`404 Not Found`)
```json
{
  "detail": "Task with ID 'invalid-id' not found."
}
```

### Internal Server Error (`500 Internal Server Error`)
```json
{
  "error": "Internal Server Error",
  "message": "An unexpected error occurred while processing your request."
}
```
*(Internal stack traces are logged server-side and never returned in response bodies.)*
