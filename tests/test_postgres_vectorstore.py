"""Unit tests for PostgresVectorStore (pgvector, HNSW/IVFFlat, JSONB filters, asyncpg/psycopg)."""

from __future__ import annotations

import json
import math
import re
from contextlib import asynccontextmanager
from typing import Any

import pytest

from common_framework import PostgresVectorStore as RootPostgresVectorStore
from common_framework.core.exceptions import ConfigurationError
from common_framework.rag import PostgresVectorStore as RagPostgresVectorStore
from common_framework.rag.embeddings import MockEmbeddingProvider
from common_framework.rag.engine import RAGEngine
from common_framework.rag.models import Document
from common_framework.rag.storage import PostgresVectorStore
from common_framework.rag.storage.postgres import _distance_to_score, _parse_vector, _to_psycopg_sql


def _json_contains(container: Any, needle: Any) -> bool:
    """Emulate PostgreSQL JSONB @> containment semantics."""
    if isinstance(needle, dict):
        return isinstance(container, dict) and all(
            k in container and _json_contains(container[k], v) for k, v in needle.items()
        )
    if isinstance(needle, list):
        return isinstance(container, list) and all(
            any(_json_contains(c_item, n_item) for c_item in container) for n_item in needle
        )
    return container == needle


def _compute_distance(v1: list[float], v2: list[float], op: str) -> float:
    if op == "<=>":
        dot = sum(a * b for a, b in zip(v1, v2))
        n1 = math.sqrt(sum(a * a for a in v1))
        n2 = math.sqrt(sum(b * b for b in v2))
        return 1.0 - (dot / (n1 * n2)) if n1 and n2 else 1.0
    if op == "<->":
        return math.sqrt(sum((a - b) ** 2 for a, b in zip(v1, v2)))
    return -sum(a * b for a, b in zip(v1, v2))


