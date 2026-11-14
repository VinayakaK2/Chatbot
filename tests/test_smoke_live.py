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
    assert response.model == model_name or response.model in model_name or model_name.endswith(response.model)

    print("\n--- LIVE SMOKE TEST SUCCESS ---")
    print(f"Endpoint: {base_url}")
    print(f"Model ID: {response.model}")
    print(f"Elapsed:  {elapsed_seconds:.2f}s")
    print(f"Output:   {response.text.strip()[:100]}...")
    if response.usage:
        print(f"Usage:    {response.usage}")
    print("-------------------------------")


@pytest.mark.asyncio
async def test_live_agent_loop_tool_calling_smoke() -> None:
    """Execute live multi-turn agent execution loop with tool calling if live endpoint is configured."""
    reset_settings()
    settings = get_settings()

    if not settings.is_model_configured or not settings.agent_model_base_url:
        pytest.skip("Live agent loop test skipped: AGENT_MODEL_BASE_URL is unconfigured.")

    from pydantic import BaseModel, Field
    from core.agent_loop import AgentExecutionRequest, AgentLoop
    from core.tool_contracts import ToolDefinition, ToolRiskCategory
    from core.tool_registry import ToolRegistry

    class AddInput(BaseModel):
        a: int = Field(..., description="First integer")
        b: int = Field(..., description="Second integer")

    registry = ToolRegistry()
    registry.register(
        ToolDefinition.from_pydantic(
            name="add_numbers",
            description="Add two integers together. Always call this tool to calculate additions.",
            parameters_model=AddInput,
            risk_category=ToolRiskCategory.READ_ONLY,
            handler=lambda a, b: a + b,
        )
    )

    adapter = VLLMAdapter(
        base_url=settings.agent_model_base_url,
        model_name=settings.agent_model_name or "Qwen/Qwen2.5-Coder-14B-Instruct-AWQ",
        api_key=settings.agent_model_api_key,
        timeout_seconds=settings.agent_model_timeout_seconds,
        max_retries=1,
    )

    loop = AgentLoop(model_provider=adapter, registry=registry)
    req = AgentExecutionRequest(
        task_id="live-tool-smoke",
        prompt="Use the add_numbers tool to calculate 17 + 25. Then answer with the result.",
        max_iterations=5,
        max_tokens=256,
        temperature=0.0,
    )

    start_time = time.perf_counter()
    res = await loop.run(req)
    elapsed = time.perf_counter() - start_time

    print("\n--- LIVE AGENT LOOP SMOKE TEST ---")
    print(f"Status:             {res.status.value}")
    print(f"Iterations:         {res.iterations}")
    print(f"Tool calls count:   {res.tool_calls_count}")
    print(f"Final response:     {res.final_response}")
    print(f"Elapsed:            {elapsed:.2f}s")
    print("----------------------------------")

    assert res.status in ("completed", "limit_reached", "failed")
    if res.status == "completed":
        assert res.final_response is not None and len(res.final_response.strip()) > 0
        assert "42" in res.final_response

