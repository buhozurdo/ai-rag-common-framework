"""PostgreSQL + pgvector vector store with HNSW/IVFFlat indexes and JSONB metadata filtering."""

from __future__ import annotations

import asyncio
import json
import math
import os
import re
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Literal, Optional

from common_framework.core.exceptions import ConfigurationError
from common_framework.rag.bm25 import reciprocal_rank_fusion
from common_framework.rag.models import Document, SearchResult
from common_framework.rag.vectorstore import DEFAULT_COLLECTION, BaseVectorStore

_SQL_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")
_DOLLAR_PARAM_RE = re.compile(r"\$(\d+)")

_METRIC_SPECS: dict[str, tuple[str, str]] = {
    "cosine": ("<=>", "vector_cosine_ops"),
    "l2": ("<->", "vector_l2_ops"),
    "inner_product": ("<#>", "vector_ip_ops"),
}


def _validate_identifier(name: str, kind: str) -> str:
    """Validate a PostgreSQL identifier to prevent SQL injection."""
    if not _SQL_IDENT_RE.match(name):
        raise ValueError(
            f"Invalid PostgreSQL {kind} {name!r}: must match ^[A-Za-z_][A-Za-z0-9_]{{0,62}}$"
        )
    return name


def _format_vector(vec: list[float]) -> str:
    """Format a float list as a pgvector literal '[x1,x2,...]'."""
    if not vec:
        raise ValueError("Vector embedding must not be empty.")
    parts: list[str] = []
    for x in vec:
        fx = float(x)
        if not math.isfinite(fx):
            raise ValueError(f"Vector embedding contains non-finite value: {x!r}")
        parts.append(repr(fx))
    return f"[{','.join(parts)}]"


def _parse_vector(raw: Any) -> Optional[list[float]]:
    """Parse a pgvector column value (text, list, or array-like) into list[float]."""
    if raw is None:
        return None
    if isinstance(raw, str):
        stripped = raw.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            inner = stripped[1:-1].strip()
            return [float(x) for x in inner.split(",")] if inner else []
        raise ValueError(f"Unexpected pgvector text format: {raw!r}")
    if hasattr(raw, "tolist"):
        return [float(x) for x in raw.tolist()]
    return [float(x) for x in raw]


def _parse_metadata(raw: Any) -> dict[str, Any]:
    """Normalize JSONB metadata returned as str or mapping into dict."""
    if raw is None:
        return {}
    if isinstance(raw, str):
        return dict(json.loads(raw))
    return dict(raw)


def _distance_to_score(distance: float, metric: str) -> float:
    """Convert a pgvector distance into a normalized similarity score in [0.0, 1.0]."""
    if metric == "cosine":
        # pgvector <=> returns cosine distance (1 - cos_sim) in [0, 2]
        cos_sim = max(-1.0, min(1.0, 1.0 - float(distance)))
        return (cos_sim + 1.0) * 0.5
    if metric == "l2":
        return 1.0 / (1.0 + max(0.0, float(distance)))
    # inner_product: <#> returns -dot(u, v); map monotonically via sigmoid to (0, 1)
    clamped = max(-60.0, min(60.0, float(distance)))
    return 1.0 / (1.0 + math.exp(clamped))


def _to_psycopg_sql(sql: str, params: tuple[Any, ...]) -> tuple[str, list[Any]]:
    """Convert PostgreSQL $1/$2 positional placeholders to psycopg %s placeholders."""
    ordered: list[Any] = []

    def _replace(match: re.Match[str]) -> str:
        idx = int(match.group(1)) - 1
        ordered.append(params[idx])
        return "%s"

    return _DOLLAR_PARAM_RE.sub(_replace, sql), ordered


def _normalize_dsn(dsn: str) -> str:
    """Strip SQLAlchemy-style driver suffixes so asyncpg/psycopg accept the URI."""
    for prefix in ("postgresql+asyncpg://", "postgresql+psycopg://", "postgres+asyncpg://"):
        if dsn.startswith(prefix):
            return "postgresql://" + dsn[len(prefix) :]
    return dsn