class _FakePostgresBackend:
    """In-memory SQL simulator handling PostgresVectorStore DDL, CRUD, vector & FTS queries."""

    def __init__(self) -> None:
        self.rows: dict[tuple[str, str], dict[str, Any]] = {}
        self.ddl_history: list[str] = []
        self.session_settings: list[str] = []
        self.closed = False

    def _run_query(self, sql: str, params: tuple[Any, ...]) -> list[tuple[Any, ...]]:
        norm = " ".join(sql.split())
        upper = norm.upper()

        if upper.startswith(("CREATE ", "ALTER ")):
            self.ddl_history.append(norm)
            return []

        if upper.startswith("SET LOCAL "):
            self.session_settings.append(norm)
            return []

        if upper.startswith("INSERT INTO "):
            doc_id, collection, content, meta_json, vec_lit = params
            self.rows[(collection, doc_id)] = {
                "id": doc_id,
                "collection": collection,
                "content": content,
                "metadata": json.loads(meta_json),
                "embedding": _parse_vector(vec_lit),
            }
            return []

        if upper.startswith("DELETE FROM "):
            if "ANY(" in upper:
                collection, doc_ids = params
                for doc_id in doc_ids:
                    self.rows.pop((collection, doc_id), None)
            else:
                (collection,) = params
                for key in [k for k in self.rows if k[0] == collection]:
                    del self.rows[key]
            return []

        if upper.startswith("SELECT DISTINCT COLLECTION"):
            return [(c,) for c in sorted({k[0] for k in self.rows})]

        if upper.startswith("SELECT COUNT(*)"):
            if params:
                (collection,) = params
                return [(sum(1 for k in self.rows if k[0] == collection),)]
            return [(len(self.rows),)]

        if "WHERE COLLECTION = $1 AND ID = $2" in upper or "WHERE COLLECTION = %S AND ID = %S" in upper:
            collection, doc_id = params
            row = self.rows.get((collection, doc_id))
            if not row:
                return []
            return [(row["id"], row["content"], json.dumps(row["metadata"]), json.dumps(row["embedding"]))]

        if "AS DISTANCE" in upper:
            op_match = re.search(r"\(EMBEDDING\s+(<=>|<->|<#>)\s+", upper)
            op = op_match.group(1) if op_match else "<=>"
            if "%S" in upper:
                # psycopg ordered params: [q_vec, collection, (optional filter), q_vec, limit]
                q_vec = _parse_vector(params[0]) or []
                collection = params[1]
                filter_meta = json.loads(params[2]) if len(params) == 5 else None
                limit = int(params[-1])
            else:
                collection = params[0]
                q_vec = _parse_vector(params[1]) or []
                filter_meta = json.loads(params[2]) if len(params) == 4 else None
                limit = int(params[-1])

            scored = []
            for row in self.rows.values():
                if row["collection"] != collection or row["embedding"] is None:
                    continue
                if filter_meta and not _json_contains(row["metadata"], filter_meta):
                    continue
                dist = _compute_distance(q_vec, row["embedding"], op)
                scored.append((
                    dist,
                    (
                        row["id"],
                        row["content"],
                        json.dumps(row["metadata"]),
                        json.dumps(row["embedding"]),
                        dist,
                    ),
                ))
            scored.sort(key=lambda item: item[0])
            return [item[1] for item in scored[:limit]]

        if "TS_RANK_CD" in upper:
            if "%S" in upper:
                # psycopg ordered params: [query_text, collection, query_text, (optional filter), limit]
                query_text, collection = params[0], params[1]
                filter_meta = json.loads(params[3]) if len(params) == 5 else None
                limit = int(params[-1])
            else:
                collection, query_text = params[0], params[1]
                filter_meta = json.loads(params[2]) if len(params) == 4 else None
                limit = int(params[-1])
            tokens = {t.lower() for t in re.findall(r"\w+", query_text)}

            ranked = []
            for row in self.rows.values():
                if row["collection"] != collection:
                    continue
                if filter_meta and not _json_contains(row["metadata"], filter_meta):
                    continue
                content_tokens = re.findall(r"\w+", row["content"].lower())
                hits = sum(1 for tok in content_tokens if tok in tokens)
                if hits > 0:
                    ranked.append((
                        float(hits),
                        (
                            row["id"],
                            row["content"],
                            json.dumps(row["metadata"]),
                            json.dumps(row["embedding"]),
                            float(hits),
                        ),
                    ))
            ranked.sort(key=lambda item: item[0], reverse=True)
            return [item[1] for item in ranked[:limit]]

        raise AssertionError(f"Unhandled fake SQL: {norm}")


class _FakeAsyncpgConnection:
    def __init__(self, backend: _FakePostgresBackend) -> None:
        self._backend = backend

    @asynccontextmanager
    async def transaction(self):
        yield self

    async def execute(self, sql: str, *params: Any) -> None:
        self._backend._run_query(sql, params)

    async def fetch(self, sql: str, *params: Any) -> list[tuple[Any, ...]]:
        return self._backend._run_query(sql, params)

    async def fetchval(self, sql: str, *params: Any) -> Any:
        rows = self._backend._run_query(sql, params)
        return rows[0][0] if rows else None


class _FakeAsyncpgPool:
    def __init__(self, backend: _FakePostgresBackend) -> None:
        self.backend = backend

    @asynccontextmanager
    async def acquire(self):
        yield _FakeAsyncpgConnection(self.backend)

    async def close(self) -> None:
        self.backend.closed = True


class _FakePsycopgCursor:
    def __init__(self, backend: _FakePostgresBackend) -> None:
        self._backend = backend
        self._results: list[tuple[Any, ...]] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None

    async def execute(self, sql: str, params: list[Any]) -> None:
        self._results = self._backend._run_query(sql, tuple(params))

    async def fetchall(self) -> list[tuple[Any, ...]]:
        return self._results


