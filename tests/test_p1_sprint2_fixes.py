"""Unit tests covering fixes for P1 audit bugs BUG-22 to BUG-27."""

import asyncio
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from common_framework.agents.hitl import ApprovalDecision, ApprovalRequest, ApprovalResponse, BaseApprovalHook
from common_framework.agents.react import ReActAgent
from common_framework.core.config import Settings
from common_framework.core.llm import (
    BaseLLMClient,
    ChatMessage,
    ChatResponse,
    LLMFactory,
    OpenAICompatibleClient,
    TokenUsage,
)
from common_framework.rag.engine import RAGEngine
from common_framework.rag.embeddings import MockEmbeddingProvider
from common_framework.rag.models import Document
from common_framework.rag.vectorstore import InMemoryVectorStore
from common_framework.tools.base import tool
from common_framework.tools.registry import ToolRegistry


class DummyStrictClient(BaseLLMClient):
    """Client with strict constructor parameters (no **kwargs)."""

    def __init__(self, settings=None, client=None):
        super().__init__(settings=settings)

    async def complete(self, messages, **kwargs):
        return ChatResponse(content="ok", model="dummy", usage=TokenUsage(prompt_tokens=1, completion_tokens=1, total_tokens=2))

    async def complete_stream(self, messages, **kwargs):
        raise NotImplementedError

    async def structured_complete(self, messages, response_model, **kwargs):
        raise NotImplementedError


# BUG-22: LLMFactory kwargs safety
def test_llm_factory_safe_instantiate_filters_unsupported_kwargs():
    """Verify LLMFactory._safe_instantiate filters out kwargs not accepted by strict constructors."""
    client = LLMFactory._safe_instantiate(
        DummyStrictClient,
        settings=Settings(),
        client=None,
        unsupported_param="some_value",
        custom_header={"X-Test": "1"},
    )
    assert isinstance(client, DummyStrictClient)
    assert getattr(client, "unsupported_param", None) == "some_value"


def test_llm_factory_create_with_extended_kwargs():
    """Verify LLMFactory.create forwards known parameters and handles extra kwargs."""
    client = LLMFactory.create(
        "openai",
        api_key="sk-test",
        base_url="https://api.openai.com/v1",
        arbitrary_flag=True,
    )
    assert isinstance(client, OpenAICompatibleClient)
    assert client.api_key == "sk-test"


# BUG-23: Filter index cache LRU containment
@pytest.mark.asyncio
async def test_filter_indices_cache_bounded_lru():
    """Verify that filter indices cache is capped and evicts oldest entries without unbounded growth."""
    store = InMemoryVectorStore()
    docs = [
        Document(id=f"d_{i}", content=f"Document {i}", embedding=[0.1 * i, 0.2], metadata={"category": f"cat_{i % 10}"})
        for i in range(20)
    ]
    await store.add_documents(docs)

    # Issue 600 unique filter queries on the same collection
    for i in range(600):
        await store.similarity_search(
            query_embedding=[0.1, 0.2],
            top_k=2,
            filter_metadata={"unique_query_id": i, "category": "cat_0"},
        )

    coll_cache = store._filter_indices_cache["default"]
    assert len(coll_cache) <= 512, f"Expected cache <= 512, got {len(coll_cache)}"


# BUG-24: Nested unhashable metadata filtering
@pytest.mark.asyncio
async def test_vectorstore_nested_unhashable_metadata_filtering():
    """Verify that documents with nested dicts, lists, and sets can be filtered without TypeError."""
    store = InMemoryVectorStore()
    doc1 = Document(
        id="nested_1",
        content="Nested metadata doc 1",
        embedding=[0.5, 0.5],
        metadata={
            "tags": ["python", "ai", "rag"],
            "author": {"name": "Alice", "role": "engineer"},
            "versions": {1, 2},
        },
    )
    doc2 = Document(
        id="nested_2",
        content="Nested metadata doc 2",
        embedding=[0.5, 0.5],
        metadata={
            "tags": ["java", "cloud"],
            "author": {"name": "Bob", "role": "manager"},
            "versions": {3},
        },
    )
    await store.add_documents([doc1, doc2])

    # Search with nested dict filter
    results = await store.similarity_search(
        query_embedding=[0.5, 0.5],
        top_k=5,
        filter_metadata={"author": {"name": "Alice", "role": "engineer"}},
    )
    assert len(results) == 1
    assert results[0].document.id == "nested_1"

    # Search with list filter
    results_list = await store.similarity_search(
        query_embedding=[0.5, 0.5],
        top_k=5,
        filter_metadata={"tags": ["java", "cloud"]},
    )
    assert len(results_list) == 1
    assert results_list[0].document.id == "nested_2"


