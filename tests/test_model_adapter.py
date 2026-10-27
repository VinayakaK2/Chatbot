"""Comprehensive tests for vLLM / OpenAI-compatible adapter normalization and failure handling."""

import json
import httpx
import pytest
from pydantic import SecretStr

from model.contracts import (
    ModelAuthError,
    ModelConnectionError,
    ModelProviderError,
    ModelRateLimitError,
    ModelResponseError,
    ModelTimeoutError,
)
from model.vllm_adapter import VLLMAdapter


@pytest.mark.asyncio
class TestVLLMAdapterSuccess:
    """Verify response normalization for valid upstream vLLM payloads."""

    async def test_successful_response_normalized(self) -> None:
        """Ensure standard chat completion response maps to normalized ModelResponse."""
        raw_response = {
            "id": "chatcmpl-test-12345",
            "object": "chat.completion",
            "created": 1729000000,
            "model": "Qwen/Qwen2.5-Coder-14B-Instruct-AWQ",
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": "def add(a, b):\n    return a + b\n",
                    },
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": 15,
                "completion_tokens": 12,
                "total_tokens": 27,
            },
            "system_fingerprint": "fp_vllm_1",
        }

        def mock_handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path == "/v1/chat/completions"
            body = json.loads(request.content)
            assert body["model"] == "Qwen/Qwen2.5-Coder-14B-Instruct-AWQ"
            assert body["messages"][0]["content"] == "Write an add function."
            return httpx.Response(200, json=raw_response)

        client = httpx.AsyncClient(transport=httpx.MockTransport(mock_handler))
        adapter = VLLMAdapter(
            base_url="https://vllm.colab.test/v1",
            model_name="Qwen/Qwen2.5-Coder-14B-Instruct-AWQ",
            api_key=SecretStr("sk-test-token"),
            client=client,
        )

        result = await adapter.generate(prompt="Write an add function.")
        assert result.text == "def add(a, b):\n    return a + b\n"
        assert result.finish_reason == "stop"
        assert result.model == "Qwen/Qwen2.5-Coder-14B-Instruct-AWQ"
        assert result.request_id == "chatcmpl-test-12345"
        assert result.usage == {"prompt_tokens": 15, "completion_tokens": 12, "total_tokens": 27}
        assert result.raw_metadata["system_fingerprint"] == "fp_vllm_1"

    async def test_tool_call_payload_preserved_unexecuted(self) -> None:
        """Ensure tool_calls payload emitted by vLLM is parsed and preserved in metadata."""
        tool_call_payload = [
            {
                "id": "call_12345",
                "type": "function",
                "function": {
                    "name": "read_file",
                    "arguments": "{\"path\": \"main.py\"}",
                },
            }
        ]
        raw_response = {
            "id": "chatcmpl-tool-test",
            "model": "Qwen/Qwen2.5-Coder-14B-Instruct-AWQ",
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": tool_call_payload,
                    },
                    "finish_reason": "tool_calls",
                }
            ],
            "usage": {"prompt_tokens": 20, "completion_tokens": 30, "total_tokens": 50},
        }

        client = httpx.AsyncClient(transport=httpx.MockTransport(lambda req: httpx.Response(200, json=raw_response)))
        adapter = VLLMAdapter(
            base_url="https://vllm.colab.test/v1",
            model_name="Qwen/Qwen2.5-Coder-14B-Instruct-AWQ",
            client=client,
        )

        result = await adapter.generate(prompt="Read main.py")
        assert result.text == ""  # Content was None
        assert result.finish_reason == "tool_calls"
        assert result.tool_calls is not None
        assert len(result.tool_calls) == 1
        assert result.tool_calls[0]["id"] == "call_12345"

    async def test_missing_optional_fields_handled_safely(self) -> None:
        """Handle responses where usage, id, or finish_reason are missing."""
        minimal_response = {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": "Minimal response",
                    }
                }
            ]
        }

        client = httpx.AsyncClient(transport=httpx.MockTransport(lambda req: httpx.Response(200, json=minimal_response)))
        adapter = VLLMAdapter(
            base_url="https://vllm.colab.test/v1",
            model_name="Qwen/Qwen2.5-Coder-14B-Instruct-AWQ",
            client=client,
        )

        result = await adapter.generate(prompt="Hello")
        assert result.text == "Minimal response"
        assert result.finish_reason is None
        assert result.usage is None
        assert result.request_id is None
        assert result.tool_calls is None