class _FakePsycopgConnection:
    def __init__(self, backend: _FakePostgresBackend) -> None:
        self._backend = backend

    @asynccontextmanager
    async def transaction(self):
        yield self

    async def execute(self, sql: str, params: list[Any]) -> None:
        self._backend._run_query(sql, tuple(params))

    def cursor(self) -> _FakePsycopgCursor:
        return _FakePsycopgCursor(self._backend)


class _FakePsycopgPool:
    def __init__(self, backend: _FakePostgresBackend) -> None:
        self.backend = backend

    @asynccontextmanager
    async def connection(self):
        yield _FakePsycopgConnection(self.backend)

    async def close(self) -> None:
        self.backend.closed = True


def test_postgres_vectorstore_exports_and_validation() -> None:
    assert PostgresVectorStore is RagPostgresVectorStore
    assert PostgresVectorStore is RootPostgresVectorStore

    with pytest.raises(ValueError, match="Invalid PostgreSQL table_name"):
        PostgresVectorStore(table_name="docs; DROP TABLE users")
    with pytest.raises(ValueError, match="Invalid PostgreSQL schema"):
        PostgresVectorStore(schema="bad-schema")
    with pytest.raises(ValueError, match="Unsupported index_type"):
        PostgresVectorStore(index_type="btree")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="Unsupported distance_metric"):
        PostgresVectorStore(distance_metric="jaccard")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="min_size"):
        PostgresVectorStore(min_size=5, max_size=2)


def test_postgres_ddl_generation_hnsw_and_ivfflat() -> None:
    hnsw_store = PostgresVectorStore(
        schema="rag_prod",
        table_name="chunks",
        dimension=1536,
        index_type="hnsw",
        distance_metric="cosine",
        hnsw_m=24,
        hnsw_ef_construction=128,
    )
    ddl = "\n".join(hnsw_store.build_schema_ddl())
    assert "CREATE EXTENSION IF NOT EXISTS vector;" in ddl
    assert '"rag_prod"."chunks"' in ddl
    assert "embedding vector(1536)" in ddl
    assert "USING gin (metadata)" in ddl
    assert "USING hnsw (embedding vector_cosine_ops) WITH (m = 24, ef_construction = 128)" in ddl

    ivf_store = PostgresVectorStore(
        table_name="vectors",
        dimension=384,
        index_type="ivfflat",
        distance_metric="l2",
        ivfflat_lists=250,
        create_extension=False,
    )
    ivf_ddl = "\n".join(ivf_store.build_schema_ddl())
    assert "CREATE EXTENSION" not in ivf_ddl
    assert "USING ivfflat (embedding vector_l2_ops) WITH (lists = 250)" in ivf_ddl


def test_postgres_helpers_and_placeholder_conversion() -> None:
    sql, params = _to_psycopg_sql("SELECT * FROM t WHERE a = $2 AND b = $1", ("first", "second"))
    assert sql == "SELECT * FROM t WHERE a = %s AND b = %s"
    assert params == ["second", "first"]

    assert _distance_to_score(0.0, "cosine") == pytest.approx(1.0)
    assert _distance_to_score(2.0, "cosine") == pytest.approx(0.0)
    assert _distance_to_score(0.0, "l2") == pytest.approx(1.0)
    assert 0.0 < _distance_to_score(-5.0, "inner_product") <= 1.0


