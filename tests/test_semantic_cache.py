"""Tests for the zero-dependency in-memory SemanticCache and CachedLLMClient."""

from __future__ import annotations

import time
from typing import Any
import pytest

from common_framework.core.cache import (
    CachedLLMClient,
    SemanticCache,
    SemanticCacheEntry,
    SemanticCacheHit,
    SemanticCacheStats,
)
from common_framework.core.llm import (
    BaseLLMClient,
    ChatMessage,
    ChatResponse,
    ChatStreamChunk,
    Role,
    TokenUsage,
)
from common_framework.rag.embeddings import BaseEmbeddingProvider
from common_framework.rag.engine import RAGEngine
from common_framework.rag.models import Document, RAGResponse, SearchResult


class DeterministicEmbedding(BaseEmbeddingProvider):
    """Simple embedding provider that returns predictable vectors for testing."""

    def __init__(self, mapping: dict[str, list[float]]) -> None:
        self.mapping = mapping
        self.default = [0.0, 1.0, 0.0]

    async def embed_query(self, text: str) -> list[float]:
        return self.mapping.get(text, self.default)

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [await self.embed_query(t) for t in texts]


class MockLLM(BaseLLMClient):
    """Mock LLM that counts completion calls."""

    def __init__(self) -> None:
        super().__init__()
        self.call_count: int = 0
        self.stream_call_count: int = 0

    async def complete(
        self,
        messages: list[ChatMessage],
        model: str | None = None,
        temperature: float | None = None,
        **kwargs: Any,
    ) -> ChatResponse:
        self.call_count += 1
        return ChatResponse(
            content=f"Answer to: {messages[-1].content}",
            model="mock-llm",
            usage=TokenUsage(prompt_tokens=10, completion_tokens=5, total_tokens=15),
        )

    async def structured_complete(
        self,
        messages: list[ChatMessage],
        response_model: type[Any],
        model: str | None = None,
        temperature: float | None = None,
        **kwargs: Any,
    ) -> Any:
        return response_model()

    async def complete_stream(
        self,
        messages: list[ChatMessage],
        model: str | None = None,
        temperature: float | None = None,
        **kwargs: Any,
    ):
        self.stream_call_count += 1
        yield ChatStreamChunk(delta="Hello ", model="mock-llm")
        yield ChatStreamChunk(delta="world!", model="mock-llm", finish_reason="stop")


# ============================================================================
# SemanticCache Unit Tests
# ============================================================================

def test_cache_validation() -> None:
    with pytest.raises(ValueError, match="similarity_threshold must be between 0.0 and 1.0"):
        SemanticCache(similarity_threshold=1.5)

    with pytest.raises(ValueError, match="similarity_threshold must be between 0.0 and 1.0"):
        SemanticCache(similarity_threshold=-0.1)

    with pytest.raises(ValueError, match="max_entries must be at least 1"):
        SemanticCache(max_entries=0)


@pytest.mark.asyncio
async def test_cache_miss_and_hit_exact() -> None:
    provider = DeterministicEmbedding({
        "What is Python?": [1.0, 0.0, 0.0],
        "Explain Python": [0.99, 0.05, 0.0],
        "What is Rust?": [0.0, 0.0, 1.0],
    })
    cache = SemanticCache(embedding_provider=provider, similarity_threshold=0.90)

    # Initial miss
    hit = await cache.get("What is Python?")
    assert hit is None
    stats = cache.stats()
    assert stats.misses == 1
    assert stats.hits == 0

    # Store entry
    await cache.set("What is Python?", "Python is a programming language.")
    assert cache.size() == 1

    # Exact hit
    hit = await cache.get("What is Python?")
    assert hit is not None
    assert hit.response == "Python is a programming language."
    assert hit.matched_query == "What is Python?"
    assert hit.similarity_score >= 0.99

    stats = cache.stats()
    assert stats.hits == 1
    assert stats.misses == 1
    assert stats.hit_ratio == 0.5


@pytest.mark.asyncio
async def test_cache_hit_semantic_similarity() -> None:
    # [1.0, 0.0, 0.0] and [0.99, 0.1, 0.0] have high cosine similarity
    provider = DeterministicEmbedding({
        "What is Python?": [1.0, 0.0, 0.0],
        "Explain Python": [0.99, 0.05, 0.0],
        "Tell me about cooking": [0.0, 1.0, 0.0],
    })
    cache = SemanticCache(embedding_provider=provider, similarity_threshold=0.95)

    await cache.set("What is Python?", "A versatile high-level language.")

    # High similarity query
    hit = await cache.get("Explain Python")
    assert hit is not None
    assert hit.matched_query == "What is Python?"
    assert hit.response == "A versatile high-level language."

    # Dissimilar query (miss)
    miss = await cache.get("Tell me about cooking")
    assert miss is None


