"""In-memory semantic caching engine for LLM completions and RAG queries."""

from __future__ import annotations

import logging
import time
from typing import Any, Optional, Sequence, Union

from pydantic import BaseModel, Field

from common_framework.core.llm import (
    BaseLLMClient,
    ChatMessage,
    ChatResponse,
    ChatStreamChunk,
    Role,
    TokenUsage,
)
from common_framework.rag.embeddings import BaseEmbeddingProvider, EmbeddingFactory
from common_framework.rag.vectorstore import cosine_similarity

logger = logging.getLogger("common_framework.cache")

DEFAULT_CACHE_THRESHOLD = 0.90
DEFAULT_MAX_ENTRIES = 1000


class SemanticCacheEntry(BaseModel):
    """An individual entry stored in the semantic cache."""

    query: str
    embedding: list[float]
    response: Any
    namespace: str = "default"
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: float = Field(default_factory=time.time)
    last_accessed_at: float = Field(default_factory=time.time)
    ttl: Optional[float] = None

    def is_expired(self, current_time: Optional[float] = None) -> bool:
        """Return True if the entry has an active TTL and has expired."""
        if self.ttl is None:
            return False
        now = current_time if current_time is not None else time.time()
        return (now - self.created_at) > self.ttl


class SemanticCacheHit(BaseModel):
    """Result returned on a successful semantic cache match."""

    query: str
    matched_query: str
    similarity_score: float
    response: Any
    namespace: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class SemanticCacheStats(BaseModel):
    """Aggregated statistics for a semantic cache instance."""

    hits: int = 0
    misses: int = 0
    evictions: int = 0
    total_entries: int = 0

    @property
    def hit_ratio(self) -> float:
        """Return ratio of cache hits over total access attempts (0.0 - 1.0)."""
        total = self.hits + self.misses
        return self.hits / total if total > 0 else 0.0


