"""Core module for configuration, exception handling, and LLM abstractions."""

from common_framework.core.config import Settings, get_settings
from common_framework.core.deprecation import (
    FrameworkDeprecationWarning,
    deprecated,
    warn_deprecated_parameter,
)
from common_framework.core.exceptions import (
    AuthenticationError,
    CircuitBreakerOpenError,
    ConfigurationError,
    FrameworkError,
    LLMError,
    LLMProviderError,
    ModelNotFoundError,
    RateLimitError,
    StructuredOutputError,
)
from common_framework.core.llm import (
    BaseLLMClient,
    ChatMessage,
    ChatResponse,
    LLMFactory,
    Role,
    TokenUsage,
)
from common_framework.core.resilience import (
    CircuitBreaker,
    CircuitBreakerConfig,
    CircuitBreakerStats,
    CircuitState,
    ResilientLLMClient,
)

__all__ = [
    "Settings",
    "get_settings",
    "FrameworkError",
    "ConfigurationError",
    "LLMError",
    "LLMProviderError",
    "AuthenticationError",
    "RateLimitError",
    "ModelNotFoundError",
    "StructuredOutputError",
    "CircuitBreakerOpenError",
    "CircuitState",
    "CircuitBreakerConfig",
    "CircuitBreakerStats",
    "CircuitBreaker",
    "ResilientLLMClient",
    "BaseLLMClient",
    "ChatMessage",
    "ChatResponse",
    "TokenUsage",
    "Role",
    "LLMFactory",
    "FrameworkDeprecationWarning",
    "deprecated",
    "warn_deprecated_parameter",
]
