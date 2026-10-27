"""FastAPI application factory and middleware configuration."""

import logging
from typing import Optional
from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from api.routes.health import router as health_router
from api.routes.tasks import router as tasks_router
from core.config import Settings, get_settings
from core.logging_config import setup_logging
from core.task_service import TaskService, get_task_service

logger = logging.getLogger("agent_runtime.api.app")


def create_app(settings: Optional[Settings] = None) -> FastAPI:
    """Create and configure the FastAPI application instance."""
    app_settings = settings or get_settings()
    setup_logging(app_settings.log_level)

    app = FastAPI(
        title="Coding Agent Runtime API",
        description="Backend API and model execution service for Agent Runtime",
        version="0.1.0",
        docs_url="/docs" if app_settings.environment == "development" else None,
        redoc_url=None,
    )

    # Store settings in dependency overrides if explicitly provided
    if settings is not None:
        app.dependency_overrides[get_settings] = lambda: settings

    # Restrict CORS to configured local frontend origins
    app.add_middleware(
        CORSMiddleware,
        allow_origins=app_settings.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["*"],
    )

    # Register Routers
    app.include_router(health_router)
    app.include_router(tasks_router)

    # Custom Exception Handlers
    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
        """Handle request schema validation failures with actionable, safe error output."""
        errors = []
        for err in exc.errors():
            loc = " -> ".join(str(item) for item in err.get("loc", []))
            errors.append({"field": loc, "message": err.get("msg", "Invalid value")})
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            content={
                "error": "Validation Error",
                "message": "The request payload failed schema validation.",
                "details": errors,
            },
        )

    @app.exception_handler(Exception)
    async def global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        """Handle unexpected exceptions safely without exposing internal stack traces."""
        logger.error("Unhandled server exception: %s", type(exc).__name__, exc_info=True)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={
                "error": "Internal Server Error",
                "message": "An unexpected error occurred while processing your request.",
            },
        )

    return app
