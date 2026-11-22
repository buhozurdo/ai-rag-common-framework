"""Unit tests for unified LLM client."""

import json
import httpx
import pytest
from pydantic import BaseModel

from common_framework.core.config import Settings
from common_framework.core.exceptions import (
    AuthenticationError,
    ConfigurationError,
    LLMProviderError,
    ModelNotFoundError,
    RateLimitError,
    StructuredOutputError,
)
from common_framework.core.llm import (
    AnthropicClient,
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
async def test_anthropic_http_error_handling():
    """Verify AnthropicClient maps 401 to AuthenticationError, 404 to ModelNotFoundError, 429 to RateLimitError, and 500 to LLMProviderError."""
    # 1. 401 AuthenticationError
    def auth_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": {"type": "authentication_error", "message": "Invalid API key"}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(auth_handler)) as http_client:
        client = AnthropicClient(api_key="bad-key", client=http_client)
        with pytest.raises(AuthenticationError) as exc:
            await client.complete([ChatMessage(role=Role.USER, content="Hi")])
        assert exc.value.status_code == 401
        assert "Invalid API key" in str(exc.value)

    # 2. 404 ModelNotFoundError
    def not_found_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"error": {"type": "not_found_error", "message": "model claude-unknown not found"}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(not_found_handler)) as http_client:
        client = AnthropicClient(api_key="sk-ant", client=http_client)
        with pytest.raises(ModelNotFoundError) as exc:
            await client.complete([ChatMessage(role=Role.USER, content="Hi")])
        assert exc.value.status_code == 404
        assert "not found" in str(exc.value)

    # 3. 429 RateLimitError with retry-after
    def rate_limit_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, headers={"retry-after": "12.5"}, json={"error": {"type": "rate_limit_error", "message": "Rate limit exceeded"}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(rate_limit_handler)) as http_client:
        client = AnthropicClient(api_key="sk-ant", client=http_client, settings=Settings(DEFAULT_MAX_RETRIES=0))
        with pytest.raises(RateLimitError) as exc:
            await client.complete([ChatMessage(role=Role.USER, content="Hi")])
        assert exc.value.status_code == 429
        assert exc.value.retry_after == 12.5

    # 4. 500 LLMProviderError
    def server_err_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": {"type": "api_error", "message": "Internal server error"}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(server_err_handler)) as http_client:
        client = AnthropicClient(api_key="sk-ant", client=http_client, settings=Settings(DEFAULT_MAX_RETRIES=0))
        with pytest.raises(LLMProviderError) as exc:
            await client.complete([ChatMessage(role=Role.USER, content="Hi")])
        assert exc.value.status_code == 500


@pytest.mark.asyncio
async def test_anthropic_streaming_midstream_error():
    """Verify AnthropicClient complete_stream detects event: error mid-stream and raises LLMProviderError."""
    sse_body = (
        'event: message_start\ndata: {"type":"message_start","message":{"id":"msg_1","type":"message","role":"assistant","usage":{"input_tokens":5}}}\n\n'
        'event: content_block_start\ndata: {"type":"content_block_start","index":0,"content_block":{"type":"text","text":""}}\n\n'
        'event: content_block_delta\ndata: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"Generating..."}}\n\n'
        'event: error\ndata: {"type":"error","error":{"type":"overloaded_error","message":"Anthropic engine is overloaded"}}\n\n'
    )

    def sse_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, text=sse_body)

    async with httpx.AsyncClient(transport=httpx.MockTransport(sse_handler)) as http_client:
        client = AnthropicClient(api_key="sk-ant", client=http_client)
        chunks = []
        with pytest.raises(LLMProviderError, match="Anthropic streaming mid-stream error.*overloaded"):
            async for chunk in client.complete_stream([ChatMessage(role=Role.USER, content="Stream test")]):
                chunks.append(chunk.delta)
        assert "".join(chunks) == "Generating..."


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


@pytest.mark.asyncio
async def test_openai_streaming_usage_and_cost_tracking():
    """Verify OpenAI stream captures usage and calls cost tracker."""
    from common_framework.observability.cost import global_cost_tracker

    initial_calls = len(global_cost_tracker._records)

    sse_lines = [
        'data: {"choices": [{"delta": {"content": "Hello"}}], "model": "gpt-4o"}\n\n',
        'data: {"choices": [{"delta": {"content": " world!"}}], "model": "gpt-4o"}\n\n',
        'data: {"choices": [], "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}, "model": "gpt-4o"}\n\n',
        'data: [DONE]\n\n',
    ]

    def mock_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content="".join(sse_lines).encode("utf-8"),
        )

    mock_http = httpx.AsyncClient(transport=httpx.MockTransport(mock_handler))
    client = OpenAICompatibleClient(api_key="sk-test", client=mock_http)

    chunks = []
    async for chunk in client.complete_stream([ChatMessage(role=Role.USER, content="Hi")]):
        chunks.append(chunk)

    text = "".join(c.delta for c in chunks)
    assert text == "Hello world!"
    assert any(c.usage is not None for c in chunks)
    assert len(global_cost_tracker._records) == initial_calls + 1


