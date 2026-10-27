"""Tests for configuration validation, secret redaction, and diagnostics."""

import os
import pytest
from pydantic import SecretStr, ValidationError

from core.config import Settings, get_settings, reset_settings


class TestConfiguration:
    """Validate environment configuration parsing and security constraints."""

    def setup_method(self) -> None:
        """Reset cached settings before each test."""
        reset_settings()

    def teardown_method(self) -> None:
        """Reset cached settings after each test."""
        reset_settings()

    def test_default_configuration_loads_safely(self) -> None:
        """Ensure settings load with secure default values when no environment is set."""
        settings = Settings()
        assert settings.host == "127.0.0.1"
        assert settings.port == 8000
        assert settings.environment == "development"
        assert settings.agent_model_base_url is None
        assert settings.is_model_configured is False

    def test_valid_model_configuration_loads(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Ensure valid model endpoint parameters are accepted and parsed correctly."""
        monkeypatch.setenv("AGENT_MODEL_BASE_URL", "https://colab-vllm.example.com/v1/")
        monkeypatch.setenv("AGENT_MODEL_API_KEY", "sk-secret-key-12345")
        monkeypatch.setenv("AGENT_MODEL_NAME", "Qwen/Qwen2.5-Coder-14B-Instruct-AWQ")
        monkeypatch.setenv("AGENT_MODEL_TIMEOUT_SECONDS", "45.0")
        monkeypatch.setenv("AGENT_MODEL_MAX_RETRIES", "3")

        settings = Settings()
        assert settings.is_model_configured is True
        # Trailing slash should be stripped for uniform URL assembly
        assert settings.agent_model_base_url == "https://colab-vllm.example.com/v1"
        assert settings.agent_model_name == "Qwen/Qwen2.5-Coder-14B-Instruct-AWQ"
        assert settings.agent_model_timeout_seconds == 45.0
        assert settings.agent_model_max_retries == 3
        assert isinstance(settings.agent_model_api_key, SecretStr)
        assert settings.agent_model_api_key.get_secret_value() == "sk-secret-key-12345"

    def test_invalid_url_scheme_raises_validation_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Reject URLs lacking http:// or https:// scheme."""
        monkeypatch.setenv("AGENT_MODEL_BASE_URL", "ftp://invalid-host.com")
        with pytest.raises(ValidationError) as exc_info:
            Settings()
        assert "AGENT_MODEL_BASE_URL must start with http:// or https://" in str(exc_info.value)

    def test_invalid_port_range_raises_validation_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Reject out-of-range TCP port numbers."""
        monkeypatch.setenv("PORT", "70000")
        with pytest.raises(ValidationError) as exc_info:
            Settings()
        assert "Port must be between 1 and 65535" in str(exc_info.value)

    def test_negative_timeout_raises_validation_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Reject non-positive timeout values."""
        monkeypatch.setenv("AGENT_MODEL_TIMEOUT_SECONDS", "-5.0")
        with pytest.raises(ValidationError) as exc_info:
            Settings()
        assert "Timeout must be between 0.1 and 600.0 seconds" in str(exc_info.value)

    def test_sanitized_diagnostics_redacts_api_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Verify that diagnostics dict never exposes raw secret values."""
        raw_secret = "sk-super-secret-production-token-999"
        monkeypatch.setenv("AGENT_MODEL_API_KEY", raw_secret)
        settings = Settings()

        diag = settings.get_sanitized_diagnostics()
        assert raw_secret not in str(diag)
        assert diag["agent_model_api_key_set"] is True
        assert "agent_model_api_key" not in diag
