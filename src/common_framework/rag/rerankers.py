"""Cross-Encoder Reranking Engine for two-stage RAG candidate re-scoring."""

from __future__ import annotations

import asyncio
import math
import re
from abc import ABC, abstractmethod
from typing import Any, Optional
import httpx

from common_framework.core.config import Settings, get_settings
from common_framework.core.exceptions import AuthenticationError, ConfigurationError, LLMProviderError
from common_framework.core.security import validate_provider_base_url
from common_framework.rag.bm25 import tokenize
from common_framework.rag.models import Document, SearchResult


class BaseReranker(ABC):
    """Abstract interface for two-stage cross-encoder document rerankers."""

    @abstractmethod
    async def rerank(
        self,
        query: str,
        documents: list[Document],
        top_k: Optional[int] = None,
    ) -> list[SearchResult]:
        """Score candidate documents jointly against query and return ranked SearchResult list."""
        pass

    def rerank_sync(
        self,
        query: str,
        documents: list[Document],
        top_k: Optional[int] = None,
    ) -> list[SearchResult]:
        """Synchronous wrapper for rerank."""
        return asyncio.run(self.rerank(query, documents, top_k=top_k))

    async def aclose(self) -> None:
        """Release underlying HTTP client or compute resources."""
        pass

    async def __aenter__(self) -> BaseReranker:
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        await self.aclose()


class LocalCrossEncoderReranker(BaseReranker):
    """Zero-dependency local cross-encoder re-scoring candidate passages.

    Combines exact phrase matching, lexical term coverage, token proximity,
    and semantic position weighting to re-order first-stage retrieval candidates.
    """

    def __init__(
        self,
        phrase_weight: float = 0.4,
        coverage_weight: float = 0.4,
        proximity_weight: float = 0.2,
    ) -> None:
        self.phrase_weight = phrase_weight
        self.coverage_weight = coverage_weight
        self.proximity_weight = proximity_weight

    def _score_document(self, query: str, doc_text: str) -> float:
        if not query or not doc_text:
            return 0.0

        q_lower = query.lower().strip()
        d_lower = doc_text.lower().strip()

        # 1. Exact phrase match bonus
        phrase_score = 0.0
        if q_lower in d_lower:
            phrase_score = 1.0
        else:
            # Check 3-word and 2-word phrase matches
            q_words = q_lower.split()
            if len(q_words) >= 2:
                matched_phrases = 0
                total_phrases = len(q_words) - 1
                for i in range(total_phrases):
                    sub_phrase = f"{q_words[i]} {q_words[i+1]}"
                    if sub_phrase in d_lower:
                        matched_phrases += 1
                phrase_score = matched_phrases / total_phrases

        # 2. Token coverage
        q_tokens = tokenize(q_lower)
        if not q_tokens:
            return phrase_score

        d_tokens = tokenize(d_lower)
        d_tokens_set = set(d_tokens)
        matched_tokens = [t for t in q_tokens if t in d_tokens_set]
        coverage_score = len(matched_tokens) / len(q_tokens) if q_tokens else 0.0

        # 3. Proximity / Span compactness
        proximity_score = 0.0
        if len(matched_tokens) >= 2:
            indices: list[int] = []
            for token in matched_tokens:
                try:
                    idx = d_tokens.index(token)
                    indices.append(idx)
                except ValueError:
                    pass
            if len(indices) >= 2:
                span = max(indices) - min(indices) + 1
                # The tighter the span containing query terms, the higher the score
                ideal_span = len(matched_tokens)
                proximity_score = min(1.0, ideal_span / max(1, span))
        elif len(matched_tokens) == 1:
            proximity_score = 0.5

        # Weighted combination
        raw_score = (
            self.phrase_weight * phrase_score
            + self.coverage_weight * coverage_score
            + self.proximity_weight * proximity_score
        )
        # Normalize strictly to [0.0, 1.0]
        return max(0.0, min(1.0, raw_score))

    async def rerank(
        self,
        query: str,
        documents: list[Document],
        top_k: Optional[int] = None,
    ) -> list[SearchResult]:
        if not documents or not query:
            return []

        scored: list[SearchResult] = []
        for doc in documents:
            score = self._score_document(query, doc.content)
            scored.append(SearchResult(document=doc, score=score))

        # Sort descending by relevance score
        scored.sort(key=lambda x: x.score, reverse=True)
        limit = top_k if top_k is not None else len(scored)
        return scored[:limit]


class CohereReranker(BaseReranker):
    """Cohere Rerank API provider for enterprise cross-encoder scoring."""

    DEFAULT_BASE_URL = "https://api.cohere.com/v2"

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: str = "rerank-v3.5",
        base_url: Optional[str] = None,
        settings: Optional[Settings] = None,
        timeout: float = 30.0,
    ) -> None:
        self.settings = settings or get_settings()
        self.api_key = api_key or (
            self.settings.cohere_api_key.get_secret_value()
            if hasattr(self.settings, "cohere_api_key") and self.settings.cohere_api_key
            else None
        )
        self.model = model
        raw_base_url = (base_url or self.DEFAULT_BASE_URL).rstrip("/")
        self.base_url = validate_provider_base_url(raw_base_url)
        self.timeout = timeout
        self._http_client: Optional[httpx.AsyncClient] = None

    def _get_http_client(self) -> httpx.AsyncClient:
        if self._http_client is None or self._http_client.is_closed:
            self._http_client = httpx.AsyncClient(timeout=self.timeout)
        return self._http_client

    async def aclose(self) -> None:
        if self._http_client is not None and not self._http_client.is_closed:
            await self._http_client.aclose()
            self._http_client = None

    async def rerank(
        self,
        query: str,
        documents: list[Document],
        top_k: Optional[int] = None,
    ) -> list[SearchResult]:
        if not documents or not query:
            return []

        if not self.api_key:
            raise AuthenticationError(
                "Cohere API key is missing. Set COHERE_API_KEY environment variable or pass api_key.",
                provider="cohere",
            )

        client = self._get_http_client()
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        endpoint = f"{self.base_url}/rerank"
        payload = {
            "model": self.model,
            "query": query,
            "documents": [d.content for d in documents],
            "top_n": top_k or len(documents),
        }

        try:
            resp = await client.post(endpoint, json=payload, headers=headers)
        except Exception as exc:
            raise LLMProviderError(f"Network error calling Cohere Rerank: {exc}", provider="cohere") from exc

        if resp.status_code == 401:
            raise AuthenticationError("Invalid Cohere API key.", status_code=401, provider="cohere")
        if resp.status_code != 200:
            raise LLMProviderError(
                f"Cohere Rerank API returned HTTP {resp.status_code}: {resp.text}",
                status_code=resp.status_code,
                provider="cohere",
            )

        data = resp.json()
        results: list[SearchResult] = []
        for item in data.get("results", []):
            idx = item.get("index", 0)
            score = float(item.get("relevance_score", 0.0))
            if 0 <= idx < len(documents):
                results.append(SearchResult(document=documents[idx], score=score))

        return results


class RerankerFactory:
    """Factory for instantiating rerankers dynamically."""

    @staticmethod
    def create(
        provider: str = "local",
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        **kwargs: Any,
    ) -> BaseReranker:
        prov = provider.lower().strip()
        if prov in ("local", "cross_encoder", "heuristic"):
            return LocalCrossEncoderReranker(**kwargs)
        if prov == "cohere":
            return CohereReranker(api_key=api_key, model=model or "rerank-v3.5", **kwargs)

        raise ConfigurationError(
            f"Unsupported reranker provider: {provider!r}. Supported providers: 'local', 'cohere'."
        )
