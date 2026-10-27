"""Application configuration and environment variable validation."""

import os
from typing import Any, List, Optional
from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Maximum allowed prompt character length to prevent buffer and memory exhaustion
DEFAULT_MAX_PROMPT_LENGTH: int = 32_768

# Default timeout in seconds for upstream model API calls
DEFAULT_MODEL_TIMEOUT_SECONDS: float = 60.0

# Maximum allowed model retries for transient upstream failures
DEFAULT_MODEL_MAX_RETRIES: int = 2


class Settings(BaseSettings):
    """Runtime configuration loaded from environment variables and local environment files."""

    model_config = SettingsConfigDict(
        env_file=(".env", ".env.local"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Server Bind Configuration
    host: str = Field(default="127.0.0.1", description="Host address to bind the API server")
    port: int = Field(default=8000, description="TCP port to bind the API server")
    environment: str = Field(default="development", description="Execution environment (development, test, production)")
    log_level: str = Field(default="INFO", description="Standard logging level")

    # Security: Narrow Local CORS Boundaries
    cors_origins: List[str] = Field(
        default=["http://localhost:5173", "http://127.0.0.1:5173"],
        description="Allowed CORS origins for the frontend GUI",
    )

    # Model Provider Settings (Colab vLLM / OpenAI-compatible endpoint)
    agent_model_base_url: Optional[str] = Field(
        default=None,
        validation_alias="AGENT_MODEL_BASE_URL",
        description="Base URL for the OpenAI-compatible vLLM server",
    )
    agent_model_api_key: Optional[SecretStr] = Field(
        default=None,
        validation_alias="AGENT_MODEL_API_KEY",
        description="Secret API key for upstream model authentication",
    )
    agent_model_name: Optional[str] = Field(
        default="Qwen/Qwen2.5-Coder-14B-Instruct-AWQ",
        validation_alias="AGENT_MODEL_NAME",
        description="Identifier of the model served by vLLM",
    )
    agent_model_timeout_seconds: float = Field(
        default=DEFAULT_MODEL_TIMEOUT_SECONDS,
        validation_alias="AGENT_MODEL_TIMEOUT_SECONDS",
        description="Request timeout in seconds for upstream completions",
    )
    agent_model_max_retries: int = Field(
        default=DEFAULT_MODEL_MAX_RETRIES,
        validation_alias="AGENT_MODEL_MAX_RETRIES",
        description="Maximum retry attempts for transient model connection errors",
    )

    # Task and System Limits
    max_prompt_length: int = Field(
        default=DEFAULT_MAX_PROMPT_LENGTH,
        description="Maximum accepted character count for user prompts",
    )

    @field_validator("port")
    @classmethod
    def validate_port_range(cls, v: int) -> int:
        """Validate that the TCP port falls within the valid unprivileged range."""
        if not (1 <= v <= 65535):
            raise ValueError(f"Port must be between 1 and 65535, got {v}")
        return v

    @field_validator("agent_model_base_url")
    @classmethod
    def validate_base_url(cls, v: Optional[str]) -> Optional[str]:
        """Normalize and validate the model base URL format."""
        if v is None:
            return None
        stripped = v.strip()
        if not stripped:
            return None
        if not (stripped.startswith("http://") or stripped.startswith("https://")):
            raise ValueError(f"AGENT_MODEL_BASE_URL must start with http:// or https://, got: {stripped}")
        from urllib.parse import urlsplit
        parts = urlsplit(stripped)
        if parts.username or parts.password:
            raise ValueError("AGENT_MODEL_BASE_URL must not contain embedded user credentials. Use AGENT_MODEL_API_KEY instead.")
        if parts.query:
            raise ValueError("AGENT_MODEL_BASE_URL must not contain query parameters.")
        return stripped.rstrip("/")

    @field_validator("agent_model_timeout_seconds")
    @classmethod
    def validate_timeout(cls, v: float) -> float:
        """Ensure model timeout is strictly positive and bounded."""
        if v <= 0.0 or v > 600.0:
            raise ValueError(f"Timeout must be between 0.1 and 600.0 seconds, got {v}")
        return v

    @field_validator("agent_model_max_retries")
    @classmethod
    def validate_retries(cls, v: int) -> int:
        """Ensure retry count is non-negative and bounded."""
        if v < 0 or v > 10:
            raise ValueError(f"Max retries must be between 0 and 10, got {v}")
        return v

    @property
    def is_model_configured(self) -> bool:
        """Check whether the model provider configuration has required minimal endpoint coordinates."""
        return bool(self.agent_model_base_url and self.agent_model_name)

    def get_sanitized_diagnostics(self) -> dict[str, Any]:
        """Return a safe dictionary of configuration parameters with credentials redacted."""
        clean_url: Optional[str] = None
        if self.agent_model_base_url:
            from urllib.parse import urlsplit
            p = urlsplit(self.agent_model_base_url)
            host = p.hostname or ""
            port_suffix = f":{p.port}" if p.port and p.port not in (80, 443) else ""
            clean_url = f"{p.scheme}://{host}{port_suffix}{p.path.rstrip('/')}"

        return {
            "host": self.host,
            "port": self.port,
            "environment": self.environment,
            "log_level": self.log_level,
            "cors_origins": self.cors_origins,
            "is_model_configured": self.is_model_configured,
            "agent_model_base_url": clean_url,
            "agent_model_name": self.agent_model_name,
            "agent_model_api_key_set": bool(self.agent_model_api_key),
            "agent_model_timeout_seconds": self.agent_model_timeout_seconds,
            "agent_model_max_retries": self.agent_model_max_retries,
            "max_prompt_length": self.max_prompt_length,
        }


# Global memoized settings instance
_settings_instance: Optional[Settings] = None


def get_settings() -> Settings:
    """Retrieve or initialize the cached singleton settings object."""
    global _settings_instance
    if _settings_instance is None:
        _settings_instance = Settings()
    return _settings_instance


def reset_settings() -> None:
    """Reset cached settings instance (primarily used for unit test isolation)."""
    global _settings_instance
    _settings_instance = None
