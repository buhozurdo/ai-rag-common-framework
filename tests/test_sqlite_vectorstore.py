"""Comprehensive unit tests for Embedded SQLiteVectorStore and FTS5 BM25 hybrid search."""

from __future__ import annotations

import asyncio
from pathlib import Path
import pytest

import httpx
from common_framework.rag.embeddings import MockEmbeddingProvider
from common_framework.rag.engine import RAGEngine
from common_framework.rag.models import Document, SearchResult
from common_framework.rag.storage.sqlite import SQLiteVectorStore
from common_framework.rag.sqlite_store import SQLiteVectorStore as SQLiteVectorStoreAlias
from common_framework import SQLiteVectorStore as SQLiteVectorStoreRoot
from common_framework.core.llm import OpenAICompatibleClient


@pytest.mark.asyncio
async def test_sqlite_vectorstore_exports():
    """Ensure SQLiteVectorStore is properly exported at all expected entrypoints."""
    assert SQLiteVectorStore is SQLiteVectorStoreAlias
    assert SQLiteVectorStore is SQLiteVectorStoreRoot


@pytest.mark.asyncio
async def test_sqlite_vectorstore_in_memory_crud():
    """Test basic in-memory document addition, retrieval, count, and deletion."""
    store = SQLiteVectorStore(db_path=":memory:")

    doc1 = Document(
        id="doc-1",
        content="Artificial intelligence and machine learning frameworks.",
        metadata={"category": "ai", "year": 2026},
        embedding=[1.0, 0.0, 0.0],
    )
    doc2 = Document(
        id="doc-2",
        content="Relational databases and SQL optimization patterns.",
        metadata={"category": "database", "year": 2025},
        embedding=[0.0, 1.0, 0.0],
    )

    added = await store.add_documents([doc1, doc2], collection="tech")
    assert added == ["doc-1", "doc-2"]

    assert await store.count("tech") == 2
    assert await store.count() == 2
    assert await store.list_collections() == ["tech"]

    retrieved = store.get("doc-1", collection="tech")
    assert retrieved is not None
    assert retrieved.id == "doc-1"
    assert retrieved.content == doc1.content
    assert retrieved.metadata == doc1.metadata
    assert retrieved.embedding == [1.0, 0.0, 0.0]

    # Delete single doc
    await store.delete(["doc-1"], collection="tech")
    assert await store.count("tech") == 1
    assert store.get("doc-1", collection="tech") is None

    # Clear
    store.clear()
    assert await store.count() == 0
    store.close()


@pytest.mark.asyncio
async def test_sqlite_vectorstore_missing_embedding_raises():
    """Verify that adding documents without embeddings raises ValueError."""
    store = SQLiteVectorStore(db_path=":memory:")
    doc = Document(id="bad-doc", content="No embedding present", embedding=None)

    with pytest.raises(ValueError, match="has no embedding"):
        await store.add_documents([doc])
    store.close()


@pytest.mark.asyncio
async def test_sqlite_vectorstore_multi_collection_isolation():
    """Verify multi-collection isolation and collection dropping."""
    store = SQLiteVectorStore(db_path=":memory:")

    doc_a = Document(id="doc-1", content="Sales pipeline", embedding=[1.0, 0.0])
    doc_b = Document(id="doc-1", content="Engineering pipeline", embedding=[0.0, 1.0])

    await store.add_documents([doc_a], collection="sales")
    await store.add_documents([doc_b], collection="engineering")

    collections = await store.list_collections()
    assert sorted(collections) == ["engineering", "sales"]
    assert await store.count("sales") == 1
    assert await store.count("engineering") == 1
    assert await store.count() == 2

    # Dropping one collection leaves the other untouched
    await store.delete_collection("sales")
    assert await store.list_collections() == ["engineering"]
    assert await store.count("sales") == 0
    assert await store.count("engineering") == 1
    store.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("use_numpy", [True, False])
async def test_sqlite_vectorstore_similarity_search(use_numpy: bool):
    """Test dense cosine similarity search with and without numpy acceleration."""
    store = SQLiteVectorStore(db_path=":memory:", use_numpy=use_numpy)

    docs = [
        Document(id="vec-x", content="Vector along X axis", embedding=[1.0, 0.0, 0.0]),
        Document(id="vec-y", content="Vector along Y axis", embedding=[0.0, 1.0, 0.0]),
        Document(id="vec-xy", content="Vector along XY diagonal", embedding=[0.707, 0.707, 0.0]),
    ]
    await store.add_documents(docs, collection="vectors")

    # Search closest to X axis
    results = await store.similarity_search([1.0, 0.0, 0.0], top_k=2, collection="vectors")
    assert len(results) == 2
    assert results[0].document.id == "vec-x"
    assert results[0].score > 0.99
    assert results[1].document.id == "vec-xy"

    # Dimension mismatch check
    with pytest.raises(ValueError, match="Vector dimension mismatch"):
        await store.similarity_search([1.0, 0.0], top_k=2, collection="vectors")

    # Empty query embedding
    empty_res = await store.similarity_search([], top_k=2, collection="vectors")
    assert empty_res == []

    store.close()


