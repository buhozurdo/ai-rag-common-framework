"""Unit tests for exception hierarchy."""

from common_framework.core.exceptions import (
    AuthenticationError,
    ConfigurationError,
    FrameworkError,
    LLMError,
    LLMProviderError,
    ModelNotFoundError,
    StructuredOutputError,
)


def test_exception_inheritance():
    """Verify exception hierarchy."""
    assert issubclass(ConfigurationError, FrameworkError)
    assert issubclass(LLMError, FrameworkError)
    assert issubclass(LLMProviderError, LLMError)
    assert issubclass(AuthenticationError, LLMProviderError)
    assert issubclass(ModelNotFoundError, LLMProviderError)
    assert issubclass(StructuredOutputError, LLMError)


def test_exception_payload():
    """Verify exception message and metadata storage."""
    err = LLMProviderError(
        message="Rate limit reached",
        status_code=429,
        provider="openai",
        details={"retry_after": 5},
    )
    assert err.message == "Rate limit reached"
    assert err.status_code == 429
    assert err.provider == "openai"
    assert err.details == {"retry_after": 5}
    assert "LLMProviderError" in repr(err)
