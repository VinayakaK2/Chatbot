"""Integration tests for application health and readiness endpoints."""

from unittest.mock import patch
import pytest
import httpx

from api.app import create_app
from core.config import Settings


@pytest.mark.asyncio
class TestHealthEndpoints:
    """Validate liveness and model readiness API endpoints."""

    async def test_health_endpoint_unconfigured_model(self) -> None:
        """Verify health check returns healthy status with unconfigured model indicator."""
        settings = Settings(
            AGENT_MODEL_BASE_URL=None,
            AGENT_MODEL_API_KEY=None,
        )
        app = create_app(settings)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/api/health")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"
        assert data["version"] == "0.1.0"
        assert data["model_configured"] is False
        assert data["model_id"] is None
        assert "timestamp" in data
        # Ensure internal endpoint URL is never exposed in health response
        assert "model_endpoint" not in data

    async def test_health_endpoint_configured_model(self) -> None:
        """Verify health check reflects configured model metadata without leaking secrets or URLs."""
        settings = Settings(
            AGENT_MODEL_BASE_URL="https://vllm.colab.internal/v1",
            AGENT_MODEL_API_KEY="sk-secret-token",
            AGENT_MODEL_NAME="Qwen/Qwen2.5-Coder-14B-Instruct-AWQ",
        )
        app = create_app(settings)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/api/health")

        assert response.status_code == 200
        data = response.json()
        assert data["model_configured"] is True
        assert data["model_id"] == "Qwen/Qwen2.5-Coder-14B-Instruct-AWQ"
        # Confirm model_endpoint is completely absent from health check
        assert "model_endpoint" not in data
        assert "sk-secret-token" not in response.text
        assert "vllm.colab.internal" not in response.text

    async def test_readiness_endpoint_unconfigured(self) -> None:
        """Verify readiness endpoint safely returns unconfigured when no endpoint is set."""
        settings = Settings(
            AGENT_MODEL_BASE_URL=None,
        )
        app = create_app(settings)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/api/readiness")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "unconfigured"
        assert data["model_name"] is None
        assert "offline mode" in data["message"]
        assert "checked_at" in data

    async def test_readiness_endpoint_upstream_ready(self) -> None:
        """Verify readiness probe returns ready when upstream /models returns HTTP 200."""
        settings = Settings(
            AGENT_MODEL_BASE_URL="https://vllm.colab.test/v1",
            AGENT_MODEL_NAME="Qwen/Qwen2.5-Coder-14B-Instruct-AWQ",
        )
        app = create_app(settings)

        original_get = httpx.AsyncClient.get

        async def mock_get(client_self, url, **kwargs):
            if "vllm.colab.test" in str(url):
                return httpx.Response(200, json={"object": "list", "data": []})
            return await original_get(client_self, url, **kwargs)

        with patch.object(httpx.AsyncClient, "get", new=mock_get):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.get("/api/readiness")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ready"
        assert data["model_name"] == "Qwen/Qwen2.5-Coder-14B-Instruct-AWQ"
        assert data["endpoint"] == "https://vllm.colab.test/v1"
        assert "Successfully connected" in data["message"]

    async def test_readiness_endpoint_upstream_failure(self) -> None:
        """Verify readiness probe returns unreachable when upstream returns non-200."""
        settings = Settings(
            AGENT_MODEL_BASE_URL="https://vllm.colab.test/v1",
            AGENT_MODEL_NAME="Qwen/Qwen2.5-Coder-14B-Instruct-AWQ",
        )
        app = create_app(settings)

        original_get = httpx.AsyncClient.get

        async def mock_get(client_self, url, **kwargs):
            if "vllm.colab.test" in str(url):
                return httpx.Response(502, text="Bad Gateway")
            return await original_get(client_self, url, **kwargs)

        with patch.object(httpx.AsyncClient, "get", new=mock_get):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.get("/api/readiness")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "unreachable"
        assert "502" in data["message"]

    async def test_readiness_endpoint_probe_timeout(self) -> None:
        """Verify readiness probe handles timeout safely within bounded threshold."""
        settings = Settings(
            AGENT_MODEL_BASE_URL="https://vllm.colab.test/v1",
            AGENT_MODEL_NAME="Qwen/Qwen2.5-Coder-14B-Instruct-AWQ",
        )
        app = create_app(settings)

        original_get = httpx.AsyncClient.get

        async def mock_get(client_self, url, **kwargs):
            if "vllm.colab.test" in str(url):
                raise httpx.TimeoutException("Read timed out")
            return await original_get(client_self, url, **kwargs)

        with patch.object(httpx.AsyncClient, "get", new=mock_get):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.get("/api/readiness")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "unreachable"
        assert "timed out" in data["message"].lower()

    async def test_readiness_endpoint_connection_refused(self) -> None:
        """Verify readiness probe handles connection error safely."""
        settings = Settings(
            AGENT_MODEL_BASE_URL="https://vllm.colab.test/v1",
            AGENT_MODEL_NAME="Qwen/Qwen2.5-Coder-14B-Instruct-AWQ",
        )
        app = create_app(settings)

        original_get = httpx.AsyncClient.get

        async def mock_get(client_self, url, **kwargs):
            if "vllm.colab.test" in str(url):
                raise httpx.ConnectError("Connection refused")
            return await original_get(client_self, url, **kwargs)

        with patch.object(httpx.AsyncClient, "get", new=mock_get):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.get("/api/readiness")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "unreachable"
        assert "connection refused" in data["message"].lower()
