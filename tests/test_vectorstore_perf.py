"""Tests and equivalence benchmarks for pre-normalized and NumPy-accelerated vector search."""

from __future__ import annotations

import math
import pytest

from common_framework.rag.models import Document, SearchResult
from common_framework.rag.vectorstore import InMemoryVectorStore, normalize_vector, _HAS_NUMPY


def test_normalize_vector():
    """Verify normalize_vector returns unit norm and original magnitude."""
    v = [3.0, 4.0]
    unit, norm = normalize_vector(v)
    assert math.isclose(norm, 5.0, rel_tol=1e-6)
    assert math.isclose(unit[0], 0.6, rel_tol=1e-6)
    assert math.isclose(unit[1], 0.8, rel_tol=1e-6)
    assert math.isclose(math.sqrt(unit[0]**2 + unit[1]**2), 1.0, rel_tol=1e-6)

    # Zero vector returns zero norm and unchanged vector
    zero_unit, zero_norm = normalize_vector([0.0, 0.0, 0.0])
    assert zero_norm == 0.0
    assert zero_unit == [0.0, 0.0, 0.0]


@pytest.mark.asyncio
async def test_numpy_and_pure_python_search_equivalence():
    """Verify that NumPy BLAS GEMV path and Pure Python pre-normalized path produce identical results."""
    store_py = InMemoryVectorStore(use_numpy=False)
    store_np = InMemoryVectorStore(use_numpy=True)

    docs = [
        Document(id="doc-1", content="Doc 1", embedding=[1.0, 0.0, 0.0], metadata={"category": "A"}),
        Document(id="doc-2", content="Doc 2", embedding=[0.7071, 0.7071, 0.0], metadata={"category": "A"}),
        Document(id="doc-3", content="Doc 3", embedding=[0.0, 1.0, 0.0], metadata={"category": "B"}),
        Document(id="doc-4", content="Doc 4", embedding=[-1.0, 0.0, 0.0], metadata={"category": "B"}),
    ]

    await store_py.add_documents(docs)
    await store_np.add_documents(docs)

    query_embedding = [0.9, 0.1, 0.0]

    # Search all
    results_py = await store_py.similarity_search(query_embedding, top_k=4)
    results_np = await store_np.similarity_search(query_embedding, top_k=4)

    assert len(results_py) == len(results_np) == 4
    for r_py, r_np in zip(results_py, results_np):
        assert r_py.document.id == r_np.document.id
        assert math.isclose(r_py.score, r_np.score, abs_tol=1e-5)

    # Search with metadata filter
    filtered_py = await store_py.similarity_search(query_embedding, top_k=4, filter_metadata={"category": "B"})
    filtered_np = await store_np.similarity_search(query_embedding, top_k=4, filter_metadata={"category": "B"})

    assert len(filtered_py) == len(filtered_np) == 2
    for r_py, r_np in zip(filtered_py, filtered_np):
        assert r_py.document.id == r_np.document.id
        assert math.isclose(r_py.score, r_np.score, abs_tol=1e-5)


@pytest.mark.asyncio
async def test_vectorstore_cache_invalidation_lifecycle():
    """Verify matrix cache correctly invalidates on document addition, deletion, and clear."""
    store = InMemoryVectorStore(use_numpy=True)

    d1 = Document(id="d1", content="first", embedding=[1.0, 0.0])
    d2 = Document(id="d2", content="second", embedding=[0.0, 1.0])
    await store.add_documents([d1, d2])

    # Initial search populates matrix cache
    res1 = await store.similarity_search([1.0, 0.0], top_k=2)
    assert len(res1) == 2
    assert store._matrix_cache["default"] is not None

    # Deleting document invalidates matrix cache
    await store.delete(["d2"])
    assert store._matrix_cache["default"] is None

    # Subsequent search operates only on remaining document
    res2 = await store.similarity_search([0.0, 1.0], top_k=2)
    assert len(res2) == 1
    assert res2[0].document.id == "d1"
    assert store._matrix_cache["default"] is not None

    # Adding document invalidates cache again
    d3 = Document(id="d3", content="third", embedding=[0.0, 1.0])
    await store.add_documents([d3])
    assert store._matrix_cache["default"] is None

    res3 = await store.similarity_search([0.0, 1.0], top_k=2)
    assert len(res3) == 2
    assert res3[0].document.id == "d3"

    # Clearing wipes normalized vectors and cache
    store.clear()
    assert len(store._normalized_vectors) == 0
    assert len(store._matrix_cache) == 0


@pytest.mark.asyncio
async def test_vectorstore_empty_or_zero_query():
    """Verify graceful handling of empty or zero-norm queries."""
    store = InMemoryVectorStore()
    d1 = Document(id="d1", content="first", embedding=[1.0, 2.0])
    await store.add_documents([d1])

    assert await store.similarity_search([], top_k=2) == []
    assert await store.similarity_search([0.0, 0.0], top_k=2) == []


@pytest.mark.asyncio
async def test_vectorstore_large_collection_top_k():
    """Verify argpartition top-k selection on larger collection."""
    store = InMemoryVectorStore(use_numpy=True)
    docs = [
        Document(id=f"doc-{i}", content=f"Doc {i}", embedding=[float(i), 1.0])
        for i in range(50)
    ]
    await store.add_documents(docs)

    # Query closest to doc-49
    res = await store.similarity_search([49.0, 1.0], top_k=5)
    assert len(res) == 5
    assert res[0].document.id == "doc-49"