@pytest.mark.asyncio
async def test_cache_namespace_isolation() -> None:
    cache = SemanticCache()

    await cache.set("Hello", "Response A", namespace="tenant_a")
    await cache.set("Hello", "Response B", namespace="tenant_b")

    hit_a = await cache.get("Hello", namespace="tenant_a")
    hit_b = await cache.get("Hello", namespace="tenant_b")
    hit_c = await cache.get("Hello", namespace="tenant_c")

    assert hit_a is not None and hit_a.response == "Response A"
    assert hit_b is not None and hit_b.response == "Response B"
    assert hit_c is None

    # Clear specific namespace
    cache.clear("tenant_a")
    assert await cache.get("Hello", namespace="tenant_a") is None
    assert await cache.get("Hello", namespace="tenant_b") is not None

    # Clear all
    cache.clear()
    assert cache.size() == 0


@pytest.mark.asyncio
async def test_cache_ttl_expiration() -> None:
    cache = SemanticCache(default_ttl=0.05)

    await cache.set("quick", "Fast response", ttl=0.03)
    assert cache.size() == 1

    hit = await cache.get("quick")
    assert hit is not None

    time.sleep(0.05)

    # After expiration, get should return None and prune
    hit = await cache.get("quick")
    assert hit is None
    assert cache.size() == 0


@pytest.mark.asyncio
async def test_cache_lru_eviction() -> None:
    provider = DeterministicEmbedding({
        "q1": [1.0, 0.0, 0.0],
        "q2": [0.0, 1.0, 0.0],
        "q3": [0.0, 0.0, 1.0],
    })
    cache = SemanticCache(embedding_provider=provider, max_entries=2)

    await cache.set("q1", "r1")
    await cache.set("q2", "r2")
    assert cache.size() == 2

    # Access q1 to update last_accessed_at
    await cache.get("q1")

    # Add q3, which should evict q2 (since q1 was accessed more recently)
    await cache.set("q3", "r3")
    assert cache.size() == 2
    assert cache.stats().evictions == 1

    # q1 and q3 should be in cache, q2 should be gone
    assert await cache.get("q1") is not None
    assert await cache.get("q3") is not None
    assert await cache.get("q2") is None


# ============================================================================
# CachedLLMClient Tests
# ============================================================================

@pytest.mark.asyncio
async def test_cached_llm_client_complete() -> None:
    mock_llm = MockLLM()
    cached_llm = CachedLLMClient(client=mock_llm)

    messages = [ChatMessage(role=Role.USER, content="Hello AI")]

    # 1. First call: miss, calls LLM
    res1 = await cached_llm.complete(messages=messages)
    assert mock_llm.call_count == 1
    assert "mock-llm" in res1.model
    assert "(cached)" not in res1.model

    # 2. Second call: hit, does not call LLM
    res2 = await cached_llm.complete(messages=messages)
    assert mock_llm.call_count == 1
    assert "(cached)" in res2.model
    assert res2.content == res1.content
    assert res2.raw.get("cache_hit") is True


@pytest.mark.asyncio
async def test_cached_llm_client_streaming() -> None:
    mock_llm = MockLLM()
    cached_llm = CachedLLMClient(client=mock_llm)

    messages = [ChatMessage(role=Role.USER, content="Stream this")]

    # 1. First call: streams from LLM, caches accumulated content
    chunks1 = []
    async for chunk in cached_llm.complete_stream(messages=messages):
        chunks1.append(chunk.delta)
    assert mock_llm.stream_call_count == 1
    assert "".join(chunks1) == "Hello world!"

    # 2. Second call: served from cache in a single chunk
    chunks2 = []
    async for chunk in cached_llm.complete_stream(messages=messages):
        chunks2.append(chunk.delta)
        assert "(cached)" in chunk.model
    assert mock_llm.stream_call_count == 1  # No additional LLM call
    assert "".join(chunks2) == "Hello world!"


# ============================================================================
# RAGEngine with SemanticCache Tests
# ============================================================================

@pytest.mark.asyncio
async def test_rag_engine_with_semantic_cache() -> None:
    mock_llm = MockLLM()
    cache = SemanticCache()
    engine = RAGEngine(llm_client=mock_llm, cache=cache)

    await engine.ingest_texts(["Python was created by Guido van Rossum in 1991."])

    # First query -> executes LLM synthesis and caches result
    res1 = await engine.query("Who created Python?")
    assert mock_llm.call_count == 1
    assert "(cached)" not in res1.model

    # Second query -> hits semantic cache, bypasses LLM
    res2 = await engine.query("Who created Python?")
    assert mock_llm.call_count == 1
    assert "(cached)" in res2.model
    assert res2.answer == res1.answer

    # Streaming query also benefits from cache
    stream_chunks = []
    async for chunk in engine.query_stream("Who created Python?"):
        stream_chunks.append(chunk.delta)
        assert "(cached)" in chunk.model
    assert mock_llm.call_count == 1
    assert mock_llm.stream_call_count == 0
    assert "".join(stream_chunks) == res1.answer
