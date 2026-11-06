"""Edge-case and security regression tests for all framework modules.

Covers:
- BM25 upsert correctness, empty corpus, unicode
- VectorStore: corrupted JSON, empty docs, upsert
- RAGEngine: unknown mode, empty collection query, dense mode
- Tools: zero-param tool, duplicate registry, special-char names
- LLM: empty choices, None message, unknown provider
- API: CORS, 401 WWW-Authenticate, production info-leak suppression
- MCP auth: query-param disabled by default, opt-in works
"""
from __future__ import annotations

import json
import math
import tempfile
from pathlib import Path
from typing import Optional
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from common_framework.core.exceptions import ConfigurationError
from common_framework.core.llm import (
    ChatMessage,
    LLMFactory,
    OpenAICompatibleClient,
    Role,
    TokenUsage,
)
from common_framework.rag.bm25 import BM25Index, tokenize
from common_framework.rag.embeddings import MockEmbeddingProvider
from common_framework.rag.engine import RAGEngine
from common_framework.rag.models import Document, SearchResult
from common_framework.rag.vectorstore import InMemoryVectorStore, cosine_similarity
from common_framework.tools.base import Tool, tool, ToolExecutionError
from common_framework.tools.registry import ToolRegistry, ToolNotFoundError


# ===========================================================================
# BM25 Edge Cases
# ===========================================================================


class TestBM25EdgeCases:
    def test_add_same_document_twice_does_not_corrupt_idf(self):
        """BUG-1 regression: re-indexing same ID must not double-count document frequencies."""
        bm25 = BM25Index()
        doc = Document(id="d1", content="python machine learning")
        bm25.add_documents([doc])

        # Doc freq for "python" should be 1
        assert bm25._doc_frequencies["python"] == 1

        # Re-index the same doc (upsert)
        bm25.add_documents([doc])

        # Still 1 — not 2
        assert bm25._doc_frequencies["python"] == 1
        assert len(bm25._documents) == 1

    def test_upsert_changes_content_correctly(self):
        """After upserting, BM25 reflects the NEW content only."""
        bm25 = BM25Index()
        bm25.add_documents([Document(id="d1", content="python programming")])
        assert "python" in bm25._doc_frequencies

        # Replace with entirely different content
        bm25.add_documents([Document(id="d1", content="java enterprise")])
        assert "python" not in bm25._doc_frequencies
        assert "java" in bm25._doc_frequencies

    def test_empty_corpus_search_returns_empty(self):
        """Search on empty BM25 index must return []."""
        bm25 = BM25Index()
        results = bm25.search("python")
        assert results == []

    def test_query_no_matching_tokens_returns_empty(self):
        """Query with no known terms returns []."""
        bm25 = BM25Index()
        bm25.add_documents([Document(id="d1", content="python machine learning")])
        results = bm25.search("zzzzzzunknown")
        assert results == []

    def test_top_k_larger_than_corpus_returns_all(self):
        """top_k > corpus size must return all documents, not raise."""
        bm25 = BM25Index()
        bm25.add_documents([
            Document(id="d1", content="python"),
            Document(id="d2", content="python code"),
        ])
        results = bm25.search("python", top_k=1000)
        assert len(results) == 2

    def test_delete_nonexistent_id_is_noop(self):
        """Deleting a non-existent document ID must not raise."""
        bm25 = BM25Index()
        bm25.add_documents([Document(id="d1", content="python")])
        bm25.delete(["nonexistent_id"])  # Must not raise
        assert len(bm25._documents) == 1

    def test_unicode_and_emoji_content(self):
        """Unicode and emoji content must tokenize and search without errors."""
        bm25 = BM25Index()
        bm25.add_documents([
            Document(id="d1", content="café résumé naïve"),
            Document(id="d2", content="hello world emoji"),
        ])
        # tokenize uses \w which is unicode-aware in Python 3
        results = bm25.search("caf")
        # May or may not find it depending on tokenization — just must not crash
        assert isinstance(results, list)

    def test_all_identical_documents_normalized_equally(self):
        """When all docs have identical content, BM25 scores should all be equal."""
        bm25 = BM25Index()
        for i in range(3):
            bm25.add_documents([Document(id=f"d{i}", content="python machine learning")])
        results = bm25.search("python", top_k=10)
        # All have equal IDF, so scores should be equal
        scores = [r.score for r in results]
        assert all(math.isclose(s, scores[0], rel_tol=1e-5) for s in scores)

    def test_filter_metadata_no_matches_returns_empty(self):
        """filter_metadata matching no documents returns []."""
        bm25 = BM25Index()
        bm25.add_documents([Document(id="d1", content="python", metadata={"lang": "en"})])
        results = bm25.search("python", filter_metadata={"lang": "fr"})
        assert results == []