@pytest.mark.asyncio
async def test_postgres_missing_dsn_raises_configuration_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("POSTGRES_DSN", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    store = PostgresVectorStore()
    with pytest.raises(ConfigurationError, match="PostgreSQL connection string missing"):
        await store.count()


@pytest.mark.asyncio
@pytest.mark.parametrize("pool_cls", [_FakeAsyncpgPool, _FakePsycopgPool])
async def test_postgres_crud_jsonb_filter_and_hybrid_search(pool_cls: type[Any]) -> None:
    backend = _FakePostgresBackend()
    pool = pool_cls(backend)

    async with PostgresVectorStore(
        pool=pool,
        index_type="hnsw",
        hnsw_ef_search=80,
    ) as store:
        docs = [
            Document(
                id="d1",
                content="pgvector HNSW index accelerates approximate nearest neighbor search",
                metadata={"tenant": "acme", "tags": ["rag", "postgres"], "nested": {"tier": "pro"}},
                embedding=[1.0, 0.0, 0.0],
            ),
            Document(
                id="d2",
                content="SQLite FTS5 provides embedded BM25 keyword retrieval",
                metadata={"tenant": "acme", "tags": ["sqlite"], "nested": {"tier": "free"}},
                embedding=[0.0, 1.0, 0.0],
            ),
            Document(
                id="d3",
                content="pgvector JSONB containment enables multi-tenant isolation",
                metadata={"tenant": "beta", "tags": ["postgres"], "nested": {"tier": "pro"}},
                embedding=[0.9, 0.1, 0.0],
            ),
        ]

        ids = await store.add_documents(docs, collection="kb")
        assert ids == ["d1", "d2", "d3"]
        assert store.dimension == 3
        assert any("ALTER TABLE" in stmt and "vector(3)" in stmt for stmt in backend.ddl_history)
        assert any("USING hnsw" in stmt for stmt in backend.ddl_history)

        assert await store.count("kb") == 3
        assert await store.list_collections() == ["kb"]

        fetched = await store.get_document("d1", collection="kb")
        assert fetched is not None and fetched.metadata["nested"]["tier"] == "pro"

        # Similarity search with JSONB nested filter
        results = await store.similarity_search(
            [1.0, 0.0, 0.0],
            top_k=2,
            collection="kb",
            filter_metadata={"tenant": "acme", "nested": {"tier": "pro"}},
        )
        assert len(results) == 1
        assert results[0].document.id == "d1"
        assert results[0].score == pytest.approx(1.0)
        assert any("SET LOCAL hnsw.ef_search = 80" in s for s in backend.session_settings)

        # Hybrid search combines dense + FTS via RRF
        hybrid = await store.hybrid_search(
            query_text="pgvector HNSW",
            query_embedding=[1.0, 0.0, 0.0],
            top_k=2,
            collection="kb",
            filter_metadata={"tenant": "acme"},
        )
        assert len(hybrid) >= 1
        assert hybrid[0].document.id == "d1"

        # Delete single document & collection
        await store.delete(["d2"], collection="kb")
        assert await store.count("kb") == 2
        await store.delete_collection("kb")
        assert await store.count() == 0

    # External pool is not closed on context exit
    assert backend.closed is False


@pytest.mark.asyncio
async def test_postgres_validation_and_rag_engine_integration() -> None:
    backend = _FakePostgresBackend()
    pool = _FakeAsyncpgPool(backend)
    store = PostgresVectorStore(pool=pool, dimension=4, index_type="ivfflat", ivfflat_probes=10)

    with pytest.raises(ValueError, match="has no embedding"):
        await store.add_documents([Document(id="bad", content="x", embedding=None)])

    with pytest.raises(ValueError, match="Vector dimension mismatch"):
        await store.add_documents([Document(id="bad-dim", content="x", embedding=[1.0, 2.0])])

    with pytest.raises(ValueError, match="Vector dimension mismatch"):
        await store.similarity_search([1.0, 2.0])

    # RAGEngine integration using MockEmbeddingProvider(dimension=4)
    embedder = MockEmbeddingProvider(dimension=4)
    engine = RAGEngine(vector_store=store, embedding_provider=embedder)
    added = await engine.ingest_texts(
        ["PostgreSQL pgvector supports HNSW and IVFFlat indexes."],
        collection="docs",
    )
    assert len(added) == 1
    retrieved = await engine.retrieve("HNSW index", top_k=1, collection="docs")
    assert len(retrieved) == 1
    assert "HNSW" in retrieved[0].document.content
    assert any("SET LOCAL ivfflat.probes = 10" in s for s in backend.session_settings)
