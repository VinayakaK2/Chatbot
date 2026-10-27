"""Tests for logging setup and secret redaction."""

import logging
from core.logging_config import RedactingFormatter, setup_logging


class TestLoggingRedaction:
    """Ensure sensitive credentials and headers are scrubbed from log outputs."""

    def test_bearer_token_redacted(self) -> None:
        """Verify Bearer tokens are redacted in log messages."""
        formatter = RedactingFormatter("%(message)s")
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname=__file__,
            lineno=10,
            msg="Sending request with Bearer sk-1234567890abcdef...",
            args=(),
            exc_info=None,
        )
        formatted = formatter.format(record)
        assert "sk-1234567890abcdef" not in formatted
        assert "[REDACTED]" in formatted

    def test_api_key_parameter_redacted(self) -> None:
        """Verify explicit api_key or api-key labels are masked."""
        formatter = RedactingFormatter("%(message)s")
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname=__file__,
            lineno=15,
            msg="Configured api_key: 'sk-ant-secret-token-xyz'",
            args=(),
            exc_info=None,
        )
        formatted = formatter.format(record)
        assert "sk-ant-secret-token-xyz" not in formatted
        assert "[REDACTED]" in formatted

    def test_setup_logging_initializes_logger(self) -> None:
        """Verify logger is created with appropriate handler and level."""
        logger = setup_logging("DEBUG")
        assert logger.name == "agent_runtime"
        assert logger.level == logging.DEBUG
        assert len(logger.handlers) >= 1
