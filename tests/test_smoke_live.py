"""Live model smoke test for Qwen vLLM endpoint when authorized credentials are provided."""

import os
import time
import pytest
from pydantic import SecretStr

from core.config import get_settings, reset_settings
from model.contracts import ModelResponse
from model.vllm_adapter import VLLMAdapter


@pytest.mark.asyncio
async def test_live_chat_completion_smoke() -> None:
    """Execute live chat completion against served Qwen model if live endpoint is configured."""
    reset_settings()
    settings = get_settings()

    if not settings.is_model_configured or not settings.agent_model_base_url:
        pytest.skip(
            "Live model smoke test skipped: AGENT_MODEL_BASE_URL is unconfigured. "
            "To execute: launch colab_vllm_server.ipynb in Colab and export AGENT_MODEL_BASE_URL and AGENT_MODEL_API_KEY."
        )

    base_url = settings.agent_model_base_url
    api_key = settings.agent_model_api_key
    model_name = settings.agent_model_name or "Qwen/Qwen2.5-Coder-14B-Instruct-AWQ"

    adapter = VLLMAdapter(
        base_url=base_url,
        model_name=model_name,
        api_key=api_key,
        timeout_seconds=settings.agent_model_timeout_seconds,
        max_retries=1,
    )

    # 1. Connectivity probe
    is_connected = await adapter.check_connectivity()
    assert is_connected is True, f"Failed to connect to configured model endpoint: {base_url}"

    # 2. Bounded text completion request
    start_time = time.perf_counter()
    response: ModelResponse = await adapter.generate(
        prompt="Write a Python one-liner to reverse a string.",
        max_tokens=64,
        temperature=0.1,
    )
    elapsed_seconds = time.perf_counter() - start_time

    # 3. Assertions on normalized response
    assert response.text is not None and len(response.text.strip()) > 0
    assert response.finish_reason in ("stop", "length")
    assert response.model == model_name

    print("\n--- LIVE SMOKE TEST SUCCESS ---")
    print(f"Endpoint: {base_url}")
    print(f"Model ID: {response.model}")
    print(f"Elapsed:  {elapsed_seconds:.2f}s")
    print(f"Output:   {response.text.strip()[:100]}...")
    if response.usage:
        print(f"Usage:    {response.usage}")
    print("-------------------------------")
