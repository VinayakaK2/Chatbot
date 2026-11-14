"""Deterministic test fakes, mocks, and scripted test doubles for agent runtime testing."""

import asyncio
from typing import Any, Callable, Dict, List, Optional, Union
import uuid

from model.contracts import (
    ModelError,
    ModelMessage,
    ModelProvider,
    ModelResponse,
)


class FakeModelProvider(ModelProvider):
    """Scripted deterministic model provider for multi-turn execution loop tests."""

    def __init__(
        self,
        responses: Optional[List[Union[ModelResponse, Exception, str]]] = None,
        default_model: str = "fake-qwen-coder",
        delay_seconds: float = 0.0,
    ) -> None:
        """Initialize fake provider with a queue of scripted responses or exceptions."""
        self.responses: List[Union[ModelResponse, Exception, str]] = list(responses or [])
        self.default_model = default_model
        self.delay_seconds = delay_seconds
        self.calls: List[Dict[str, Any]] = []

    def queue_text_response(
        self,
        text: str,
        finish_reason: str = "stop",
        usage: Optional[Dict[str, int]] = None,
    ) -> None:
        """Queue a standard text completion response."""
        self.responses.append(
            ModelResponse(
                text=text,
                finish_reason=finish_reason,
                model=self.default_model,
                usage=usage or {"prompt_tokens": 10, "completion_tokens": 15, "total_tokens": 25},
            )
        )

    def queue_tool_call_response(
        self,
        tool_name: str,
        arguments: Union[Dict[str, Any], str],
        call_id: Optional[str] = None,
        text: str = "",
        finish_reason: str = "tool_calls",
    ) -> None:
        """Queue a model response proposing a single tool call."""
        cid = call_id or f"call_{uuid.uuid4().hex[:8]}"
        args_str = arguments if isinstance(arguments, str) else json_dumps_safe(arguments)
        self.responses.append(
            ModelResponse(
                text=text,
                finish_reason=finish_reason,
                model=self.default_model,
                tool_calls=[
                    {
                        "id": cid,
                        "type": "function",
                        "function": {
                            "name": tool_name,
                            "arguments": args_str,
                        },
                    }
                ],
                usage={"prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30},
            )
        )

    def queue_multi_tool_call_response(
        self,
        calls: List[Dict[str, Any]],
        text: str = "",
        finish_reason: str = "tool_calls",
    ) -> None:
        """Queue a response proposing multiple tool calls in a single turn."""
        formatted_calls = []
        for c in calls:
            cid = c.get("id") or f"call_{uuid.uuid4().hex[:8]}"
            name = c.get("name") or c.get("tool_name", "tool")
            args = c.get("arguments", {})
            args_str = args if isinstance(args, str) else json_dumps_safe(args)
            formatted_calls.append(
                {
                    "id": cid,
                    "type": "function",
                    "function": {"name": name, "arguments": args_str},
                }
            )

        self.responses.append(
            ModelResponse(
                text=text,
                finish_reason=finish_reason,
                model=self.default_model,
                tool_calls=formatted_calls,
                usage={"prompt_tokens": 30, "completion_tokens": 20, "total_tokens": 50},
            )
        )

    def queue_exception(self, exception: Exception) -> None:
        """Queue an exception to be raised on the next turn."""
        self.responses.append(exception)

    async def generate(
        self,
        prompt: str,
        max_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
        **kwargs: Any,
    ) -> ModelResponse:
        """Record the call parameters and return or raise the next queued scripted response."""
        if self.delay_seconds > 0.0:
            await asyncio.sleep(self.delay_seconds)

        self.calls.append(
            {
                "prompt": prompt,
                "max_tokens": max_tokens,
                "temperature": temperature,
                "messages": kwargs.get("messages"),
                "tools": kwargs.get("tools"),
            }
        )

        if not self.responses:
            # Fallback default final response if queue is exhausted
            return ModelResponse(
                text="Default fake completion response.",
                finish_reason="stop",
                model=self.default_model,
                usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            )

        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        elif isinstance(item, str):
            return ModelResponse(
                text=item,
                finish_reason="stop",
                model=self.default_model,
            )
        return item

    async def check_connectivity(self) -> bool:
        """Fake connectivity probe returns True."""
        return True


def json_dumps_safe(data: Any) -> str:
    """Safe JSON serializer for fake test arguments."""
    import json
    return json.dumps(data)
