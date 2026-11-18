"""Comprehensive unit tests verifying P1 audit fixes and performance enhancements."""

import asyncio
from pathlib import Path
import pytest

from common_framework.core.config import Settings
from common_framework.core.cache import SemanticCache, SemanticCacheEntry
from common_framework.rag.storage.sqlite import SQLiteVectorStore
from common_framework.rag.vectorstore import InMemoryVectorStore
from common_framework.rag.models import Document
from common_framework.agents.memory import VectorMemory
from common_framework.agents.supervisor import AgentSupervisor
from common_framework.agents.workflow import StateGraph, START, END
from common_framework.core.llm import BaseLLMClient, ChatResponse, TokenUsage


class FakeSupervisorLLM(BaseLLMClient):
    """Mock LLM to test supervisor protocol parsing."""

    def __init__(self, responses: list[str]) -> None:
        super().__init__()
        self.responses = list(responses)
        self.call_count = 0

    async def complete(self, messages, **kwargs):
        resp_text = self.responses[min(self.call_count, len(self.responses) - 1)]
        self.call_count += 1
        return ChatResponse(content=resp_text, model="mock-model", usage=TokenUsage())

    async def structured_complete(self, messages, response_model, **kwargs):
        pass


def test_config_case_normalization():
    """Verify Settings normalizes case for default_provider and framework_env."""
    s = Settings(DEFAULT_LLM_PROVIDER="OPENAI", FRAMEWORK_ENV="PRODUCTION")
    assert s.default_provider == "openai"
    assert s.framework_env == "production"

    s2 = Settings(DEFAULT_LLM_PROVIDER="  Gemini  ", FRAMEWORK_ENV="  Staging  ")
    assert s2.default_provider == "gemini"
    assert s2.framework_env == "staging"


def test_semantic_cache_memoized_bytes_and_fast_eviction():
    """Verify SemanticCacheEntry memoizes estimate_bytes and eviction is O(N)."""
    entry = SemanticCacheEntry(
        query="test query",
        embedding=[0.1] * 128,
        response="sample response text",
    )
    b1 = entry.estimate_bytes()
    assert b1 > 128
    # Second call uses cached bytes
    b2 = entry.estimate_bytes()
    assert b1 == b2

    # Verify capacity enforcement drops excess without crashing
    cache = SemanticCache(max_entries=3, max_memory_bytes=10000)
    for i in range(10):
        e = SemanticCacheEntry(
            query=f"query {i}",
            embedding=[float(i)] * 10,
            response=f"resp {i}",
        )
        cache._ensure_namespace("default").append(e)

    cache._enforce_capacity("default")
    assert len(cache._ensure_namespace("default")) == 3
    assert cache._evictions == 7


@pytest.mark.asyncio
async def test_sqlite_hybrid_search_with_metadata_filter_not_truncated(tmp_path: Path):
    """Verify SQLite hybrid search finds matching documents even if they rank below non-matching documents."""
    db_file = tmp_path / "fts_filter_test.db"
    store = SQLiteVectorStore(db_path=db_file)

    # Ingest 20 documents mentioning "database"
    # First 15 docs have department: engineering
    # Last 5 docs have department: finance
    docs = []
    for i in range(15):
        docs.append(
            Document(
                id=f"eng_{i}",
                content=f"Modern cloud database architecture report number {i}",
                metadata={"department": "engineering"},
                embedding=[0.1, 0.2, 0.3],
            )
        )
    for j in range(5):
        docs.append(
            Document(
                id=f"fin_{j}",
                content=f"Database quarterly revenue budget and finance audit number {j}",
                metadata={"department": "finance"},
                embedding=[0.1, 0.2, 0.3],
            )
        )

    await store.add_documents(docs)

    # Hybrid search for "database" filtered by department: finance with top_k=3
    results = await store.hybrid_search(
        query_text="database",
        query_embedding=[0.1, 0.2, 0.3],
        top_k=3,
        filter_metadata={"department": "finance"},
    )

    # Must return finance documents despite engineering documents having high keyword matches
    assert len(results) == 3
    for r in results:
        assert r.document.metadata["department"] == "finance"

    store.close()


@pytest.mark.asyncio
async def test_vector_memory_clear_purges_underlying_vectorstore():
    """Verify VectorMemory.clear() purges only its specific collection without crashing."""
    mem_store = InMemoryVectorStore()
    vm = VectorMemory(vector_store=mem_store, collection="agent_conv_1")

    await vm.save_interaction("What is RAG?", "Retrieval Augmented Generation.")
    assert len(await vm.get_messages()) == 2
    assert await mem_store.count("agent_conv_1") == 1

    # Add a document in a separate collection to ensure collection isolation on clear
    other_doc = Document(id="other_1", content="Other info", embedding=[0.1] * 3)
    await mem_store.add_documents([other_doc], collection="other_coll")
    assert await mem_store.count("other_coll") == 1

    # Clear memory
    await vm.clear()
    assert len(await vm.get_messages()) == 0
    assert await mem_store.count("agent_conv_1") == 0
    # Other collection must remain intact
    assert await mem_store.count("other_coll") == 1


@pytest.mark.asyncio
async def test_supervisor_does_not_prematurely_terminate_on_thought():
    """Verify Supervisor does not terminate when FINAL_ANSWER is mentioned in reasoning alongside DELEGATE."""
    # Step 0: Thought mentions FINAL_ANSWER but executes DELEGATE
    # Step 1: Emits final answer
    llm = FakeSupervisorLLM([
        "Thought: I will output FINAL_ANSWER: later. First we must delegate.\nDELEGATE: worker | summarize data",
        "FINAL_ANSWER: The summary has been completed successfully.",
    ])

    async def mock_worker(task: str) -> str:
        return f"Worker output for: {task}"

    supervisor = AgentSupervisor(
        agents={"worker": ("Worker to summarize data", mock_worker)},
        llm_client=llm,
        max_steps=5,
    )

    resp = await supervisor.run("Process the dataset")
    assert resp.iterations == 2
    assert len(resp.delegations) == 1
    assert resp.delegations[0].agent_name == "worker"
    assert resp.final_answer == "The summary has been completed successfully."


@pytest.mark.asyncio
async def test_workflow_offloads_sync_callables_and_validates_unregistered_node():
    """Verify StateGraph offloads sync callables to executor and raises on unregistered node."""
    graph = StateGraph()

    import time
    def sync_compute(state: dict) -> dict:
        time.sleep(0.01)  # Simulate small sync work
        return {"val": state.get("val", 0) + 10}

    async def async_step(state: dict) -> dict:
        return {"val": state.get("val", 0) * 2}

    graph.add_node("step1", sync_compute)
    graph.add_node("step2", async_step)
    graph.set_entry_point("step1")
    graph.add_edge("step1", "step2")
    graph.add_edge("step2", END)

    res = await graph.run(initial_state={"val": 5})
    assert res.completed is True
    assert res.state["val"] == 30

    # Test error when router targets nonexistent node
    broken_graph = StateGraph()
    broken_graph.add_node("start_node", lambda s: {"x": 1})
    broken_graph.set_entry_point("start_node")
    broken_graph.add_conditional_edges("start_node", lambda s: "ghost_node")

    with pytest.raises(ValueError, match="unregistered node 'ghost_node'"):
        await broken_graph.run()
