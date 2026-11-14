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


class TestSanitizeEndpointUrl:
    """Ensure endpoint sanitization function strictly handles edge cases and never leaks credentials."""

    def test_sanitize_valid_urls(self) -> None:
        """Verify standard HTTP and HTTPS endpoints retain valid path and port."""
        from api.routes.health import sanitize_endpoint_url

        assert sanitize_endpoint_url("http://127.0.0.1:8000/v1") == "http://127.0.0.1:8000/v1"
        assert sanitize_endpoint_url("https://vllm.tunnel.io/v1/") == "https://vllm.tunnel.io/v1"
        assert sanitize_endpoint_url("https://vllm.internal:9000/api") == "https://vllm.internal:9000/api"

    def test_sanitize_strips_credentials_queries_and_fragments(self) -> None:
        """Verify user credentials, queries, and fragments are stripped cleanly."""
        from api.routes.health import sanitize_endpoint_url

        dirty_url = "https://admin:supersecret@tunnel.ngrok.app:8443/v1?token=xyz123#anchor"
        sanitized = sanitize_endpoint_url(dirty_url)
        assert sanitized == "https://tunnel.ngrok.app:8443/v1"
        assert "admin" not in sanitized
        assert "supersecret" not in sanitized
        assert "xyz123" not in sanitized
        assert "anchor" not in sanitized

    @pytest.mark.parametrize(
        "malformed_url",
        [
            "not_a_valid_url",
            "ftp://files.internal/v1",
            "http://",
            "http:///no-host",
            "://corrupt",
        ],
    )
    def test_sanitize_malformed_urls_returns_invalid_marker(self, malformed_url: str) -> None:
        """Verify malformed or unsupported URLs return [INVALID_URL] instead of pseudo-valid URLs."""
        from api.routes.health import sanitize_endpoint_url

        assert sanitize_endpoint_url(malformed_url) == "[INVALID_URL]"

    def test_sanitize_none_and_empty(self) -> None:
        """Verify None or empty strings return None."""
        from api.routes.health import sanitize_endpoint_url

        assert sanitize_endpoint_url(None) is None
        assert sanitize_endpoint_url("") is None
