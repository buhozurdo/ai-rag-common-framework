"""Unit and integration tests for multi-provider LLM fallbacks and Anthropic client.

Covers:
- FallbackLLMClient cascading on 429 RateLimitError
- FallbackLLMClient cascading on 500/502/503/504 LLMProviderError
- FallbackLLMClient cascading on network timeouts (httpx.ConnectTimeout)
- AllProvidersFailedError when all candidates fail
- Structured output completion with fallback
- Per-candidate model override tuples
- LLMFactory.create_with_fallbacks and settings-based fallback creation
- AnthropicClient completion and structured completion
- RAGEngine end-to-end integration with FallbackLLMClient
"""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from pydantic import BaseModel, Field

from common_framework.core.config import Settings
from common_framework.core.exceptions import (
    AllProvidersFailedError,
    AuthenticationError,
    ConfigurationError,
    LLMProviderError,
    RateLimitError,
)
from common_framework.core.llm import (
    AnthropicClient,
    BaseLLMClient,
    ChatMessage,
    ChatResponse,
    FallbackCandidate,
    FallbackLLMClient,
    LLMFactory,
    Role,
    TokenUsage,
)
from common_framework.rag.embeddings import MockEmbeddingProvider
from common_framework.rag.engine import RAGEngine


class DummyOutputSchema(BaseModel):
    summary: str
    confidence: float = Field(ge=0.0, le=1.0)


class MockSuccessClient(BaseLLMClient):
    """Test client that always succeeds."""

    def __init__(self, name: str = "mock-success", model_name: str = "mock-model") -> None:
        super().__init__()
        self.provider_name = name
        self.model_name = model_name
        self.call_count = 0

    async def complete(
        self,
        messages: list[ChatMessage],
        model: str | None = None,
        temperature: float | None = None,
        **kwargs: Any,
    ) -> ChatResponse:
        self.call_count += 1
        return ChatResponse(
            content=f"Response from {self.provider_name}",
            model=model or self.model_name,
            usage=TokenUsage(prompt_tokens=10, completion_tokens=5, total_tokens=15),
            raw={"provider": self.provider_name},
        )

    async def structured_complete(
        self,
        messages: list[ChatMessage],
        response_model: type[Any],
        model: str | None = None,
        temperature: float | None = None,
        **kwargs: Any,
    ) -> Any:
        self.call_count += 1
        return response_model(summary=f"Structured from {self.provider_name}", confidence=0.95)


class MockFailingClient(BaseLLMClient):
    """Test client that raises a specified error."""

    def __init__(self, error_to_raise: Exception, name: str = "mock-failure") -> None:
        super().__init__()
        self.error_to_raise = error_to_raise
        self.provider_name = name
        self.call_count = 0

    async def complete(
        self,
        messages: list[ChatMessage],
        model: str | None = None,
        temperature: float | None = None,
        **kwargs: Any,
    ) -> ChatResponse:
        self.call_count += 1
        raise self.error_to_raise

    async def structured_complete(
        self,
        messages: list[ChatMessage],
        response_model: type[Any],
        model: str | None = None,
        temperature: float | None = None,
        **kwargs: Any,
    ) -> Any:
        self.call_count += 1
        raise self.error_to_raise


# ===========================================================================
# FallbackLLMClient Tests
# ===========================================================================


