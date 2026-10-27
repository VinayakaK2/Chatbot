"""Centralized logging configuration with automatic secret redaction."""

import logging
import re
import sys
from typing import Optional

# Regex patterns to detect and mask API keys, auth headers, and bearer tokens
_SECRET_PATTERNS = [
    re.compile(r"(Bearer\s+)[A-Za-z0-9_\-\.]{8,}", re.IGNORECASE),
    re.compile(r"(sk-[A-Za-z0-9_\-\.]{10,})", re.IGNORECASE),
    re.compile(r"(api[_-]?key[\"']?\s*[:=]\s*[\"']?)([A-Za-z0-9_\-\.]{8,})", re.IGNORECASE),
    re.compile(r"(authorization[\"']?\s*[:=]\s*[\"']?)([A-Za-z0-9_\-\.]{8,})", re.IGNORECASE),
]

_REDACTED_LABEL: str = "[REDACTED]"


class RedactingFormatter(logging.Formatter):
    """Custom log formatter that scrubs detected secrets from output messages."""

    def format(self, record: logging.LogRecord) -> str:
        """Format the log record and apply regex-based secret redaction to the text."""
        original = super().format(record)
        redacted = original
        for pattern in _SECRET_PATTERNS:
            if "Bearer" in pattern.pattern:
                redacted = pattern.sub(r"\1" + _REDACTED_LABEL, redacted)
            elif "api" in pattern.pattern.lower() or "auth" in pattern.pattern.lower():
                redacted = pattern.sub(r"\1" + _REDACTED_LABEL, redacted)
            else:
                redacted = pattern.sub(_REDACTED_LABEL, redacted)
        return redacted


def setup_logging(log_level: str = "INFO") -> logging.Logger:
    """Initialize root application logging with redaction and structured output."""
    level = getattr(logging, log_level.upper(), logging.INFO)
    logger = logging.getLogger("agent_runtime")
    logger.setLevel(level)

    # Avoid duplicate handlers if setup is invoked multiple times
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setLevel(level)
        formatter = RedactingFormatter(
            fmt="%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        handler.setFormatter(formatter)
        logger.addHandler(handler)

    return logger
