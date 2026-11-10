"""Embedded SQLite vector store with FTS5 BM25 hybrid search and zero external daemons."""

from __future__ import annotations

import asyncio
import json
import math
import re
import sqlite3
import struct
import time
from pathlib import Path
from typing import Any, Optional, Union

from common_framework.rag.bm25 import reciprocal_rank_fusion
from common_framework.rag.models import Document, SearchResult
from common_framework.rag.vectorstore import (
    DEFAULT_COLLECTION,
    BaseVectorStore,
    cosine_similarity,
    normalize_vector,
)

try:
    import numpy as np

    _HAS_NUMPY = True
except ImportError:  # pragma: no cover
    np = None  # type: ignore
    _HAS_NUMPY = False


def _resolve_safe_path(
    filepath: Union[str, Path],
    base_dir: Optional[Union[str, Path]] = None,
) -> Path:
    """Resolve and validate filepath against a designated base directory.

    Ensures the target filepath does not escape the allowed base directory via
    path traversal (e.g. `../` or external absolute paths).
    """
    raw_path = Path(filepath)
    designated = base_dir

    if designated is not None:
        resolved_base = Path(designated).resolve()
        if not raw_path.is_absolute():
            candidate = (resolved_base / raw_path).resolve()
        else:
            candidate = raw_path.resolve()

        if not candidate.is_relative_to(resolved_base):
            raise ValueError(
                f"Path traversal detected: '{filepath}' resolves to '{candidate}', "
                f"which is outside designated base directory '{resolved_base}'"
            )
        return candidate

    if not raw_path.is_absolute():
        candidate = (Path.cwd() / raw_path).resolve()
        if not candidate.is_relative_to(Path.cwd()):
            raise ValueError(
                f"Path traversal detected: '{filepath}' resolves to '{candidate}', "
                f"which is outside current working directory '{Path.cwd()}'"
            )
        return candidate

    return raw_path.resolve()


def _pack_vector(vec: list[float]) -> bytes:
    """Pack vector float list into binary IEEE 754 32-bit floats."""
    return struct.pack(f"{len(vec)}f", *vec)


def _unpack_vector(blob: bytes, dim: int) -> list[float]:
    """Unpack binary IEEE 754 32-bit floats into vector float list."""
    return list(struct.unpack(f"{dim}f", blob))


