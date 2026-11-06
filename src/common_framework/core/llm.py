"""Unified, lightweight LLM client abstraction supporting multiple providers and structured outputs."""

from __future__ import annotations

import asyncio
import json
import logging
from abc import ABC, abstractmethod
from enum import Enum
from typing import Any, Generic, Literal, Optional, TypeVar
import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from common_framework.core.config import Settings, get_settings
from common_framework.core.exceptions import (
    AuthenticationError,
    ConfigurationError,
    LLMProviderError,
    ModelNotFoundError,
    RateLimitError,
    StructuredOutputError,
)

logger = logging.getLogger("common_framework.llm")

T = TypeVar("T", bound=BaseModel)


class Role(str, Enum):
    """Standardized roles in a conversational exchange."""

    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class ChatMessage(BaseModel):
    """A single message in a chat completion conversation."""

    model_config = ConfigDict(frozen=True)

    role: Role | Literal["system", "user", "assistant", "tool"] = Role.USER
    content: str
    name: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        """Convert message to provider-compatible payload format."""
        data: dict[str, Any] = {
            "role": self.role.value if isinstance(self.role, Role) else str(self.role),
            "content": self.content,
        }
        if self.name:
            data["name"] = self.name
        return data


class TokenUsage(BaseModel):
    """Token consumption statistics for an LLM response."""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class ChatResponse(BaseModel):
    """Unified response model returned by any LLM provider."""

    content: str
    model: str
    usage: TokenUsage = Field(default_factory=TokenUsage)
    raw: Optional[dict[str, Any]] = None


class BaseLLMClient(ABC):
    """Abstract base class for all LLM provider clients."""

    def __init__(self, settings: Optional[Settings] = None) -> None:
        self.settings = settings or get_settings()

    @abstractmethod
    async def complete(
        self,
        messages: list[ChatMessage],
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        **kwargs: Any,
    ) -> ChatResponse:
        """Execute a conversational completion."""
        pass

    @abstractmethod
    async def structured_complete(
        self,
        messages: list[ChatMessage],
        response_model: type[T],
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        **kwargs: Any,
    ) -> T:
        """Execute a completion and validate the response against a Pydantic schema."""
        pass


