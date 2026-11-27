"""Unified, lightweight LLM client abstraction supporting multiple providers and structured outputs."""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import random
import re
from abc import ABC, abstractmethod
from enum import Enum
from typing import TYPE_CHECKING, Any, AsyncIterator, Callable, Generic, Literal, Optional, Sequence, TypeVar, Union
import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from common_framework.core._async import HttpClientMixin
from common_framework.core.config import Settings, get_settings
from common_framework.core.exceptions import (
    AllProvidersFailedError,
    AuthenticationError,
    CircuitBreakerOpenError,
    ConfigurationError,
    LLMProviderError,
    ModelNotFoundError,
    RateLimitError,
    StructuredOutputError,
)
from common_framework.core.security import validate_provider_base_url

if TYPE_CHECKING:
    from common_framework.core.resilience import (
        CircuitBreaker,
        CircuitBreakerConfig,
        CircuitBreakerStats,
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
    cached_tokens: int = 0


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


class ChatStreamChunk(BaseModel):
    """A single token delta chunk emitted during continuous generation."""

    delta: str = ""
    model: Optional[str] = None
    finish_reason: Optional[str] = None
    usage: Optional[TokenUsage] = None


def _extract_json_payload(raw: str) -> str:
    """Extract a JSON payload string from LLM output, stripping preambles and markdown fences."""
    text = raw.strip()
    if (text.startswith("{") and text.endswith("}")) or (text.startswith("[") and text.endswith("]")):
        return text

    # Check for markdown code fences (```json ... ``` or ``` ... ```)
    fence_pattern = re.compile(r"```(?:json)?\s*([\s\S]*?)\s*```", re.IGNORECASE)
    match = fence_pattern.search(text)
    if match:
        extracted = match.group(1).strip()
        if (extracted.startswith("{") and extracted.endswith("}")) or (extracted.startswith("[") and extracted.endswith("]")):
            return extracted
        return extracted

    # Find outermost curly braces or square brackets
    start_brace = text.find("{")
    start_bracket = text.find("[")

    if start_brace != -1 and (start_bracket == -1 or start_brace < start_bracket):
        end_brace = text.rfind("}")
        if end_brace > start_brace:
            return text[start_brace : end_brace + 1].strip()
    elif start_bracket != -1:
        end_bracket = text.rfind("]")
        if end_bracket > start_bracket:
            return text[start_bracket : end_bracket + 1].strip()

    return text


def _parse_retry_after(headers: httpx.Headers) -> Optional[float]:
    """Parse a numeric Retry-After header value in seconds, returning None if absent/invalid."""
    raw = headers.get("retry-after")
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


async def _post_json_with_retry(
    http_client: httpx.AsyncClient,
    url: str,
    payload: dict[str, Any],
    headers: dict[str, str],
    timeout: float,
    max_retries: int,
    provider_name: str,
    retryable_statuses: tuple[int, ...],
    error_handler: Callable[[httpx.HTTPStatusError], None],
) -> dict[str, Any]:
    """POST JSON payload with exponential backoff, jitter, and Retry-After support."""
    for attempt in range(max_retries + 1):
        try:
            response = await http_client.post(
                url,
                json=payload,
                headers=headers,
                timeout=timeout,
            )
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code in retryable_statuses and attempt < max_retries:
                backoff = 0.2 * (2 ** attempt) + random.uniform(0.01, 0.1)
                retry_after = _parse_retry_after(exc.response.headers)
                if retry_after is not None:
                    backoff = max(retry_after, backoff)
                logger.warning(
                    "Transient HTTP %s from %s. Retrying in %.2fs (attempt %d/%d)...",
                    exc.response.status_code,
                    provider_name,
                    backoff,
                    attempt + 1,
                    max_retries,
                )
                await asyncio.sleep(backoff)
                continue
            error_handler(exc)
        except httpx.RequestError as exc:
            if attempt < max_retries:
                backoff = 0.2 * (2 ** attempt) + random.uniform(0.01, 0.1)
                logger.warning(
                    "Network connection error with %s: %s. Retrying in %.2fs (attempt %d/%d)...",
                    provider_name,
                    exc,
                    backoff,
                    attempt + 1,
                    max_retries,
                )
                await asyncio.sleep(backoff)
                continue
            raise LLMProviderError(
                f"Network error connecting to {provider_name}: {exc}",
                provider=provider_name,
            ) from exc
    raise LLMProviderError(f"Exceeded max retries ({max_retries}) connecting to {provider_name}")


class BaseLLMClient(ABC):
    """Abstract base class for all LLM provider clients."""

    def __init__(self, settings: Optional[Settings] = None) -> None:
        self.settings = settings or get_settings()

    def _record_cost_if_enabled(
        self,
        response: ChatResponse,
        metadata: Optional[dict[str, Any]] = None,
    ) -> None:
        """Automatically record token usage and cost in the global cost tracker."""
        if response.usage and (response.usage.total_tokens or response.usage.prompt_tokens):
            try:
                from common_framework.observability.cost import global_cost_tracker

                global_cost_tracker.record_usage_sync(
                    model=response.model,
                    usage=response.usage,
                    metadata=metadata,
                )
            except Exception:
                pass

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

    async def complete_structured(
        self,
        messages: list[ChatMessage],
        response_model: type[T],
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        **kwargs: Any,
    ) -> T:
        """Alias for structured_complete for ergonomic compatibility."""
        return await self.structured_complete(
            messages=messages,
            response_model=response_model,
            model=model,
            temperature=temperature,
            **kwargs,
        )

    async def complete_stream(
        self,
        messages: list[ChatMessage],
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        **kwargs: Any,
    ) -> AsyncIterator[ChatStreamChunk]:
        """Execute a streaming completion yielding token chunks asynchronously.

        Default implementation falls back to complete() and yields the full response as a chunk.
        Subclasses override this to provide real-time token-by-token streaming.
        """
        response = await self.complete(
            messages=messages,
            model=model,
            temperature=temperature,
            **kwargs,
        )
        yield ChatStreamChunk(
            delta=response.content,
            model=response.model,
            usage=response.usage,
            finish_reason="stop",
        )

    async def _structured_complete_via_prompt(
        self,
        messages: list[ChatMessage],
        response_model: type[T],
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        **kwargs: Any,
    ) -> T:
        """Execute structured completion by injecting JSON schema instructions and validating."""
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
        content = _extract_json_payload(response.content)
        try:
            return response_model.model_validate_json(content)
        except (ValidationError, json.JSONDecodeError) as exc:
            raise StructuredOutputError(
                f"Failed to validate model response into {response_model.__name__}: {exc}",
                raw_output=response.content,
            ) from exc

    async def aclose(self) -> None:
        """Release underlying network resources and close persistent HTTP connection pools."""
        pass

    async def __aenter__(self: Any) -> Any:
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        await self.aclose()


class OpenAICompatibleClient(HttpClientMixin, BaseLLMClient):
    """Client for OpenAI and any OpenAI-compatible endpoint (Ollama, Gemini OpenAI endpoint, vLLM, DeepSeek)."""

    def __init__(
        self,
        api_key: Optional[Union[str, SecretStr]] = None,
        base_url: Optional[str] = None,
        provider_name: str = "openai",
        settings: Optional[Settings] = None,
        client: Optional[httpx.AsyncClient] = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(settings)
        self.provider_name = provider_name
        raw_key = api_key or self.settings.get_api_key_for_provider(provider_name)
        self.api_key = (
            raw_key.get_secret_value() if hasattr(raw_key, "get_secret_value") else raw_key
        )
        self.base_url = validate_provider_base_url(base_url or self.settings.openai_base_url)
        self._init_http_client(client=client)

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
            raise RateLimitError(
                f"Rate limit exceeded for {self.provider_name}: {text}",
                status_code=status,
                provider=self.provider_name,
                retry_after=_parse_retry_after(exc.response.headers),
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
        data = await _post_json_with_retry(
            http_client=self._get_client(),
            url=url,
            payload=payload,
            headers=self._get_headers(),
            timeout=self.settings.default_timeout_seconds,
            max_retries=self.settings.default_max_retries,
            provider_name=self.provider_name,
            retryable_statuses=(429, 500, 502, 503, 504),
            error_handler=self._handle_http_error,
        )

        choices = data.get("choices") or [{}]
        choice = choices[0] if choices else {}
        message = choice.get("message") or {}
        content = message.get("content", "") or ""

        usage_data = data.get("usage") or {}
        prompt_tokens_details = usage_data.get("prompt_tokens_details") or {}
        cached_tokens = (
            prompt_tokens_details.get("cached_tokens")
            or usage_data.get("cached_tokens")
            or usage_data.get("cachedContentTokenCount", 0)
            or 0
        )
        usage = TokenUsage(
            prompt_tokens=usage_data.get("prompt_tokens", 0),
            completion_tokens=usage_data.get("completion_tokens", 0),
            total_tokens=usage_data.get("total_tokens", 0),
            cached_tokens=cached_tokens,
        )

        res = ChatResponse(
            content=content,
            model=data.get("model", target_model),
            usage=usage,
            raw=data,
        )
        self._record_cost_if_enabled(res)
        return res

    async def structured_complete(
        self,
        messages: list[ChatMessage],
        response_model: type[T],
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        **kwargs: Any,
    ) -> T:
        return await self._structured_complete_via_prompt(
            messages=messages,
            response_model=response_model,
            model=model,
            temperature=temperature,
            response_format={"type": "json_object"},
            **kwargs,
        )

    async def complete_stream(
        self,
        messages: list[ChatMessage],
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        **kwargs: Any,
    ) -> AsyncIterator[ChatStreamChunk]:
        target_model = model or self.settings.default_model
        target_temp = (
            temperature if temperature is not None else self.settings.default_temperature
        )

        payload: dict[str, Any] = {
            "model": target_model,
            "messages": [m.to_dict() for m in messages],
            "temperature": target_temp,
            "stream": True,
            **kwargs,
        }
        if "stream_options" not in payload:
            payload["stream_options"] = {"include_usage": True}

        url = f"{self.base_url}/chat/completions"
        headers = self._get_headers()

        async def _stream(http_client: httpx.AsyncClient) -> AsyncIterator[ChatStreamChunk]:
            accumulated_usage: Optional[TokenUsage] = None
            try:
                async with http_client.stream(
                    "POST",
                    url,
                    json=payload,
                    headers=headers,
                    timeout=self.settings.default_timeout_seconds,
                ) as response:
                    if response.status_code >= 400:
                        await response.aread()
                        self._handle_http_error(
                            httpx.HTTPStatusError(
                                f"Streaming request failed with status {response.status_code}",
                                request=response.request,
                                response=response,
                            )
                        )

                    async for line in response.aiter_lines():
                        line = line.strip()
                        if not line or not line.startswith("data:"):
                            continue
                        data_str = line[5:].strip()
                        if data_str == "[DONE]":
                            break
                        try:
                            chunk_data = json.loads(data_str)
                        except json.JSONDecodeError:
                            continue

                        choices = chunk_data.get("choices") or []
                        chunk_model = chunk_data.get("model", target_model)

                        usage = None
                        if "usage" in chunk_data and chunk_data["usage"]:
                            u = chunk_data["usage"]
                            usage = TokenUsage(
                                prompt_tokens=u.get("prompt_tokens", 0),
                                completion_tokens=u.get("completion_tokens", 0),
                                total_tokens=u.get("total_tokens", 0),
                            )
                            accumulated_usage = usage

                        if choices:
                            choice = choices[0]
                            delta_obj = choice.get("delta") or {}
                            content_piece = delta_obj.get("content") or ""
                            finish_reason = choice.get("finish_reason")

                            yield ChatStreamChunk(
                                delta=content_piece,
                                model=chunk_model,
                                finish_reason=finish_reason,
                                usage=usage,
                            )
                        elif usage:
                            # Stream usage-only chunk
                            yield ChatStreamChunk(
                                delta="",
                                model=chunk_model,
                                usage=usage,
                            )
            except httpx.RequestError as exc:
                raise LLMProviderError(
                    f"Network error streaming from {self.provider_name}: {exc}",
                    provider=self.provider_name,
                ) from exc
            finally:
                if accumulated_usage:
                    self._record_cost_if_enabled(
                        ChatResponse(content="", model=target_model, usage=accumulated_usage)
                    )

        async for chunk in _stream(self._get_client()):
            yield chunk



class GeminiClient(OpenAICompatibleClient):
    """Google Gemini client utilizing Gemini's official OpenAI-compatible endpoint."""

    def __init__(
        self,
        api_key: Optional[Union[str, SecretStr]] = None,
        settings: Optional[Settings] = None,
        client: Optional[httpx.AsyncClient] = None,
        **kwargs: Any,
    ) -> None:
        active_settings = settings or get_settings()
        key = api_key or active_settings.get_api_key_for_provider("gemini")
        # Gemini provides a direct OpenAI-compatible endpoint
        gemini_base = kwargs.pop("base_url", None) or "https://generativelanguage.googleapis.com/v1beta/openai"
        super().__init__(
            api_key=key,
            base_url=gemini_base,
            provider_name="gemini",
            settings=active_settings,
            client=client,
            **kwargs,
        )


class OllamaClient(OpenAICompatibleClient):
    """Local Ollama client utilizing Ollama's OpenAI-compatible endpoint."""

    def __init__(
        self,
        base_url: Optional[str] = None,
        settings: Optional[Settings] = None,
        client: Optional[httpx.AsyncClient] = None,
        **kwargs: Any,
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
            **kwargs,
        )


class AnthropicClient(HttpClientMixin, BaseLLMClient):
    """Client for Anthropic's native Messages API (Claude 3.5 Sonnet, Haiku, Opus)."""

    def __init__(
        self,
        api_key: Optional[Union[str, SecretStr]] = None,
        base_url: Optional[str] = None,
        settings: Optional[Settings] = None,
        client: Optional[httpx.AsyncClient] = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(settings=settings)
        raw_key = api_key or self.settings.get_api_key_for_provider("anthropic")
        self.api_key: Optional[str] = (
            raw_key.get_secret_value() if hasattr(raw_key, "get_secret_value") else raw_key
        )
        default_url = getattr(self.settings, "anthropic_base_url", "https://api.anthropic.com/v1")
        self.base_url = validate_provider_base_url(base_url or default_url)
        self.provider_name = "anthropic"
        self._init_http_client(client=client)

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
            raise RateLimitError(
                f"Anthropic rate limit exceeded: {message}",
                status_code=429,
                provider="anthropic",
                retry_after=_parse_retry_after(exc.response.headers),
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

    def _build_payload(
        self,
        messages: list[ChatMessage],
        model: Optional[str],
        temperature: Optional[float],
        max_tokens: int,
        stream: bool = False,
        **kwargs: Any,
    ) -> tuple[str, dict[str, Any]]:
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
        if stream:
            payload["stream"] = True
        if system_prompts:
            payload["system"] = "\n\n".join(system_prompts)
        return target_model, payload

    async def complete(
        self,
        messages: list[ChatMessage],
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: int = 4096,
        **kwargs: Any,
    ) -> ChatResponse:
        target_model, payload = self._build_payload(
            messages=messages,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            **kwargs,
        )
        data = await _post_json_with_retry(
            http_client=self._get_client(),
            url=f"{self.base_url}/messages",
            payload=payload,
            headers=self._get_headers(),
            timeout=self.settings.default_timeout_seconds,
            max_retries=self.settings.default_max_retries,
            provider_name="Anthropic",
            retryable_statuses=(429, 500, 502, 503, 504, 529),
            error_handler=self._handle_http_error,
        )

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
        cached_toks = usage_data.get("cache_read_input_tokens", 0) or 0
        usage = TokenUsage(
            prompt_tokens=input_toks,
            completion_tokens=output_toks,
            total_tokens=input_toks + output_toks,
            cached_tokens=cached_toks,
        )

        res = ChatResponse(
            content=content,
            model=data.get("model", target_model),
            usage=usage,
            raw=data,
        )
        self._record_cost_if_enabled(res)
        return res

    async def structured_complete(
        self,
        messages: list[ChatMessage],
        response_model: type[T],
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        **kwargs: Any,
    ) -> T:
        return await self._structured_complete_via_prompt(
            messages=messages,
            response_model=response_model,
            model=model,
            temperature=temperature,
            **kwargs,
        )

    async def complete_stream(
        self,
        messages: list[ChatMessage],
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: int = 4096,
        **kwargs: Any,
    ) -> AsyncIterator[ChatStreamChunk]:
        target_model, payload = self._build_payload(
            messages=messages,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            stream=True,
            **kwargs,
        )

        url = f"{self.base_url}/messages"
        headers = self._get_headers()

        async def _stream(http_client: httpx.AsyncClient) -> AsyncIterator[ChatStreamChunk]:
            prompt_tokens = 0
            completion_tokens = 0
            try:
                async with http_client.stream(
                    "POST",
                    url,
                    json=payload,
                    headers=headers,
                    timeout=self.settings.default_timeout_seconds,
                ) as response:
                    if response.status_code >= 400:
                        await response.aread()
                        self._handle_http_error(
                            httpx.HTTPStatusError(
                                f"Streaming request failed with status {response.status_code}",
                                request=response.request,
                                response=response,
                            )
                        )

                    async for line in response.aiter_lines():
                        line = line.strip()
                        if not line or not line.startswith("data:"):
                            continue
                        data_str = line[5:].strip()
                        try:
                            event_data = json.loads(data_str)
                        except json.JSONDecodeError:
                            continue

                        event_type = event_data.get("type")
                        if event_type == "message_start":
                            message_obj = event_data.get("message") or {}
                            usage_obj = message_obj.get("usage") or {}
                            prompt_tokens = usage_obj.get("input_tokens", 0)
                        elif event_type == "content_block_delta":
                            delta_obj = event_data.get("delta") or {}
                            text_piece = delta_obj.get("text") or ""
                            yield ChatStreamChunk(delta=text_piece, model=target_model)
                        elif event_type == "message_delta":
                            delta_obj = event_data.get("delta") or {}
                            stop_reason = delta_obj.get("stop_reason")
                            usage = None
                            if "usage" in event_data and event_data["usage"]:
                                u = event_data["usage"]
                                completion_tokens = u.get("output_tokens", 0)
                                total_toks = prompt_tokens + completion_tokens
                                usage = TokenUsage(
                                    prompt_tokens=prompt_tokens,
                                    completion_tokens=completion_tokens,
                                    total_tokens=total_toks,
                                )
                            yield ChatStreamChunk(
                                delta="",
                                model=target_model,
                                finish_reason=stop_reason,
                                usage=usage,
                            )
                        elif event_type == "error":
                            err_info = event_data.get("error") or {}
                            err_msg = err_info.get("message") or str(err_info)
                            raise LLMProviderError(
                                f"Anthropic streaming mid-stream error: {err_msg}",
                                provider="anthropic",
                            )
            except httpx.RequestError as exc:
                raise LLMProviderError(
                    f"Network error streaming from Anthropic: {exc}",
                    provider="anthropic",
                ) from exc
            finally:
                if prompt_tokens > 0 or completion_tokens > 0:
                    final_usage = TokenUsage(
                        prompt_tokens=prompt_tokens,
                        completion_tokens=completion_tokens,
                        total_tokens=prompt_tokens + completion_tokens,
                    )
                    self._record_cost_if_enabled(
                        ChatResponse(content="", model=target_model, usage=final_usage)
                    )

        async for chunk in _stream(self._get_client()):
            yield chunk



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
        enable_circuit_breaker: bool = False,
        circuit_breaker_config: Optional[CircuitBreakerConfig] = None,
        circuit_breakers: Optional[Sequence[Optional[CircuitBreaker]]] = None,
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

        if circuit_breakers is not None:
            if len(circuit_breakers) != len(self.candidates):
                raise ConfigurationError(
                    "circuit_breakers length must match the number of fallback candidates."
                )
            self.circuit_breakers = list(circuit_breakers)
        elif enable_circuit_breaker or circuit_breaker_config is not None:
            from common_framework.core.resilience import CircuitBreaker

            self.circuit_breakers = [
                CircuitBreaker(
                    name=f"fallback:{idx}:{c.provider_name}",
                    config=circuit_breaker_config,
                    failure_exceptions=self.fallback_exceptions,
                )
                for idx, c in enumerate(self.candidates)
            ]
        else:
            self.circuit_breakers = [None] * len(self.candidates)

    def circuit_breaker_stats(self) -> list[CircuitBreakerStats]:
        """Return stats snapshots for all configured candidate circuit breakers."""
        return [cb.stats() for cb in self.circuit_breakers if cb is not None]

    @property
    def primary_client(self) -> BaseLLMClient:
        """Return the primary (first) candidate client in the cascade."""
        return self.candidates[0].client

    async def aclose(self) -> None:
        """Close connection pools across all configured candidate clients."""
        for candidate in self.candidates:
            await candidate.client.aclose()

    @staticmethod
    def _build_error_record(
        idx: int,
        provider: str,
        model: Optional[str],
        exc: Exception,
    ) -> dict[str, Any]:
        return {
            "candidate_index": idx,
            "provider": provider,
            "model": model,
            "error": str(exc),
            "exception_type": type(exc).__name__,
            "status_code": getattr(exc, "status_code", None),
        }

    async def _run_cascade(
        self,
        operation_label: str,
        call_fn: Callable[[FallbackCandidate, Optional[str]], Any],
    ) -> tuple[Any, list[dict[str, Any]]]:
        attempts: list[dict[str, Any]] = []
        last_exception: Optional[Exception] = None

        caught_exceptions = (CircuitBreakerOpenError, *self.fallback_exceptions)
        for idx, candidate in enumerate(self.candidates):
            effective_model = candidate.model
            provider_label = candidate.provider_name
            breaker = self.circuit_breakers[idx]
            try:
                if breaker is not None:
                    result = await breaker.call(call_fn, candidate, effective_model)
                else:
                    result = await call_fn(candidate, effective_model)
                if attempts:
                    logger.info(
                        "%s succeeded on candidate %d/%d (%s) after %d failure(s).",
                        operation_label,
                        idx + 1,
                        len(self.candidates),
                        provider_label,
                        len(attempts),
                    )
                return result, attempts
            except caught_exceptions as exc:
                last_exception = exc
                attempts.append(self._build_error_record(idx, provider_label, effective_model, exc))
                logger.warning(
                    "Fallback candidate %d/%d (%s) failed %s: %s. Failing over...",
                    idx + 1,
                    len(self.candidates),
                    provider_label,
                    operation_label.lower(),
                    exc,
                )

        raise AllProvidersFailedError(
            message=f"All {len(self.candidates)} LLM candidates in fallback sequence failed {operation_label.lower()}. Last error: {last_exception}",
            errors=attempts,
        ) from last_exception

    async def complete(
        self,
        messages: list[ChatMessage],
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        **kwargs: Any,
    ) -> ChatResponse:
        response, attempts = await self._run_cascade(
            "Completion",
            lambda c, m: c.client.complete(
                messages=messages,
                model=m or model,
                temperature=temperature,
                **kwargs,
            ),
        )
        if attempts:
            response.fallback_attempts = attempts
        return response

    async def structured_complete(
        self,
        messages: list[ChatMessage],
        response_model: type[T],
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        **kwargs: Any,
    ) -> T:
        result, _ = await self._run_cascade(
            "Structured completion",
            lambda c, m: c.client.structured_complete(
                messages=messages,
                response_model=response_model,
                model=m or model,
                temperature=temperature,
                **kwargs,
            ),
        )
        return result

    async def complete_stream(
        self,
        messages: list[ChatMessage],
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        **kwargs: Any,
    ) -> AsyncIterator[ChatStreamChunk]:
        attempts: list[dict[str, Any]] = []
        last_exception: Optional[Exception] = None

        caught_exceptions = (CircuitBreakerOpenError, *self.fallback_exceptions)
        for idx, candidate in enumerate(self.candidates):
            effective_model = candidate.model or model
            provider_label = candidate.provider_name
            breaker = self.circuit_breakers[idx]
            started = False
            is_probe = False
            stream_finished = False
            try:
                if breaker is not None:
                    is_probe = await breaker.acquire()
                stream_iter = candidate.client.complete_stream(
                    messages=messages,
                    model=effective_model,
                    temperature=temperature,
                    **kwargs,
                )
                async for chunk in stream_iter:
                    started = True
                    yield chunk
                if breaker is not None:
                    await breaker.record_success(is_probe=is_probe)
                stream_finished = True
                return
            except caught_exceptions as exc:
                if breaker is not None:
                    await breaker.record_failure(exc, is_probe=is_probe)
                stream_finished = True
                if started:
                    # Once chunks have been emitted to the caller, we cannot rewind and restart
                    raise
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
                    "Fallback streaming candidate %d/%d (%s) failed before sending data: %s. Failing over...",
                    idx + 1,
                    len(self.candidates),
                    provider_label,
                    exc,
                )
                continue
            except Exception as exc:
                if breaker is not None:
                    await breaker.record_failure(exc, is_probe=is_probe)
                stream_finished = True
                raise
            finally:
                if breaker is not None and is_probe and not stream_finished:
                    await breaker.release_probe()

        raise AllProvidersFailedError(
            message=f"All {len(self.candidates)} LLM candidates in fallback sequence failed streaming. Last error: {last_exception}",
            errors=attempts,
        ) from last_exception



class LLMFactory:
    """Factory to instantiate LLM clients based on configuration or provider name."""

    @staticmethod
    def _safe_instantiate(
        cls: type[BaseLLMClient],
        **kwargs: Any,
    ) -> BaseLLMClient:
        """Instantiate a client class, inspecting its constructor signature to forward supported parameters cleanly."""
        sig = inspect.signature(cls.__init__)
        has_varkw = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values())
        if has_varkw:
            return cls(**kwargs)

        accepted = {
            name
            for name, p in sig.parameters.items()
            if p.kind in (inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY)
        }
        filtered = {k: v for k, v in kwargs.items() if k in accepted}
        instance = cls(**filtered)
        for k, v in kwargs.items():
            if k not in accepted and not hasattr(instance, k):
                try:
                    setattr(instance, k, v)
                except Exception:
                    pass
        return instance

    @staticmethod
    def _wrap_with_circuit_breaker(
        instance: BaseLLMClient,
        circuit_breaker: Union[bool, CircuitBreaker, CircuitBreakerConfig],
        provider_label: str,
    ) -> BaseLLMClient:
        if not circuit_breaker:
            return instance
        from common_framework.core.resilience import (
            CircuitBreaker,
            CircuitBreakerConfig,
            ResilientLLMClient,
        )

        if isinstance(circuit_breaker, CircuitBreaker):
            return ResilientLLMClient(client=instance, breaker=circuit_breaker)
        if isinstance(circuit_breaker, CircuitBreakerConfig):
            return ResilientLLMClient(client=instance, config=circuit_breaker, name=provider_label)
        return ResilientLLMClient(client=instance, name=provider_label)

    @staticmethod
    def create(
        provider: Optional[str] = None,
        settings: Optional[Settings] = None,
        client: Optional[httpx.AsyncClient] = None,
        with_fallbacks: bool = False,
        circuit_breaker: Union[bool, CircuitBreaker, CircuitBreakerConfig] = False,
        **kwargs: Any,
    ) -> BaseLLMClient:
        cfg = settings or get_settings()
        target_provider = (provider or cfg.default_provider).lower().strip()

        if with_fallbacks or (provider is None and cfg.fallback_providers):
            chain = [target_provider] + [p for p in cfg.fallback_providers if p != target_provider]
            if len(chain) > 1:
                from common_framework.core.resilience import CircuitBreakerConfig as _CBConfig

                cb_cfg = circuit_breaker if isinstance(circuit_breaker, _CBConfig) else None
                return LLMFactory.create_with_fallbacks(
                    providers=chain,
                    settings=cfg,
                    client=client,
                    enable_circuit_breaker=bool(circuit_breaker),
                    circuit_breaker_config=cb_cfg,
                    **kwargs,
                )

        if target_provider == "gemini":
            created = LLMFactory._safe_instantiate(GeminiClient, settings=cfg, client=client, **kwargs)
            return LLMFactory._wrap_with_circuit_breaker(created, circuit_breaker, target_provider)
        if target_provider == "openai":
            created = LLMFactory._safe_instantiate(OpenAICompatibleClient, provider_name="openai", settings=cfg, client=client, **kwargs)
            return LLMFactory._wrap_with_circuit_breaker(created, circuit_breaker, target_provider)
        if target_provider == "anthropic":
            created = LLMFactory._safe_instantiate(AnthropicClient, settings=cfg, client=client, **kwargs)
            return LLMFactory._wrap_with_circuit_breaker(created, circuit_breaker, target_provider)
        if target_provider == "ollama":
            created = LLMFactory._safe_instantiate(OllamaClient, settings=cfg, client=client, **kwargs)
            return LLMFactory._wrap_with_circuit_breaker(created, circuit_breaker, target_provider)
        if target_provider in ("deepseek", "groq", "vllm"):
            default_urls = {
                "deepseek": "https://api.deepseek.com",
                "groq": "https://api.groq.com/openai/v1",
                "vllm": "http://localhost:8000/v1",
            }
            base_url = kwargs.pop("base_url", None) or getattr(cfg, f"{target_provider}_base_url", default_urls[target_provider])
            api_key = kwargs.pop("api_key", None)
            if not api_key:
                if target_provider == "vllm":
                    api_key = "vllm"
                else:
                    api_key = cfg.get_api_key_for_provider(target_provider) or "none"
            created = LLMFactory._safe_instantiate(
                OpenAICompatibleClient,
                provider_name=target_provider,
                base_url=base_url,
                api_key=api_key,
                settings=cfg,
                client=client,
                **kwargs,
            )
            return LLMFactory._wrap_with_circuit_breaker(created, circuit_breaker, target_provider)
        if target_provider in ("custom", "generic"):
            created = LLMFactory._safe_instantiate(OpenAICompatibleClient, provider_name="custom", settings=cfg, client=client, **kwargs)
            return LLMFactory._wrap_with_circuit_breaker(created, circuit_breaker, target_provider)

        raise ConfigurationError(
            f"Unsupported LLM provider: {target_provider!r}. "
            "Supported providers: 'gemini', 'openai', 'anthropic', 'ollama', 'deepseek', 'groq', 'vllm', 'custom'."
        )

    @staticmethod
    def create_with_fallbacks(
        providers: Sequence[str],
        settings: Optional[Settings] = None,
        client: Optional[httpx.AsyncClient] = None,
        fallback_on: Optional[tuple[type[Exception], ...]] = None,
        enable_circuit_breaker: bool = False,
        circuit_breaker_config: Optional[CircuitBreakerConfig] = None,
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
        return FallbackLLMClient(
            clients=clients,
            fallback_on=fallback_on,
            settings=cfg,
            enable_circuit_breaker=enable_circuit_breaker,
            circuit_breaker_config=circuit_breaker_config,
        )

