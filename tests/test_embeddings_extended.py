"""Extended unit tests for Embeddings providers and factory to achieve 100% coverage."""

import httpx
import pytest

from common_framework.core.config import Settings
from common_framework.core.exceptions import AuthenticationError, ConfigurationError, LLMProviderError
from common_framework.rag.embeddings import (
    EmbeddingFactory,
    MockEmbeddingProvider,
    OpenAICompatibleEmbeddingProvider,
)


@pytest.mark.asyncio
async def test_embedding_factory_all_providers():
    """Verify EmbeddingFactory creates all supported providers with appropriate configurations."""
    settings = Settings(
        openai_api_key="sk-openai-test",
        gemini_api_key="gemini-key-test",
        ollama_base_url="http://localhost:11434",
    )

    # 1. Mock provider
    mock_prov = EmbeddingFactory.create("mock", settings=settings)
    assert isinstance(mock_prov, MockEmbeddingProvider)

    # 2. OpenAI provider
    openai_prov = EmbeddingFactory.create("openai", settings=settings)
    assert isinstance(openai_prov, OpenAICompatibleEmbeddingProvider)
    assert openai_prov.model == "text-embedding-3-small"
    assert openai_prov.provider_name == "openai"

    # Custom model override
    openai_custom = EmbeddingFactory.create("openai", settings=settings, model="text-embedding-3-large")
    assert openai_custom.model == "text-embedding-3-large"

    # 3. Ollama provider
    ollama_prov = EmbeddingFactory.create("ollama", settings=settings)
    assert isinstance(ollama_prov, OpenAICompatibleEmbeddingProvider)
    assert ollama_prov.model == "nomic-embed-text"
    assert ollama_prov.base_url == "http://localhost:11434/v1"

    # 4. Gemini provider
    gemini_prov = EmbeddingFactory.create("gemini", settings=settings)
    assert isinstance(gemini_prov, OpenAICompatibleEmbeddingProvider)
    assert gemini_prov.model == "text-embedding-004"
    assert gemini_prov.api_key == "gemini-key-test"
    assert "googleapis.com" in gemini_prov.base_url

    # 5. Unsupported provider raises ConfigurationError
    with pytest.raises(ConfigurationError, match="Unsupported embedding provider: 'unknown'"):
        EmbeddingFactory.create("unknown", settings=settings)


@pytest.mark.asyncio
async def test_openai_compatible_embedding_provider_success():
    """Verify successful query and document embedding extraction using MockTransport."""
    def mock_handler(request: httpx.Request) -> httpx.Response:
        assert request.headers.get("Authorization") == "Bearer sk-test-key"
        # Simulate unordered response data to test index-based sorting
        return httpx.Response(
            200,
            json={
                "data": [
                    {"embedding": [0.3, 0.4], "index": 1},
                    {"embedding": [0.1, 0.2], "index": 0},
                ]
            },
        )

    transport = httpx.MockTransport(mock_handler)
    async with httpx.AsyncClient(transport=transport) as client:
        provider = OpenAICompatibleEmbeddingProvider(
            api_key="sk-test-key",
            base_url="https://api.openai.com/v1",
            model="text-embedding-3-small",
            client=client,
        )

        # Empty inputs should return empty list
        assert await provider.embed_documents([]) == []

        # Document batch embedding
        doc_embeddings = await provider.embed_documents(["first", "second"])
        assert len(doc_embeddings) == 2
        assert doc_embeddings[0] == [0.1, 0.2]
        assert doc_embeddings[1] == [0.3, 0.4]

        # Single query embedding
        query_embedding = await provider.embed_query("sample query")
        assert query_embedding == [0.1, 0.2]

        await provider.aclose()


@pytest.mark.asyncio
async def test_openai_compatible_embedding_provider_auth_error():
    """Verify 401/403 status codes raise AuthenticationError."""
    def mock_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": {"message": "Invalid API Key"}})

    transport = httpx.MockTransport(mock_handler)
    async with httpx.AsyncClient(transport=transport) as client:
        provider = OpenAICompatibleEmbeddingProvider(
            api_key="sk-invalid",
            base_url="https://api.openai.com/v1",
            client=client,
        )

        with pytest.raises(AuthenticationError) as exc_info:
            await provider.embed_query("test")
        assert exc_info.value.status_code == 401


@pytest.mark.asyncio
async def test_openai_compatible_embedding_provider_http_and_network_errors():
    """Verify HTTP status errors and connection network errors raise LLMProviderError."""
    # 1. 500 Server error
    def error_500_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="Internal Server Error")

    transport_500 = httpx.MockTransport(error_500_handler)
    async with httpx.AsyncClient(transport=transport_500) as client:
        provider = OpenAICompatibleEmbeddingProvider(
            api_key="sk-test",
            base_url="https://api.openai.com/v1",
            client=client,
        )
        with pytest.raises(LLMProviderError, match="Internal Server Error"):
            await provider.embed_documents(["hello"])

    # 2. Network disconnect error
    def network_error_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("Connection refused by remote host", request=request)

    transport_net = httpx.MockTransport(network_error_handler)
    async with httpx.AsyncClient(transport=transport_net) as client:
        provider = OpenAICompatibleEmbeddingProvider(
            api_key="sk-test",
            base_url="https://api.openai.com/v1",
            client=client,
        )
        with pytest.raises(LLMProviderError, match="Network error connecting"):
            await provider.embed_documents(["hello"])


@pytest.mark.asyncio
async def test_openai_compatible_embedding_provider_internal_client():
    """Verify internal client lifecycle creation and closure."""
    provider = OpenAICompatibleEmbeddingProvider(
        api_key="sk-test",
        base_url="https://api.openai.com/v1",
        client=None,
    )
    # Lazy client creation
    client = provider._get_client()
    assert isinstance(client, httpx.AsyncClient)
    assert not client.is_closed

    # Clean teardown
    await provider.aclose()
    assert provider._internal_client is None

