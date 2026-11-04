"""Vector store abstractions and in-memory implementation."""

from __future__ import annotations

import math
from abc import ABC, abstractmethod
from typing import Optional

from common_framework.rag.models import Document, SearchResult


def cosine_similarity(v1: list[float], v2: list[float]) -> float:
    """Compute cosine similarity between two vector lists."""
    if len(v1) != len(v2) or not v1:
        return 0.0

    dot_product = sum(a * b for a, b in zip(v1, v2))
    norm_v1 = math.sqrt(sum(a * a for a in v1))
    norm_v2 = math.sqrt(sum(b * b for b in v2))

    if norm_v1 == 0.0 or norm_v2 == 0.0:
        return 0.0

    score = dot_product / (norm_v1 * norm_v2)
    # Clamp score to [0.0, 1.0] for normalized cosine distance
    return max(0.0, min(1.0, (score + 1.0) / 2.0 if score < 0 else score))


class BaseVectorStore(ABC):
    """Abstract interface for dense vector storage and retrieval."""

    @abstractmethod
    async def add_documents(self, documents: list[Document]) -> list[str]:
        """Store documents along with their vector embeddings."""
        pass

    @abstractmethod
    async def similarity_search(
        self, query_embedding: list[float], top_k: int = 4
    ) -> list[SearchResult]:
        """Retrieve the top_k most similar documents to the query vector."""
        pass

    @abstractmethod
    async def delete(self, document_ids: list[str]) -> None:
        """Remove documents by their identifiers."""
        pass

    @abstractmethod
    async def count(self) -> int:
        """Return total number of documents stored."""
        pass


class InMemoryVectorStore(BaseVectorStore):
    """High-performance, pure-Python in-memory vector store with zero external dependencies."""

    def __init__(self) -> None:
        self._documents: dict[str, Document] = {}

    async def add_documents(self, documents: list[Document]) -> list[str]:
        added_ids: list[str] = []
        for doc in documents:
            if doc.embedding is None:
                raise ValueError(f"Document {doc.id!r} has no embedding. Generate embeddings before adding.")
            self._documents[doc.id] = doc
            added_ids.append(doc.id)
        return added_ids

    async def similarity_search(
        self, query_embedding: list[float], top_k: int = 4
    ) -> list[SearchResult]:
        if not self._documents or not query_embedding:
            return []

        scored: list[SearchResult] = []
        for doc in self._documents.values():
            if doc.embedding is not None:
                sim = cosine_similarity(query_embedding, doc.embedding)
                scored.append(SearchResult(document=doc, score=sim))

        scored.sort(key=lambda x: x.score, reverse=True)
        return scored[:top_k]

    async def delete(self, document_ids: list[str]) -> None:
        for doc_id in document_ids:
            self._documents.pop(doc_id, None)

    async def count(self) -> int:
        return len(self._documents)

    def get(self, document_id: str) -> Optional[Document]:
        """Retrieve a specific document by its ID."""
        return self._documents.get(document_id)

    def clear(self) -> None:
        """Remove all documents from memory."""
        self._documents.clear()
