"""Unit tests for Okapi BM25 keyword search, Reciprocal Rank Fusion, and Hybrid Search."""

import pytest
from common_framework.rag.bm25 import BM25Index, reciprocal_rank_fusion, tokenize
from common_framework.rag.engine import RAGEngine
from common_framework.rag.embeddings import MockEmbeddingProvider
from common_framework.rag.models import Document, SearchResult
from common_framework.rag.vectorstore import InMemoryVectorStore


def test_tokenize():
    """Verify tokenizer strips punctuation and normalizes casing."""
    tokens = tokenize("Hello, World! MCP & RAG-Pipeline 2026.")
    assert "hello" in tokens
    assert "world" in tokens
    assert "mcp" in tokens
    assert "rag" in tokens
    assert "pipeline" in tokens
    assert "2026" in tokens


def test_bm25_exact_keyword_matching():
    """Verify BM25 strongly ranks documents with exact technical identifiers and acronyms."""
    index = BM25Index()

    docs = [
        Document(id="doc-1", content="General discussion about security vulnerabilities and bugs."),
        Document(id="doc-2", content="Critical fix for CVE-2026-9999 remote code execution."),
        Document(id="doc-3", content="Database maintenance script for postgresql replication."),
    ]
    index.add_documents(docs)

    results = index.search("CVE-2026-9999", top_k=2)
    assert len(results) >= 1
    assert results[0].document.id == "doc-2"
    assert results[0].score > 0.0


def test_bm25_metadata_filtering_and_deletion():
    """Verify metadata filtering and document removal in BM25Index."""
    index = BM25Index()

    docs = [
        Document(id="d1", content="Python web framework", metadata={"lang": "python"}),
        Document(id="d2", content="Python data science library", metadata={"lang": "python"}),
        Document(id="d3", content="Rust web framework", metadata={"lang": "rust"}),
    ]
    index.add_documents(docs)

    # Filter by metadata
    filtered = index.search("framework", top_k=5, filter_metadata={"lang": "rust"})
    assert len(filtered) == 1
    assert filtered[0].document.id == "d3"

    # Delete d3
    index.delete(["d3"])
    filtered_after = index.search("framework", top_k=5, filter_metadata={"lang": "rust"})
    assert len(filtered_after) == 0


def test_reciprocal_rank_fusion():
    """Verify Reciprocal Rank Fusion combines rankings correctly."""
    doc_a = Document(id="doc-a", content="Doc A")
    doc_b = Document(id="doc-b", content="Doc B")
    doc_c = Document(id="doc-c", content="Doc C")

    dense_results = [
        SearchResult(document=doc_a, score=0.9),
        SearchResult(document=doc_b, score=0.8),
    ]
    sparse_results = [
        SearchResult(document=doc_b, score=1.0),
        SearchResult(document=doc_c, score=0.7),
    ]

    fused = reciprocal_rank_fusion(
        dense_results=dense_results,
        sparse_results=sparse_results,
        k=60,
        dense_weight=0.5,
        sparse_weight=0.5,
        top_k=3,
    )

    assert len(fused) == 3
    # Doc B is ranked #2 in dense and #1 in sparse, so it receives strong joint support!
    doc_ids = [r.document.id for r in fused]
    assert doc_ids[0] == "doc-b"


@pytest.mark.asyncio
async def test_in_memory_vectorstore_hybrid_search():
    """Verify InMemoryVectorStore.hybrid_search combines dense and sparse results."""
    store = InMemoryVectorStore()

    # Doc 1 has an exact rare keyword "KUBERNETES-INGRESS-CONTROLLER"
    # Doc 2 has general web text
    doc1 = Document(
        id="d1",
        content="Guide for KUBERNETES-INGRESS-CONTROLLER routing and setup.",
        embedding=[0.2, 0.8],
    )
    doc2 = Document(
        id="d2",
        content="General container management overview.",
        embedding=[0.9, 0.1],
    )

    await store.add_documents([doc1, doc2])

    # Hybrid search for the exact identifier
    results = await store.hybrid_search(
        query_text="KUBERNETES-INGRESS-CONTROLLER",
        query_embedding=[0.9, 0.1],  # Even if dense embedding points to doc2
        top_k=1,
        dense_weight=0.4,
        sparse_weight=0.6,
    )

    assert len(results) == 1
    # BM25 weight pulls doc1 to the top!
    assert results[0].document.id == "d1"


@pytest.mark.asyncio
async def test_rag_engine_hybrid_vs_dense_mode():
    """Verify RAGEngine.retrieve supports both mode='hybrid' and mode='dense'."""
    engine = RAGEngine(
        vector_store=InMemoryVectorStore(),
        embedding_provider=MockEmbeddingProvider(dimension=16),
    )

    await engine.ingest_texts([
        "Alpha release is 4 versions behind.",
        "Beta release is 2 versions behind.",
    ])

    hybrid_results = await engine.retrieve("Beta", top_k=1, mode="hybrid")
    assert len(hybrid_results) == 1
    assert "Beta" in hybrid_results[0].document.content

    dense_results = await engine.retrieve("Alpha", top_k=1, mode="dense")
    assert len(dense_results) == 1