class PostgresVectorStore(BaseVectorStore):
    """Enterprise vector store backed by PostgreSQL + pgvector.

    Features:
    - Native pgvector similarity search with HNSW or IVFFlat indexing.
    - Cosine, L2, and inner product distance metrics mapped to [0.0, 1.0] scores.
    - Multi-collection isolation and GIN-indexed JSONB metadata containment filtering (`@>`).
    - Hybrid search combining pgvector dense retrieval with PostgreSQL full-text search via RRF.
    - Connection pooling supporting both `asyncpg` and `psycopg` (v3) drivers, or injected pools.
    """

    def __init__(
        self,
        dsn: Optional[str] = None,
        *,
        pool: Optional[Any] = None,
        backend: Literal["auto", "asyncpg", "psycopg"] = "auto",
        table_name: str = "documents",
        schema: str = "public",
        dimension: Optional[int] = None,
        index_type: Literal["hnsw", "ivfflat", "none"] = "hnsw",
        distance_metric: Literal["cosine", "l2", "inner_product"] = "cosine",
        hnsw_m: int = 16,
        hnsw_ef_construction: int = 64,
        hnsw_ef_search: Optional[int] = None,
        ivfflat_lists: int = 100,
        ivfflat_probes: Optional[int] = None,
        min_size: int = 1,
        max_size: int = 10,
        create_extension: bool = True,
    ) -> None:
        self.dsn = dsn or os.getenv("POSTGRES_DSN") or os.getenv("DATABASE_URL")
        self._pool = pool
        self._owns_pool = pool is None
        self.backend = backend
        self.table_name = _validate_identifier(table_name, "table_name")
        self.schema = _validate_identifier(schema, "schema")
        self._qualified_table = f'"{self.schema}"."{self.table_name}"'

        if dimension is not None and dimension <= 0:
            raise ValueError(f"dimension must be positive, got {dimension}")
        self.dimension = dimension

        if index_type not in ("hnsw", "ivfflat", "none"):
            raise ValueError(f"Unsupported index_type {index_type!r}: expected 'hnsw', 'ivfflat', or 'none'")
        self.index_type = index_type

        if distance_metric not in _METRIC_SPECS:
            raise ValueError(
                f"Unsupported distance_metric {distance_metric!r}: expected one of {tuple(_METRIC_SPECS)}"
            )
        self.distance_metric = distance_metric

        for name, val in (
            ("hnsw_m", hnsw_m),
            ("hnsw_ef_construction", hnsw_ef_construction),
            ("ivfflat_lists", ivfflat_lists),
            ("min_size", min_size),
            ("max_size", max_size),
        ):
            if val <= 0:
                raise ValueError(f"{name} must be positive, got {val}")
        if min_size > max_size:
            raise ValueError(f"min_size ({min_size}) cannot exceed max_size ({max_size})")
        if hnsw_ef_search is not None and hnsw_ef_search <= 0:
            raise ValueError(f"hnsw_ef_search must be positive, got {hnsw_ef_search}")
        if ivfflat_probes is not None and ivfflat_probes <= 0:
            raise ValueError(f"ivfflat_probes must be positive, got {ivfflat_probes}")

        self.hnsw_m = hnsw_m
        self.hnsw_ef_construction = hnsw_ef_construction
        self.hnsw_ef_search = hnsw_ef_search
        self.ivfflat_lists = ivfflat_lists
        self.ivfflat_probes = ivfflat_probes
        self.min_size = min_size
        self.max_size = max_size
        self.create_extension = create_extension

        self._init_lock = asyncio.Lock()
        self._schema_initialized = False
        self._column_typed = dimension is not None
        self._vector_index_created = False
        self._driver: Optional[Literal["asyncpg", "psycopg"]] = self._detect_pool_driver(pool)

    @staticmethod
    def _detect_pool_driver(pool: Optional[Any]) -> Optional[Literal["asyncpg", "psycopg"]]:
        if pool is None:
            return None
        if hasattr(pool, "connection") and not hasattr(pool, "acquire"):
            return "psycopg"
        return "asyncpg"

    def _resolve_driver(self) -> Literal["asyncpg", "psycopg"]:
        if self._driver is not None:
            return self._driver
        if self.backend in ("asyncpg", "psycopg"):
            return self.backend
        if self.dsn and self.dsn.startswith("postgresql+psycopg://"):
            return "psycopg"
        try:
            import asyncpg  # noqa: F401

            return "asyncpg"
        except ImportError:
            try:
                import psycopg_pool  # noqa: F401

                return "psycopg"
            except ImportError as exc:
                raise ConfigurationError(
                    "PostgresVectorStore requires 'asyncpg' or 'psycopg[binary]' + 'psycopg_pool'. "
                    "Install with: pip install asyncpg pgvector"
                ) from exc

    async def _ensure_pool(self) -> Any:
        if self._pool is not None:
            return self._pool
        if not self.dsn:
            raise ConfigurationError(
                "PostgreSQL connection string missing. Pass `dsn=...` or set POSTGRES_DSN / DATABASE_URL."
            )
        driver = self._resolve_driver()
        clean_dsn = _normalize_dsn(self.dsn)
        if driver == "asyncpg":
            try:
                import asyncpg
            except ImportError as exc:
                raise ConfigurationError(
                    "asyncpg is required for backend='asyncpg'. Install with: pip install asyncpg"
                ) from exc
            self._pool = await asyncpg.create_pool(
                dsn=clean_dsn,
                min_size=self.min_size,
                max_size=self.max_size,
            )
        else:
            try:
                from psycopg_pool import AsyncConnectionPool
            except ImportError as exc:
                raise ConfigurationError(
                    "psycopg_pool is required for backend='psycopg'. Install with: pip install 'psycopg[binary]' psycopg_pool"
                ) from exc
            self._pool = AsyncConnectionPool(
                conninfo=clean_dsn,
                min_size=self.min_size,
                max_size=self.max_size,
                open=False,
            )
            await self._pool.open()
        self._driver = driver
        return self._pool

    @asynccontextmanager
    async def _acquire(self) -> AsyncIterator[tuple[Any, Literal["asyncpg", "psycopg"]]]:
        pool = await self._ensure_pool()
        driver = self._driver or self._detect_pool_driver(pool) or "asyncpg"
        if driver == "psycopg":
            async with pool.connection() as conn:
                yield conn, "psycopg"
        else:
            async with pool.acquire() as conn:
                yield conn, "asyncpg"

    async def _exec(self, conn: Any, driver: Literal["asyncpg", "psycopg"], sql: str, *params: Any) -> None:
        if driver == "psycopg":
            pg_sql, pg_params = _to_psycopg_sql(sql, params)
            await conn.execute(pg_sql, pg_params)
        else:
            await conn.execute(sql, *params)

    async def _fetch(self, conn: Any, driver: Literal["asyncpg", "psycopg"], sql: str, *params: Any) -> list[Any]:
        if driver == "psycopg":
            pg_sql, pg_params = _to_psycopg_sql(sql, params)
            async with conn.cursor() as cur:
                await cur.execute(pg_sql, pg_params)
                return list(await cur.fetchall())
        return list(await conn.fetch(sql, *params))

    async def _fetchval(self, conn: Any, driver: Literal["asyncpg", "psycopg"], sql: str, *params: Any) -> Any:
        if driver == "psycopg":
            rows = await self._fetch(conn, driver, sql, *params)
            return rows[0][0] if rows else None
        return await conn.fetchval(sql, *params)

    def build_schema_ddl(self, dimension: Optional[int] = None) -> list[str]:
        """Return the ordered DDL statements used to create table and indexes."""
        dim = dimension if dimension is not None else self.dimension
        col_type = f"vector({dim})" if dim is not None else "vector"
        stmts: list[str] = []
        if self.create_extension:
            stmts.append("CREATE EXTENSION IF NOT EXISTS vector;")
        stmts.append(
            f"""
            CREATE TABLE IF NOT EXISTS {self._qualified_table} (
                id TEXT NOT NULL,
                collection TEXT NOT NULL DEFAULT '{DEFAULT_COLLECTION}',
                content TEXT NOT NULL,
                metadata JSONB NOT NULL DEFAULT '{{}}'::jsonb,
                embedding {col_type},
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                PRIMARY KEY (collection, id)
            );
            """.strip()
        )
        stmts.append(
            f"CREATE INDEX IF NOT EXISTS idx_{self.table_name}_collection "
            f"ON {self._qualified_table} (collection);"
        )
        stmts.append(
            f"CREATE INDEX IF NOT EXISTS idx_{self.table_name}_metadata_gin "
            f"ON {self._qualified_table} USING gin (metadata);"
        )
        stmts.append(
            f"CREATE INDEX IF NOT EXISTS idx_{self.table_name}_content_fts "
            f"ON {self._qualified_table} USING gin (to_tsvector('simple', content));"
        )
        vector_ddl = self._build_vector_index_ddl(dim)
        if vector_ddl:
            stmts.append(vector_ddl)
        return stmts

    def _build_vector_index_ddl(self, dimension: Optional[int]) -> Optional[str]:
        if dimension is None or self.index_type == "none":
            return None
        _, ops = _METRIC_SPECS[self.distance_metric]
        if self.index_type == "hnsw":
            return (
                f"CREATE INDEX IF NOT EXISTS idx_{self.table_name}_embedding_hnsw "
                f"ON {self._qualified_table} USING hnsw (embedding {ops}) "
                f"WITH (m = {self.hnsw_m}, ef_construction = {self.hnsw_ef_construction});"
            )
        return (
            f"CREATE INDEX IF NOT EXISTS idx_{self.table_name}_embedding_ivfflat "
            f"ON {self._qualified_table} USING ivfflat (embedding {ops}) "
            f"WITH (lists = {self.ivfflat_lists});"
        )

    async def initialize(self, dimension: Optional[int] = None) -> None:
        """Initialize the connection pool, table, and pgvector indexes."""
        async with self._init_lock:
            dim = dimension if dimension is not None else self.dimension
            if dim is not None:
                if dim <= 0:
                    raise ValueError(f"dimension must be positive, got {dim}")
                self.dimension = dim

            if self._schema_initialized and (self._vector_index_created or self.dimension is None or self.index_type == "none"):
                return

            async with self._acquire() as (conn, driver):
                if not self._schema_initialized:
                    for stmt in self.build_schema_ddl(self.dimension):
                        await self._exec(conn, driver, stmt)
                    self._schema_initialized = True
                    self._column_typed = self.dimension is not None
                    self._vector_index_created = self.dimension is not None and self.index_type != "none"
                elif not self._vector_index_created and self.dimension is not None and self.index_type != "none":
                    if not self._column_typed:
                        await self._exec(
                            conn,
                            driver,
                            f"ALTER TABLE {self._qualified_table} "
                            f"ALTER COLUMN embedding TYPE vector({self.dimension});",
                        )
                        self._column_typed = True
                    vector_ddl = self._build_vector_index_ddl(self.dimension)
                    if vector_ddl:
                        await self._exec(conn, driver, vector_ddl)
                    self._vector_index_created = True

    async def add_documents(
        self, documents: list[Document], collection: str = DEFAULT_COLLECTION
    ) -> list[str]:
        """Upsert documents and their embeddings into PostgreSQL."""
        if not documents:
            return []

        for doc in documents:
            if doc.embedding is None:
                raise ValueError(
                    f"Document {doc.id!r} has no embedding. Generate embeddings before adding."
                )

        inferred_dim = len(documents[0].embedding)  # type: ignore[arg-type]
        expected_dim = self.dimension if self.dimension is not None else inferred_dim
        for doc in documents:
            emb_len = len(doc.embedding)  # type: ignore[arg-type]
            if emb_len != expected_dim:
                raise ValueError(
                    f"Vector dimension mismatch for document {doc.id!r}: expected {expected_dim} dims, got {emb_len}"
                )

        await self.initialize(dimension=expected_dim)

        upsert_sql = (
            f"INSERT INTO {self._qualified_table} (id, collection, content, metadata, embedding) "
            "VALUES ($1, $2, $3, $4::jsonb, $5::vector) "
            "ON CONFLICT (collection, id) DO UPDATE SET "
            "content = EXCLUDED.content, "
            "metadata = EXCLUDED.metadata, "
            "embedding = EXCLUDED.embedding, "
            "created_at = NOW()"
        )

        added_ids: list[str] = []
        async with self._acquire() as (conn, driver):
            for doc in documents:
                vec_lit = _format_vector(doc.embedding)  # type: ignore[arg-type]
                meta_json = json.dumps(doc.metadata or {})
                await self._exec(conn, driver, upsert_sql, doc.id, collection, doc.content, meta_json, vec_lit)
                added_ids.append(doc.id)

        return added_ids

    async def _apply_search_params(self, conn: Any, driver: Literal["asyncpg", "psycopg"]) -> None:
        if self.index_type == "hnsw" and self.hnsw_ef_search is not None:
            await self._exec(conn, driver, f"SET LOCAL hnsw.ef_search = {int(self.hnsw_ef_search)}")
        elif self.index_type == "ivfflat" and self.ivfflat_probes is not None:
            await self._exec(conn, driver, f"SET LOCAL ivfflat.probes = {int(self.ivfflat_probes)}")

    async def similarity_search(
        self,
        query_embedding: list[float],
        top_k: int = 4,
        collection: str = DEFAULT_COLLECTION,
        filter_metadata: Optional[dict[str, Any]] = None,
    ) -> list[SearchResult]:
        """Retrieve top_k similar documents using pgvector distance operator."""
        if top_k <= 0 or not query_embedding:
            return []
        if self.dimension is not None and len(query_embedding) != self.dimension:
            raise ValueError(
                f"Vector dimension mismatch: query vector has {len(query_embedding)} dims, "
                f"store configured for {self.dimension} dims"
            )

        await self.initialize()
        op, _ = _METRIC_SPECS[self.distance_metric]
        q_vec = _format_vector(query_embedding)

        params: list[Any] = [collection, q_vec]
        where_clauses = ["collection = $1", "embedding IS NOT NULL"]
        if filter_metadata:
            params.append(json.dumps(filter_metadata))
            where_clauses.append(f"metadata @> ${len(params)}::jsonb")
        params.append(top_k)
        limit_param = f"${len(params)}"

        sql = (
            f"SELECT id, content, metadata, embedding::text AS embedding, "
            f"(embedding {op} $2::vector) AS distance "
            f"FROM {self._qualified_table} "
            f"WHERE {' AND '.join(where_clauses)} "
            f"ORDER BY embedding {op} $2::vector ASC "
            f"LIMIT {limit_param}"
        )

        async with self._acquire() as (conn, driver):
            needs_tx = (
                (self.index_type == "hnsw" and self.hnsw_ef_search is not None)
                or (self.index_type == "ivfflat" and self.ivfflat_probes is not None)
            )
            if needs_tx and hasattr(conn, "transaction"):
                async with conn.transaction():
                    await self._apply_search_params(conn, driver)
                    rows = await self._fetch(conn, driver, sql, *params)
            else:
                if needs_tx:
                    await self._apply_search_params(conn, driver)
                rows = await self._fetch(conn, driver, sql, *params)

        return [
            SearchResult(
                document=Document(
                    id=row[0],
                    content=row[1],
                    metadata=_parse_metadata(row[2]),
                    embedding=_parse_vector(row[3]),
                ),
                score=_distance_to_score(float(row[4]), self.distance_metric),
            )
            for row in rows
        ]

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
        """Perform hybrid search combining pgvector dense retrieval with PostgreSQL FTS via RRF."""
        if top_k <= 0:
            return []

        candidate_count = top_k * 3
        dense_results = await self.similarity_search(
            query_embedding=query_embedding,
            top_k=candidate_count,
            collection=collection,
            filter_metadata=filter_metadata,
        )

        sparse_results: list[SearchResult] = []
        if query_text.strip():
            params: list[Any] = [collection, query_text]
            where_clauses = [
                "collection = $1",
                "to_tsvector('simple', content) @@ plainto_tsquery('simple', $2)",
            ]
            if filter_metadata:
                params.append(json.dumps(filter_metadata))
                where_clauses.append(f"metadata @> ${len(params)}::jsonb")
            params.append(candidate_count)
            limit_param = f"${len(params)}"

            fts_sql = (
                f"SELECT id, content, metadata, embedding::text AS embedding, "
                f"ts_rank_cd(to_tsvector('simple', content), plainto_tsquery('simple', $2)) AS rank_score "
                f"FROM {self._qualified_table} "
                f"WHERE {' AND '.join(where_clauses)} "
                f"ORDER BY rank_score DESC "
                f"LIMIT {limit_param}"
            )
            async with self._acquire() as (conn, driver):
                rows = await self._fetch(conn, driver, fts_sql, *params)

            if rows:
                max_rank = max(float(r[4]) for r in rows) or 1.0
                for row in rows:
                    norm_score = min(1.0, max(0.0, float(row[4]) / max_rank))
                    sparse_results.append(
                        SearchResult(
                            document=Document(
                                id=row[0],
                                content=row[1],
                                metadata=_parse_metadata(row[2]),
                                embedding=_parse_vector(row[3]),
                            ),
                            score=norm_score,
                        )
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
        """Delete documents by ID from a collection."""
        if not document_ids:
            return
        await self.initialize()
        sql = f"DELETE FROM {self._qualified_table} WHERE collection = $1 AND id = ANY($2::text[])"
        async with self._acquire() as (conn, driver):
            await self._exec(conn, driver, sql, collection, list(document_ids))

    async def delete_collection(self, collection: str) -> None:
        """Delete all documents in the specified collection."""
        await self.initialize()
        sql = f"DELETE FROM {self._qualified_table} WHERE collection = $1"
        async with self._acquire() as (conn, driver):
            await self._exec(conn, driver, sql, collection)

    async def list_collections(self) -> list[str]:
        """List all distinct collections present in the store."""
        await self.initialize()
        sql = f"SELECT DISTINCT collection FROM {self._qualified_table} ORDER BY collection"
        async with self._acquire() as (conn, driver):
            rows = await self._fetch(conn, driver, sql)
        return [str(row[0]) for row in rows]

    async def count(self, collection: Optional[str] = None) -> int:
        """Return document count for a collection, or across all collections if None."""
        await self.initialize()
        async with self._acquire() as (conn, driver):
            if collection is None:
                val = await self._fetchval(conn, driver, f"SELECT COUNT(*) FROM {self._qualified_table}")
            else:
                val = await self._fetchval(
                    conn,
                    driver,
                    f"SELECT COUNT(*) FROM {self._qualified_table} WHERE collection = $1",
                    collection,
                )
        return int(val or 0)

    async def get_document(
        self, document_id: str, collection: str = DEFAULT_COLLECTION
    ) -> Optional[Document]:
        """Fetch a single document by ID within a collection."""
        await self.initialize()
        sql = (
            f"SELECT id, content, metadata, embedding::text AS embedding "
            f"FROM {self._qualified_table} WHERE collection = $1 AND id = $2"
        )
        async with self._acquire() as (conn, driver):
            rows = await self._fetch(conn, driver, sql, collection, document_id)
        if not rows:
            return None
        row = rows[0]
        return Document(
            id=row[0],
            content=row[1],
            metadata=_parse_metadata(row[2]),
            embedding=_parse_vector(row[3]),
        )

    async def close(self) -> None:
        """Close the managed connection pool if owned by this store."""
        if self._pool is not None and self._owns_pool:
            await self._pool.close()
            self._pool = None

    async def aclose(self) -> None:
        """Alias for close()."""
        await self.close()

    async def __aenter__(self) -> PostgresVectorStore:
        await self.initialize()
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        await self.close()
