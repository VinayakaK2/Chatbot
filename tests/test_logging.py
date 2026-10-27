"""Tests for logging setup and secret redaction."""

import logging
from core.logging_config import RedactingFormatter, setup_logging


class TestLoggingRedaction:
    """Ensure sensitive credentials, tokens, URL secrets, and headers are scrubbed from log outputs."""

    def test_bearer_token_redacted(self) -> None:
        """Verify Bearer tokens are redacted in log messages."""
        formatter = RedactingFormatter("%(message)s")
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname=__file__,
            lineno=10,
            msg="Sending request with Bearer abcdef1234567890...",
            args=(),
            exc_info=None,
        )
        formatted = formatter.format(record)
        assert "abcdef1234567890" not in formatted
        assert "[REDACTED]" in formatted

    def test_sk_prefixed_token_redacted(self) -> None:
        """Verify sk- prefixed secret tokens are masked."""
        formatter = RedactingFormatter("%(message)s")
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname=__file__,
            lineno=12,
            msg="Key provided: sk-colab-super-secret-token",
            args=(),
            exc_info=None,
        )
        formatted = formatter.format(record)
        assert "sk-colab-super-secret-token" not in formatted
        assert "[REDACTED]" in formatted

    def test_api_key_parameter_redacted(self) -> None:
        """Verify explicit api_key or api-key labels are masked."""
        formatter = RedactingFormatter("%(message)s")
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname=__file__,
            lineno=15,
            msg="Configured api_key: 'custom-secret-key-xyz'",
            args=(),
            exc_info=None,
        )
        formatted = formatter.format(record)
        assert "custom-secret-key-xyz" not in formatted
        assert "[REDACTED]" in formatted

    def test_password_and_token_redacted(self) -> None:
        """Verify password and token labels are masked."""
        formatter = RedactingFormatter("%(message)s")
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname=__file__,
            lineno=18,
            msg="User credentials password=mysecretpassword123, token=myauthtoken456",
            args=(),
            exc_info=None,
        )
        formatted = formatter.format(record)
        assert "mysecretpassword123" not in formatted
        assert "myauthtoken456" not in formatted
        assert "[REDACTED]" in formatted

    def test_url_credentials_redacted(self) -> None:
        """Verify embedded basic-auth user credentials in URLs are masked."""
        formatter = RedactingFormatter("%(message)s")
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname=__file__,
            lineno=22,
            msg="Connecting to https://user:superpass123@vllm.node.internal:8000/v1",
            args=(),
            exc_info=None,
        )
        formatted = formatter.format(record)
        assert "superpass123" not in formatted
        assert "[USER_PASS_REDACTED]@" in formatted

    def test_url_query_secrets_redacted(self) -> None:
        """Verify query parameters containing api_key or token are masked."""
        formatter = RedactingFormatter("%(message)s")
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname=__file__,
            lineno=26,
            msg="Upstream request to https://vllm.proxy/models?api_key=secretquerytoken&tier=prod",
            args=(),
            exc_info=None,
        )
        formatted = formatter.format(record)
        assert "secretquerytoken" not in formatted
        assert "api_key=[REDACTED]" in formatted

    def test_setup_logging_initializes_logger(self) -> None:
        """Verify logger is created with appropriate handler and level."""
        logger = setup_logging("DEBUG")
        assert logger.name == "agent_runtime"
        assert logger.level == logging.DEBUG
        assert len(logger.handlers) >= 1
