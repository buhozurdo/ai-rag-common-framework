"""Vector store abstractions and in-memory implementation with multi-collection isolation."""

from __future__ import annotations

import json
import math
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Optional, Union

from common_framework.rag.bm25 import BM25Index, reciprocal_rank_fusion
from common_framework.rag.models import Document, SearchResult

DEFAULT_COLLECTION = "default"


def cosine_similarity(v1: list[float], v2: list[float]) -> float:
    """Compute cosine similarity between two vector lists."""
    if not v1 or not v2:
        return 0.0

    if len(v1) != len(v2):
        raise ValueError(f"Vector dimension mismatch: query vector has {len(v1)} dims, document has {len(v2)} dims")

    dot_product = sum(a * b for a, b in zip(v1, v2))
    norm_v1 = math.sqrt(sum(a * a for a in v1))
    norm_v2 = math.sqrt(sum(b * b for b in v2))

    if norm_v1 == 0.0 or norm_v2 == 0.0:
        return 0.0

    score = dot_product / (norm_v1 * norm_v2)
    # Clamp score to [0.0, 1.0] for normalized cosine distance
    return max(0.0, min(1.0, (score + 1.0) / 2.0 if score < 0 else score))


class BaseVectorStore(ABC):
    """Abstract interface for dense vector storage and retrieval with multi-collection support."""

    @abstractmethod
    async def add_documents(
        self, documents: list[Document], collection: str = DEFAULT_COLLECTION
    ) -> list[str]:
        """Store documents along with their vector embeddings into a specific collection."""
        pass

    @abstractmethod
    async def similarity_search(
        self,
        query_embedding: list[float],
        top_k: int = 4,
        collection: str = DEFAULT_COLLECTION,
        filter_metadata: Optional[dict[str, Any]] = None,
    ) -> list[SearchResult]:
        """Retrieve top_k similar documents from a collection with optional metadata filtering."""
        pass

    @abstractmethod
    async def hybrid_search(
        self,
        query_text: str,
        query_embedding: list[float],
        top_k: int = 4,
        collection: str = DEFAULT_COLLECTION,
        dense_weight: float = 0.5,
        sparse_weight: float = 0.5,
        filter_metadata: Optional[dict[str, Any]] = None,
    ) -> list[SearchResult]:
        """Perform hybrid search combining dense similarity with sparse BM25 keyword matching via RRF."""
        pass

    @abstractmethod
    async def delete(
        self, document_ids: list[str], collection: str = DEFAULT_COLLECTION
    ) -> None:
        """Remove documents by their identifiers from a collection."""
        pass

    @abstractmethod
    async def delete_collection(self, collection: str) -> None:
        """Drop an entire collection and its vector index."""
        pass

    @abstractmethod
    async def list_collections(self) -> list[str]:
        """List all collection names currently stored."""
        pass

    @abstractmethod
    async def count(self, collection: Optional[str] = None) -> int:
        """Return number of documents in a collection, or total across all collections if None."""
        pass


