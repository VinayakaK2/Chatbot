"""Bootstrap entry point for the Coding Agent Runtime backend service."""

import sys
import uvicorn
from api.app import create_app
from core.config import get_settings
from core.logging_config import setup_logging


def main() -> int:
    """Bootstrap configuration, logging, and start the Uvicorn ASGI server."""
    settings = get_settings()
    logger = setup_logging(settings.log_level)
    logger.info(
        "Starting Coding Agent Runtime on %s:%s (env=%s)",
        settings.host,
        settings.port,
        settings.environment,
    )

    app = create_app(settings)
    uvicorn.run(app, host=settings.host, port=settings.port, log_config=None)
    return 0


if __name__ == "__main__":
    sys.exit(main())