class OpenAICompatibleClient(BaseLLMClient):
    """Client for OpenAI and any OpenAI-compatible endpoint (Ollama, Gemini OpenAI endpoint, vLLM, DeepSeek)."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        provider_name: str = "openai",
        settings: Optional[Settings] = None,
        client: Optional[httpx.AsyncClient] = None,
    ) -> None:
        super().__init__(settings)
        self.provider_name = provider_name
        self.api_key = api_key or self.settings.get_api_key_for_provider(provider_name)
        self.base_url = (base_url or self.settings.openai_base_url).rstrip("/")
        self._external_client = client

    def _get_headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def _handle_http_error(self, exc: httpx.HTTPStatusError) -> None:
        status = exc.response.status_code
        text = exc.response.text
        if status in (401, 403):
            raise AuthenticationError(
                f"Authentication failed for {self.provider_name}: {text}",
                status_code=status,
                provider=self.provider_name,
            ) from exc
        if status == 429:
            retry_header = exc.response.headers.get("retry-after")
            retry_after = float(retry_header) if retry_header and retry_header.isdigit() else None
            raise RateLimitError(
                f"Rate limit exceeded for {self.provider_name}: {text}",
                status_code=status,
                provider=self.provider_name,
                retry_after=retry_after,
            ) from exc
        if status == 404:
            raise ModelNotFoundError(
                f"Model or endpoint not found on {self.provider_name}: {text}",
                status_code=status,
                provider=self.provider_name,
            ) from exc
        raise LLMProviderError(
            f"Error from {self.provider_name} (HTTP {status}): {text}",
            status_code=status,
            provider=self.provider_name,
        ) from exc

    async def complete(
        self,
        messages: list[ChatMessage],
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        **kwargs: Any,
    ) -> ChatResponse:
        target_model = model or self.settings.default_model
        target_temp = (
            temperature if temperature is not None else self.settings.default_temperature
        )

        payload: dict[str, Any] = {
            "model": target_model,
            "messages": [m.to_dict() for m in messages],
            "temperature": target_temp,
            **kwargs,
        }

        url = f"{self.base_url}/chat/completions"
        headers = self._get_headers()
        max_retries = self.settings.default_max_retries

        async def _send(http_client: httpx.AsyncClient) -> ChatResponse:
            for attempt in range(max_retries + 1):
                try:
                    response = await http_client.post(
                        url,
                        json=payload,
                        headers=headers,
                        timeout=self.settings.default_timeout_seconds,
                    )
                    response.raise_for_status()
                    data = response.json()

                    choices = data.get("choices") or [{}]
                    choice = choices[0] if choices else {}
                    message = choice.get("message") or {}
                    content = message.get("content", "") or ""

                    usage_data = data.get("usage") or {}
                    usage = TokenUsage(
                        prompt_tokens=usage_data.get("prompt_tokens", 0),
                        completion_tokens=usage_data.get("completion_tokens", 0),
                        total_tokens=usage_data.get("total_tokens", 0),
                    )

                    return ChatResponse(
                        content=content,
                        model=data.get("model", target_model),
                        usage=usage,
                        raw=data,
                    )
                except httpx.HTTPStatusError as exc:
                    if exc.response.status_code in (429, 500, 502, 503, 504) and attempt < max_retries:
                        backoff = 0.2 * (2 ** attempt)
                        logger.warning(
                            "Transient HTTP %s from %s. Retrying in %.2fs (attempt %d/%d)...",
                            exc.response.status_code,
                            self.provider_name,
                            backoff,
                            attempt + 1,
                            max_retries,
                        )
                        await asyncio.sleep(backoff)
                        continue
                    self._handle_http_error(exc)
                except httpx.RequestError as exc:
                    if attempt < max_retries:
                        backoff = 0.2 * (2 ** attempt)
                        logger.warning(
                            "Network connection error with %s: %s. Retrying in %.2fs (attempt %d/%d)...",
                            self.provider_name,
                            exc,
                            backoff,
                            attempt + 1,
                            max_retries,
                        )
                        await asyncio.sleep(backoff)
                        continue
                    raise LLMProviderError(
                        f"Network error connecting to {self.provider_name}: {exc}",
                        provider=self.provider_name,
                    ) from exc
            raise LLMProviderError(f"Exceeded max retries ({max_retries}) connecting to {self.provider_name}")

        if self._external_client:
            return await _send(self._external_client)

        async with httpx.AsyncClient() as http_client:
            return await _send(http_client)

    async def structured_complete(
        self,
        messages: list[ChatMessage],
        response_model: type[T],
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        **kwargs: Any,
    ) -> T:
        schema = response_model.model_json_schema()
        system_instruction = (
            "You MUST respond ONLY with a valid JSON object matching this JSON schema:\n"
            f"{json.dumps(schema, indent=2)}\n"
            "Do not output markdown code blocks, explanations, or any other text."
        )

        enhanced_messages = [ChatMessage(role=Role.SYSTEM, content=system_instruction), *messages]

        response = await self.complete(
            messages=enhanced_messages,
            model=model,
            temperature=temperature if temperature is not None else 0.0,
            response_format={"type": "json_object"},
            **kwargs,
        )

        content = response.content.strip()
        # Clean potential markdown wrapping if returned
        if content.startswith("```json"):
            content = content[7:]
        elif content.startswith("```"):
            content = content[3:]
        if content.endswith("```"):
            content = content[:-3]
        content = content.strip()

        try:
            return response_model.model_validate_json(content)
        except (ValidationError, json.JSONDecodeError) as exc:
            raise StructuredOutputError(
                f"Failed to validate model response into {response_model.__name__}: {exc}",
                raw_output=response.content,
            ) from exc


class GeminiClient(OpenAICompatibleClient):
    """Google Gemini client utilizing Gemini's official OpenAI-compatible endpoint."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        settings: Optional[Settings] = None,
        client: Optional[httpx.AsyncClient] = None,
    ) -> None:
        active_settings = settings or get_settings()
        key = api_key or active_settings.gemini_api_key
        # Gemini provides a direct OpenAI-compatible endpoint
        gemini_base = "https://generativelanguage.googleapis.com/v1beta/openai"
        super().__init__(
            api_key=key,
            base_url=gemini_base,
            provider_name="gemini",
            settings=active_settings,
            client=client,
        )


class OllamaClient(OpenAICompatibleClient):
    """Local Ollama client utilizing Ollama's OpenAI-compatible endpoint."""

    def __init__(
        self,
        base_url: Optional[str] = None,
        settings: Optional[Settings] = None,
        client: Optional[httpx.AsyncClient] = None,
    ) -> None:
        active_settings = settings or get_settings()
        root_url = (base_url or active_settings.ollama_base_url).rstrip("/")
        ollama_base = f"{root_url}/v1"
        super().__init__(
            api_key="ollama",  # dummy key for headers
            base_url=ollama_base,
            provider_name="ollama",
            settings=active_settings,
            client=client,
        )


class LLMFactory:
    """Factory to instantiate LLM clients based on configuration or provider name."""

    @staticmethod
    def create(
        provider: Optional[str] = None,
        settings: Optional[Settings] = None,
        client: Optional[httpx.AsyncClient] = None,
        **kwargs: Any,
    ) -> BaseLLMClient:
        cfg = settings or get_settings()
        target_provider = (provider or cfg.default_provider).lower().strip()

        if target_provider == "gemini":
            return GeminiClient(settings=cfg, client=client, **kwargs)
        if target_provider == "openai":
            return OpenAICompatibleClient(provider_name="openai", settings=cfg, client=client, **kwargs)
        if target_provider == "ollama":
            return OllamaClient(settings=cfg, client=client, **kwargs)
        if target_provider in ("custom", "generic"):
            return OpenAICompatibleClient(provider_name="custom", settings=cfg, client=client, **kwargs)

        raise ConfigurationError(
            f"Unsupported LLM provider: {target_provider!r}. "
            "Supported providers: 'gemini', 'openai', 'ollama', 'custom'."
        )
