"""Health and readiness response schemas."""

from datetime import datetime, timezone
from typing import Literal, Optional
from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    """Liveness and basic system health status response."""

    status: Literal["healthy", "degraded", "unhealthy"] = Field(
        default="healthy",
        description="Overall service health status",
    )
    version: str = Field(
        default="0.1.0",
        description="Application software version",
    )
    timestamp: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(),
        description="ISO 8601 UTC timestamp of the health check",
    )
    model_configured: bool = Field(
        ...,
        description="True if model endpoint settings are configured",
    )
    model_id: Optional[str] = Field(
        default=None,
        description="Configured model identifier (if configured)",
    )
    model_endpoint: Optional[str] = Field(
        default=None,
        description="Sanitized base URL of the model endpoint",
    )


class ModelReadinessResponse(BaseModel):
    """Model connectivity probe and readiness response."""

    status: Literal["ready", "unconfigured", "unreachable", "error"] = Field(
        ...,
        description="Model connectivity status",
    )
    model_name: Optional[str] = Field(
        default=None,
        description="Configured target model name",
    )
    endpoint: Optional[str] = Field(
        default=None,
        description="Sanitized endpoint address",
    )
    message: str = Field(
        ...,
        description="Human-readable description of connectivity state",
    )
    checked_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(),
        description="ISO 8601 UTC timestamp of connectivity probe",
    )
