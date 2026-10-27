"""Health and readiness endpoints for the agent runtime API."""

import logging
from typing import Optional
from fastapi import APIRouter, Depends, status
import httpx

from api.schemas.health import HealthResponse, ModelReadinessResponse
from core.config import Settings, get_settings

router = APIRouter(prefix="/api", tags=["System Health"])
logger = logging.getLogger("agent_runtime.api.health")


@router.get(
    "/health",
    response_model=HealthResponse,
    status_code=status.HTTP_200_OK,
    summary="Application liveness and configuration check",
)
def get_health(settings: Settings = Depends(get_settings)) -> HealthResponse:
    """Return backend process liveness and sanitized model configuration status."""
    return HealthResponse(
        status="healthy",
        version="0.1.0",
        model_configured=settings.is_model_configured,
        model_id=settings.agent_model_name if settings.is_model_configured else None,
        model_endpoint=settings.agent_model_base_url if settings.is_model_configured else None,
    )


@router.get(
    "/readiness",
    response_model=ModelReadinessResponse,
    status_code=status.HTTP_200_OK,
    summary="Model connectivity probe and readiness status",
)
async def check_readiness(settings: Settings = Depends(get_settings)) -> ModelReadinessResponse:
    """Probe the upstream model server endpoint without triggering generation."""
    if not settings.is_model_configured:
        return ModelReadinessResponse(
            status="unconfigured",
            model_name=None,
            endpoint=None,
            message="Model endpoint is not configured. Backend is operating in offline mode.",
        )

    endpoint = settings.agent_model_base_url
    # Sanitize URL for presentation
    sanitized_url = endpoint or ""
    probe_url = f"{endpoint}/models"

    headers = {}
    if settings.agent_model_api_key:
        headers["Authorization"] = f"Bearer {settings.agent_model_api_key.get_secret_value()}"

    try:
        # Bounded probe with short timeout to prevent blocking health checks
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(probe_url, headers=headers)
            if resp.status_code == 200:
                return ModelReadinessResponse(
                    status="ready",
                    model_name=settings.agent_model_name,
                    endpoint=sanitized_url,
                    message="Successfully connected to vLLM OpenAI-compatible endpoint.",
                )
            return ModelReadinessResponse(
                status="unreachable",
                model_name=settings.agent_model_name,
                endpoint=sanitized_url,
                message=f"Model endpoint returned non-200 status code: {resp.status_code}",
            )
    except httpx.ConnectError:
        return ModelReadinessResponse(
            status="unreachable",
            model_name=settings.agent_model_name,
            endpoint=sanitized_url,
            message="Connection refused or DNS lookup failed for configured model endpoint.",
        )
    except httpx.TimeoutException:
        return ModelReadinessResponse(
            status="unreachable",
            model_name=settings.agent_model_name,
            endpoint=sanitized_url,
            message="Model readiness probe timed out after 5.0 seconds.",
        )
    except Exception as exc:
        logger.warning("Unexpected error during readiness probe: %s", type(exc).__name__)
        return ModelReadinessResponse(
            status="error",
            model_name=settings.agent_model_name,
            endpoint=sanitized_url,
            message="An unexpected error occurred while probing model endpoint.",
        )