@pytest.mark.asyncio
async def test_sqlite_vectorstore_metadata_filtering():
    """Verify metadata filtering during dense search."""
    store = SQLiteVectorStore(db_path=":memory:")

    docs = [
        Document(
            id="doc-python-prod",
            content="Python in production",
            metadata={"lang": "python", "env": "prod"},
            embedding=[1.0, 0.0],
        ),
        Document(
            id="doc-python-dev",
            content="Python in development",
            metadata={"lang": "python", "env": "dev"},
            embedding=[1.0, 0.0],
        ),
        Document(
            id="doc-rust-prod",
            content="Rust in production",
            metadata={"lang": "rust", "env": "prod"},
            embedding=[1.0, 0.0],
        ),
    ]
    await store.add_documents(docs)

    results = await store.similarity_search(
        [1.0, 0.0],
        top_k=5,
        filter_metadata={"lang": "python", "env": "prod"},
    )
    assert len(results) == 1
    assert results[0].document.id == "doc-python-prod"
    store.close()


@pytest.mark.asyncio
async def test_sqlite_vectorstore_fts5_hybrid_search():
    """Verify FTS5 BM25 keyword matching and RRF hybrid fusion."""
    store = SQLiteVectorStore(db_path=":memory:")

    docs = [
        Document(
            id="sqlite-arch",
            content="SQLite architecture employs write-ahead logging (WAL) for concurrency.",
            embedding=[0.9, 0.1, 0.0],
        ),
        Document(
            id="postgres-arch",
            content="PostgreSQL utilizes multi-version concurrency control (MVCC).",
            embedding=[0.8, 0.2, 0.0],
        ),
        Document(
            id="gardening-tips",
            content="Tomatoes require consistent watering and fertile soil.",
            embedding=[0.0, 0.0, 1.0],
        ),
    ]
    await store.add_documents(docs)

    # Hybrid query looking specifically for write-ahead logging
    results = await store.hybrid_search(
        query_text="write-ahead logging WAL",
        query_embedding=[0.9, 0.1, 0.0],
        top_k=2,
    )
    assert len(results) == 2
    assert results[0].document.id == "sqlite-arch"

    # Query with special characters should not crash FTS5
    special_res = await store.hybrid_search(
        query_text="WAL: (concurrency*) 'quotes'!",
        query_embedding=[0.9, 0.1, 0.0],
        top_k=2,
    )
    assert len(special_res) > 0
    assert special_res[0].document.id == "sqlite-arch"

    store.close()


@pytest.mark.asyncio
async def test_sqlite_vectorstore_disk_persistence(tmp_path: Path):
    """Verify file-based persistence across store instances and WAL mode."""
    db_file = tmp_path / "persistent_vectors.db"

    # First instance: create and populate
    store1 = SQLiteVectorStore(db_path=db_file)
    doc = Document(
        id="persisted-doc",
        content="Persistent knowledge base content.",
        metadata={"status": "saved"},
        embedding=[0.5, 0.5, 0.5],
    )
    await store1.add_documents([doc], collection="permanent")
    store1.close()

    assert db_file.exists()

    # Second instance: load and verify content
    store2 = SQLiteVectorStore(db_path=db_file)
    assert await store2.count("permanent") == 1
    loaded = store2.get("persisted-doc", collection="permanent")
    assert loaded is not None
    assert loaded.content == "Persistent knowledge base content."
    assert loaded.embedding == [0.5, 0.5, 0.5]

    search_res = await store2.similarity_search([0.5, 0.5, 0.5], top_k=1, collection="permanent")
    assert len(search_res) == 1
    assert search_res[0].document.id == "persisted-doc"
    store2.close()


def test_sqlite_vectorstore_path_traversal_protection(tmp_path: Path):
    """Ensure path traversal attacks outside base_dir are rejected."""
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()

    # Valid path inside sandbox
    store = SQLiteVectorStore(db_path="valid.db", base_dir=sandbox)
    store.close()

    # Traversal attempt escaping sandbox
    with pytest.raises(ValueError, match="Path traversal detected"):
        SQLiteVectorStore(db_path="../../forbidden.db", base_dir=sandbox)


@pytest.mark.asyncio
async def test_sqlite_vectorstore_rag_engine_integration():
    """Verify end-to-end integration with RAGEngine using SQLiteVectorStore."""
    def mock_llm_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "model": "gpt-4o-mini",
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": "Antigravity orchestrates agentic coding workflows seamlessly with RAG.",
                        }
                    }
                ],
                "usage": {"prompt_tokens": 50, "completion_tokens": 15, "total_tokens": 65},
            },
        )

    transport = httpx.MockTransport(mock_llm_handler)
    async with httpx.AsyncClient(transport=transport) as http_client:
        llm = OpenAICompatibleClient(api_key="sk-test", client=http_client)
        store = SQLiteVectorStore(db_path=":memory:")
        embedder = MockEmbeddingProvider(dimension=4)

        engine = RAGEngine(
            embedding_provider=embedder,
            vector_store=store,
            llm_client=llm,
        )

        # Ingest documents
        doc = Document(
            id="eng-1",
            content="Antigravity orchestrates agentic coding workflows seamlessly.",
            metadata={"topic": "antigravity"},
        )
        await engine.ingest_documents([doc])
        assert await store.count() == 1

        # Retrieve
        retrieved = await engine.retrieve("agentic coding", top_k=1)
        assert len(retrieved) == 1
        assert retrieved[0].document.metadata.get("parent_id", retrieved[0].document.id) == "eng-1"

        # Query
        response = await engine.query("How does Antigravity work?")
        assert "Antigravity orchestrates" in response.answer

        # Sync methods verification
        sync_retrieved = engine.retrieve_sync("agentic coding", top_k=1)
        assert len(sync_retrieved) == 1

        sync_resp = engine.query_sync("Tell me about Antigravity")
        assert len(sync_resp.answer) > 0

        store.close()


def test_sqlite_vectorstore_context_manager():
    """Verify context manager cleanup."""
    with SQLiteVectorStore(db_path=":memory:") as store:
        assert store.count is not None
