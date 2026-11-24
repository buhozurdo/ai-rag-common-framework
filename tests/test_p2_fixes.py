"""Unit tests covering fixes for P2 audit findings (structured output preambles, rerank_sync event loop safety, memory thread locks, multi-query candidate pools, CORS warning)."""

from __future__ import annotations

import asyncio
import logging
import threading
from typing import Optional
from unittest.mock import AsyncMock, patch

import pytest
from pydantic import BaseModel

from common_framework.agents.memory import ConversationBufferMemory
from common_framework.api.app import create_app
from common_framework.core.config import Settings
from common_framework.core.llm import (
    ChatMessage,
    ChatResponse,
    OpenAICompatibleClient,
    Role,
    TokenUsage,
    _extract_json_payload,
)
from common_framework.rag.engine import RAGEngine
from common_framework.rag.embeddings import MockEmbeddingProvider
from common_framework.rag.models import Document, SearchResult
from common_framework.rag.rerankers import BaseReranker, LocalCrossEncoderReranker
from common_framework.rag.strategies import MultiQueryStrategy
from common_framework.rag.vectorstore import InMemoryVectorStore


class SampleOutput(BaseModel):
    name: str
    score: float


# ---------------------------------------------------------------------------
# 1. Structured output preamble resilience
# ---------------------------------------------------------------------------


def test_extract_json_payload_variations():
    """Verify _extract_json_payload strips commentary, preambles, and code fences."""
    # Direct JSON
    assert _extract_json_payload('{"name": "test", "score": 1.0}') == '{"name": "test", "score": 1.0}'

    # Markdown fence with json
    fenced = '```json\n{"name": "test", "score": 1.0}\n```'
    assert _extract_json_payload(fenced) == '{"name": "test", "score": 1.0}'

    # Preamble + markdown fence + postscript
    chatty = (
        "Here is the parsed output requested:\n"
        "```json\n"
        '{"name": "test", "score": 1.0}\n'
        "```\n"
        "Hope this helps with your query!"
    )
    assert _extract_json_payload(chatty) == '{"name": "test", "score": 1.0}'

    # Preamble without fences
    no_fence = 'Certainly! The answer is {"name": "test", "score": 1.0} as verified.'
    assert _extract_json_payload(no_fence) == '{"name": "test", "score": 1.0}'


@pytest.mark.asyncio
async def test_openai_structured_complete_with_preamble():
    """Verify structured_complete validates models even when LLM output includes preambles."""
    client = OpenAICompatibleClient(settings=Settings(openai_api_key="sk-test-fake"))

    chatty_content = (
        "Sure, here is the structured output according to your schema:\n"
        "```json\n"
        '{"name": "Alice", "score": 98.5}\n'
        "```\n"
        "Let me know if you need any adjustments."
    )

    fake_response = ChatResponse(
        content=chatty_content,
        model="gpt-4o",
        usage=TokenUsage(prompt_tokens=10, completion_tokens=20, total_tokens=30),
    )

    with patch.object(client, "complete", new_callable=AsyncMock, return_value=fake_response):
        result = await client.structured_complete(
            messages=[ChatMessage(role=Role.USER, content="Give me Alice's score")],
            response_model=SampleOutput,
        )
        assert result.name == "Alice"
        assert result.score == 98.5


# ---------------------------------------------------------------------------
# 2. rerank_sync running event loop safety
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rerank_sync_inside_running_event_loop():
    """Verify rerank_sync succeeds without RuntimeError when invoked inside an active asyncio loop."""
    reranker = LocalCrossEncoderReranker()
    docs = [
        Document(id="d1", content="Python asyncio programming and concurrency guide"),
        Document(id="d2", content="Cooking Italian pasta with tomato sauce"),
    ]

    # This is called inside an active async test loop
    results = reranker.rerank_sync("asyncio concurrency", docs, top_k=2)
    assert len(results) == 2
    assert results[0].document.id == "d1"
    assert results[0].score > results[1].score


# ---------------------------------------------------------------------------
# 3. MultiQueryStrategy candidate pool preservation
# ---------------------------------------------------------------------------


class MockReranker(BaseReranker):
    def __init__(self):
        self.received_docs_count = 0

    async def rerank(
        self,
        query: str,
        documents: list[Document],
        top_k: Optional[int] = None,
    ) -> list[SearchResult]:
        self.received_docs_count = len(documents)
        results = [SearchResult(document=d, score=1.0) for d in documents]
        return results[:top_k] if top_k else results


@pytest.mark.asyncio
async def test_multiquery_strategy_preserves_candidate_pool_for_reranker():
    """Verify MultiQueryStrategy passes an expanded candidate pool to the reranker before truncating to top_k."""
    store = InMemoryVectorStore()
    docs = [
        Document(
            id=f"doc-{i}",
            content=f"Document content number {i}",
            embedding=[float(i)] + [0.0] * 63,
        )
        for i in range(25)
    ]
    await store.add_documents(docs)

    mock_reranker = MockReranker()
    engine = RAGEngine(
        vector_store=store,
        embedding_provider=MockEmbeddingProvider(),
        reranker=mock_reranker,
    )

    strategy = MultiQueryStrategy(num_queries=2, include_original=True)
    with patch.object(strategy, "generate_queries", new_callable=AsyncMock, return_value=["query 1", "query 2"]):
        results = await strategy.retrieve(
            query="test query",
            engine=engine,
            top_k=3,
        )

    assert len(results) == 3
    # Reranker should have received a candidate pool larger than the final top_k=3
    assert mock_reranker.received_docs_count > 3


# ---------------------------------------------------------------------------
# 4. Memory thread locks & sync retrieval
# ---------------------------------------------------------------------------


def test_conversation_buffer_memory_thread_safety():
    """Verify concurrent sync writes and reads across threads do not corrupt internal state."""
    memory = ConversationBufferMemory(max_messages=50)

    def _worker(thread_id: int):
        for i in range(20):
            memory.add_user_message(f"Msg from thread {thread_id} item {i}")
            msgs = memory.get_messages_sync()
            assert len(msgs) <= 50

    threads = [threading.Thread(target=_worker, args=(t,)) for t in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    final_msgs = memory.get_messages_sync()
    assert len(final_msgs) == 50

    memory.clear_sync()
    assert len(memory.get_messages_sync()) == 0


# ---------------------------------------------------------------------------
# 5. Production CORS warning
# ---------------------------------------------------------------------------


def test_cors_production_wildcard_warning(caplog):
    """Verify create_app emits a security warning when wildcard origin is enabled in production."""
    prod_settings = Settings(framework_env="production")

    with caplog.at_level(logging.WARNING):
        app = create_app(cors_origins=None, settings=prod_settings)
        assert app is not None
        assert any("CORS wildcard origin enabled in production environment" in record.message for record in caplog.records)
