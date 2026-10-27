"""Integration tests for application health and readiness endpoints."""

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

    async def test_health_endpoint_configured_model(self) -> None:
        """Verify health check reflects configured model metadata without leaking secrets."""
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
        assert data["model_endpoint"] == "https://vllm.colab.internal/v1"
        assert "sk-secret-token" not in response.text

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