class TestFallbackLLMClient:
    def test_empty_candidates_raises_configuration_error(self):
        """FallbackLLMClient requires at least one client."""
        with pytest.raises(ConfigurationError, match="requires at least one"):
            FallbackLLMClient(clients=[])

    @pytest.mark.asyncio
    async def test_primary_succeeds_no_fallback_needed(self):
        """When primary succeeds, secondary candidate is not invoked."""
        primary = MockSuccessClient(name="primary-gemini")
        secondary = MockSuccessClient(name="secondary-openai")

        fallback_client = FallbackLLMClient(clients=[primary, secondary])
        messages = [ChatMessage(role=Role.USER, content="Hello")]

        res = await fallback_client.complete(messages=messages)

        assert res.content == "Response from primary-gemini"
        assert primary.call_count == 1
        assert secondary.call_count == 0
        assert res.fallback_attempts == []

    @pytest.mark.asyncio
    async def test_fallback_on_rate_limit_429(self):
        """Fails over to secondary when primary encounters HTTP 429."""
        primary = MockFailingClient(
            error_to_raise=RateLimitError("Quota reached", status_code=429, provider="gemini"),
            name="gemini",
        )
        secondary = MockSuccessClient(name="openai", model_name="gpt-4o-mini")

        fallback_client = FallbackLLMClient(clients=[primary, secondary])
        messages = [ChatMessage(role=Role.USER, content="Hello")]

        res = await fallback_client.complete(messages=messages)

        assert res.content == "Response from openai"
        assert primary.call_count == 1
        assert secondary.call_count == 1
        assert len(res.fallback_attempts) == 1
        assert res.fallback_attempts[0]["provider"] == "gemini"
        assert res.fallback_attempts[0]["status_code"] == 429

    @pytest.mark.asyncio
    async def test_fallback_on_server_error_503(self):
        """Fails over to secondary when primary encounters HTTP 503 Service Unavailable."""
        primary = MockFailingClient(
            error_to_raise=LLMProviderError("Service Overloaded", status_code=503, provider="openai"),
            name="openai",
        )
        secondary = MockSuccessClient(name="ollama", model_name="llama3")

        fallback_client = FallbackLLMClient(clients=[primary, secondary])
        messages = [ChatMessage(role=Role.USER, content="Generate plan")]

        res = await fallback_client.complete(messages=messages)

        assert res.content == "Response from ollama"
        assert primary.call_count == 1
        assert secondary.call_count == 1
        assert len(res.fallback_attempts) == 1
        assert res.fallback_attempts[0]["status_code"] == 503

    @pytest.mark.asyncio
    async def test_fallback_on_network_timeout(self):
        """Fails over on connection timeout."""
        primary = MockFailingClient(
            error_to_raise=httpx.ConnectTimeout("Connection timed out to primary"),
            name="primary",
        )
        secondary = MockSuccessClient(name="secondary")

        fallback_client = FallbackLLMClient(clients=[primary, secondary])
        res = await fallback_client.complete(messages=[ChatMessage(role=Role.USER, content="Test")])

        assert res.content == "Response from secondary"
        assert primary.call_count == 1
        assert secondary.call_count == 1

    @pytest.mark.asyncio
    async def test_all_providers_fail_raises_all_providers_failed_error(self):
        """When all candidates fail, AllProvidersFailedError is raised with full telemetry."""
        c1 = MockFailingClient(RateLimitError("429 from P1", provider="p1"), name="p1")
        c2 = MockFailingClient(LLMProviderError("500 from P2", status_code=500, provider="p2"), name="p2")

        fallback_client = FallbackLLMClient(clients=[c1, c2])

        with pytest.raises(AllProvidersFailedError) as exc_info:
            await fallback_client.complete(messages=[ChatMessage(role=Role.USER, content="Hi")])

        err = exc_info.value
        assert "All 2 LLM candidates in fallback sequence failed" in str(err)
        assert len(err.errors) == 2
        assert err.errors[0]["provider"] == "p1"
        assert err.errors[1]["provider"] == "p2"

    @pytest.mark.asyncio
    async def test_unhandled_exception_does_not_failover(self):
        """Exceptions not in fallback_exceptions fail immediately without cascading."""
        c1 = MockFailingClient(
            AuthenticationError("Invalid API Key", status_code=401, provider="bad-key"),
            name="bad-key",
        )
        c2 = MockSuccessClient(name="should-not-run")

        # Custom fallback exceptions: only failover on 429 RateLimitError
        fallback_client = FallbackLLMClient(clients=[c1, c2], fallback_on=(RateLimitError,))

        with pytest.raises(AuthenticationError):
            await fallback_client.complete(messages=[ChatMessage(role=Role.USER, content="Test")])

        assert c1.call_count == 1
        assert c2.call_count == 0

    @pytest.mark.asyncio
    async def test_structured_complete_fallback(self):
        """structured_complete successfully fails over to secondary on transient failure."""
        primary = MockFailingClient(RateLimitError("Rate limit", provider="primary"), name="primary")
        secondary = MockSuccessClient(name="secondary")

        fallback_client = FallbackLLMClient(clients=[primary, secondary])
        result = await fallback_client.structured_complete(
            messages=[ChatMessage(role=Role.USER, content="Analyze")],
            response_model=DummyOutputSchema,
        )

        assert isinstance(result, DummyOutputSchema)
        assert result.summary == "Structured from secondary"
        assert result.confidence == 0.95
        assert primary.call_count == 1
        assert secondary.call_count == 1

    @pytest.mark.asyncio
    async def test_structured_complete_all_fail(self):
        """structured_complete raises AllProvidersFailedError when all fail."""
        c1 = MockFailingClient(RateLimitError("429", provider="p1"), name="p1")
        c2 = MockFailingClient(LLMProviderError("500", provider="p2"), name="p2")

        fallback_client = FallbackLLMClient(clients=[c1, c2])

        with pytest.raises(AllProvidersFailedError):
            await fallback_client.structured_complete(
                messages=[ChatMessage(role=Role.USER, content="Hi")],
                response_model=DummyOutputSchema,
            )

    @pytest.mark.asyncio
    async def test_candidate_model_override_tuple(self):
        """Tuples of (client, model_name) override the model on completion."""
        client1 = MockSuccessClient(name="openai", model_name="default-model")
        client2 = MockSuccessClient(name="openai", model_name="default-model")

        # Fallback from gpt-4o to gpt-4o-mini
        fallback_client = FallbackLLMClient(
            clients=[
                (client1, "gpt-4o"),
                (client2, "gpt-4o-mini"),
            ]
        )

        res = await fallback_client.complete(messages=[ChatMessage(role=Role.USER, content="Hi")])
        assert res.model == "gpt-4o"


