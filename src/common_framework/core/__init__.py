"""Core module for configuration, exception handling, and LLM abstractions."""

from common_framework.core.config import Settings, get_settings
from common_framework.core.exceptions import (
    AuthenticationError,
    ConfigurationError,
    FrameworkError,
    LLMError,
    LLMProviderError,
    ModelNotFoundError,
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

__all__ = [
    "Settings",
    "get_settings",
    "FrameworkError",
    "ConfigurationError",
    "LLMError",
    "LLMProviderError",
    "AuthenticationError",
    "ModelNotFoundError",
    "StructuredOutputError",
    "BaseLLMClient",
    "ChatMessage",
    "ChatResponse",
    "TokenUsage",
    "Role",
    "LLMFactory",
]
