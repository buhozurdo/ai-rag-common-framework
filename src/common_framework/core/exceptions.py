"""Standard exception hierarchy for the framework."""

from typing import Any, Optional


class FrameworkError(Exception):
    """Base exception for all errors raised by the framework."""

    def __init__(self, message: str, details: Optional[dict[str, Any]] = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(message={self.message!r}, details={self.details!r})"


class ConfigurationError(FrameworkError):
    """Raised when configuration or environment parameters are missing or invalid."""
    pass


class LLMError(FrameworkError):
    """Base exception for LLM-related failures."""
    pass


class LLMProviderError(LLMError):
    """Raised when an upstream LLM provider returns an HTTP or operational error."""

    def __init__(
        self,
        message: str,
        status_code: Optional[int] = None,
        provider: Optional[str] = None,
        details: Optional[dict[str, Any]] = None,
    ) -> None:
        super().__init__(message, details)
        self.status_code = status_code
        self.provider = provider


class AuthenticationError(LLMProviderError):
    """Raised when API credentials for a provider are missing, invalid, or expired."""
    pass


class RateLimitError(LLMProviderError):
    """Raised when an upstream provider returns HTTP 429 Too Many Requests or quota exceeded."""

    def __init__(
        self,
        message: str,
        status_code: int = 429,
        provider: Optional[str] = None,
        retry_after: Optional[float] = None,
        details: Optional[dict[str, Any]] = None,
    ) -> None:
        super().__init__(message, status_code=status_code, provider=provider, details=details)
        self.retry_after = retry_after


class ModelNotFoundError(LLMProviderError):

    """Raised when the requested model name is not recognized or unavailable."""
    pass


class StructuredOutputError(LLMError):
    """Raised when the LLM response fails validation against the target Pydantic schema."""

    def __init__(
        self,
        message: str,
        raw_output: Optional[str] = None,
        details: Optional[dict[str, Any]] = None,
    ) -> None:
        super().__init__(message, details)
        self.raw_output = raw_output
