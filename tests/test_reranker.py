"""Unit and integration tests for Cross-Encoder Reranking Engine (FEAT-ADV-01)."""

import pytest
import httpx
from unittest.mock import AsyncMock, patch

from common_framework.core.exceptions import (
    AuthenticationError,
    ConfigurationError,
    LLMProviderError,
)
from common_framework.rag.engine import RAGEngine
from common_framework.rag.embeddings import MockEmbeddingProvider
from common_framework.rag.models import Document, SearchResult
from common_framework.rag.rerankers import (
    BaseReranker,
    CohereReranker,
    LocalCrossEncoderReranker,
    RerankerFactory,
)
from common_framework.rag.vectorstore import InMemoryVectorStore


@pytest.fixture
def sample_documents() -> list[Document]:
    return [
        Document(
            content="The quick brown fox jumps over the lazy dog.",
            metadata={"source": "doc1"},
            id="doc-1",
        ),
        Document(
            content="PostgreSQL is an open-source relational database management system.",
            metadata={"source": "doc2"},
            id="doc-2",
        ),
        Document(
            content="FastAPI is a modern, fast web framework for building APIs with Python.",
            metadata={"source": "doc3"},
            id="doc-3",
        ),
        Document(
            content="Python web development with FastAPI and relational database integration.",
            metadata={"source": "doc4"},
            id="doc-4",
        ),
    ]


@pytest.mark.asyncio
async def test_local_cross_encoder_reranking(sample_documents):
    reranker = LocalCrossEncoderReranker()

    query = "FastAPI web framework for Python APIs"
    results = await reranker.rerank(query=query, documents=sample_documents, top_k=2)

    assert len(results) == 2
    assert all(isinstance(r, SearchResult) for r in results)
    # doc3 and doc4 should have the highest relevance to FastAPI Python
    top_doc_ids = [r.document.id for r in results]
    assert "doc-3" in top_doc_ids
    assert results[0].score >= results[1].score


@pytest.mark.asyncio
async def test_local_cross_encoder_exact_phrase_bonus():
    reranker = LocalCrossEncoderReranker()
    docs = [
        Document(content="Some unrelated text about astronomy and stars.", id="d1"),
        Document(content="Artificial intelligence and machine learning models in production.", id="d2"),
        Document(content="Introduction to machine learning models for developers.", id="d3"),
    ]

    query = "machine learning models"
    results = await reranker.rerank(query=query, documents=docs)

    assert len(results) == 3
    # Both d2 and d3 have exact phrase match, d1 has zero
    assert results[0].score > results[2].score
    assert results[2].document.id == "d1"
    assert results[2].score == 0.0


@pytest.mark.asyncio
async def test_local_cross_encoder_empty_inputs(sample_documents):
    reranker = LocalCrossEncoderReranker()

    assert await reranker.rerank(query="", documents=sample_documents) == []
    assert await reranker.rerank(query="FastAPI", documents=[]) == []


def test_local_cross_encoder_sync_wrapper(sample_documents):
    reranker = LocalCrossEncoderReranker()
    results = reranker.rerank_sync(query="PostgreSQL database", documents=sample_documents, top_k=1)
    assert len(results) == 1
    assert results[0].document.id == "doc-2"


@pytest.mark.asyncio
async def test_cohere_reranker_missing_api_key(sample_documents):
    reranker = CohereReranker(api_key=None, settings=None)
    with pytest.raises(AuthenticationError, match="Cohere API key is missing"):
        await reranker.rerank(query="test", documents=sample_documents)


@pytest.mark.asyncio
async def test_cohere_reranker_mock_success(sample_documents):
    async def mock_handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v2/rerank"
        assert request.headers["authorization"] == "Bearer test-cohere-key"
        payload = {
            "results": [
                {"index": 2, "relevance_score": 0.95},
                {"index": 3, "relevance_score": 0.82},
            ]
        }
        return httpx.Response(200, json=payload)

    transport = httpx.MockTransport(mock_handler)
    mock_client = httpx.AsyncClient(transport=transport)

    reranker = CohereReranker(api_key="test-cohere-key")
    reranker._http_client = mock_client

    async with reranker:
        results = await reranker.rerank(query="FastAPI", documents=sample_documents, top_k=2)

    assert len(results) == 2
    assert results[0].document.id == "doc-3"
    assert results[0].score == 0.95
    assert results[1].document.id == "doc-4"
    assert results[1].score == 0.82


@pytest.mark.asyncio
async def test_cohere_reranker_http_errors(sample_documents):
    # Test 401 Unauthorized
    async def mock_401(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"message": "invalid api token"})

    reranker_401 = CohereReranker(api_key="bad-key")
    reranker_401._http_client = httpx.AsyncClient(transport=httpx.MockTransport(mock_401))

    with pytest.raises(AuthenticationError, match="Invalid Cohere API key"):
        await reranker_401.rerank(query="test", documents=sample_documents)
    await reranker_401.aclose()

    # Test 500 Internal Server Error
    async def mock_500(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="Internal Server Error")

    reranker_500 = CohereReranker(api_key="valid-key")
    reranker_500._http_client = httpx.AsyncClient(transport=httpx.MockTransport(mock_500))

    with pytest.raises(LLMProviderError, match="HTTP 500"):
        await reranker_500.rerank(query="test", documents=sample_documents)
    await reranker_500.aclose()


def test_reranker_factory():
    local_1 = RerankerFactory.create("local")
    assert isinstance(local_1, LocalCrossEncoderReranker)

    local_2 = RerankerFactory.create("cross_encoder")
    assert isinstance(local_2, LocalCrossEncoderReranker)

    cohere = RerankerFactory.create("cohere", api_key="sk-test")
    assert isinstance(cohere, CohereReranker)

    with pytest.raises(ConfigurationError, match="Unsupported reranker provider"):
        RerankerFactory.create("unknown_provider")


@pytest.mark.asyncio
async def test_rag_engine_two_stage_reranking_integration():
    embedding = MockEmbeddingProvider()
    vector_store = InMemoryVectorStore()
    reranker = LocalCrossEncoderReranker()

    engine = RAGEngine(
        vector_store=vector_store,
        embedding_provider=embedding,
        reranker=reranker,
    )

    docs = [
        Document(
            content="Database indexing speeds up queries in PostgreSQL and MySQL databases.",
            metadata={"category": "tech"},
            id="doc-db",
        ),
        Document(
            content="Deep learning and neural networks are subfields of artificial intelligence.",
            metadata={"category": "ai"},
            id="doc-ai",
        ),
        Document(
            content="PostgreSQL database query optimization techniques and EXPLAIN execution plans.",
            metadata={"category": "tech"},
            id="doc-opt",
        ),
    ]

    await engine.ingest_documents(docs)

    # Retrieval with reranking enabled
    results = await engine.retrieve(
        query="PostgreSQL database query optimization",
        top_k=2,
        mode="hybrid",
    )

    assert len(results) <= 2
    assert results[0].document.metadata.get("parent_id") == "doc-opt"
    assert "PostgreSQL" in results[0].document.content
    assert results[0].score > 0.5