@pytest.mark.asyncio
class TestVLLMAdapterFailures:
    """Verify error categorization and defensive boundaries on provider failures."""

    async def test_authentication_failure_401_raises_model_auth_error(self) -> None:
        """HTTP 401 returns ModelAuthError."""
        client = httpx.AsyncClient(
            transport=httpx.MockTransport(lambda req: httpx.Response(401, text="Unauthorized"))
        )
        adapter = VLLMAdapter("https://vllm.test/v1", "Qwen", max_retries=0, client=client)

        with pytest.raises(ModelAuthError) as exc_info:
            await adapter.generate("Test prompt")
        assert "Authentication failed" in exc_info.value.message
        assert exc_info.value.status_code == 401

    async def test_rate_limit_429_raises_model_rate_limit_error(self) -> None:
        """HTTP 429 returns ModelRateLimitError."""
        client = httpx.AsyncClient(
            transport=httpx.MockTransport(lambda req: httpx.Response(429, text="Rate limit exceeded"))
        )
        adapter = VLLMAdapter("https://vllm.test/v1", "Qwen", max_retries=0, client=client)

        with pytest.raises(ModelRateLimitError) as exc_info:
            await adapter.generate("Test prompt")
        assert "rate limit" in exc_info.value.message.lower()
        assert exc_info.value.status_code == 429

    async def test_server_error_500_raises_model_provider_error(self) -> None:
        """HTTP 500 returns ModelProviderError."""
        client = httpx.AsyncClient(
            transport=httpx.MockTransport(lambda req: httpx.Response(500, text="Internal Server Error"))
        )
        adapter = VLLMAdapter("https://vllm.test/v1", "Qwen", max_retries=0, client=client)

        with pytest.raises(ModelProviderError) as exc_info:
            await adapter.generate("Test prompt")
        assert "500" in exc_info.value.message

    async def test_connection_error_raises_model_connection_error(self) -> None:
        """Network drop or DNS failure raises ModelConnectionError."""
        def handler(req: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("Connection refused")

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        adapter = VLLMAdapter("https://vllm.test/v1", "Qwen", max_retries=0, client=client)

        with pytest.raises(ModelConnectionError) as exc_info:
            await adapter.generate("Test prompt")
        assert "Unable to establish connection" in exc_info.value.message

    async def test_timeout_raises_model_timeout_error(self) -> None:
        """Exceeded timeout threshold raises ModelTimeoutError."""
        def handler(req: httpx.Request) -> httpx.Response:
            raise httpx.TimeoutException("Read timed out")

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        adapter = VLLMAdapter("https://vllm.test/v1", "Qwen", timeout_seconds=10.0, max_retries=0, client=client)

        with pytest.raises(ModelTimeoutError) as exc_info:
            await adapter.generate("Test prompt")
        assert "timed out after 10.0s" in exc_info.value.message

    async def test_malformed_json_raises_model_response_error(self) -> None:
        """Non-JSON response payload raises ModelResponseError."""
        client = httpx.AsyncClient(
            transport=httpx.MockTransport(lambda req: httpx.Response(200, text="<html>502 Bad Gateway</html>"))
        )
        adapter = VLLMAdapter("https://vllm.test/v1", "Qwen", max_retries=0, client=client)

        with pytest.raises(ModelResponseError) as exc_info:
            await adapter.generate("Test prompt")
        assert "Malformed JSON" in exc_info.value.message

    async def test_empty_choices_raises_model_response_error(self) -> None:
        """Response with empty choices list raises ModelResponseError."""
        client = httpx.AsyncClient(
            transport=httpx.MockTransport(lambda req: httpx.Response(200, json={"choices": []}))
        )
        adapter = VLLMAdapter("https://vllm.test/v1", "Qwen", max_retries=0, client=client)

        with pytest.raises(ModelResponseError) as exc_info:
            await adapter.generate("Test prompt")
        assert "did not contain any choices" in exc_info.value.message

    async def test_transient_503_retry_succeeds_on_second_attempt(self) -> None:
        """Transient 503 error is retried and succeeds on subsequent attempt."""
        call_count = 0

        def handler(req: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return httpx.Response(503, text="Service Temporarily Unavailable")
            return httpx.Response(200, json={
                "choices": [{"message": {"role": "assistant", "content": "Recovered successfully!"}}]
            })

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        adapter = VLLMAdapter("https://vllm.test/v1", "Qwen", max_retries=2, client=client)

        result = await adapter.generate("Test retry")
        assert result.text == "Recovered successfully!"
        assert call_count == 2
