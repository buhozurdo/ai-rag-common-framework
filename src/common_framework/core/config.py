"""Centralized configuration system based on pydantic-settings."""

from functools import lru_cache
from typing import Any, Literal, Optional, Union
from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Global configuration settings for the AI RAG Common Framework."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
        frozen=True,
    )

    # General framework environment
    framework_env: Literal["development", "staging", "production", "test"] = Field(
        default="development",
        alias="FRAMEWORK_ENV",
        description="Current runtime environment",
    )
    log_level: str = Field(
        default="INFO",
        alias="LOG_LEVEL",
        description="Application logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)",
    )

    # LLM Provider defaults
    default_provider: Literal["gemini", "openai", "anthropic", "ollama"] = Field(
        default="gemini",
        alias="DEFAULT_LLM_PROVIDER",
        description="Default LLM provider to use if none is explicitly specified",
    )
    default_model: str = Field(
        default="gemini-2.5-flash",
        alias="DEFAULT_LLM_MODEL",
        description="Default model identifier",
    )
    default_temperature: float = Field(
        default=0.7,
        ge=0.0,
        le=2.0,
        alias="DEFAULT_TEMPERATURE",
        description="Default sampling temperature for model responses",
    )
    default_timeout_seconds: float = Field(
        default=60.0,
        gt=0.0,
        alias="DEFAULT_TIMEOUT_SECONDS",
        description="Default HTTP timeout in seconds for LLM calls",
    )
    default_max_retries: int = Field(
        default=3,
        ge=0,
        alias="DEFAULT_MAX_RETRIES",
        description="Maximum retry attempts on transient network/quota failures",
    )
    fallback_providers: list[str] = Field(
        default_factory=list,
        alias="FALLBACK_PROVIDERS",
        description="Ordered list of secondary LLM providers to fail over to on errors",
    )

    @field_validator("fallback_providers", mode="before")
    @classmethod
    def _parse_fallback_providers(cls, value: Any) -> list[str]:
        if isinstance(value, str):
            parts = [p.strip() for p in value.split(",") if p.strip()]
            return parts
        return list(value) if value is not None else []

    # API Keys & Endpoints
    gemini_api_key: Optional[SecretStr] = Field(
        default=None,
        alias="GEMINI_API_KEY",
        description="Google Gemini / AI Studio API Key",
    )
    openai_api_key: Optional[SecretStr] = Field(
        default=None,
        alias="OPENAI_API_KEY",
        description="OpenAI API Key",
    )
    openai_base_url: str = Field(
        default="https://api.openai.com/v1",
        alias="OPENAI_BASE_URL",
        description="Base URL for OpenAI-compatible endpoints",
    )
    anthropic_api_key: Optional[SecretStr] = Field(
        default=None,
        alias="ANTHROPIC_API_KEY",
        description="Anthropic API Key",
    )
    anthropic_base_url: str = Field(
        default="https://api.anthropic.com/v1",
        alias="ANTHROPIC_BASE_URL",
        description="Base URL for Anthropic Messages API",
    )
    ollama_base_url: str = Field(
        default="http://localhost:11434",
        alias="OLLAMA_BASE_URL",
        description="Base URL for Ollama local instances",
    )

    def get_api_key_for_provider(self, provider: str) -> Optional[str]:
        """Retrieve the plaintext API key associated with a specific provider."""
        provider_normalized = provider.lower().strip()
        secret: Optional[Union[SecretStr, str]] = None
        if provider_normalized == "gemini":
            secret = self.gemini_api_key
        elif provider_normalized == "openai":
            secret = self.openai_api_key
        elif provider_normalized == "anthropic":
            secret = self.anthropic_api_key
        elif provider_normalized == "ollama":
            return None

        if secret is None:
            return None
        return secret.get_secret_value() if isinstance(secret, SecretStr) else str(secret)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return a cached instance of application settings."""
    return Settings()
