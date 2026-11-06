"""Unified, lightweight LLM client abstraction supporting multiple providers and structured outputs."""

from __future__ import annotations

import asyncio
import json
import logging
from abc import ABC, abstractmethod
from enum import Enum
from typing import Any, Generic, Literal, Optional, Sequence, TypeVar, Union
import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from common_framework.core.config import Settings, get_settings
from common_framework.core.exceptions import (
    AllProvidersFailedError,
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
    fallback_attempts: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Prior failed attempts if this response was produced via a fallback chain",
    )


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


class AnthropicClient(BaseLLMClient):
    """Client for Anthropic's native Messages API (Claude 3.5 Sonnet, Haiku, Opus)."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        settings: Optional[Settings] = None,
        client: Optional[httpx.AsyncClient] = None,
    ) -> None:
        super().__init__(settings=settings)
        self.api_key = api_key or self.settings.anthropic_api_key
        default_url = getattr(self.settings, "anthropic_base_url", "https://api.anthropic.com/v1")
        self.base_url = (base_url or default_url).rstrip("/")
        self.provider_name = "anthropic"
        self._external_client = client

    def _get_headers(self) -> dict[str, str]:
        headers = {
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        if self.api_key:
            headers["x-api-key"] = self.api_key
        return headers

    def _handle_http_error(self, exc: httpx.HTTPStatusError) -> None:
        status_code = exc.response.status_code
        try:
            error_data = exc.response.json()
            message = error_data.get("error", {}).get("message", exc.response.text)
        except Exception:
            message = exc.response.text

        if status_code in (401, 403):
            raise AuthenticationError(
                f"Anthropic authentication failed: {message}",
                status_code=status_code,
                provider="anthropic",
            ) from exc
        if status_code == 429:
            retry_header = exc.response.headers.get("retry-after")
            retry_after = (
                float(retry_header)
                if retry_header and retry_header.replace(".", "", 1).isdigit()
                else None
            )
            raise RateLimitError(
                f"Anthropic rate limit exceeded: {message}",
                status_code=429,
                provider="anthropic",
                retry_after=retry_after,
            ) from exc
        if status_code == 404:
            raise ModelNotFoundError(
                f"Anthropic model not found: {message}",
                status_code=404,
                provider="anthropic",
            ) from exc

        raise LLMProviderError(
            f"Anthropic API error (HTTP {status_code}): {message}",
            status_code=status_code,
            provider="anthropic",
        ) from exc

    async def complete(
        self,
        messages: list[ChatMessage],
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: int = 4096,
        **kwargs: Any,
    ) -> ChatResponse:
        target_model = model or "claude-3-5-sonnet-latest"
        target_temp = (
            temperature if temperature is not None else self.settings.default_temperature
        )

        system_prompts: list[str] = []
        anthropic_messages: list[dict[str, Any]] = []

        for m in messages:
            role_val = m.role.value if isinstance(m.role, Role) else str(m.role)
            if role_val == "system":
                system_prompts.append(m.content)
            elif role_val in ("user", "tool"):
                anthropic_messages.append({"role": "user", "content": m.content})
            elif role_val == "assistant":
                anthropic_messages.append({"role": "assistant", "content": m.content})

        if not anthropic_messages:
            anthropic_messages.append({"role": "user", "content": "Hello"})

        payload: dict[str, Any] = {
            "model": target_model,
            "messages": anthropic_messages,
            "max_tokens": kwargs.pop("max_tokens", max_tokens),
            "temperature": target_temp,
            **kwargs,
        }
        if system_prompts:
            payload["system"] = "\n\n".join(system_prompts)

        url = f"{self.base_url}/messages"
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

                    content_blocks = data.get("content") or []
                    text_parts = [
                        b.get("text", "")
                        for b in content_blocks
                        if isinstance(b, dict) and b.get("type") == "text"
                    ]
                    content = "".join(text_parts)

                    usage_data = data.get("usage") or {}
                    input_toks = usage_data.get("input_tokens", 0)
                    output_toks = usage_data.get("output_tokens", 0)
                    usage = TokenUsage(
                        prompt_tokens=input_toks,
                        completion_tokens=output_toks,
                        total_tokens=input_toks + output_toks,
                    )

                    return ChatResponse(
                        content=content,
                        model=data.get("model", target_model),
                        usage=usage,
                        raw=data,
                    )
                except httpx.HTTPStatusError as exc:
                    # Anthropic uses 429 and 529 for overloaded
                    if (
                        exc.response.status_code in (429, 500, 502, 503, 504, 529)
                        and attempt < max_retries
                    ):
                        backoff = 0.2 * (2**attempt)
                        logger.warning(
                            "Transient HTTP %s from Anthropic. Retrying in %.2fs (attempt %d/%d)...",
                            exc.response.status_code,
                            backoff,
                            attempt + 1,
                            max_retries,
                        )
                        await asyncio.sleep(backoff)
                        continue
                    self._handle_http_error(exc)
                except httpx.RequestError as exc:
                    if attempt < max_retries:
                        backoff = 0.2 * (2**attempt)
                        logger.warning(
                            "Network connection error with Anthropic: %s. Retrying in %.2fs (attempt %d/%d)...",
                            exc,
                            backoff,
                            attempt + 1,
                            max_retries,
                        )
                        await asyncio.sleep(backoff)
                        continue
                    raise LLMProviderError(
                        f"Network error connecting to Anthropic: {exc}",
                        provider="anthropic",
                    ) from exc
            raise LLMProviderError(f"Exceeded max retries ({max_retries}) connecting to Anthropic")

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
            **kwargs,
        )

        content = response.content.strip()
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
                f"Failed to validate Anthropic response into {response_model.__name__}: {exc}",
                raw_output=response.content,
            ) from exc


class FallbackCandidate:
    """A configured candidate target within a fallback cascade."""

    def __init__(
        self,
        client: BaseLLMClient,
        model: Optional[str] = None,
        provider_name: Optional[str] = None,
    ) -> None:
        self.client = client
        self.model = model
        self.provider_name = provider_name or getattr(
            client, "provider_name", client.__class__.__name__
        )

    def __repr__(self) -> str:
        return f"<FallbackCandidate provider={self.provider_name!r} model={self.model!r}>"


class FallbackLLMClient(BaseLLMClient):
    """Resilient LLM client executing an automatic fallback cascade across multiple providers/models.

    If a candidate encounters a recoverable failure (RateLimitError, LLMProviderError,
    network connection error, or timeout), execution immediately fails over to the
    next candidate in sequence.
    """

    DEFAULT_FALLBACK_EXCEPTIONS: tuple[type[Exception], ...] = (
        RateLimitError,
        LLMProviderError,
        httpx.RequestError,
        TimeoutError,
        asyncio.TimeoutError,
    )

    def __init__(
        self,
        clients: Sequence[Union[BaseLLMClient, tuple[BaseLLMClient, Optional[str]], FallbackCandidate]],
        fallback_on: Optional[tuple[type[Exception], ...]] = None,
        settings: Optional[Settings] = None,
    ) -> None:
        super().__init__(settings=settings)
        if not clients:
            raise ConfigurationError("FallbackLLMClient requires at least one candidate client.")

        self.candidates: list[FallbackCandidate] = []
        for item in clients:
            if isinstance(item, FallbackCandidate):
                self.candidates.append(item)
            elif isinstance(item, tuple):
                client_obj, model_name = item
                self.candidates.append(FallbackCandidate(client=client_obj, model=model_name))
            elif isinstance(item, BaseLLMClient):
                self.candidates.append(FallbackCandidate(client=item))
            else:
                raise ConfigurationError(f"Invalid fallback candidate: {item!r}")

        self.fallback_exceptions = fallback_on or self.DEFAULT_FALLBACK_EXCEPTIONS

    @property
    def primary_client(self) -> BaseLLMClient:
        """Return the primary (first) candidate client in the cascade."""
        return self.candidates[0].client

    async def complete(
        self,
        messages: list[ChatMessage],
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        **kwargs: Any,
    ) -> ChatResponse:
        attempts: list[dict[str, Any]] = []
        last_exception: Optional[Exception] = None

        for idx, candidate in enumerate(self.candidates):
            effective_model = candidate.model or model
            provider_label = candidate.provider_name
            try:
                response = await candidate.client.complete(
                    messages=messages,
                    model=effective_model,
                    temperature=temperature,
                    **kwargs,
                )
                if attempts:
                    response.fallback_attempts = attempts
                    logger.info(
                        "Fallback succeeded on candidate %d/%d (%s, model=%s) after %d failure(s).",
                        idx + 1,
                        len(self.candidates),
                        provider_label,
                        response.model,
                        len(attempts),
                    )
                return response
            except self.fallback_exceptions as exc:
                last_exception = exc
                err_record = {
                    "candidate_index": idx,
                    "provider": provider_label,
                    "model": effective_model,
                    "error": str(exc),
                    "exception_type": type(exc).__name__,
                    "status_code": getattr(exc, "status_code", None),
                }
                attempts.append(err_record)
                logger.warning(
                    "Fallback candidate %d/%d (%s, model=%s) failed with %s (%s). Failing over to next candidate...",
                    idx + 1,
                    len(self.candidates),
                    provider_label,
                    effective_model,
                    type(exc).__name__,
                    exc,
                )
                continue

        raise AllProvidersFailedError(
            message=f"All {len(self.candidates)} LLM candidates in fallback sequence failed. Last error: {last_exception}",
            errors=attempts,
        ) from last_exception

    async def structured_complete(
        self,
        messages: list[ChatMessage],
        response_model: type[T],
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        **kwargs: Any,
    ) -> T:
        attempts: list[dict[str, Any]] = []
        last_exception: Optional[Exception] = None

        for idx, candidate in enumerate(self.candidates):
            effective_model = candidate.model or model
            provider_label = candidate.provider_name
            try:
                result = await candidate.client.structured_complete(
                    messages=messages,
                    response_model=response_model,
                    model=effective_model,
                    temperature=temperature,
                    **kwargs,
                )
                if attempts:
                    logger.info(
                        "Structured completion succeeded on candidate %d/%d (%s) after %d failure(s).",
                        idx + 1,
                        len(self.candidates),
                        provider_label,
                        len(attempts),
                    )
                return result
            except self.fallback_exceptions as exc:
                last_exception = exc
                err_record = {
                    "candidate_index": idx,
                    "provider": provider_label,
                    "model": effective_model,
                    "error": str(exc),
                    "exception_type": type(exc).__name__,
                    "status_code": getattr(exc, "status_code", None),
                }
                attempts.append(err_record)
                logger.warning(
                    "Fallback candidate %d/%d (%s) failed structured completion: %s. Failing over...",
                    idx + 1,
                    len(self.candidates),
                    provider_label,
                    exc,
                )
                continue

        raise AllProvidersFailedError(
            message=f"All {len(self.candidates)} LLM candidates in fallback sequence failed structured completion. Last error: {last_exception}",
            errors=attempts,
        ) from last_exception


class LLMFactory:
    """Factory to instantiate LLM clients based on configuration or provider name."""

    @staticmethod
    def create(
        provider: Optional[str] = None,
        settings: Optional[Settings] = None,
        client: Optional[httpx.AsyncClient] = None,
        with_fallbacks: bool = False,
        **kwargs: Any,
    ) -> BaseLLMClient:
        cfg = settings or get_settings()
        target_provider = (provider or cfg.default_provider).lower().strip()

        if with_fallbacks or (provider is None and cfg.fallback_providers):
            chain = [target_provider] + [p for p in cfg.fallback_providers if p != target_provider]
            if len(chain) > 1:
                return LLMFactory.create_with_fallbacks(
                    providers=chain,
                    settings=cfg,
                    client=client,
                    **kwargs,
                )

        if target_provider == "gemini":
            return GeminiClient(settings=cfg, client=client, **kwargs)
        if target_provider == "openai":
            return OpenAICompatibleClient(provider_name="openai", settings=cfg, client=client, **kwargs)
        if target_provider == "anthropic":
            return AnthropicClient(settings=cfg, client=client, **kwargs)
        if target_provider == "ollama":
            return OllamaClient(settings=cfg, client=client, **kwargs)
        if target_provider in ("custom", "generic"):
            return OpenAICompatibleClient(provider_name="custom", settings=cfg, client=client, **kwargs)

        raise ConfigurationError(
            f"Unsupported LLM provider: {target_provider!r}. "
            "Supported providers: 'gemini', 'openai', 'anthropic', 'ollama', 'custom'."
        )

    @staticmethod
    def create_with_fallbacks(
        providers: Sequence[str],
        settings: Optional[Settings] = None,
        client: Optional[httpx.AsyncClient] = None,
        fallback_on: Optional[tuple[type[Exception], ...]] = None,
        **kwargs: Any,
    ) -> FallbackLLMClient:
        """Create a FallbackLLMClient cascading across the given provider sequence."""
        cfg = settings or get_settings()
        if not providers:
            raise ConfigurationError("create_with_fallbacks requires at least one provider.")

        clients = [
            LLMFactory.create(provider=p, settings=cfg, client=client, with_fallbacks=False, **kwargs)
            for p in providers
        ]
        return FallbackLLMClient(clients=clients, fallback_on=fallback_on, settings=cfg)

