"""Integration tests for task submission contracts and error schemas."""

import pytest
import httpx

from api.app import create_app
from core.config import Settings
from core.task_service import TaskService, set_task_service


@pytest.mark.asyncio
class TestTaskEndpoints:
    """Validate task submission contracts and validation boundaries."""

    def setup_method(self) -> None:
        """Initialize fresh task service for test isolation."""
        set_task_service(TaskService())

    async def test_valid_task_submission_phase_0(self) -> None:
        """Verify valid task submission succeeds and returns standardized contract."""
        app = create_app(Settings())
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/api/tasks",
                json={"prompt": "Inspect codebase and check test coverage."},
            )

        assert response.status_code == 201
        data = response.json()
        assert "task_id" in data
        assert data["prompt"] == "Inspect codebase and check test coverage."
        assert data["status"] == "pending"
        assert "Phase 0 Foundation" in data["response_text"]
        assert "created_at" in data

    async def test_empty_prompt_rejected(self) -> None:
        """Reject prompt containing only whitespace with HTTP 422."""
        app = create_app(Settings())
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/api/tasks",
                json={"prompt": "   \n\t  "},
            )

        assert response.status_code == 422
        data = response.json()
        assert data["error"] == "Validation Error"
        assert any("whitespace" in d["message"].lower() for d in data.get("details", []))

    async def test_missing_prompt_rejected(self) -> None:
        """Reject request payload without required prompt field."""
        app = create_app(Settings())
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/api/tasks",
                json={},
            )

        assert response.status_code == 422
        data = response.json()
        assert data["error"] == "Validation Error"

    async def test_oversized_prompt_rejected(self) -> None:
        """Reject prompt exceeding maximum allowed length."""
        oversized = "a" * 32_769
        app = create_app(Settings())
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/api/tasks",
                json={"prompt": oversized},
            )

        assert response.status_code == 422

    async def test_get_nonexistent_task_returns_404(self) -> None:
        """Querying a non-existent task ID returns HTTP 404 with actionable message."""
        app = create_app(Settings())
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/api/tasks/non-existent-uuid-12345")

        assert response.status_code == 404
        data = response.json()
        assert "not found" in data["detail"].lower()