class SemanticCache:
    """Zero-dependency in-memory semantic cache using cosine similarity and LRU eviction.

    Avoids redundant, costly LLM completions and retrieval steps for queries
    that are semantically equivalent above a configurable similarity threshold.
    """

    def __init__(
        self,
        embedding_provider: Optional[BaseEmbeddingProvider] = None,
        similarity_threshold: float = DEFAULT_CACHE_THRESHOLD,
        max_entries: int = DEFAULT_MAX_ENTRIES,
        default_ttl: Optional[float] = None,
    ) -> None:
        if not (0.0 <= similarity_threshold <= 1.0):
            raise ValueError(f"similarity_threshold must be between 0.0 and 1.0, got {similarity_threshold}")
        if max_entries < 1:
            raise ValueError(f"max_entries must be at least 1, got {max_entries}")

        self.embedding_provider = embedding_provider or EmbeddingFactory.create("mock")
        self.similarity_threshold = similarity_threshold
        self.max_entries = max_entries
        self.default_ttl = default_ttl

        # Partitioned store: namespace -> list of SemanticCacheEntry
        self._entries: dict[str, list[SemanticCacheEntry]] = {}
        self._hits: int = 0
        self._misses: int = 0
        self._evictions: int = 0

    def _ensure_namespace(self, namespace: str) -> list[SemanticCacheEntry]:
        if namespace not in self._entries:
            self._entries[namespace] = []
        return self._entries[namespace]

    def _evict_expired(self, namespace: str, now: float) -> None:
        """Remove expired entries in the specified namespace."""
        if namespace in self._entries:
            valid_entries = [e for e in self._entries[namespace] if not e.is_expired(now)]
            self._entries[namespace] = valid_entries

    def _enforce_capacity(self, namespace: str) -> None:
        """Evict least-recently-accessed entry if namespace exceeds max_entries."""
        entries = self._ensure_namespace(namespace)
        if len(entries) > self.max_entries:
            # Sort by last_accessed_at ascending and remove the oldest
            entries.sort(key=lambda x: x.last_accessed_at)
            entries.pop(0)
            self._evictions += 1

    async def get(
        self,
        query: str,
        embedding: Optional[list[float]] = None,
        namespace: str = "default",
        similarity_threshold: Optional[float] = None,
    ) -> Optional[SemanticCacheHit]:
        """Look up the query in the semantic cache.

        If a stored query in the namespace has similarity >= threshold, updates LRU
        access time and returns a SemanticCacheHit. Otherwise returns None.
        """
        threshold = similarity_threshold if similarity_threshold is not None else self.similarity_threshold
        now = time.time()
        self._evict_expired(namespace, now)

        entries = self._ensure_namespace(namespace)
        if not entries:
            self._misses += 1
            return None

        # Compute query embedding if not provided
        query_emb = embedding
        if query_emb is None:
            query_emb = await self.embedding_provider.embed_query(query)

        best_score = -1.0
        best_entry: Optional[SemanticCacheEntry] = None

        for entry in entries:
            score = cosine_similarity(query_emb, entry.embedding)
            if score > best_score:
                best_score = score
                best_entry = entry

        if best_entry is not None and best_score >= threshold:
            best_entry.last_accessed_at = now
            self._hits += 1
            logger.debug(
                "Semantic cache HIT (score=%.4f >= threshold=%.4f, namespace=%r) for query: %r",
                best_score,
                threshold,
                namespace,
                query,
            )
            return SemanticCacheHit(
                query=query,
                matched_query=best_entry.query,
                similarity_score=best_score,
                response=best_entry.response,
                namespace=namespace,
                metadata=best_entry.metadata,
            )

        self._misses += 1
        logger.debug(
            "Semantic cache MISS (best_score=%.4f < threshold=%.4f, namespace=%r) for query: %r",
            best_score,
            threshold,
            namespace,
            query,
        )
        return None

    async def set(
        self,
        query: str,
        response: Any,
        embedding: Optional[list[float]] = None,
        namespace: str = "default",
        ttl: Optional[float] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> None:
        """Store a query-response pair in the semantic cache."""
        now = time.time()
        query_emb = embedding
        if query_emb is None:
            query_emb = await self.embedding_provider.embed_query(query)

        entry_ttl = ttl if ttl is not None else self.default_ttl
        entry = SemanticCacheEntry(
            query=query,
            embedding=query_emb,
            response=response,
            namespace=namespace,
            metadata=metadata or {},
            created_at=now,
            last_accessed_at=now,
            ttl=entry_ttl,
        )

        entries = self._ensure_namespace(namespace)
        entries.append(entry)
        self._enforce_capacity(namespace)
        logger.debug("Stored entry in semantic cache (namespace=%r, query=%r)", namespace, query)

    def clear(self, namespace: Optional[str] = None) -> None:
        """Wipe all cached entries from memory, optionally scoped to a namespace."""
        if namespace is not None:
            self._entries.pop(namespace, None)
        else:
            self._entries.clear()

    def size(self, namespace: Optional[str] = None) -> int:
        """Return the number of stored entries (total or in a specific namespace)."""
        now = time.time()
        if namespace is not None:
            self._evict_expired(namespace, now)
            return len(self._entries.get(namespace, []))
        total = 0
        for ns in list(self._entries.keys()):
            self._evict_expired(ns, now)
            total += len(self._entries.get(ns, []))
        return total

    def stats(self) -> SemanticCacheStats:
        """Return performance and usage statistics for this cache."""
        return SemanticCacheStats(
            hits=self._hits,
            misses=self._misses,
            evictions=self._evictions,
            total_entries=self.size(),
        )


class CachedLLMClient(BaseLLMClient):
    """Wrapper that wraps any BaseLLMClient with semantic response caching."""

    def __init__(
        self,
        client: BaseLLMClient,
        cache: Optional[SemanticCache] = None,
        namespace: str = "llm_completions",
    ) -> None:
        super().__init__(settings=client.settings)
        self.underlying_client = client
        self.cache = cache or SemanticCache()
        self.namespace = namespace

    def _extract_query_text(self, messages: list[ChatMessage]) -> str:
        """Extract user prompt text for semantic indexing."""
        user_messages = [m.content for m in messages if getattr(m, "role", None) in ("user", "User", Role.USER)]
        return user_messages[-1] if user_messages else (messages[-1].content if messages else "")

    async def complete(
        self,
        messages: list[ChatMessage],
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        **kwargs: Any,
    ) -> ChatResponse:
        query_text = self._extract_query_text(messages)
        if query_text:
            hit = await self.cache.get(query=query_text, namespace=self.namespace)
            if hit is not None:
                cached_data = hit.response
                if isinstance(cached_data, ChatResponse):
                    return ChatResponse(
                        content=cached_data.content,
                        model=f"{cached_data.model} (cached)",
                        usage=TokenUsage(),
                        raw={"cache_hit": True, "similarity_score": hit.similarity_score},
                    )
                if isinstance(cached_data, str):
                    return ChatResponse(
                        content=cached_data,
                        model=f"{model or 'model'} (cached)",
                        usage=TokenUsage(),
                        raw={"cache_hit": True, "similarity_score": hit.similarity_score},
                    )

        # Cache miss — invoke actual LLM
        response = await self.underlying_client.complete(
            messages=messages,
            model=model,
            temperature=temperature,
            **kwargs,
        )

        if query_text:
            await self.cache.set(
                query=query_text,
                response=response,
                namespace=self.namespace,
                metadata={"model": response.model},
            )

        return response

    async def structured_complete(
        self,
        messages: list[ChatMessage],
        response_model: type[Any],
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        **kwargs: Any,
    ) -> Any:
        # Pass directly through to underlying client
        return await self.underlying_client.structured_complete(
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
    ):
        query_text = self._extract_query_text(messages)
        if query_text:
            hit = await self.cache.get(query=query_text, namespace=self.namespace)
            if hit is not None:
                content = hit.response.content if isinstance(hit.response, ChatResponse) else str(hit.response)
                yield ChatStreamChunk(delta=content, model=f"{model or 'model'} (cached)", finish_reason="stop")
                return

        # Cache miss — stream from underlying client and accumulate
        collected: list[str] = []
        async for chunk in self.underlying_client.complete_stream(
            messages=messages,
            model=model,
            temperature=temperature,
            **kwargs,
        ):
            if chunk.delta:
                collected.append(chunk.delta)
            yield chunk

        if query_text and collected:
            full_response = ChatResponse(
                content="".join(collected),
                model=model or "streamed-model",
            )
            await self.cache.set(query=query_text, response=full_response, namespace=self.namespace)
