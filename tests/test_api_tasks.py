"""Integration tests for task submission contracts and error schemas."""

import pytest
import httpx

from api.app import create_app
from api.schemas.tasks import TaskStatus
from core.config import Settings
from core.task_service import TaskService, set_task_service
from model.contracts import (
    ModelAuthError,
    ModelProvider,
    ModelResponse,
    ModelTimeoutError,
)


class MockModelProvider(ModelProvider):
    """Deterministic mock provider for unit and integration testing."""

    def __init__(
        self,
        response_text: str = "Mocked Qwen response text.",
        should_fail_with: Exception = None,
    ) -> None:
        """Initialize mock provider with static output or simulated failure."""
        self.response_text = response_text
        self.should_fail_with = should_fail_with
        self.invoked = False
        self.last_prompt = None

    async def generate(self, prompt: str, **kwargs) -> ModelResponse:
        """Return standardized mock completion or raise simulated exception."""
        self.invoked = True
        self.last_prompt = prompt
        if self.should_fail_with:
            raise self.should_fail_with
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
        settings = Settings(agent_model_base_url=None)
        set_task_service(TaskService(settings))
        app = create_app(settings)
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
        assert data["completed_at"] is not None

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
        assert data["error"] is None
        assert data["completed_at"] is not None
        assert mock_provider.invoked is True
        assert mock_provider.last_prompt == "Explain dependency injection."

    async def test_task_timeout_error_state(self) -> None:
        """Verify task records failed status when upstream model request times out."""
        timeout_exc = ModelTimeoutError("Upstream model request timed out after 60.0s")
        mock_provider = MockModelProvider(should_fail_with=timeout_exc)
        service = TaskService(Settings(), model_provider=mock_provider)
        set_task_service(service)

        app = create_app(Settings())
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/api/tasks",
                json={"prompt": "Run a very slow query."},
            )

        assert response.status_code == 201
        data = response.json()
        assert data["status"] == "failed"
        assert "timed out" in data["error"].lower()
        assert data["response_text"] is None
        assert data["completed_at"] is not None

    async def test_task_auth_error_state(self) -> None:
        """Verify task records failed status when upstream model rejects credentials."""
        auth_exc = ModelAuthError("Authentication failed with upstream model server (HTTP 401)")
        mock_provider = MockModelProvider(should_fail_with=auth_exc)
        service = TaskService(Settings(), model_provider=mock_provider)
        set_task_service(service)

        app = create_app(Settings())
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/api/tasks",
                json={"prompt": "Test auth failure."},
            )

        assert response.status_code == 201
        data = response.json()
        assert data["status"] == "failed"
        assert "authentication failed" in data["error"].lower()

    async def test_task_lookup_success(self) -> None:
        """Verify task can be queried by ID and matches the submitted task state."""
        mock_provider = MockModelProvider(response_text="Lookup test response.")
        service = TaskService(Settings(), model_provider=mock_provider)
        set_task_service(service)

        app = create_app(Settings())
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            create_resp = await client.post(
                "/api/tasks",
                json={"prompt": "Task for lookup testing."},
            )
            assert create_resp.status_code == 201
            task_id = create_resp.json()["task_id"]

            lookup_resp = await client.get(f"/api/tasks/{task_id}")
            assert lookup_resp.status_code == 200
            lookup_data = lookup_resp.json()
            assert lookup_data["task_id"] == task_id
            assert lookup_data["prompt"] == "Task for lookup testing."
            assert lookup_data["status"] == "completed"
            assert lookup_data["response_text"] == "Lookup test response."

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

    async def test_task_events_history_retrieval(self) -> None:
        """Verify task lifecycle events are recorded and retrievable."""
        mock_provider = MockModelProvider(response_text="Event test answer.")
        service = TaskService(Settings(), model_provider=mock_provider)
        set_task_service(service)

        app = create_app(Settings())
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            create_resp = await client.post("/api/tasks", json={"prompt": "Emit events test"})
            assert create_resp.status_code == 201
            task_id = create_resp.json()["task_id"]

            events_resp = await client.get(f"/api/tasks/{task_id}/events/history")
            assert events_resp.status_code == 200
            events = events_resp.json()
            assert len(events) >= 2
            event_types = [e["event_type"] for e in events]
            assert "task_created" in event_types
            assert "task_terminal" in event_types

    async def test_task_cancel(self) -> None:
        """Verify cancellation endpoint cancels a task."""
        mock_provider = MockModelProvider(response_text="Finished")
        service = TaskService(Settings(), model_provider=mock_provider)
        set_task_service(service)

        app = create_app(Settings())
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            create_resp = await client.post("/api/tasks", json={"prompt": "Task to cancel"})
            task_id = create_resp.json()["task_id"]

            cancel_resp = await client.post(f"/api/tasks/{task_id}/cancel")
            assert cancel_resp.status_code == 200

    async def test_task_tool_approval_and_rejection(self) -> None:
        """Verify tool approval and rejection recording."""
        mock_provider = MockModelProvider(response_text="Done")
        service = TaskService(Settings(), model_provider=mock_provider)
        set_task_service(service)

        app = create_app(Settings())
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            create_resp = await client.post("/api/tasks", json={"prompt": "Approval test"})
            task_id = create_resp.json()["task_id"]

            appr_resp = await client.post(
                f"/api/tasks/{task_id}/approve",
                json={"call_id": "call-123", "reason": "Authorized by user"},
            )
            assert appr_resp.status_code == 200
            assert appr_resp.json()["status"] == "approved"

            rej_resp = await client.post(
                f"/api/tasks/{task_id}/reject",
                json={"call_id": "call-456", "reason": "Too risky"},
            )
            assert rej_resp.status_code == 200
            assert rej_resp.json()["status"] == "rejected"
