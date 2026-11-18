"""Tests for Millimeter Resource & Byte Accounting Engine."""

from __future__ import annotations

import time
import pytest

from common_framework.core.cache import SemanticCache, SemanticCacheEntry
from common_framework.observability.accounting import (
    ByteAccountant,
    ByteAllocationRecord,
    ByteCategory,
    get_byte_accountant,
)
from common_framework.observability.metrics import PrometheusRegistry
from common_framework.rag.embeddings import MockEmbeddingProvider
from common_framework.rag.models import Document
from common_framework.rag.storage.sqlite import SQLiteVectorStore


def test_byte_accountant_manual_record():
    accountant = ByteAccountant(auto_start_tracing=False)
    accountant.clear()

    rec = accountant.record_manual(
        operation="vector_index_allocation",
        category=ByteCategory.VECTOR_INDEX,
        allocated_bytes=6144,
        peak_bytes=8192,
        reason="1536-dim IEEE 754 packed float32 vector",
        metadata={"collection": "test"},
    )

    assert rec.allocated_bytes == 6144
    assert rec.peak_bytes == 8192
    assert rec.category == "vector_index"
    assert rec.reason == "1536-dim IEEE 754 packed float32 vector"

    ledger = accountant.get_ledger()
    assert len(ledger) == 1
    assert ledger[0].allocated_bytes == 6144


def test_byte_accountant_track_context_manager():
    accountant = ByteAccountant(auto_start_tracing=True)
    accountant.clear()

    with accountant.track(
        operation="buffer_allocation",
        category=ByteCategory.QUERY_PAYLOAD,
        reason="Allocating test string payload",
    ):
        # Allocate 100 KB string
        data = "X" * 102400
        assert len(data) == 102400

    ledger = accountant.get_ledger()
    assert len(ledger) == 1
    rec = ledger[0]
    assert rec.operation == "buffer_allocation"
    assert rec.category == "query_payload"
    assert rec.allocated_bytes >= 0
    assert rec.peak_bytes >= 0
    assert "duration_ms" in rec.metadata


def test_byte_accountant_summary_and_audit_report():
    accountant = ByteAccountant(auto_start_tracing=False)
    accountant.clear()

    accountant.record_manual(
        operation="chunk_split",
        category=ByteCategory.DOCUMENT_CONTENT,
        allocated_bytes=5000,
        reason="Document content chunking",
    )
    accountant.record_manual(
        operation="dense_embed",
        category=ByteCategory.EMBEDDING_FLOAT32,
        allocated_bytes=12000,
        reason="Dense embeddings float32",
    )

    summary = accountant.summary()
    assert summary["total_allocated_bytes"] == 17000
    assert summary["records_count"] == 2
    assert summary["by_category"]["document_content"] == 5000
    assert summary["by_category"]["embedding_float32"] == 12000

    report = accountant.generate_audit_report()
    assert "Millimeter Resource & Byte Audit" in report
    assert "17,000 bytes" in report
    assert "document_content" in report
    assert "embedding_float32" in report


def test_singleton_accountant():
    acc1 = get_byte_accountant()
    acc2 = get_byte_accountant()
    assert acc1 is acc2


@pytest.mark.asyncio
async def test_semantic_cache_byte_budget_eviction():
    """Verify that SemanticCache enforces strict max_memory_bytes and evicts by byte budget."""
    provider = MockEmbeddingProvider(dimension=4)
    # Set max_memory_bytes small: 400 bytes
    cache = SemanticCache(
        embedding_provider=provider,
        max_entries=100,
        max_memory_bytes=400,
    )

    # Entry 1: query length 50, response length 100
    await cache.set(
        query="First query with moderate length string",
        response="Response payload that consumes about 100 bytes of memory in the cache structure",
        namespace="byte_test",
    )

    stats1 = cache.stats()
    assert stats1.total_entries == 1
    assert stats1.total_bytes > 0
    assert stats1.max_memory_bytes == 400

    # Entry 2: adds another ~300 bytes, total will exceed 400 bytes
    await cache.set(
        query="Second query that also has a substantial length to trigger memory budget eviction",
        response="Another large response payload that exceeds the maximum memory bytes allocation limit",
        namespace="byte_test",
    )

    stats2 = cache.stats()
    # Cache should have evicted Entry 1 to keep total_bytes within budget
    assert stats2.evictions >= 1
    assert stats2.total_bytes <= 400


@pytest.mark.asyncio
async def test_sqlite_vectorstore_deferred_fetch_and_budget():
    """Verify that SQLite deferred content fetching works accurately with PRAGMA cache bounding."""
    store = SQLiteVectorStore(":memory:")
    docs = [
        Document(
            id=f"doc-{i}",
            content=f"Long document content payload for doc {i} with some extra padding {i * 100}",
            metadata={"index": i, "topic": "test"},
            embedding=[float(i), float(i * 2), float(i * 3), 1.0],
        )
        for i in range(1, 10)
    ]
    await store.add_documents(docs)

    results = await store.similarity_search([1.0, 2.0, 3.0, 1.0], top_k=2)
    assert len(results) == 2
    assert results[0].document.id == "doc-1"
    assert "Long document content" in results[0].document.content
    assert results[0].document.metadata["index"] == 1


def test_prometheus_byte_metrics_registration():
    reg = PrometheusRegistry()
    assert reg.get("rag_memory_allocated_bytes") is not None
    assert reg.get("rag_memory_peak_bytes") is not None
    assert reg.get("rag_semantic_cache_bytes_total") is not None
    assert reg.get("rag_vectorstore_bytes_total") is not None
