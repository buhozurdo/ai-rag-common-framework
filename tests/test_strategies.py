"""Unit tests for Multi-Query and HyDE retrieval strategies (FEAT-17)."""

import pytest

from common_framework import (
    BaseLLMClient,
    ChatMessage,
    ChatResponse,
    Document,
    InMemoryVectorStore,
    LocalCrossEncoderReranker,
    MockEmbeddingProvider,
    RAGEngine,
    SearchResult,
    TokenUsage,
)
from common_framework.rag.strategies import (
    HyDEStrategy,
    MultiQueryStrategy,
    multi_list_rrf,
)


class ScriptedLLMClient(BaseLLMClient):
    """Mock LLM returning scripted responses."""

    def __init__(self, responses: list[str]) -> None:
        self.responses = list(responses)
        self.call_count = 0

    async def complete(self, messages, **kwargs):
        if self.call_count < len(self.responses):
            content = self.responses[self.call_count]
        else:
            content = self.responses[-1] if self.responses else "Default answer"
        self.call_count += 1
        return ChatResponse(content=content, model="scripted-mock", usage=TokenUsage())

    async def complete_stream(self, messages, **kwargs):
        resp = await self.complete(messages, **kwargs)
        yield resp

    async def structured_complete(self, messages, response_model, **kwargs):
        raise NotImplementedError


def test_multi_list_rrf_scoring_and_deduplication():
    d1 = Document(id="d1", content="Python memory management")
    d2 = Document(id="d2", content="Python garbage collector")
    d3 = Document(id="d3", content="CPython GIL architecture")

    list1 = [SearchResult(document=d1, score=0.9), SearchResult(document=d2, score=0.8)]
    list2 = [SearchResult(document=d2, score=0.95), SearchResult(document=d3, score=0.7)]
    list3 = [SearchResult(document=d1, score=0.85), SearchResult(document=d3, score=0.6)]

    fused = multi_list_rrf([list1, list2, list3], k=60, top_k=3)

    assert len(fused) == 3
    # d1 appears in list1 (#1) and list3 (#1)
    # d2 appears in list1 (#2) and list2 (#1)
    # d3 appears in list2 (#2) and list3 (#2)
    doc_ids = [r.document.id for r in fused]
    assert doc_ids[0] in ("d1", "d2")
    assert all(0.0 <= r.score <= 1.0 for r in fused)


def test_multi_list_rrf_weights():
    d1 = Document(id="d1", content="Chunk A")
    d2 = Document(id="d2", content="Chunk B")

    list1 = [SearchResult(document=d1, score=0.9)]
    list2 = [SearchResult(document=d2, score=0.9)]

    # Heavy weight on list2
    fused = multi_list_rrf([list1, list2], weights=[0.2, 0.8], k=60, top_k=2)

    assert len(fused) == 2
    assert fused[0].document.id == "d2"
    assert fused[1].document.id == "d1"


def test_multi_list_rrf_edge_cases():
    assert multi_list_rrf([]) == []
    assert multi_list_rrf([[], []]) == []

    d = Document(id="d1", content="Solo")
    single = [SearchResult(document=d, score=0.5)]
    fused = multi_list_rrf([single], k=60)
    assert len(fused) == 1
    assert fused[0].score == 1.0  # normalized max

    with pytest.raises(ValueError, match="k must be >= 1"):
        multi_list_rrf([single], k=0)

    with pytest.raises(ValueError, match="Length of weights"):
        multi_list_rrf([single, single], weights=[1.0])

    with pytest.raises(ValueError, match="must be non-negative"):
        multi_list_rrf([single], weights=[-1.0])

    with pytest.raises(ValueError, match="positive"):
        multi_list_rrf([single], weights=[0.0])


@pytest.mark.asyncio
async def test_multi_query_strategy_generation():
    mock_llm = ScriptedLLMClient([
        "1. How does Python manage RAM internally?\n2. What is CPython memory allocation?\n- Python reference counting mechanism"
    ])
    strategy = MultiQueryStrategy(llm_client=mock_llm, num_queries=3, include_original=True)
    queries = await strategy.generate_queries("Explain Python memory management", mock_llm)

    assert len(queries) == 4
    assert queries[0] == "Explain Python memory management"
    assert "How does Python manage RAM internally?" in queries
    assert "What is CPython memory allocation?" in queries
    assert "Python reference counting mechanism" in queries


@pytest.mark.asyncio
async def test_multi_query_strategy_retrieval_and_rrf():
    mock_llm = ScriptedLLMClient([
        "1. Docker container networking modes\n2. Bridge vs host network in Docker"
    ])
    strategy = MultiQueryStrategy(llm_client=mock_llm, num_queries=2, include_original=True)

    vector_store = InMemoryVectorStore()
    embedding_provider = MockEmbeddingProvider()
    engine = RAGEngine(vector_store=vector_store, embedding_provider=embedding_provider)

    await engine.ingest_documents([
        Document(id="doc1", content="Docker networking allows containers to communicate with each other using bridge or overlay drivers."),
        Document(id="doc2", content="Python decorators wrap functions to extend behavior without modifying source code."),
        Document(id="doc3", content="In host networking mode, Docker container shares network namespace directly with host."),
    ])

    results = await strategy.retrieve("How does Docker networking work?", engine=engine, top_k=2)

    assert len(results) == 2
    matched_ids = [r.document.metadata.get("parent_id", r.document.id) for r in results]
    assert "doc1" in matched_ids
    assert "doc3" in matched_ids
    assert "doc2" not in matched_ids