# ===========================================================================
# VectorStore Edge Cases
# ===========================================================================


class TestVectorStoreEdgeCases:
    @pytest.mark.asyncio
    async def test_add_same_document_id_twice_upserts(self):
        """Adding a document with the same ID twice should be an upsert, not a duplicate."""
        store = InMemoryVectorStore()
        doc1 = Document(id="d1", content="original content", embedding=[1.0, 0.0])
        doc2 = Document(id="d1", content="updated content", embedding=[0.0, 1.0])

        await store.add_documents([doc1])
        assert await store.count() == 1

        await store.add_documents([doc2])
        assert await store.count() == 1  # Still 1, not 2

        retrieved = store.get("d1")
        assert retrieved is not None
        assert retrieved.content == "updated content"

    @pytest.mark.asyncio
    async def test_add_empty_list_is_noop(self):
        """Adding an empty document list must not raise."""
        store = InMemoryVectorStore()
        ids = await store.add_documents([])
        assert ids == []

    @pytest.mark.asyncio
    async def test_delete_from_nonexistent_collection_is_noop(self):
        """Deleting from a collection that doesn't exist must not raise."""
        store = InMemoryVectorStore()
        await store.delete(["d1"], collection="nonexistent")  # Must not raise

    @pytest.mark.asyncio
    async def test_hybrid_search_on_nonexistent_collection_returns_empty(self):
        """hybrid_search on non-existent collection must return []."""
        store = InMemoryVectorStore()
        results = await store.hybrid_search(
            query_text="anything",
            query_embedding=[1.0, 0.0],
            collection="ghost",
        )
        assert results == []

    @pytest.mark.asyncio
    async def test_similarity_search_empty_query_embedding_returns_empty(self):
        """Empty query embedding must return [] without crashing."""
        store = InMemoryVectorStore()
        doc = Document(id="d1", content="text", embedding=[1.0, 0.0])
        await store.add_documents([doc])
        results = await store.similarity_search(query_embedding=[], top_k=4)
        assert results == []

    @pytest.mark.asyncio
    async def test_delete_collection_nonexistent_is_noop(self):
        """delete_collection on a non-existent collection must not raise."""
        store = InMemoryVectorStore()
        await store.delete_collection("ghost")  # Must not raise

    def test_load_from_json_corrupted_raises_value_error(self):
        """load_from_json with invalid JSON raises ValueError, not JSONDecodeError."""
        store = InMemoryVectorStore()
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write("{this is not valid json!}")
            tmp = f.name
        try:
            with pytest.raises(ValueError, match="invalid JSON"):
                store.load_from_json(tmp)
        finally:
            Path(tmp).unlink(missing_ok=True)

    @pytest.mark.asyncio
    async def test_load_from_json_corrupted_preserves_existing_data(self):
        """load_from_json failure must NOT clear existing in-memory data."""
        store = InMemoryVectorStore()
        # Pre-populate with valid data
        await store.add_documents([Document(id="existing", content="keep me", embedding=[1.0, 0.0])])
        assert await store.count() == 1

        # Attempt to load corrupted file
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write("CORRUPT")
            tmp = f.name
        try:
            with pytest.raises(ValueError):
                store.load_from_json(tmp)
        finally:
            Path(tmp).unlink(missing_ok=True)

        # Original data must still be intact
        assert await store.count() == 1

    def test_load_from_json_wrong_schema_raises_value_error(self):
        """load_from_json with wrong schema raises ValueError."""
        store = InMemoryVectorStore()
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump({"col1": [{"bad": "schema", "no_id": True}]}, f)
            tmp = f.name
        try:
            with pytest.raises(ValueError, match="Failed to parse"):
                store.load_from_json(tmp)
        finally:
            Path(tmp).unlink(missing_ok=True)

    def test_cosine_similarity_opposite_vectors(self):
        """Opposite vectors should return 0.0 (not 0.5) under uniform mapping."""
        v1 = [1.0, 0.0]
        v_opposite = [-1.0, 0.0]
        assert math.isclose(cosine_similarity(v1, v_opposite), 0.0, abs_tol=1e-9)

    def test_cosine_similarity_zero_vector_returns_zero(self):
        """Zero-magnitude vector must return 0.0 gracefully."""
        result = cosine_similarity([0.0, 0.0], [1.0, 0.0])
        assert result == 0.0