# BUG-25: RAGEngine _run_sync and multi-loop isolation
def test_rag_engine_run_sync_in_new_thread():
    """Verify _run_sync executes synchronously without cross-loop deadlock."""
    engine = RAGEngine(
        vector_store=InMemoryVectorStore(),
        embedding_provider=MockEmbeddingProvider(dimension=4),
        llm_client=DummyStrictClient(),
    )
    doc_ids = engine.ingest_texts_sync(["Quick brown fox", "Lazy dog"])
    assert len(doc_ids) == 2


@pytest.mark.asyncio
async def test_collection_locks_partitioned_by_loop():
    """Verify vector store locks are partitioned per event loop preventing cross-loop deadlocks."""
    store = InMemoryVectorStore()
    loop = asyncio.get_running_loop()
    lock = store._get_collection_lock("default")
    assert (id(loop), "default") in store._locks
    assert store._get_collection_lock("default") is lock


# BUG-26: ReActAgent HITL fail-close
class FailingApprovalHook(BaseApprovalHook):
    async def review(self, request: ApprovalRequest) -> ApprovalResponse:
        raise RuntimeError("Approval service connection timed out")


class MalformedApprovalHook(BaseApprovalHook):
    async def review(self, request: ApprovalRequest) -> ApprovalResponse:
        return None  # type: ignore


@pytest.mark.asyncio
async def test_react_agent_hitl_fail_close_on_exception():
    """Verify ReActAgent blocks execution when approval hook raises an exception."""
    registry = ToolRegistry()
    mock_fn = MagicMock(return_value="executed sensitive operation")

    @tool(name="delete_database", description="Dangerous deletion tool", requires_approval=True)
    def delete_db() -> str:
        return mock_fn()

    registry.register(delete_db)

    llm = MagicMock()
    llm.complete = AsyncMock(side_effect=[
        ChatResponse(content="Action: delete_database\nAction Input: {}\nThought: Need to delete DB", model="test-m"),
        ChatResponse(content="Final Answer: Operation was aborted.", model="test-m"),
    ])

    agent = ReActAgent(
        llm_client=llm,
        tools=registry,
        approval_hook=FailingApprovalHook(),
        max_iterations=3,
    )
    result = await agent.run("Please delete the database")
    assert not mock_fn.called
    assert any("Approval hook failed" in s.observation for s in result.steps)


@pytest.mark.asyncio
async def test_react_agent_hitl_fail_close_on_malformed_review():
    """Verify ReActAgent blocks execution when approval hook returns None or invalid object."""
    registry = ToolRegistry()
    mock_fn = MagicMock(return_value="executed sensitive operation")

    @tool(name="transfer_funds", description="Transfer funds tool", requires_approval=True)
    def transfer() -> str:
        return mock_fn()

    registry.register(transfer)

    llm = MagicMock()
    llm.complete = AsyncMock(side_effect=[
        ChatResponse(content="Action: transfer_funds\nAction Input: {}\nThought: Transferring", model="test-m"),
        ChatResponse(content="Final Answer: Aborted.", model="test-m"),
    ])

    agent = ReActAgent(
        llm_client=llm,
        tools=registry,
        approval_hook=MalformedApprovalHook(),
        max_iterations=3,
    )
    result = await agent.run("Transfer $1,000,000")
    assert not mock_fn.called
    assert any("invalid review" in s.observation.lower() or "blocked" in s.observation.lower() for s in result.steps)


# BUG-27: VectorStore absolute path traversal protection
@pytest.mark.asyncio
async def test_vectorstore_rejects_external_absolute_path_without_base_dir():
    """Verify that saving or loading to an unauthorized absolute path is blocked when base_dir=None."""
    store = InMemoryVectorStore()
    doc = Document(id="d_sec", content="sensitive content", embedding=[0.1, 0.2])
    await store.add_documents([doc])

    # System sensitive absolute paths
    system_paths = [
        "C:\\Windows\\System32\\malicious_store.json",
        "C:\\Program Files\\malicious_store.json",
        "/etc/passwd_vector_dump.json",
        "/root/.ssh/authorized_keys.json",
    ]

    for p in system_paths:
        with pytest.raises(ValueError, match="Path traversal detected"):
            store.save_to_json(p)

        with pytest.raises(ValueError, match="Path traversal detected"):
            store.load_from_json(p)
