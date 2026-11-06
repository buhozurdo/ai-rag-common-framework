"""Unit tests for unified LLM client."""

import json
import httpx
import pytest
from pydantic import BaseModel

from common_framework.core.config import Settings
from common_framework.core.exceptions import (
    AuthenticationError,
    ConfigurationError,
    ModelNotFoundError,
    RateLimitError,
    StructuredOutputError,
)
from common_framework.core.llm import (
    ChatMessage,
    GeminiClient,
    LLMFactory,
    OllamaClient,
    OpenAICompatibleClient,
    Role,
)


class SampleUserSchema(BaseModel):
    name: str
    age: int
    role: str


@pytest.mark.asyncio
async def test_chat_message_to_dict():
    """Verify serialization of ChatMessage."""
    msg = ChatMessage(role=Role.USER, content="Hello World", name="UserTest")
    data = msg.to_dict()
    assert data == {"role": "user", "content": "Hello World", "name": "UserTest"}


@pytest.mark.asyncio
async def test_openai_complete_success():
    """Verify successful chat completion response parsing."""
    def mock_handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/chat/completions"
        assert request.headers.get("Authorization") == "Bearer sk-test"
        response_body = {
            "model": "gpt-4o-mini",
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": "Framework configured successfully.",
                    }
                }
            ],
            "usage": {
                "prompt_tokens": 15,
                "completion_tokens": 8,
                "total_tokens": 23,
            },
        }
        return httpx.Response(200, json=response_body)

    transport = httpx.MockTransport(mock_handler)
    async with httpx.AsyncClient(transport=transport) as http_client:
        client = OpenAICompatibleClient(
            api_key="sk-test",
            base_url="https://api.openai.com/v1",
            client=http_client,
        )

        response = await client.complete([ChatMessage(role=Role.USER, content="Test")])
        assert response.content == "Framework configured successfully."
        assert response.model == "gpt-4o-mini"
        assert response.usage.prompt_tokens == 15
        assert response.usage.completion_tokens == 8
        assert response.usage.total_tokens == 23


@pytest.mark.asyncio
async def test_structured_complete_success():
    """Verify structured output parsing into Pydantic model."""
    def mock_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "model": "gpt-4o-mini",
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": json.dumps({"name": "Alice", "age": 30, "role": "Engineer"}),
                        }
                    }
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20},
            },
        )

    transport = httpx.MockTransport(mock_handler)
    async with httpx.AsyncClient(transport=transport) as http_client:
        client = OpenAICompatibleClient(api_key="sk-test", client=http_client)
        user = await client.structured_complete(
            messages=[ChatMessage(role=Role.USER, content="Extract user info")],
            response_model=SampleUserSchema,
        )
        assert isinstance(user, SampleUserSchema)
        assert user.name == "Alice"
        assert user.age == 30
        assert user.role == "Engineer"


@pytest.mark.asyncio
async def test_structured_complete_invalid_json():
    """Verify StructuredOutputError is raised when model returns invalid JSON."""
    def mock_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"role": "assistant", "content": "Not a valid JSON"}}]
            },
        )

    transport = httpx.MockTransport(mock_handler)
    async with httpx.AsyncClient(transport=transport) as http_client:
        client = OpenAICompatibleClient(api_key="sk-test", client=http_client)
        with pytest.raises(StructuredOutputError):
            await client.structured_complete(
                messages=[ChatMessage(role=Role.USER, content="Test")],
                response_model=SampleUserSchema,
            )


@pytest.mark.asyncio
async def test_auth_error_handling():
    """Verify 401 raises AuthenticationError."""
    def mock_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text="Unauthorized: Invalid API key")

    transport = httpx.MockTransport(mock_handler)
    async with httpx.AsyncClient(transport=transport) as http_client:
        client = OpenAICompatibleClient(api_key="bad-key", client=http_client)
        with pytest.raises(AuthenticationError) as exc_info:
            await client.complete([ChatMessage(role=Role.USER, content="Test")])
        assert exc_info.value.status_code == 401


@pytest.mark.asyncio
async def test_rate_limit_error_handling():
    """Verify 429 raises RateLimitError after exhausting retries."""
    settings = Settings(DEFAULT_MAX_RETRIES=1)
    call_count = 0

    def mock_handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        return httpx.Response(429, headers={"retry-after": "5"}, text="Too Many Requests")

    transport = httpx.MockTransport(mock_handler)
    async with httpx.AsyncClient(transport=transport) as http_client:
        client = OpenAICompatibleClient(api_key="sk-test", settings=settings, client=http_client)
        with pytest.raises(RateLimitError) as exc_info:
            await client.complete([ChatMessage(role=Role.USER, content="Test")])
        assert exc_info.value.status_code == 429
        assert exc_info.value.retry_after == 5.0
        assert call_count == 2  # 1 initial + 1 retry