def test_multi_query_strategy_sync():
    mock_llm = ScriptedLLMClient([
        "1. Alternative query variant A\n2. Alternative query variant B"
    ])
    strategy = MultiQueryStrategy(llm_client=mock_llm, num_queries=2)

    vector_store = InMemoryVectorStore()
    embedding_provider = MockEmbeddingProvider()
    engine = RAGEngine(vector_store=vector_store, embedding_provider=embedding_provider)

    engine.ingest_texts_sync([
        "Advanced indexing architectures for vector search databases.",
        "PostgreSQL pgvector extension enables HNSW indexing.",
    ])

    results = strategy.retrieve_sync("Vector indexing", engine=engine, top_k=2)
    assert len(results) == 2


@pytest.mark.asyncio
async def test_hyde_strategy_generation_and_retrieval():
    hypothetical_passage = (
        "Raft achieves consensus by electing a distinguished leader who manages the replicated log. "
        "During network partitions, only a partition containing a majority quorum can elect a leader and commit log entries."
    )
    mock_llm = ScriptedLLMClient([hypothetical_passage])
    strategy = HyDEStrategy(
        llm_client=mock_llm,
        include_original_query=True,
        hypothetical_weight=0.7,
        original_weight=0.3,
    )

    vector_store = InMemoryVectorStore()
    embedding_provider = MockEmbeddingProvider()
    engine = RAGEngine(vector_store=vector_store, embedding_provider=embedding_provider)

    await engine.ingest_documents([
        Document(id="raft_doc", content="The Raft consensus protocol uses leader election and majority quorum to maintain partition tolerance."),
        Document(id="sql_doc", content="SQL databases enforce ACID transactions via write-ahead logging (WAL) and MVCC."),
    ])

    results = await strategy.retrieve("How does Raft handle network partitions?", engine=engine, top_k=1)

    assert len(results) == 1
    assert results[0].document.metadata.get("parent_id", results[0].document.id) == "raft_doc"


def test_hyde_strategy_sync():
    mock_llm = ScriptedLLMClient(["Hypothetical explanation of quantum computing qubits."])
    strategy = HyDEStrategy(llm_client=mock_llm, include_original_query=False)

    vector_store = InMemoryVectorStore()
    embedding_provider = MockEmbeddingProvider()
    engine = RAGEngine(vector_store=vector_store, embedding_provider=embedding_provider)

    engine.ingest_texts_sync([
        "Quantum computers utilize quantum bits or qubits in superposition.",
        "Classical computers operate with binary bits 0 and 1.",
    ])

    results = strategy.retrieve_sync("Explain quantum bits", engine=engine, top_k=1)
    assert len(results) == 1
    assert "qubits" in results[0].document.content


@pytest.mark.asyncio
async def test_rag_engine_native_strategy_integration():
    mock_llm = ScriptedLLMClient([
        "1. Microservices communication protocols\n2. REST vs gRPC service messaging",
        "Microservices often communicate via gRPC or REST APIs.",
    ])
    strategy = MultiQueryStrategy(llm_client=mock_llm, num_queries=2)

    vector_store = InMemoryVectorStore()
    embedding_provider = MockEmbeddingProvider()
    engine = RAGEngine(
        vector_store=vector_store,
        embedding_provider=embedding_provider,
        llm_client=mock_llm,
        retrieval_strategy=strategy,
    )

    await engine.ingest_documents([
        Document(id="m1", content="Microservices communicate via synchronous gRPC and asynchronous Kafka event streams."),
        Document(id="m2", content="Monolithic applications package all features into a single deployable binary unit."),
    ])

    # 1. Native retrieve uses configured strategy
    retrieved = await engine.retrieve("How do microservices communicate?", top_k=1)
    assert len(retrieved) == 1
    assert retrieved[0].document.metadata.get("parent_id", retrieved[0].document.id) == "m1"

    # 2. Strategy override can bypass strategy
    direct = await engine.retrieve("How do microservices communicate?", top_k=1, strategy=None)
    assert len(direct) == 1

    # 3. Native query uses strategy automatically
    response = await engine.query("How do microservices communicate?", top_k=1)
    assert "gRPC" in response.answer
    assert len(response.sources) == 1


@pytest.mark.asyncio
async def test_strategy_with_cross_encoder_reranker():
    mock_llm = ScriptedLLMClient([
        "1. Redis in-memory cache eviction policies\n2. LRU and LFU cache management in Redis"
    ])
    strategy = MultiQueryStrategy(llm_client=mock_llm, num_queries=2)
    reranker = LocalCrossEncoderReranker()

    vector_store = InMemoryVectorStore()
    embedding_provider = MockEmbeddingProvider()
    engine = RAGEngine(
        vector_store=vector_store,
        embedding_provider=embedding_provider,
        reranker=reranker,
        retrieval_strategy=strategy,
    )

    await engine.ingest_documents([
        Document(id="d_other", content="PostgreSQL query planner uses statistics to build optimal execution plans."),
        Document(id="d_exact", content="Redis cache eviction policies include allkeys-lru, volatile-lru, and volatile-ttl."),
    ])

    results = await engine.retrieve("Redis cache eviction policies", top_k=1)
    assert len(results) == 1
    assert results[0].document.metadata.get("parent_id", results[0].document.id) == "d_exact"
