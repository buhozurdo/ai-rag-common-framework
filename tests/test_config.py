"""Unit tests for configuration management."""

import pytest
from common_framework.core.config import Settings, get_settings


def test_default_settings():
    """Verify default settings initialization."""
    settings = Settings()
    assert settings.framework_env in ("development", "staging", "production", "test")
    assert settings.default_provider in ("gemini", "openai", "anthropic", "ollama")
    assert settings.default_temperature == 0.7
    assert settings.default_timeout_seconds == 60.0
    assert settings.default_max_retries == 3


def test_settings_custom_values():
    """Verify custom environment values injection."""
    settings = Settings(
        FRAMEWORK_ENV="production",
        DEFAULT_LLM_PROVIDER="openai",
        DEFAULT_LLM_MODEL="gpt-4o-mini",
        OPENAI_API_KEY="sk-test-key-123",
        DEFAULT_TEMPERATURE=0.2,
    )
    assert settings.framework_env == "production"
    assert settings.default_provider == "openai"
    assert settings.default_model == "gpt-4o-mini"
    assert settings.openai_api_key == "sk-test-key-123"
    assert settings.get_api_key_for_provider("openai") == "sk-test-key-123"
    assert settings.default_temperature == 0.2


def test_get_settings_cached():
    """Verify get_settings returns the same cached instance."""
    s1 = get_settings()
    s2 = get_settings()
    assert s1 is s2