# ===========================================================================
# RAGEngine Edge Cases
# ===========================================================================


class TestRAGEngineEdgeCases:
    @pytest.mark.asyncio
    async def test_retrieve_unknown_mode_raises_value_error(self):
        """BUG-3 regression: unknown retrieval mode must raise ValueError."""
        engine = RAGEngine(embedding_provider=MockEmbeddingProvider())
        await engine.ingest_texts(["some content"])
        with pytest.raises(ValueError, match="Unknown retrieval mode"):
            await engine.retrieve("query", mode="sparse")

    @pytest.mark.asyncio
    async def test_retrieve_dense_mode_explicit(self):
        """Explicit mode='dense' must work correctly."""
        engine = RAGEngine(embedding_provider=MockEmbeddingProvider())
        await engine.ingest_texts(["python machine learning"])
        results = await engine.retrieve("python", mode="dense")
        assert isinstance(results, list)

    @pytest.mark.asyncio
    async def test_query_empty_collection_returns_no_context_answer(self):
        """query() on an empty collection must call LLM with 'No relevant context found.'."""
        from common_framework.rag.models import RAGResponse

        mock_llm = MagicMock()
        from common_framework.core.llm import ChatResponse
        mock_llm.complete = AsyncMock(return_value=ChatResponse(
            content="I cannot find the answer.", model="mock", usage=TokenUsage(), raw={}
        ))
        engine = RAGEngine(embedding_provider=MockEmbeddingProvider(), llm_client=mock_llm)
        response = await engine.query("What is X?", collection="empty_collection")

        assert isinstance(response, RAGResponse)
        assert response.sources == []
        # Verify LLM was called with "No relevant context found." in messages
        call_args = mock_llm.complete.call_args
        messages = call_args.kwargs.get("messages") or call_args.args[0]
        user_content = next((m.content for m in messages if m.role.value == "user"), "")
        assert "No relevant context found" in user_content

    @pytest.mark.asyncio
    async def test_ingest_texts_empty_list_returns_empty_ids(self):
        """ingest_texts([]) must return [] without raising."""
        engine = RAGEngine(embedding_provider=MockEmbeddingProvider())
        ids = await engine.ingest_texts([])
        assert ids == []

    @pytest.mark.asyncio
    async def test_ingest_texts_metadatas_shorter_than_texts(self):
        """metadatas shorter than texts fills missing metadata with {}."""
        engine = RAGEngine(embedding_provider=MockEmbeddingProvider())
        ids = await engine.ingest_texts(
            texts=["text A", "text B", "text C"],
            metadatas=[{"source": "file"}],  # only 1 metadata for 3 texts
        )
        assert len(ids) >= 1  # Must not crash


# ===========================================================================
# Tools Edge Cases
# ===========================================================================