# ===========================================================================
# LLMFactory Multi-Provider & Anthropic Tests
# ===========================================================================


class TestLLMFactoryAndAnthropic:
    def test_factory_create_with_fallbacks(self):
        """LLMFactory.create_with_fallbacks builds FallbackLLMClient with specified chain."""
        client = LLMFactory.create_with_fallbacks(providers=["gemini", "openai", "ollama"])
        assert isinstance(client, FallbackLLMClient)
        assert len(client.candidates) == 3
        assert client.candidates[0].provider_name == "gemini"
        assert client.candidates[1].provider_name == "openai"
        assert client.candidates[2].provider_name == "ollama"

    def test_factory_create_with_fallbacks_empty_raises(self):
        """Empty providers list raises ConfigurationError."""
        with pytest.raises(ConfigurationError, match="requires at least one provider"):
            LLMFactory.create_with_fallbacks(providers=[])

    def test_factory_create_with_settings_fallback_providers(self):
        """When FALLBACK_PROVIDERS is set in Settings, with_fallbacks=True activates it."""
        settings = Settings(
            DEFAULT_LLM_PROVIDER="gemini",
            FALLBACK_PROVIDERS=["openai", "ollama"],
        )
        client = LLMFactory.create(settings=settings, with_fallbacks=True)
        assert isinstance(client, FallbackLLMClient)
        assert len(client.candidates) == 3

    def test_factory_creates_anthropic_client(self):
        """TASK-04 resolution: LLMFactory creates AnthropicClient for 'anthropic'."""
        client = LLMFactory.create(provider="anthropic")
        assert isinstance(client, AnthropicClient)
        assert client.provider_name == "anthropic"

    @pytest.mark.asyncio
    async def test_anthropic_complete_success(self):
        """Verify AnthropicClient native Messages API completion parsing."""
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {
            "id": "msg_123",
            "type": "message",
            "role": "assistant",
            "content": [{"type": "text", "text": "Hello from Claude 3.5 Sonnet"}],
            "model": "claude-3-5-sonnet-latest",
            "usage": {"input_tokens": 15, "output_tokens": 8},
        }

        mock_http = MagicMock()
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=False)
        mock_http.post = AsyncMock(return_value=mock_response)

        client = AnthropicClient(
            api_key="sk-ant-test",
            client=mock_http,
        )

        messages = [
            ChatMessage(role=Role.SYSTEM, content="Be concise"),
            ChatMessage(role=Role.USER, content="Hello!"),
        ]

        res = await client.complete(messages=messages)

        assert res.content == "Hello from Claude 3.5 Sonnet"
        assert res.model == "claude-3-5-sonnet-latest"
        assert res.usage.prompt_tokens == 15
        assert res.usage.completion_tokens == 8
        assert res.usage.total_tokens == 23

        # Verify system prompt was moved to top-level 'system' param per Anthropic spec
        call_kwargs = mock_http.post.call_args.kwargs
        payload = call_kwargs["json"]
        assert payload["system"] == "Be concise"
        assert payload["messages"] == [{"role": "user", "content": "Hello!"}]
        assert call_kwargs["headers"]["x-api-key"] == "sk-ant-test"
        assert call_kwargs["headers"]["anthropic-version"] == "2023-06-01"


# ===========================================================================
# RAGEngine End-to-End Integration with FallbackLLMClient
# ===========================================================================


class TestRAGEngineWithFallback:
    @pytest.mark.asyncio
    async def test_rag_engine_queries_with_fallback_client(self):
        """Verify RAGEngine query synthesis automatically leverages FallbackLLMClient."""
        primary_failing = MockFailingClient(
            error_to_raise=RateLimitError("Primary Gemini quota exhausted", provider="gemini"),
            name="gemini",
        )
        secondary_succeeding = MockSuccessClient(name="openai-backup", model_name="gpt-4o-mini")

        fallback_llm = FallbackLLMClient(clients=[primary_failing, secondary_succeeding])

        engine = RAGEngine(
            embedding_provider=MockEmbeddingProvider(),
            llm_client=fallback_llm,
        )

        await engine.ingest_texts(["Python is a programming language.", "FastAPI builds APIs."])

        # Query should seamlessly failover from Gemini to OpenAI-backup
        response = await engine.query("What is Python?")

        assert "Response from openai-backup" in response.answer
        assert len(response.sources) > 0
        assert primary_failing.call_count == 1
        assert secondary_succeeding.call_count == 1
