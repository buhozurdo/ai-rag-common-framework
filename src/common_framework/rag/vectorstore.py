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


try:
    import numpy as np

    _HAS_NUMPY = True
except ImportError:  # pragma: no cover
    np = None  # type: ignore
    _HAS_NUMPY = False


def normalize_vector(v: list[float]) -> tuple[list[float], float]:
    """Compute Euclidean (L2) norm and return (unit_vector, original_norm)."""
    norm = math.sqrt(sum(x * x for x in v))
    if norm == 0.0:
        return list(v), 0.0
    return [x / norm for x in v], norm


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
    # Map the full cosine range [-1, +1] uniformly to [0.0, 1.0].
    # Using conditional mapping (passthrough for positive, (s+1)/2 for negative) produces a
    # discontinuity at 0: a barely-negative vector gets ~0.5 while an orthogonal vector stays at
    # 0.0, completely inverting the semantic ordering. The uniform mapping preserves rank order.
    score = max(-1.0, min(1.0, score))
    return (score + 1.0) / 2.0


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
    """High-performance in-memory vector store with pre-normalized vectors, multi-collection isolation,

    and opportunistic NumPy/BLAS GEMV matrix multiplication acceleration.
    """

    def __init__(self, use_numpy: Optional[bool] = None) -> None:
        # Partitioned storage: collection_name -> (doc_id -> Document)
        self._collections: dict[str, dict[str, Document]] = {}
        # Partitioned BM25 index: collection_name -> BM25Index
        self._bm25_indexes: dict[str, BM25Index] = {}
        # Pre-normalized unit vectors: collection_name -> (doc_id -> unit_vector)
        self._normalized_vectors: dict[str, dict[str, list[float]]] = {}
        # Matrix cache for BLAS GEMV: collection_name -> (doc_ids_list, np.ndarray)
        self._matrix_cache: dict[str, Optional[tuple[list[str], Any]]] = {}
        self.use_numpy = _HAS_NUMPY if use_numpy is None else (use_numpy and _HAS_NUMPY)

    def _ensure_collection(self, collection: str) -> tuple[dict[str, Document], BM25Index]:
        if collection not in self._collections:
            self._collections[collection] = {}
            self._bm25_indexes[collection] = BM25Index()
            self._normalized_vectors[collection] = {}
            self._matrix_cache[collection] = None
        return self._collections[collection], self._bm25_indexes[collection]

    def _invalidate_matrix_cache(self, collection: str) -> None:
        self._matrix_cache[collection] = None

    async def add_documents(
        self, documents: list[Document], collection: str = DEFAULT_COLLECTION
    ) -> list[str]:
        doc_store, bm25 = self._ensure_collection(collection)
        norm_store = self._normalized_vectors[collection]
        added_ids: list[str] = []

        for doc in documents:
            if doc.embedding is None:
                raise ValueError(f"Document {doc.id!r} has no embedding. Generate embeddings before adding.")
            doc_store[doc.id] = doc
            unit_vec, _ = normalize_vector(doc.embedding)
            norm_store[doc.id] = unit_vec
            added_ids.append(doc.id)

        bm25.add_documents(documents)
        self._invalidate_matrix_cache(collection)
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
        if not doc_store:
            return []

        # Validate vector dimensions against stored documents
        first_doc = next(iter(doc_store.values()))
        if first_doc.embedding is not None and len(query_embedding) != len(first_doc.embedding):
            raise ValueError(
                f"Vector dimension mismatch: query vector has {len(query_embedding)} dims, document has {len(first_doc.embedding)} dims"
            )

        # Pre-normalize query embedding once
        q_unit, q_norm = normalize_vector(query_embedding)
        if q_norm == 0.0:
            return []

        norm_store = self._normalized_vectors.get(collection, {})

        # Fast path: NumPy BLAS matrix GEMV multiplication
        if self.use_numpy and np is not None:
            cached = self._matrix_cache.get(collection)
            if cached is None:
                doc_ids = [d_id for d_id, d in doc_store.items() if d.embedding is not None]
                if not doc_ids:
                    return []
                matrix = np.array([norm_store[d_id] for d_id in doc_ids], dtype=np.float32)
                self._matrix_cache[collection] = (doc_ids, matrix)

            doc_ids, matrix = self._matrix_cache[collection]  # type: ignore
            if len(doc_ids) == 0:
                return []

            q_arr = np.array(q_unit, dtype=np.float32)
            raw_scores = matrix.dot(q_arr)  # Single BLAS GEMV call
            scaled_scores = (np.clip(raw_scores, -1.0, 1.0) + 1.0) * 0.5

            if filter_metadata:
                scored: list[SearchResult] = []
                for idx, doc_id in enumerate(doc_ids):
                    doc = doc_store[doc_id]
                    if all(doc.metadata.get(k) == v for k, v in filter_metadata.items()):
                        scored.append(SearchResult(document=doc, score=float(scaled_scores[idx])))
                scored.sort(key=lambda x: x.score, reverse=True)
                return scored[:top_k]

            # No metadata filter: fast top_k index selection
            if len(doc_ids) <= top_k:
                top_indices = np.argsort(scaled_scores)[::-1]
            else:
                partition_idx = np.argpartition(scaled_scores, -top_k)[-top_k:]
                top_indices = partition_idx[np.argsort(scaled_scores[partition_idx])[::-1]]

            return [
                SearchResult(document=doc_store[doc_ids[idx]], score=float(scaled_scores[idx]))
                for idx in top_indices
            ]

        # Pure Python pre-normalized fallback (No NumPy)
        scored_py: list[SearchResult] = []
        for doc_id, doc in doc_store.items():
            if filter_metadata:
                matches = all(doc.metadata.get(k) == v for k, v in filter_metadata.items())
                if not matches:
                    continue

            unit_doc = norm_store.get(doc_id)
            if unit_doc is not None:
                dot = sum(a * b for a, b in zip(q_unit, unit_doc))
                sim = (max(-1.0, min(1.0, dot)) + 1.0) / 2.0
                scored_py.append(SearchResult(document=doc, score=sim))

        scored_py.sort(key=lambda x: x.score, reverse=True)
        return scored_py[:top_k]

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
            norm_store = self._normalized_vectors.get(collection, {})
            for doc_id in document_ids:
                doc_store.pop(doc_id, None)
                norm_store.pop(doc_id, None)
            self._bm25_indexes[collection].delete(document_ids)
            self._invalidate_matrix_cache(collection)

    async def delete_collection(self, collection: str) -> None:
        self._collections.pop(collection, None)
        self._bm25_indexes.pop(collection, None)
        self._normalized_vectors.pop(collection, None)
        self._matrix_cache.pop(collection, None)

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
        self._normalized_vectors.clear()
        self._matrix_cache.clear()

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
        """Load collections and documents from a local JSON file and rebuild all indexes.

        Parses and validates all data before clearing in-memory state to prevent data loss
        on corrupt or malformed input files.
        """
        import json as _json  # already imported at module level; alias avoids shadowing

        source_path = Path(filepath)
        if not source_path.exists():
            raise FileNotFoundError(f"Vector store file not found: {source_path}")

        try:
            with open(source_path, "r", encoding="utf-8") as f:
                data = _json.load(f)
        except _json.JSONDecodeError as exc:
            raise ValueError(f"Vector store file contains invalid JSON: {source_path}: {exc}") from exc

        # Validate all documents BEFORE clearing current state to prevent data loss
        try:
            if isinstance(data, list):
                loaded: dict[str, list[Document]] = {
                    DEFAULT_COLLECTION: [Document.model_validate(item) for item in data]
                }
            elif isinstance(data, dict):
                loaded = {
                    coll: [Document.model_validate(item) for item in items]
                    for coll, items in data.items()
                }
            else:
                raise ValueError(
                    f"Unexpected JSON structure in {source_path}: "
                    f"expected list or dict, got {type(data).__name__}"
                )
        except Exception as exc:
            raise ValueError(f"Failed to parse vector store data from {source_path}: {exc}") from exc

        # All data validated — safe to replace current state
        self.clear()
        total_loaded = 0
        for coll_name, doc_list in loaded.items():
            store, bm25 = self._ensure_collection(coll_name)
            norm_store = self._normalized_vectors[coll_name]
            for d in doc_list:
                store[d.id] = d
                if d.embedding is not None:
                    unit_vec, _ = normalize_vector(d.embedding)
                    norm_store[d.id] = unit_vec
            bm25.add_documents(doc_list)
            self._invalidate_matrix_cache(coll_name)
            total_loaded += len(doc_list)

        return total_loaded