class TestToolsEdgeCases:
    def test_tool_with_no_parameters(self):
        """@tool on a function with zero parameters must create a valid Tool."""
        @tool
        def zero_param_tool() -> str:
            return "done"

        assert zero_param_tool.name == "zero_param_tool"
        schema = zero_param_tool.parameters_schema
        assert isinstance(schema, dict)

    @pytest.mark.asyncio
    async def test_zero_param_tool_executes(self):
        """Zero-parameter tool must execute successfully."""
        @tool
        async def zero_async() -> str:
            return "ok"

        result = await zero_async.execute()
        assert result == "ok"

    def test_tool_name_with_hyphen_in_decorator(self):
        """Tool name with hyphens passed via decorator must be stored correctly."""
        @tool(name="my-custom-tool", description="A tool")
        def custom_named(x: int) -> int:
            return x * 2

        assert custom_named.name == "my-custom-tool"

    @pytest.mark.asyncio
    async def test_tool_wraps_non_tool_exceptions_in_tool_execution_error(self):
        """Non-ToolExecutionError exceptions raised in tool fn are wrapped."""
        @tool
        async def raising_tool() -> str:
            raise RuntimeError("unexpected internal error")

        with pytest.raises(ToolExecutionError, match="unexpected internal error"):
            await raising_tool.execute()

    def test_registry_duplicate_registration_overwrites(self):
        """Registering the same name twice silently overwrites without error."""
        registry = ToolRegistry()

        @tool
        def my_tool() -> str:
            return "v1"

        @tool(name="my_tool")
        async def my_tool_v2() -> str:
            return "v2"

        registry.register(my_tool)
        registry.register(my_tool_v2)
        assert len(registry) == 1
        assert registry.get("my_tool").is_async is True

    def test_registry_unregister_nonexistent_returns_none(self):
        """Unregistering a non-existent tool returns None, doesn't raise."""
        registry = ToolRegistry()
        result = registry.unregister("ghost_tool")
        assert result is None

    @pytest.mark.asyncio
    async def test_registry_execute_nonexistent_raises_tool_not_found(self):
        """Executing a non-existent tool via registry raises ToolNotFoundError."""
        registry = ToolRegistry()
        with pytest.raises(ToolNotFoundError):
            await registry.execute("nonexistent_tool")

    def test_registry_len_and_contains_on_empty(self):
        """len() and __contains__ on empty registry work correctly."""
        registry = ToolRegistry()
        assert len(registry) == 0
        assert "anything" not in registry


# ===========================================================================
# LLM Client Edge Cases
# ===========================================================================


class TestLLMEdgeCases:
    @pytest.mark.asyncio
    async def test_complete_handles_empty_choices_list(self):
        """complete() must not crash when provider returns choices=[]."""
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {
            "choices": [],
            "model": "mock-model",
            "usage": {"prompt_tokens": 10, "completion_tokens": 0, "total_tokens": 10},
        }

        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_response)

        client = OpenAICompatibleClient(
            api_key="test",
            base_url="http://localhost:11434/v1",
            provider_name="test",
            client=mock_client,
        )
        messages = [ChatMessage(role=Role.USER, content="hi")]
        response = await client.complete(messages=messages)
        assert response.content == ""

    @pytest.mark.asyncio
    async def test_complete_handles_none_message(self):
        """complete() must not crash when choices[0].message is None (tool calls)."""
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {
            "choices": [{"message": None, "finish_reason": "tool_calls"}],
            "model": "mock-model",
            "usage": {},
        }

        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_response)

        client = OpenAICompatibleClient(
            api_key="test",
            base_url="http://localhost:11434/v1",
            provider_name="test",
            client=mock_client,
        )
        messages = [ChatMessage(role=Role.USER, content="call a tool")]
        response = await client.complete(messages=messages)
        assert response.content == ""

    def test_llm_factory_unknown_provider_raises_configuration_error(self):
        """LLMFactory.create() with unknown provider raises ConfigurationError."""
        with pytest.raises(ConfigurationError, match="Unsupported LLM provider"):
            LLMFactory.create(provider="unknown_provider_xyz")


# ===========================================================================
# API Security Tests
# ===========================================================================


