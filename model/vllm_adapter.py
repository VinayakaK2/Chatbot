"""OpenAI-compatible vLLM model provider adapter with bounded retries and error normalization."""

import asyncio
import logging
from typing import Any, Dict, List, Optional
import httpx
from pydantic import SecretStr

from model.contracts import (
    ModelAuthError,
    ModelConnectionError,
    ModelProvider,
    ModelProviderError,
    ModelRateLimitError,
    ModelResponse,
    ModelResponseError,
    ModelTimeoutError,
)
logger = logging.getLogger("agent_runtime.model.vllm_adapter")


class VLLMAdapter(ModelProvider):
    """Adapter for OpenAI-compatible vLLM endpoints serving Qwen models."""

    def __init__(
        self,
        base_url: str,
        model_name: str,
        api_key: Optional[SecretStr | str] = None,
        timeout_seconds: float = 60.0,
        max_retries: int = 2,
        client: Optional[httpx.AsyncClient] = None,
    ) -> None:
        """Initialize vLLM adapter with validated endpoint coordinates."""
        self.base_url = base_url.rstrip("/")
        self.model_name = model_name
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries
        self._injected_client = client

        # Extract raw secret safely
        if isinstance(api_key, SecretStr):
            self._api_key_str = api_key.get_secret_value()
        elif isinstance(api_key, str) and api_key.strip():
            self._api_key_str = api_key.strip()
        else:
            self._api_key_str = None

    def _get_headers(self) -> Dict[str, str]:
        """Construct HTTP headers with authentication if key is present."""
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "ngrok-skip-browser-warning": "true",
        }
        if self._api_key_str:
            headers["Authorization"] = f"Bearer {self._api_key_str}"
        return headers

    def _create_client(self) -> httpx.AsyncClient:
        """Return injected client or instantiate a new AsyncClient with configured timeout."""
        if self._injected_client is not None:
            return self._injected_client
        return httpx.AsyncClient(timeout=self.timeout_seconds)

    async def check_connectivity(self) -> bool:
        """Check if upstream vLLM service is healthy and reachable."""
        probe_url = f"{self.base_url}/models"
        headers = self._get_headers()
        try:
            client = self._create_client()
            should_close = self._injected_client is None
            try:
                resp = await client.get(probe_url, headers=headers)
                return resp.status_code == 200
            finally:
                if should_close:
                    await client.aclose()
        except Exception as exc:
            logger.debug("Connectivity check failed: %s", type(exc).__name__)
            return False

    async def generate(
        self,
        prompt: str,
        max_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
        **kwargs: Any,
    ) -> ModelResponse:
        """Send chat completion prompt to vLLM endpoint with transient retry handling."""
        if not prompt or not prompt.strip():
            raise ModelResponseError("Prompt cannot be empty")

        url = f"{self.base_url}/chat/completions"
        headers = self._get_headers()

        payload: Dict[str, Any] = {
            "model": self.model_name,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens or 2048,
            "temperature": temperature if temperature is not None else 0.2,
        }

        # Multi-turn conversation messages support for Phase 3
        if "messages" in kwargs and kwargs["messages"]:
            raw_messages = kwargs["messages"]
            formatted_messages = []
            for m in raw_messages:
                if hasattr(m, "to_openai_dict"):
                    formatted_messages.append(m.to_openai_dict())
                elif isinstance(m, dict):
                    formatted_messages.append(m)
                elif hasattr(m, "model_dump"):
                    formatted_messages.append(m.model_dump(exclude_none=True))
                else:
                    formatted_messages.append(dict(m))
            payload["messages"] = formatted_messages

        # Include optional tool definitions if provided for future phases
        if "tools" in kwargs and kwargs["tools"]:
            payload["tools"] = kwargs["tools"]

        last_exception: Optional[Exception] = None
        attempt = 0

        while attempt <= self.max_retries:
            attempt += 1
            client = self._create_client()
            should_close = self._injected_client is None

            try:
                logger.info(
                    "Dispatching model completion attempt %d/%d to %s (model=%s)",
                    attempt,
                    self.max_retries + 1,
                    self.base_url,
                    self.model_name,
                )
                resp = await client.post(url, json=payload, headers=headers)

                # HTTP 401 / 403: Credentials rejected (non-transient)
                if resp.status_code in (401, 403):
                    raise ModelAuthError(
                        f"Authentication failed with upstream model server (HTTP {resp.status_code})",
                        status_code=resp.status_code,
                    )

                # HTTP 429: Rate limit (non-transient for immediate retries)
                if resp.status_code == 429:
                    raise ModelRateLimitError(
                        "Upstream model rate limit exceeded (HTTP 429)",
                        status_code=429,
                    )

                # HTTP 5xx: Server side error (may be transient, e.g. 503)
                if resp.status_code >= 500:
                    err_msg = f"Upstream server error (HTTP {resp.status_code})"
                    if resp.status_code == 503 and attempt <= self.max_retries:
                        logger.warning("Upstream 503 Service Unavailable, retrying in 1s...")
                        await asyncio.sleep(1.0 * attempt)
                        continue
                    raise ModelProviderError(err_msg, status_code=resp.status_code)

                # Other non-200 HTTP statuses
                if resp.status_code != 200:
                    raise ModelProviderError(
                        f"Model request failed with unexpected HTTP status {resp.status_code}",
                        status_code=resp.status_code,
                    )

                # Parse JSON payload
                try:
                    data = resp.json()
                except Exception as exc:
                    raise ModelResponseError("Malformed JSON response from model provider") from exc

                return self._normalize_response(data)

            except (httpx.ConnectError, httpx.NetworkError) as exc:
                last_exception = ModelConnectionError(
                    f"Unable to establish connection to model server at {self.base_url}"
                )
                if attempt <= self.max_retries:
                    logger.warning("Network connection blip, retrying attempt %d...", attempt + 1)
                    await asyncio.sleep(0.5 * attempt)
                    continue
                raise last_exception from exc

            except httpx.TimeoutException as exc:
                last_exception = ModelTimeoutError(
                    f"Model completion request timed out after {self.timeout_seconds}s"
                )
                if attempt <= self.max_retries:
                    logger.warning("Request timed out, retrying attempt %d...", attempt + 1)
                    await asyncio.sleep(0.5 * attempt)
                    continue
                raise last_exception from exc

            finally:
                if should_close:
                    await client.aclose()

        if last_exception:
            raise last_exception
        raise ModelProviderError("Failed to complete request after maximum retries.")

    def _normalize_response(self, data: Dict[str, Any]) -> ModelResponse:
        """Extract and normalize completion fields from raw OpenAI-compatible response."""
        if not isinstance(data, dict):
            raise ModelResponseError("Expected JSON object in model response")

        choices = data.get("choices")
        if not choices or not isinstance(choices, list) or len(choices) == 0:
            raise ModelResponseError("Model response did not contain any choices")

        first_choice = choices[0]
        if not isinstance(first_choice, dict):
            raise ModelResponseError("First choice in model response was not a dictionary")

        message = first_choice.get("message", {})
        content = message.get("content") or ""
        finish_reason = first_choice.get("finish_reason")
        tool_calls = message.get("tool_calls")

        # Parse usage stats safely
        raw_usage = data.get("usage")
        usage_dict: Optional[Dict[str, int]] = None
        if isinstance(raw_usage, dict):
            usage_dict = {
                "prompt_tokens": raw_usage.get("prompt_tokens", 0),
                "completion_tokens": raw_usage.get("completion_tokens", 0),
                "total_tokens": raw_usage.get("total_tokens", 0),
            }

        return ModelResponse(
            text=content,
            finish_reason=finish_reason,
            model=data.get("model", self.model_name),
            usage=usage_dict,
            request_id=data.get("id"),
            tool_calls=tool_calls if isinstance(tool_calls, list) else None,
            raw_metadata={
                "created": data.get("created"),
                "system_fingerprint": data.get("system_fingerprint"),
            },
        )
