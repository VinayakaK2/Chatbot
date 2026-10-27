"""Integration tests for task submission contracts and error schemas."""

import pytest
import httpx

from api.app import create_app
from api.schemas.tasks import TaskStatus
from core.config import Settings
from core.task_service import TaskService, set_task_service
from model.contracts import ModelProvider, ModelResponse


class MockModelProvider(ModelProvider):
    """Deterministic mock provider for unit and integration testing."""

    def __init__(self, response_text: str = "Mocked Qwen response text.") -> None:
        """Initialize mock provider with static output."""
        self.response_text = response_text
        self.invoked = False
        self.last_prompt = None

    async def generate(self, prompt: str, **kwargs) -> ModelResponse:
        """Return standardized mock completion."""
        self.invoked = True
        self.last_prompt = prompt
        return ModelResponse(
            text=self.response_text,
            finish_reason="stop",
            model="Qwen/Qwen2.5-Coder-14B-Instruct-AWQ",
            usage={"prompt_tokens": 10, "completion_tokens": 15, "total_tokens": 25},
            request_id="mock-request-uuid-1",
        )

    async def check_connectivity(self) -> bool:
        """Simulate healthy endpoint."""
        return True


@pytest.mark.asyncio
class TestTaskEndpoints:
    """Validate task submission contracts and validation boundaries."""

    def setup_method(self) -> None:
        """Reset task service state before each test."""
        set_task_service(TaskService(Settings()))

    async def test_unconfigured_model_task_submission(self) -> None:
        """Verify task submission safely fails with clear message when model is unconfigured."""
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
        assert data["status"] == "failed"
        assert "unconfigured" in data["error"].lower()

    async def test_configured_mock_model_task_submission(self) -> None:
        """Verify task submission succeeds and executes via injected model provider."""
        mock_provider = MockModelProvider(response_text="Generated code analysis from Qwen.")
        service = TaskService(Settings(), model_provider=mock_provider)
        set_task_service(service)

        app = create_app(Settings())
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/api/tasks",
                json={"prompt": "Explain dependency injection."},
            )

        assert response.status_code == 201
        data = response.json()
        assert data["status"] == "completed"
        assert data["response_text"] == "Generated code analysis from Qwen."
        assert data["model"] == "Qwen/Qwen2.5-Coder-14B-Instruct-AWQ"
        assert data["finish_reason"] == "stop"
        assert data["usage"]["total_tokens"] == 25
        assert mock_provider.invoked is True
        assert mock_provider.last_prompt == "Explain dependency injection."

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