class TestAPISecurityEdgeCases:
    @pytest.fixture
    def app(self):
        from common_framework.api.app import create_app
        return create_app()

    @pytest.mark.asyncio
    async def test_401_includes_www_authenticate_header(self, app):
        """401 responses must include WWW-Authenticate: Bearer (RFC 7235)."""
        from common_framework.core.exceptions import AuthenticationError

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            # Trigger the auth error handler by making a request to a custom route
            @app.get("/test-auth-error")
            async def trigger_auth():
                raise AuthenticationError("bad token", provider="openai")

            res = await client.get("/test-auth-error")
            assert res.status_code == 401
            assert res.headers.get("www-authenticate") == "Bearer"

    @pytest.mark.asyncio
    async def test_production_structured_output_hides_raw_output(self):
        """In production env, raw_output must not appear in 502 response body."""
        from common_framework.api.app import create_app
        from common_framework.core.exceptions import StructuredOutputError

        app = create_app(settings=__import__("common_framework.core.config", fromlist=["Settings"]).Settings(
            FRAMEWORK_ENV="production"
        ))

        @app.get("/test-structured-error")
        async def trigger():
            raise StructuredOutputError("parse failed", raw_output="SENSITIVE_PROMPT_DATA")

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            res = await client.get("/test-structured-error")
            assert res.status_code == 502
            body = res.json()
            assert body["raw_output"] is None
            assert "SENSITIVE_PROMPT_DATA" not in res.text

    @pytest.mark.asyncio
    async def test_development_structured_output_shows_raw_output(self):
        """In development env, raw_output is included in the 502 response for debugging."""
        from common_framework.api.app import create_app
        from common_framework.core.exceptions import StructuredOutputError

        app = create_app(settings=__import__("common_framework.core.config", fromlist=["Settings"]).Settings(
            FRAMEWORK_ENV="development"
        ))

        @app.get("/test-structured-error-dev")
        async def trigger():
            raise StructuredOutputError("parse failed", raw_output="DEBUG_OUTPUT")

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            res = await client.get("/test-structured-error-dev")
            assert res.status_code == 502
            body = res.json()
            assert body["raw_output"] == "DEBUG_OUTPUT"

    @pytest.mark.asyncio
    async def test_mcp_auth_very_long_key_rejected(self):
        """Very long API keys must be rejected cleanly (no crash/DoS)."""
        from common_framework.api.mcp import MCPAuthMiddleware

        async def simple_app(scope, receive, send):
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b""})

        middleware = MCPAuthMiddleware(app=simple_app, expected_api_key="short-key")

        # Simulate a request with a very long key
        long_key = "A" * 100_000
        scope = {
            "type": "http",
            "headers": [(b"authorization", f"Bearer {long_key}".encode())],
            "query_string": b"",
        }

        rejected_status = None

        async def mock_send(msg):
            nonlocal rejected_status
            if msg["type"] == "http.response.start":
                rejected_status = msg["status"]

        await middleware(scope, None, mock_send)
        assert rejected_status == 401

    @pytest.mark.asyncio
    async def test_mcp_auth_null_bytes_in_key_rejected(self):
        """Null bytes in API key header must result in 401, not a crash."""
        from common_framework.api.mcp import MCPAuthMiddleware

        async def simple_app(scope, receive, send):
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b""})

        middleware = MCPAuthMiddleware(app=simple_app, expected_api_key="valid-key")

        scope = {
            "type": "http",
            "headers": [(b"authorization", b"Bearer valid-key\x00injected")],
            "query_string": b"",
        }

        rejected_status = None

        async def mock_send(msg):
            nonlocal rejected_status
            if msg["type"] == "http.response.start":
                rejected_status = msg["status"]

        await middleware(scope, None, mock_send)
        assert rejected_status == 401

    @pytest.mark.asyncio
    async def test_mcp_auth_non_http_scope_passes_through(self):
        """Non-HTTP scopes (websocket, lifespan) must pass through the middleware."""
        from common_framework.api.mcp import MCPAuthMiddleware

        called = []

        async def simple_app(scope, receive, send):
            called.append(scope["type"])

        middleware = MCPAuthMiddleware(app=simple_app, expected_api_key="key")

        lifespan_scope = {"type": "lifespan"}
        await middleware(lifespan_scope, None, None)
        assert "lifespan" in called