@pytest.mark.asyncio
async def test_retry_success_after_transient_failure():
    """Verify client recovers on retry when first request fails with 503."""
    settings = Settings(DEFAULT_MAX_RETRIES=2)
    attempts = 0

    def mock_handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(503, text="Service Unavailable")
        return httpx.Response(
            200,
            json={
                "model": "gpt-4o-mini",
                "choices": [{"message": {"role": "assistant", "content": "Recovered!"}}],
                "usage": {"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7},
            },
        )

    transport = httpx.MockTransport(mock_handler)
    async with httpx.AsyncClient(transport=transport) as http_client:
        client = OpenAICompatibleClient(api_key="sk-test", settings=settings, client=http_client)
        response = await client.complete([ChatMessage(role=Role.USER, content="Test")])
        assert response.content == "Recovered!"
        assert attempts == 2



@pytest.mark.asyncio
async def test_not_found_error_handling():
    """Verify 404 raises ModelNotFoundError."""
    def mock_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text="Model not found")

    transport = httpx.MockTransport(mock_handler)
    async with httpx.AsyncClient(transport=transport) as http_client:
        client = OpenAICompatibleClient(api_key="sk-test", client=http_client)
        with pytest.raises(ModelNotFoundError) as exc_info:
            await client.complete([ChatMessage(role=Role.USER, content="Test")])
        assert exc_info.value.status_code == 404


def test_factory_creation():
    """Verify LLMFactory instantiates correct client types."""
    settings = Settings(
        GEMINI_API_KEY="gemini-key",
        OPENAI_API_KEY="openai-key",
        OLLAMA_BASE_URL="http://localhost:11434",
    )

    gemini_client = LLMFactory.create("gemini", settings=settings)
    assert isinstance(gemini_client, GeminiClient)

    openai_client = LLMFactory.create("openai", settings=settings)
    assert isinstance(openai_client, OpenAICompatibleClient)

    ollama_client = LLMFactory.create("ollama", settings=settings)
    assert isinstance(ollama_client, OllamaClient)

    with pytest.raises(ConfigurationError):
        LLMFactory.create("unsupported_provider", settings=settings)


@pytest.mark.asyncio
async def test_openai_persistent_client_pool_and_lifecycle():
    """Verify OpenAICompatibleClient reuses persistent httpx client and aclose closes it."""
    client = OpenAICompatibleClient(api_key="sk-test")
    assert client._internal_client is None

    http_client1 = client._get_client()
    assert http_client1 is not None
    assert not http_client1.is_closed

    http_client2 = client._get_client()
    assert http_client1 is http_client2  # Exact same instance reused

    await client.aclose()
    assert http_client1.is_closed
    assert client._internal_client is None

    # Lazy re-instantiation after aclose
    http_client3 = client._get_client()
    assert http_client3 is not None
    assert not http_client3.is_closed
    assert http_client3 is not http_client1

    # Test async context manager
    async with OpenAICompatibleClient(api_key="sk-test") as ctx_client:
        active_http = ctx_client._get_client()
        assert not active_http.is_closed
    assert active_http.is_closed


@pytest.mark.asyncio
async def test_anthropic_persistent_client_pool_and_lifecycle():
    """Verify AnthropicClient reuses persistent httpx client and aclose closes it."""
    from common_framework.core.llm import AnthropicClient

    client = AnthropicClient(api_key="sk-ant-test")
    http1 = client._get_client()
    http2 = client._get_client()
    assert http1 is http2

    await client.aclose()
    assert http1.is_closed
    assert client._internal_client is None


@pytest.mark.asyncio
async def test_embedding_persistent_client_pool_and_lifecycle():
    """Verify OpenAICompatibleEmbeddingProvider reuses persistent httpx client."""
    from common_framework.rag.embeddings import OpenAICompatibleEmbeddingProvider

    provider = OpenAICompatibleEmbeddingProvider(api_key="sk-test")
    http1 = provider._get_client()
    http2 = provider._get_client()
    assert http1 is http2

    async with provider:
        assert not http1.is_closed
    assert http1.is_closed


@pytest.mark.asyncio
async def test_fallback_client_aclose_cascading():
    """Verify FallbackLLMClient cascades aclose to all candidates."""
    from common_framework.core.llm import FallbackLLMClient

    c1 = OpenAICompatibleClient(api_key="sk-test-1")
    c2 = OpenAICompatibleClient(api_key="sk-test-2")
    http1 = c1._get_client()
    http2 = c2._get_client()

    fallback = FallbackLLMClient(clients=[c1, c2])
    await fallback.aclose()

    assert http1.is_closed
    assert http2.is_closed

