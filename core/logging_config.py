"""Centralized logging configuration with automatic secret redaction."""

import logging
import re
import sys
from typing import Optional

_REDACTED_LABEL: str = "[REDACTED]"

# Multi-layered patterns to detect and mask API keys, auth headers, tokens, passwords, and URL secrets
_SECRET_PATTERNS = [
    (re.compile(r"(Bearer\s+)[A-Za-z0-9_\-\.]{8,}", re.IGNORECASE), r"\1" + _REDACTED_LABEL),
    (re.compile(r"(sk-[A-Za-z0-9_\-\.]{8,})", re.IGNORECASE), _REDACTED_LABEL),
    (re.compile(r"(api[_-]?key[\"']?\s*[:=]\s*[\"']?)[A-Za-z0-9_\-\.]{6,}", re.IGNORECASE), r"\1" + _REDACTED_LABEL),
    (re.compile(r"(authorization[\"']?\s*[:=]\s*[\"']?)[A-Za-z0-9_\-\.]{6,}", re.IGNORECASE), r"\1" + _REDACTED_LABEL),
    (re.compile(r"(password[\"']?\s*[:=]\s*[\"']?)[^\s\"',]+", re.IGNORECASE), r"\1" + _REDACTED_LABEL),
    (re.compile(r"(token[\"']?\s*[:=]\s*[\"']?)[A-Za-z0-9_\-\.]{8,}", re.IGNORECASE), r"\1" + _REDACTED_LABEL),
    (re.compile(r"(secret[\"']?\s*[:=]\s*[\"']?)[A-Za-z0-9_\-\.]{6,}", re.IGNORECASE), r"\1" + _REDACTED_LABEL),
    (re.compile(r"(https?://)([^:\s]+:[^@\s]+@)", re.IGNORECASE), r"\1[USER_PASS_REDACTED]@"),
    (re.compile(r"([?&](?:api_key|key|token|access_token|secret)=)[^&\s]+", re.IGNORECASE), r"\1" + _REDACTED_LABEL),
]


class RedactingFormatter(logging.Formatter):
    """Custom log formatter that scrubs detected secrets from output messages."""

    def format(self, record: logging.LogRecord) -> str:
        """Format the log record and apply regex-based secret redaction to the text."""
        original = super().format(record)
        redacted = original
        for pattern, replacement in _SECRET_PATTERNS:
            redacted = pattern.sub(replacement, redacted)
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