@pytest.mark.asyncio
async def test_anthropic_streaming_usage_and_mid_stream_error():
    """Verify Anthropic stream captures usage and raises LLMProviderError on mid-stream error."""
    from common_framework.core.exceptions import LLMProviderError
    from common_framework.core.llm import AnthropicClient

    # 1. Test error midway through stream
    error_sse = [
        'data: {"type": "message_start", "message": {"usage": {"input_tokens": 20}}}\n\n',
        'data: {"type": "content_block_delta", "delta": {"text": "Start"}}\n\n',
        'data: {"type": "error", "error": {"type": "overloaded_error", "message": "Overloaded"}}\n\n',
    ]

    def error_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content="".join(error_sse).encode("utf-8"),
        )

    client_err = AnthropicClient(
        api_key="sk-ant",
        client=httpx.AsyncClient(transport=httpx.MockTransport(error_handler)),
    )

    with pytest.raises(LLMProviderError, match="Anthropic streaming mid-stream error: Overloaded"):
        async for chunk in client_err.complete_stream([ChatMessage(role=Role.USER, content="Hi")]):
            pass

    # 2. Test successful stream with usage recording
    from common_framework.observability.cost import global_cost_tracker
    initial_calls = len(global_cost_tracker._records)

    success_sse = [
        'data: {"type": "message_start", "message": {"usage": {"input_tokens": 20}}}\n\n',
        'data: {"type": "content_block_delta", "delta": {"text": " Claude is speaking"}}\n\n',
        'data: {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 10}}\n\n',
    ]

    def ok_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content="".join(success_sse).encode("utf-8"),
        )

    client_ok = AnthropicClient(
        api_key="sk-ant",
        client=httpx.AsyncClient(transport=httpx.MockTransport(ok_handler)),
    )

    chunks = []
    async for chunk in client_ok.complete_stream([ChatMessage(role=Role.USER, content="Hi")]):
        chunks.append(chunk)

    assert "".join(c.delta for c in chunks) == " Claude is speaking"
    assert len(global_cost_tracker._records) == initial_calls + 1


@pytest.mark.asyncio
async def test_gemini_client_complete_and_streaming():
    """Verify GeminiClient completes and streams against Google OpenAI-compatible endpoint."""
    call_log = []

    def gemini_mock(request: httpx.Request) -> httpx.Response:
        call_log.append(request)
        assert "generativelanguage.googleapis.com" in str(request.url)
        if request.url.path.endswith("/chat/completions"):
            req_body = json.loads(request.content.decode("utf-8"))
            if req_body.get("stream"):
                sse = (
                    'data: {"choices": [{"delta": {"content": "Gemini stream"}}], "model": "gemini-2.5-flash"}\n\n'
                    'data: [DONE]\n\n'
                )
                return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=sse.encode())
            else:
                resp = {
                    "model": "gemini-2.5-flash",
                    "choices": [{"message": {"role": "assistant", "content": "Gemini answer"}, "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 12, "completion_tokens": 6, "total_tokens": 18},
                }
                return httpx.Response(200, json=resp)
        return httpx.Response(404)

    mock_http = httpx.AsyncClient(transport=httpx.MockTransport(gemini_mock))
    client = GeminiClient(api_key="gemini-key", client=mock_http)
    assert client.provider_name == "gemini"

    # Complete
    res = await client.complete([ChatMessage(role=Role.USER, content="Hello Gemini")])
    assert res.content == "Gemini answer"
    assert res.model == "gemini-2.5-flash"
    assert res.usage.total_tokens == 18

    # Stream
    streamed = []
    async for chunk in client.complete_stream([ChatMessage(role=Role.USER, content="Stream Gemini")]):
        streamed.append(chunk.delta)
    assert "".join(streamed) == "Gemini stream"


@pytest.mark.asyncio
async def test_ollama_client_complete_and_streaming():
    """Verify OllamaClient completes and streams against local endpoint."""
    def ollama_mock(request: httpx.Request) -> httpx.Response:
        assert "localhost" in str(request.url) or "127.0.0.1" in str(request.url)
        assert request.url.path.endswith("/v1/chat/completions")
        req_body = json.loads(request.content.decode("utf-8"))
        if req_body.get("stream"):
            sse = (
                'data: {"choices": [{"delta": {"content": "Ollama local"}}], "model": "llama3.2"}\n\n'
                'data: [DONE]\n\n'
            )
            return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=sse.encode())
        else:
            resp = {
                "model": "llama3.2",
                "choices": [{"message": {"role": "assistant", "content": "Ollama local answer"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 8, "completion_tokens": 4, "total_tokens": 12},
            }
            return httpx.Response(200, json=resp)

    mock_http = httpx.AsyncClient(transport=httpx.MockTransport(ollama_mock))
    client = OllamaClient(base_url="http://localhost:11434", client=mock_http)
    assert client.provider_name == "ollama"

    # Complete
    res = await client.complete([ChatMessage(role=Role.USER, content="Hello Ollama")])
    assert res.content == "Ollama local answer"
    assert res.model == "llama3.2"

    # Stream
    streamed = []
    async for chunk in client.complete_stream([ChatMessage(role=Role.USER, content="Stream Ollama")]):
        streamed.append(chunk.delta)
    assert "".join(streamed) == "Ollama local"



