"""Tests for path traversal protection and filesystem isolation in InMemoryVectorStore persistence."""

import os
from pathlib import Path
import pytest

from common_framework.rag.vectorstore import InMemoryVectorStore
from common_framework.rag.models import Document


@pytest.mark.asyncio
async def test_save_and_load_within_instance_base_dir(tmp_path: Path):
    """Verify that saving and loading within a designated instance base_dir succeeds."""
    storage_dir = tmp_path / "safe_storage"
    storage_dir.mkdir()

    store = InMemoryVectorStore(base_dir=storage_dir)
    doc = Document(id="sec1", content="Secure content", embedding=[0.1, 0.2, 0.3])
    await store.add_documents([doc])

    saved_path = store.save_to_json("vector_dump.json")
    assert saved_path == (storage_dir / "vector_dump.json").resolve()
    assert saved_path.exists()

    new_store = InMemoryVectorStore(base_dir=storage_dir)
    loaded = new_store.load_from_json("vector_dump.json")
    assert loaded == 1
    assert new_store.get("sec1") is not None


@pytest.mark.asyncio
async def test_save_and_load_within_nested_subdir_of_base_dir(tmp_path: Path):
    """Verify nested paths within base_dir are created and loaded correctly."""
    base_dir = tmp_path / "base"
    store = InMemoryVectorStore(base_dir=base_dir)
    doc = Document(id="nested_doc", content="Nested", embedding=[0.5, 0.5])
    await store.add_documents([doc])

    saved_path = store.save_to_json("nested/sub/store.json")
    assert saved_path == (base_dir / "nested" / "sub" / "store.json").resolve()
    assert saved_path.exists()

    new_store = InMemoryVectorStore(base_dir=base_dir)
    loaded = new_store.load_from_json("nested/sub/store.json")
    assert loaded == 1


@pytest.mark.asyncio
async def test_save_to_json_rejects_relative_path_traversal(tmp_path: Path):
    """Verify relative traversal escaping base_dir is blocked with ValueError."""
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()

    store = InMemoryVectorStore(base_dir=sandbox)
    doc = Document(id="d1", content="Attack vector", embedding=[0.1, 0.2])
    await store.add_documents([doc])

    with pytest.raises(ValueError, match="Path traversal detected"):
        store.save_to_json("../../escaped_file.json")

    with pytest.raises(ValueError, match="Path traversal detected"):
        store.save_to_json("../escaped.json")


@pytest.mark.asyncio
async def test_load_from_json_rejects_relative_path_traversal(tmp_path: Path):
    """Verify relative traversal in load_from_json escaping base_dir is blocked."""
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()

    store = InMemoryVectorStore(base_dir=sandbox)
    with pytest.raises(ValueError, match="Path traversal detected"):
        store.load_from_json("../../etc/passwd")


@pytest.mark.asyncio
async def test_save_and_load_rejects_absolute_path_outside_base_dir(tmp_path: Path):
    """Verify absolute paths outside the designated base_dir are rejected."""
    sandbox = tmp_path / "sandbox"
    outside_dir = tmp_path / "outside"
    sandbox.mkdir()
    outside_dir.mkdir()

    store = InMemoryVectorStore(base_dir=sandbox)
    doc = Document(id="d2", content="Content", embedding=[0.4, 0.6])
    await store.add_documents([doc])

    outside_file = outside_dir / "forbidden.json"
    with pytest.raises(ValueError, match="Path traversal detected"):
        store.save_to_json(outside_file)

    with pytest.raises(ValueError, match="Path traversal detected"):
        store.load_from_json(outside_file)


@pytest.mark.asyncio
async def test_method_level_base_dir_override(tmp_path: Path):
    """Verify explicit base_dir argument in save_to_json and load_from_json enforces containment."""
    custom_dir = tmp_path / "custom_dir"
    custom_dir.mkdir()

    store = InMemoryVectorStore()  # No instance base_dir
    doc = Document(id="d3", content="Method override test", embedding=[0.2, 0.8])
    await store.add_documents([doc])

    saved = store.save_to_json("custom.json", base_dir=custom_dir)
    assert saved == (custom_dir / "custom.json").resolve()
    assert saved.exists()

    # Traversal using method-level base_dir
    with pytest.raises(ValueError, match="Path traversal detected"):
        store.save_to_json("../evil.json", base_dir=custom_dir)

    with pytest.raises(ValueError, match="Path traversal detected"):
        store.load_from_json("../evil.json", base_dir=custom_dir)

    # Valid load with method-level base_dir
    new_store = InMemoryVectorStore()
    assert new_store.load_from_json("custom.json", base_dir=custom_dir) == 1


@pytest.mark.asyncio
async def test_default_protection_blocks_escaping_cwd(tmp_path: Path):
    """Verify that even when no base_dir is set, relative paths escaping cwd are blocked."""
    store = InMemoryVectorStore()
    doc = Document(id="cwd_test", content="CWD protection", embedding=[0.1, 0.1])
    await store.add_documents([doc])

    # Traversal up beyond cwd
    deep_traversal = "../" * 15 + "evil.json"
    with pytest.raises(ValueError, match="Path traversal detected"):
        store.save_to_json(deep_traversal)

    with pytest.raises(ValueError, match="Path traversal detected"):
        store.load_from_json(deep_traversal)


@pytest.mark.asyncio
async def test_concurrent_vectorstore_operations():
    """Verify that concurrent additions, searches, and deletes run safely with asyncio.Lock."""
    import asyncio
    store = InMemoryVectorStore()

    # Pre-populate some documents
    base_docs = [
        Document(id=f"base_{i}", content=f"Base document number {i}", embedding=[float(i), 1.0 - float(i)])
        for i in range(10)
    ]
    await store.add_documents(base_docs)

    async def ingest_batch(batch_id: int):
        docs = [
            Document(id=f"doc_{batch_id}_{j}", content=f"Batch {batch_id} content {j}", embedding=[0.5, 0.5])
            for j in range(5)
        ]
        return await store.add_documents(docs)

    async def search_sim():
        return await store.similarity_search(query_embedding=[0.5, 0.5], top_k=5)

    async def search_hyb():
        return await store.hybrid_search(
            query_text="document", query_embedding=[0.5, 0.5], top_k=5
        )

    async def del_docs(batch_id: int):
        await asyncio.sleep(0.001)
        await store.delete([f"doc_{batch_id}_0"])

    # Launch mixed concurrent coroutines
    tasks = [
        ingest_batch(1),
        search_sim(),
        ingest_batch(2),
        search_hyb(),
        del_docs(1),
        search_sim(),
        ingest_batch(3),
        search_hyb(),
    ]

    results = await asyncio.gather(*tasks, return_exceptions=False)
    assert len(results) == 8

    # Ensure collection locks exist and clean count matches
    cnt = await store.count()
    assert cnt > 10

