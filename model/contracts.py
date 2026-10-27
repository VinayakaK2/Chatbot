"""Provider-independent model contracts, data schemas, and error hierarchy."""

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


# ==============================================================================
# Model Error Hierarchy
# ==============================================================================

class ModelError(Exception):
    """Base exception for all model provider failures."""

    def __init__(self, message: str, status_code: Optional[int] = None) -> None:
        """Initialize model exception with human-readable message and HTTP code."""
        super().__init__(message)
        self.message = message
        self.status_code = status_code


class ModelConnectionError(ModelError):
    """Raised when network connection to the model server fails (e.g., DNS, connection refused)."""


class ModelTimeoutError(ModelError):
    """Raised when an upstream completion request exceeds configured timeout limits."""


class ModelAuthError(ModelError):
    """Raised when upstream server rejects credentials (HTTP 401 or 403)."""


class ModelRateLimitError(ModelError):
    """Raised when upstream model endpoint returns HTTP 429 rate limit exceeded."""


class ModelProviderError(ModelError):
    """Raised when upstream model endpoint encounters a 5xx internal server error."""


class ModelResponseError(ModelError):
    """Raised when upstream response payload is malformed, missing, or unexpected."""


# ==============================================================================
# Data Schemas
# ==============================================================================

class ModelMessage(BaseModel):
    """Standard message representation for chat completions."""

    role: str = Field(..., description="Message author role: system, user, assistant, or tool")
    content: str = Field(..., description="Text content of the message")
    name: Optional[str] = Field(default=None, description="Optional author or tool name")
    tool_call_id: Optional[str] = Field(default=None, description="Matching tool call ID if role is tool")


class ModelRequest(BaseModel):
    """Normalized completion request dispatched to model adapters."""

    prompt: str = Field(..., description="Primary user prompt string")
    messages: Optional[List[ModelMessage]] = Field(
        default=None,
        description="Structured multi-turn message history if available",
    )
    max_tokens: Optional[int] = Field(
        default=2048,
        description="Upper bound on output tokens to generate",
    )
    temperature: Optional[float] = Field(
        default=0.2,
        description="Sampling temperature for diversity control",
    )
    stop: Optional[List[str]] = Field(
        default=None,
        description="Stop sequences terminating generation",
    )


class ModelResponse(BaseModel):
    """Standardized result returned by all model provider implementations."""

    text: str = Field(..., description="Normalized generated completion text")
    finish_reason: Optional[str] = Field(
        default=None,
        description="Reason generation halted (e.g., 'stop', 'length', 'tool_calls')",
    )
    model: str = Field(..., description="Model identifier reported by the upstream provider")
    usage: Optional[Dict[str, int]] = Field(
        default=None,
        description="Token metrics: prompt_tokens, completion_tokens, total_tokens",
    )
    request_id: Optional[str] = Field(
        default=None,
        description="Unique upstream request identifier for tracing",
    )
    tool_calls: Optional[List[Dict[str, Any]]] = Field(
        default=None,
        description="Raw tool call requests emitted by the model (parsed but unexecuted in Phase 1)",
    )
    raw_metadata: Dict[str, Any] = Field(
        default_factory=dict,
        description="Provider-specific response metadata for auditing",
    )


# ==============================================================================
# Abstract Provider Interface
# ==============================================================================

class ModelProvider(ABC):
    """Abstract contract for LLM provider adapters."""

    @abstractmethod
    async def generate(
        self,
        prompt: str,
        max_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
        **kwargs: Any,
    ) -> ModelResponse:
        """Send prompt to the model and return a normalized ModelResponse."""
        pass

    @abstractmethod
    async def check_connectivity(self) -> bool:
        """Check if the upstream endpoint is reachable without generating completions."""
        pass