class SQLiteVectorStore(BaseVectorStore):
    """Embedded, ACID-compliant vector store powered by standard library SQLite.

    Features:
    - Zero external database services or daemons required.
    - IEEE 754 packed binary BLOB vector storage with fast cosine similarity search.
    - Integrated SQLite FTS5 virtual table for native BM25 sparse keyword ranking.
    - Reciprocal Rank Fusion (RRF) hybrid search combining dense and sparse results.
    - Multi-collection isolation and granular metadata filtering.
    - Optional NumPy BLAS GEMV acceleration when NumPy is installed.
    - Thread-safe and asynchronous non-blocking operation via asyncio worker threads.
    - Strict path traversal protection.
    """

    def __init__(
        self,
        db_path: Union[str, Path] = ":memory:",
        base_dir: Optional[Union[str, Path]] = None,
        use_numpy: Optional[bool] = None,
    ) -> None:
        self.db_path_str = str(db_path)
        self.base_dir: Optional[Path] = Path(base_dir).resolve() if base_dir is not None else None
        self.use_numpy = _HAS_NUMPY if use_numpy is None else (use_numpy and _HAS_NUMPY)
        self._lock = asyncio.Lock()

        if self.db_path_str == ":memory:":
            self.resolved_path: Optional[Path] = None
            self._conn = sqlite3.connect(":memory:", check_same_thread=False)
        else:
            self.resolved_path = _resolve_safe_path(db_path, base_dir=self.base_dir)
            self.resolved_path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(str(self.resolved_path), check_same_thread=False)
            try:
                self._conn.execute("PRAGMA journal_mode = WAL;")
                self._conn.execute("PRAGMA synchronous = NORMAL;")
            except sqlite3.OperationalError:
                pass

        self._init_schema()

    def _init_schema(self) -> None:
        """Create documents and FTS5 tables if they do not exist."""
        with self._conn:
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS documents (
                    id TEXT NOT NULL,
                    collection TEXT NOT NULL,
                    content TEXT NOT NULL,
                    metadata TEXT NOT NULL,
                    embedding BLOB,
                    dim INTEGER,
                    created_at REAL NOT NULL,
                    PRIMARY KEY (collection, id)
                );
            """)
            self._conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_documents_collection ON documents(collection);
            """)
            self._conn.execute("""
                CREATE VIRTUAL TABLE IF NOT EXISTS documents_fts USING fts5(
                    id UNINDEXED,
                    collection UNINDEXED,
                    content,
                    tokenize = 'unicode61'
                );
            """)

    def _add_documents_sync(
        self, documents: list[Document], collection: str = DEFAULT_COLLECTION
    ) -> list[str]:
        added_ids: list[str] = []
        now = time.time()

        for doc in documents:
            if doc.embedding is None:
                raise ValueError(
                    f"Document {doc.id!r} has no embedding. Generate embeddings before adding."
                )

        with self._conn:
            for doc in documents:
                dim = len(doc.embedding)  # type: ignore[arg-type]
                packed_vec = _pack_vector(doc.embedding)  # type: ignore[arg-type]
                meta_json = json.dumps(doc.metadata or {})

                self._conn.execute(
                    """
                    INSERT INTO documents (id, collection, content, metadata, embedding, dim, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(collection, id) DO UPDATE SET
                        content = excluded.content,
                        metadata = excluded.metadata,
                        embedding = excluded.embedding,
                        dim = excluded.dim,
                        created_at = excluded.created_at
                    """,
                    (doc.id, collection, doc.content, meta_json, packed_vec, dim, now),
                )

                self._conn.execute(
                    "DELETE FROM documents_fts WHERE collection = ? AND id = ?",
                    (collection, doc.id),
                )
                self._conn.execute(
                    "INSERT INTO documents_fts (id, collection, content) VALUES (?, ?, ?)",
                    (doc.id, collection, doc.content),
                )
                added_ids.append(doc.id)

        return added_ids

    async def add_documents(
        self, documents: list[Document], collection: str = DEFAULT_COLLECTION
    ) -> list[str]:
        """Store documents along with their vector embeddings into a specific collection."""
        if not documents:
            return []
        async with self._lock:
            return await asyncio.to_thread(self._add_documents_sync, documents, collection)

    def _similarity_search_sync(
        self,
        query_embedding: list[float],
        top_k: int = 4,
        collection: str = DEFAULT_COLLECTION,
        filter_metadata: Optional[dict[str, Any]] = None,
    ) -> list[SearchResult]:
        if not query_embedding:
            return []

        cursor = self._conn.execute(
            """
            SELECT id, content, metadata, embedding, dim
            FROM documents
            WHERE collection = ? AND embedding IS NOT NULL
            """,
            (collection,),
        )
        rows = cursor.fetchall()
        if not rows:
            return []

        # Validate vector dimensions against stored documents
        first_dim = rows[0][4]
        if first_dim is not None and len(query_embedding) != first_dim:
            raise ValueError(
                f"Vector dimension mismatch: query vector has {len(query_embedding)} dims, document has {first_dim} dims"
            )

        # Filter by metadata if requested
        filtered_rows: list[tuple[str, str, dict[str, Any], bytes, int]] = []
        for r in rows:
            doc_id, content, meta_json, blob, dim = r
            meta = json.loads(meta_json)
            if filter_metadata:
                if not all(meta.get(k) == v for k, v in filter_metadata.items()):
                    continue
            filtered_rows.append((doc_id, content, meta, blob, dim))

        if not filtered_rows:
            return []

        # Fast path: NumPy BLAS matrix GEMV multiplication
        if self.use_numpy and np is not None:
            dim = filtered_rows[0][4]
            raw_matrix = np.frombuffer(
                b"".join(r[3] for r in filtered_rows), dtype=np.float32
            ).reshape(len(filtered_rows), dim)

            norms = np.linalg.norm(raw_matrix, axis=1, keepdims=True)
            norms[norms == 0.0] = 1.0
            normed_matrix = raw_matrix / norms

            q_arr = np.asarray(query_embedding, dtype=np.float32)
            q_norm = float(np.linalg.norm(q_arr))
            if q_norm == 0.0:
                return []
            q_arr /= q_norm

            sims = normed_matrix.dot(q_arr)
            scores = (np.clip(sims, -1.0, 1.0) + 1.0) * 0.5

            if len(scores) <= top_k:
                top_indices = np.argsort(scores)[::-1]
            else:
                part = np.argpartition(scores, -top_k)[-top_k:]
                top_indices = part[np.argsort(scores[part])[::-1]]

            return [
                SearchResult(
                    document=Document(
                        id=filtered_rows[i][0],
                        content=filtered_rows[i][1],
                        metadata=filtered_rows[i][2],
                        embedding=_unpack_vector(filtered_rows[i][3], dim),
                    ),
                    score=float(scores[i]),
                )
                for i in top_indices
            ]

        # Pure Python fallback
        scored: list[tuple[float, Document]] = []
        for r in filtered_rows:
            dim = r[4]
            emb = _unpack_vector(r[3], dim)
            score = cosine_similarity(query_embedding, emb)
            doc = Document(id=r[0], content=r[1], metadata=r[2], embedding=emb)
            scored.append((score, doc))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [SearchResult(document=doc, score=score) for score, doc in scored[:top_k]]

    async def similarity_search(
        self,
        query_embedding: list[float],
        top_k: int = 4,
        collection: str = DEFAULT_COLLECTION,
        filter_metadata: Optional[dict[str, Any]] = None,
    ) -> list[SearchResult]:
        """Retrieve top_k similar documents from a collection with optional metadata filtering."""
        async with self._lock:
            return await asyncio.to_thread(
                self._similarity_search_sync,
                query_embedding,
                top_k,
                collection,
                filter_metadata,
            )

    def _hybrid_search_sync(
        self,
        query_text: str,
        query_embedding: list[float],
        top_k: int = 4,
        collection: str = DEFAULT_COLLECTION,
        dense_weight: float = 0.5,
        sparse_weight: float = 0.5,
        filter_metadata: Optional[dict[str, Any]] = None,
    ) -> list[SearchResult]:
        candidate_count = top_k * 3

        dense_results = self._similarity_search_sync(
            query_embedding=query_embedding,
            top_k=candidate_count,
            collection=collection,
            filter_metadata=filter_metadata,
        )

        tokens = re.findall(r"\w+", query_text)
        sparse_results: list[SearchResult] = []

        if tokens:
            match_expr = " OR ".join(f'"{t}"' for t in tokens)
            try:
                cursor = self._conn.execute(
                    """
                    SELECT f.id, bm25(documents_fts) AS bm25_score, d.content, d.metadata, d.embedding, d.dim
                    FROM documents_fts f
                    JOIN documents d ON f.collection = d.collection AND f.id = d.id
                    WHERE f.collection = ? AND documents_fts MATCH ?
                    ORDER BY bm25_score ASC
                    LIMIT ?
                    """,
                    (collection, match_expr, candidate_count * 2),
                )
                raw_items: list[tuple[Document, float]] = []
                for row in cursor.fetchall():
                    doc_id, bm25_score, content, meta_raw, emb_blob, dim = row
                    meta = json.loads(meta_raw)
                    if filter_metadata and not all(
                        meta.get(k) == v for k, v in filter_metadata.items()
                    ):
                        continue
                    emb = _unpack_vector(emb_blob, dim) if emb_blob else None
                    doc = Document(id=doc_id, content=content, metadata=meta, embedding=emb)
                    # bm25_score in FTS5 is negative (lower = better match). Inverting to positive:
                    raw_score = -float(bm25_score)
                    raw_items.append((doc, raw_score))
                    if len(raw_items) >= candidate_count:
                        break

                if raw_items:
                    max_score = max(s for _, s in raw_items) or 1.0
                    for doc, raw_score in raw_items:
                        normalized_score = min(1.0, max(0.0, raw_score / max_score))
                        sparse_results.append(SearchResult(document=doc, score=normalized_score))
            except sqlite3.OperationalError:
                sparse_results = []

        return reciprocal_rank_fusion(
            dense_results=dense_results,
            sparse_results=sparse_results,
            k=60,
            dense_weight=dense_weight,
            sparse_weight=sparse_weight,
            top_k=top_k,
        )

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
        """Perform hybrid search combining dense similarity with sparse FTS5 BM25 keyword matching via RRF."""
        async with self._lock:
            return await asyncio.to_thread(
                self._hybrid_search_sync,
                query_text,
                query_embedding,
                top_k,
                collection,
                dense_weight,
                sparse_weight,
                filter_metadata,
            )

    def _delete_sync(self, document_ids: list[str], collection: str = DEFAULT_COLLECTION) -> None:
        with self._conn:
            for doc_id in document_ids:
                self._conn.execute(
                    "DELETE FROM documents WHERE collection = ? AND id = ?",
                    (collection, doc_id),
                )
                self._conn.execute(
                    "DELETE FROM documents_fts WHERE collection = ? AND id = ?",
                    (collection, doc_id),
                )

    async def delete(
        self, document_ids: list[str], collection: str = DEFAULT_COLLECTION
    ) -> None:
        """Remove documents by their identifiers from a collection."""
        if not document_ids:
            return
        async with self._lock:
            await asyncio.to_thread(self._delete_sync, document_ids, collection)

    def _delete_collection_sync(self, collection: str) -> None:
        with self._conn:
            self._conn.execute("DELETE FROM documents WHERE collection = ?", (collection,))
            self._conn.execute("DELETE FROM documents_fts WHERE collection = ?", (collection,))

    async def delete_collection(self, collection: str) -> None:
        """Drop an entire collection and its vector index."""
        async with self._lock:
            await asyncio.to_thread(self._delete_collection_sync, collection)

    def _list_collections_sync(self) -> list[str]:
        cursor = self._conn.execute("SELECT DISTINCT collection FROM documents ORDER BY collection")
        return [row[0] for row in cursor.fetchall()]

    async def list_collections(self) -> list[str]:
        """List all collection names currently stored."""
        async with self._lock:
            return await asyncio.to_thread(self._list_collections_sync)

    def _count_sync(self, collection: Optional[str] = None) -> int:
        if collection is None:
            cursor = self._conn.execute("SELECT COUNT(*) FROM documents")
        else:
            cursor = self._conn.execute(
                "SELECT COUNT(*) FROM documents WHERE collection = ?", (collection,)
            )
        row = cursor.fetchone()
        return int(row[0]) if row else 0

    async def count(self, collection: Optional[str] = None) -> int:
        """Return number of documents in a collection, or total across all collections if None."""
        async with self._lock:
            return await asyncio.to_thread(self._count_sync, collection)

    def get(self, document_id: str, collection: str = DEFAULT_COLLECTION) -> Optional[Document]:
        """Retrieve a specific document by its ID within a collection."""
        cursor = self._conn.execute(
            """
            SELECT id, content, metadata, embedding, dim
            FROM documents
            WHERE collection = ? AND id = ?
            """,
            (collection, document_id),
        )
        row = cursor.fetchone()
        if not row:
            return None
        doc_id, content, meta_json, blob, dim = row
        meta = json.loads(meta_json)
        emb = _unpack_vector(blob, dim) if blob else None
        return Document(id=doc_id, content=content, metadata=meta, embedding=emb)

    def clear(self) -> None:
        """Remove all collections and documents from the SQLite database."""
        with self._conn:
            self._conn.execute("DELETE FROM documents")
            self._conn.execute("DELETE FROM documents_fts")

    def close(self) -> None:
        """Close the underlying SQLite connection cleanly."""
        try:
            self._conn.close()
        except Exception:
            pass

    def __enter__(self) -> SQLiteVectorStore:
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.close()

    def __del__(self) -> None:
        self.close()