class InMemoryVectorStore(BaseVectorStore):
    """High-performance, pure-Python in-memory vector store with multi-collection isolation and hybrid search."""

    def __init__(self) -> None:
        # Partitioned storage: collection_name -> (doc_id -> Document)
        self._collections: dict[str, dict[str, Document]] = {}
        # Partitioned BM25 index: collection_name -> BM25Index
        self._bm25_indexes: dict[str, BM25Index] = {}

    def _ensure_collection(self, collection: str) -> tuple[dict[str, Document], BM25Index]:
        if collection not in self._collections:
            self._collections[collection] = {}
            self._bm25_indexes[collection] = BM25Index()
        return self._collections[collection], self._bm25_indexes[collection]

    async def add_documents(
        self, documents: list[Document], collection: str = DEFAULT_COLLECTION
    ) -> list[str]:
        doc_store, bm25 = self._ensure_collection(collection)
        added_ids: list[str] = []

        for doc in documents:
            if doc.embedding is None:
                raise ValueError(f"Document {doc.id!r} has no embedding. Generate embeddings before adding.")
            doc_store[doc.id] = doc
            added_ids.append(doc.id)

        bm25.add_documents(documents)
        return added_ids

    async def similarity_search(
        self,
        query_embedding: list[float],
        top_k: int = 4,
        collection: str = DEFAULT_COLLECTION,
        filter_metadata: Optional[dict[str, Any]] = None,
    ) -> list[SearchResult]:
        if collection not in self._collections or not query_embedding:
            return []

        doc_store = self._collections[collection]
        scored: list[SearchResult] = []

        for doc in doc_store.values():
            if filter_metadata:
                matches = all(doc.metadata.get(k) == v for k, v in filter_metadata.items())
                if not matches:
                    continue

            if doc.embedding is not None:
                sim = cosine_similarity(query_embedding, doc.embedding)
                scored.append(SearchResult(document=doc, score=sim))

        scored.sort(key=lambda x: x.score, reverse=True)
        return scored[:top_k]

    async def hybrid_search(
        self,
        query_text: str,
        query_embedding: list[float],
        top_k: int = 4,
        collection: str = DEFAULT_COLLECTION,
        dense_weight: float = 0.5,
        sparse_weight: float = 0.5,
        filter_metadata: Optional[dict[str, Any]] = None,
    ) -> list[SearchResult]:
        if collection not in self._collections:
            return []

        candidate_count = top_k * 3

        dense_results = await self.similarity_search(
            query_embedding=query_embedding,
            top_k=candidate_count,
            collection=collection,
            filter_metadata=filter_metadata,
        )

        sparse_results = self._bm25_indexes[collection].search(
            query=query_text,
            top_k=candidate_count,
            filter_metadata=filter_metadata,
        )

        return reciprocal_rank_fusion(
            dense_results=dense_results,
            sparse_results=sparse_results,
            k=60,
            dense_weight=dense_weight,
            sparse_weight=sparse_weight,
            top_k=top_k,
        )

    async def delete(
        self, document_ids: list[str], collection: str = DEFAULT_COLLECTION
    ) -> None:
        if collection in self._collections:
            doc_store = self._collections[collection]
            for doc_id in document_ids:
                doc_store.pop(doc_id, None)
            self._bm25_indexes[collection].delete(document_ids)

    async def delete_collection(self, collection: str) -> None:
        self._collections.pop(collection, None)
        self._bm25_indexes.pop(collection, None)

    async def list_collections(self) -> list[str]:
        return list(self._collections.keys())

    async def count(self, collection: Optional[str] = None) -> int:
        if collection is not None:
            return len(self._collections.get(collection, {}))
        return sum(len(docs) for docs in self._collections.values())

    def get(self, document_id: str, collection: str = DEFAULT_COLLECTION) -> Optional[Document]:
        """Retrieve a specific document by its ID within a collection."""
        return self._collections.get(collection, {}).get(document_id)

    def clear(self) -> None:
        """Remove all collections and documents from memory."""
        self._collections.clear()
        self._bm25_indexes.clear()

    def save_to_json(self, filepath: Union[str, Path]) -> None:
        """Serialize all collections, documents, and embeddings to a local JSON file."""
        target_path = Path(filepath)
        target_path.parent.mkdir(parents=True, exist_ok=True)
        serialized: dict[str, list[dict[str, Any]]] = {}

        for coll_name, docs in self._collections.items():
            serialized[coll_name] = [d.model_dump() for d in docs.values()]

        with open(target_path, "w", encoding="utf-8") as f:
            json.dump(serialized, f, indent=2, ensure_ascii=False)

    def load_from_json(self, filepath: Union[str, Path]) -> int:
        """Load collections and documents from a local JSON file and rebuild all indexes."""
        source_path = Path(filepath)
        if not source_path.exists():
            raise FileNotFoundError(f"Vector store file not found: {source_path}")

        with open(source_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        self.clear()
        total_loaded = 0

        # Handle both multi-collection format (dict of lists) and legacy single list
        if isinstance(data, list):
            doc_list = [Document.model_validate(item) for item in data]
            store, bm25 = self._ensure_collection(DEFAULT_COLLECTION)
            for d in doc_list:
                store[d.id] = d
            bm25.add_documents(doc_list)
            total_loaded = len(doc_list)
        elif isinstance(data, dict):
            for coll_name, items in data.items():
                doc_list = [Document.model_validate(item) for item in items]
                store, bm25 = self._ensure_collection(coll_name)
                for d in doc_list:
                    store[d.id] = d
                bm25.add_documents(doc_list)
                total_loaded += len(doc_list)

        return total_loaded
