"""Unit tests for multi-tenant collection and namespace isolation in vector stores and RAG engine."""

import pytest
from common_framework.rag.engine import RAGEngine
from common_framework.rag.embeddings import MockEmbeddingProvider
from common_framework.rag.models import Document
from common_framework.rag.vectorstore import InMemoryVectorStore


@pytest.mark.asyncio
async def test_collection_isolation():
    """Verify documents stored in one collection cannot be accessed from another."""
    store = InMemoryVectorStore()

    doc_hr = Document(id="hr-1", content="HR Salary Policy 2026", embedding=[1.0, 0.0])
    doc_eng = Document(id="eng-1", content="Engineering Architecture Guide", embedding=[0.9, 0.1])

    await store.add_documents([doc_hr], collection="hr_department")
    await store.add_documents([doc_eng], collection="eng_department")

    # HR collection count
    assert await store.count("hr_department") == 1
    assert await store.count("eng_department") == 1
    assert await store.count() == 2  # Total across all collections

    # Search inside engineering: should NEVER return HR doc
    results_eng = await store.similarity_search([1.0, 0.0], top_k=5, collection="eng_department")
    assert len(results_eng) == 1
    assert results_eng[0].document.id == "eng-1"

    # Search inside HR
    results_hr = await store.similarity_search([1.0, 0.0], top_k=5, collection="hr_department")
    assert len(results_hr) == 1
    assert results_hr[0].document.id == "hr-1"

    # Collections listing
    collections = await store.list_collections()
    assert "hr_department" in collections
    assert "eng_department" in collections


@pytest.mark.asyncio
async def test_collection_hybrid_search_isolation():
    """Verify hybrid search operates strictly within the target collection."""
    store = InMemoryVectorStore()

    doc_client_a = Document(id="ca", content="Confidential Client Alpha Financials", embedding=[0.5, 0.5])
    doc_client_b = Document(id="cb", content="Public Client Beta Marketing", embedding=[0.5, 0.5])

    await store.add_documents([doc_client_a], collection="tenant_alpha")
    await store.add_documents([doc_client_b], collection="tenant_beta")

    # Hybrid search in tenant_beta for "Financials" (which exists only in tenant_alpha)
    results = await store.hybrid_search("Financials", query_embedding=[0.5, 0.5], collection="tenant_beta")
    doc_ids = [r.document.id for r in results]
    assert "ca" not in doc_ids


@pytest.mark.asyncio
async def test_delete_collection_and_documents():
    """Verify deleting specific documents from a collection or dropping the collection entirely."""
    store = InMemoryVectorStore()

    doc1 = Document(id="d1", content="Doc 1", embedding=[1.0, 0.0])
    doc2 = Document(id="d2", content="Doc 2", embedding=[0.0, 1.0])
    await store.add_documents([doc1, doc2], collection="staging")

    assert await store.count("staging") == 2
    await store.delete(["d1"], collection="staging")
    assert await store.count("staging") == 1
    assert store.get("d1", collection="staging") is None

    # Drop entire collection
    await store.delete_collection("staging")
    assert await store.count("staging") == 0
    assert "staging" not in (await store.list_collections())


@pytest.mark.asyncio
async def test_multi_collection_json_persistence(tmp_path):
    """Verify saving and restoring multiple collections to JSON."""
    store = InMemoryVectorStore()

    doc1 = Document(id="d1", content="Collection 1 Doc", embedding=[0.1, 0.2])
    doc2 = Document(id="d2", content="Collection 2 Doc", embedding=[0.3, 0.4])

    await store.add_documents([doc1], collection="team_a")
    await store.add_documents([doc2], collection="team_b")

    filepath = tmp_path / "multi_collections.json"
    store.save_to_json(filepath)

    # Load into fresh store
    new_store = InMemoryVectorStore()
    total_loaded = new_store.load_from_json(filepath)

    assert total_loaded == 2
    assert await new_store.count("team_a") == 1
    assert await new_store.count("team_b") == 1
    assert new_store.get("d1", collection="team_a") is not None
    assert new_store.get("d2", collection="team_b") is not None


@pytest.mark.asyncio
async def test_rag_engine_collection_routing():
    """Verify RAGEngine routes queries to the specified collection."""
    engine = RAGEngine(
        vector_store=InMemoryVectorStore(),
        embedding_provider=MockEmbeddingProvider(dimension=16),
    )

    await engine.ingest_texts(["User 1 secret notes: Blueberry"], collection="user_1")
    await engine.ingest_texts(["User 2 secret notes: Raspberry"], collection="user_2")

    # Retrieve from user_1
    results_user1 = await engine.retrieve("secret notes", collection="user_1")
    assert len(results_user1) == 1
    assert "Blueberry" in results_user1[0].document.content

    # Retrieve from user_2
    results_user2 = await engine.retrieve("secret notes", collection="user_2")
    assert len(results_user2) == 1
    assert "Raspberry" in results_user2[0].document.content
