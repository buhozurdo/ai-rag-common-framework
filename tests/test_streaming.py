"""Unit and integration tests for Server-Sent Events (SSE) streaming.

Covers:
- OpenAICompatibleClient.complete_stream token-by-token emission
- OpenAICompatibleClient.complete_stream HTTP error handling
- AnthropicClient.complete_stream event parsing
- FallbackLLMClient streaming failover before chunks emitted
- RAGEngine.query_stream context retrieval and continuous generation
- FastAPI /rag/query/stream SSE endpoint integration
"""
from __future__ import annotations

import json
from typing import Any, AsyncIterator
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from fastapi import FastAPI

from common_framework.api.app import create_app
from common_framework.core.exceptions import AuthenticationError, RateLimitError
from common_framework.core.llm import (
    AnthropicClient,
    BaseLLMClient,
    ChatMessage,
    ChatResponse,
    ChatStreamChunk,
    FallbackLLMClient,
    OpenAICompatibleClient,
    Role,
    TokenUsage,
)
from common_framework.rag.embeddings import MockEmbeddingProvider
from common_framework.rag.engine import RAGEngine


class MockStreamClient(BaseLLMClient):
    """Test client yielding predetermined stream chunks."""

    def __init__(self, chunks: list[str], name: str = "mock-stream") -> None:
        super().__init__()
        self.chunks = chunks
        self.provider_name = name
        self.call_count = 0

    async def complete(
        self,
        messages: list[ChatMessage],
        model: str | None = None,
        temperature: float | None = None,
        **kwargs: Any,
    ) -> ChatResponse:
        full_content = "".join(self.chunks)
        return ChatResponse(content=full_content, model=model or "mock-model")

    async def structured_complete(
        self,
        messages: list[ChatMessage],
        response_model: type[Any],
        model: str | None = None,
        temperature: float | None = None,
        **kwargs: Any,
    ) -> Any:
        raise NotImplementedError()

    async def complete_stream(
        self,
        messages: list[ChatMessage],
        model: str | None = None,
        temperature: float | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[ChatStreamChunk]:
        self.call_count += 1
        for piece in self.chunks:
            yield ChatStreamChunk(delta=piece, model=model or "mock-model")
        yield ChatStreamChunk(delta="", finish_reason="stop", model=model or "mock-model")


class MockFailingStreamClient(BaseLLMClient):
    """Test client that fails immediately when streaming starts."""

    def __init__(self, error: Exception, name: str = "mock-fail") -> None:
        super().__init__()
        self.error = error
        self.provider_name = name
        self.call_count = 0

    async def complete(self, messages: list[ChatMessage], **kwargs: Any) -> ChatResponse:
        raise self.error

    async def structured_complete(self, messages: list[ChatMessage], **kwargs: Any) -> Any:
        raise self.error

    async def complete_stream(self, messages: list[ChatMessage], **kwargs: Any) -> AsyncIterator[ChatStreamChunk]:
        self.call_count += 1
        raise self.error
        if False:
            yield ChatStreamChunk()  # pragma: no cover


# ===========================================================================
# LLM Provider Streaming Tests
# ===========================================================================


class TestLLMStreaming:
    @pytest.mark.asyncio
    async def test_openai_complete_stream_success(self):
        """Verify OpenAI SSE stream parsing yields token pieces and [DONE] break."""
        sse_lines = [
            'data: {"choices": [{"delta": {"content": "Hello"}}], "model": "gpt-4o"}\n',
            'data: {"choices": [{"delta": {"content": " world!"}}], "model": "gpt-4o"}\n',
            'data: {"choices": [{"delta": {}, "finish_reason": "stop"}], "model": "gpt-4o"}\n',
            "data: [DONE]\n",
        ]

        async def mock_aiter_lines():
            for line in sse_lines:
                yield line

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.aiter_lines = mock_aiter_lines

        mock_context_manager = MagicMock()
        mock_context_manager.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_context_manager.__aexit__ = AsyncMock(return_value=False)

        mock_client = MagicMock()
        mock_client.stream = MagicMock(return_value=mock_context_manager)

        client = OpenAICompatibleClient(
            api_key="sk-test",
            base_url="https://api.openai.com/v1",
            client=mock_client,
        )

        chunks: list[ChatStreamChunk] = []
        async for chunk in client.complete_stream(
            messages=[ChatMessage(role=Role.USER, content="Say hello")]
        ):
            chunks.append(chunk)

        assert len(chunks) == 3
        text = "".join(c.delta for c in chunks)
        assert text == "Hello world!"
        assert chunks[2].finish_reason == "stop"

    @pytest.mark.asyncio
    async def test_openai_complete_stream_http_error(self):
        """Verify HTTP 401 on stream raises AuthenticationError."""
        mock_resp = MagicMock()
        mock_resp.status_code = 401
        mock_resp.aread = AsyncMock()
        mock_resp.json = MagicMock(return_value={"error": {"message": "Invalid API Key"}})
        mock_resp.text = "Unauthorized"
        mock_resp.request = MagicMock()

        mock_context_manager = MagicMock()
        mock_context_manager.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_context_manager.__aexit__ = AsyncMock(return_value=False)

        mock_client = MagicMock()
        mock_client.stream = MagicMock(return_value=mock_context_manager)

        client = OpenAICompatibleClient(api_key="bad-key", client=mock_client)

        with pytest.raises(AuthenticationError):
            async for _ in client.complete_stream(messages=[ChatMessage(role=Role.USER, content="Hi")]):
                pass

    @pytest.mark.asyncio
    async def test_anthropic_complete_stream_success(self):
        """Verify Anthropic native Messages API streaming events."""
        sse_lines = [
            'data: {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "Claude"}}\n',
            'data: {"type": "content_block_delta", "delta": {"type": "text_delta", "text": " is"}}\n',
            'data: {"type": "content_block_delta", "delta": {"type": "text_delta", "text": " streaming"}}\n',
            'data: {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 3}}\n',
        ]

        async def mock_aiter_lines():
            for line in sse_lines:
                yield line

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.aiter_lines = mock_aiter_lines

        mock_cm = MagicMock()
        mock_cm.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_cm.__aexit__ = AsyncMock(return_value=False)

        mock_http = MagicMock()
        mock_http.stream = MagicMock(return_value=mock_cm)

        client = AnthropicClient(api_key="sk-ant", client=mock_http)

        chunks: list[ChatStreamChunk] = []
        async for chunk in client.complete_stream(messages=[ChatMessage(role=Role.USER, content="Hi")]):
            chunks.append(chunk)

        text = "".join(c.delta for c in chunks)
        assert text == "Claude is streaming"
        assert chunks[-1].finish_reason == "end_turn"

    @pytest.mark.asyncio
    async def test_fallback_streaming_failover_before_data(self):
        """FallbackLLMClient cascades to secondary when primary fails before yielding chunks."""
        primary = MockFailingStreamClient(RateLimitError("Quota reached", provider="gemini"))
        secondary = MockStreamClient(chunks=["Fallback", " ", "stream", "!"])

        fallback_llm = FallbackLLMClient(clients=[primary, secondary])

        chunks: list[ChatStreamChunk] = []
        async for chunk in fallback_llm.complete_stream(messages=[ChatMessage(role=Role.USER, content="Test")]):
            chunks.append(chunk)

        text = "".join(c.delta for c in chunks)
        assert text == "Fallback stream!"
        assert primary.call_count == 1
        assert secondary.call_count == 1


# ===========================================================================
# RAGEngine & FastAPI Streaming Integration Tests
# ===========================================================================


class TestRAGEngineAndAPIStreaming:
    @pytest.mark.asyncio
    async def test_rag_engine_query_stream(self):
        """RAGEngine.query_stream retrieves context and streams answer tokens."""
        mock_client = MockStreamClient(chunks=["Python", " is", " awesome."])
        engine = RAGEngine(
            embedding_provider=MockEmbeddingProvider(),
            llm_client=mock_client,
        )

        await engine.ingest_texts(["Python documentation chunk."])

        stream = engine.query_stream("What is Python?")
        collected = []
        async for chunk in stream:
            collected.append(chunk.delta)

        assert "".join(collected) == "Python is awesome."
        assert mock_client.call_count == 1

    @pytest.mark.asyncio
    async def test_fastapi_rag_query_stream_sse_endpoint(self):
        """Verify POST /rag/query/stream returns valid Server-Sent Events stream."""
        mock_client = MockStreamClient(chunks=["Streamed", " answer", " from", " FastAPI!"])
        engine = RAGEngine(
            embedding_provider=MockEmbeddingProvider(),
            llm_client=mock_client,
        )
        await engine.ingest_texts(["Sample knowledge context."])

        app = create_app(
            rag_engine=engine,
            enable_rag_routes=True,
        )

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            res = await client.post(
                "/rag/query/stream",
                json={
                    "query": "Tell me something",
                    "top_k": 2,
                },
            )

            assert res.status_code == 200
            assert "text/event-stream" in res.headers.get("content-type", "")

            body = res.text
            assert "data: [DONE]" in body
            assert "Streamed" in body
            assert "FastAPI!" in body

            # Parse lines
            deltas = []
            for line in body.splitlines():
                if line.startswith("data:") and not "[DONE]" in line:
                    data = json.loads(line[5:].strip())
                    deltas.append(data.get("delta", ""))

            assert "".join(deltas) == "Streamed answer from FastAPI!"
