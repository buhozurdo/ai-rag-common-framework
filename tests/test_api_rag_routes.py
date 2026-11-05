"""Unit and integration tests for FastAPI RAG endpoints."""

from __future__ import annotations

import httpx
import pytest
from fastapi import Depends, Header, HTTPException, status

from common_framework.api import create_app, create_rag_router
from common_framework.core.llm import BaseLLMClient, ChatMessage, ChatResponse, TokenUsage
from common_framework.rag.embeddings import MockEmbeddingProvider
from common_framework.rag.engine import RAGEngine
from common_framework.rag.vectorstore import InMemoryVectorStore


class FakeLLMClient(BaseLLMClient):
    """Stub LLM returning a predictable answer referencing context."""

    def __init__(self, answer: str = "FastAPI is a modern web framework."):
        super().__init__()
        self._answer = answer

    async def complete(
        self,
        messages: list[ChatMessage],
        model: str | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
        **kwargs,
    ) -> ChatResponse:
        return ChatResponse(
            content=self._answer,
            model=model or "fake-llm",
            usage=TokenUsage(prompt_tokens=15, completion_tokens=8, total_tokens=23),
        )

    async def structured_complete(
        self,
        response_model: type[Any],
        messages: list[ChatMessage],
        model: str | None = None,
        temperature: float = 0.0,
        **kwargs,
    ) -> Any:
        return response_model()



@pytest.fixture
def test_rag_engine():
    """Create a clean isolated RAGEngine fixture with mock embeddings and fake LLM."""
    store = InMemoryVectorStore()
    emb = MockEmbeddingProvider(dimension=8)
    llm = FakeLLMClient()
    return RAGEngine(vector_store=store, embedding_provider=emb, llm_client=llm)


@pytest.mark.asyncio
async def test_rag_ingest_and_retrieve(test_rag_engine):
    """Test text ingestion and subsequent chunk retrieval."""
    app = create_app(enable_rag_routes=True, rag_engine=test_rag_engine)
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        # Ingest documents
        ingest_res = await client.post(
            "/rag/ingest",
            json={
                "texts": [
                    "FastAPI is a modern, fast web framework for building APIs with Python.",
                    "PostgreSQL is a powerful, open-source object-relational database system.",
                ],
                "collection": "tech_docs",
                "metadatas": [{"category": "web"}, {"category": "db"}],
            },
        )
        assert ingest_res.status_code == status.HTTP_201_CREATED
        data = ingest_res.json()
        assert data["count"] == 2
        assert len(data["document_ids"]) == 2
        assert data["collection"] == "tech_docs"

        # Retrieve documents (hybrid mode)
        retrieve_res = await client.post(
            "/rag/retrieve",
            json={
                "query": "FastAPI framework",
                "collection": "tech_docs",
                "top_k": 2,
                "mode": "hybrid",
            },
        )
        assert retrieve_res.status_code == status.HTTP_200_OK
        ret_data = retrieve_res.json()
        assert ret_data["count"] > 0
        assert "FastAPI" in ret_data["results"][0]["content"]
        assert ret_data["results"][0]["metadata"]["category"] == "web"


@pytest.mark.asyncio
async def test_rag_query_synthesis(test_rag_engine):
    """Test end-to-end RAG query synthesis endpoint."""
    # Pre-populate engine
    await test_rag_engine.ingest_texts(
        texts=["Microservices communicate via lightweight protocols like HTTP and gRPC."],
        collection="architecture",
    )

    app = create_app(enable_rag_routes=True, rag_engine=test_rag_engine)
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.post(
            "/rag/query",
            json={
                "query": "How do microservices communicate?",
                "collection": "architecture",
                "top_k": 1,
            },
        )
        assert res.status_code == status.HTTP_200_OK
        data = res.json()
        assert "answer" in data
        assert len(data["sources"]) == 1
        assert "microservices" in data["sources"][0]["content"].lower()
        assert data["model"] == "fake-llm"
        assert data["tokens_used"] == 23


@pytest.mark.asyncio
async def test_rag_collection_management(test_rag_engine):
    """Test listing and deleting collections via API."""
    await test_rag_engine.ingest_texts(["Finance article"], collection="finance")
    await test_rag_engine.ingest_texts(["Health article"], collection="health")

    app = create_app(enable_rag_routes=True, rag_engine=test_rag_engine)
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        # List collections
        list_res = await client.get("/rag/collections")
        assert list_res.status_code == status.HTTP_200_OK
        collections = list_res.json()["collections"]
        assert "finance" in collections
        assert "health" in collections

        # Delete finance collection
        del_res = await client.delete("/rag/collections/finance")
        assert del_res.status_code == status.HTTP_200_OK
        assert del_res.json() == {"collection": "finance", "deleted": True}

        # Verify finance is removed
        list_res_after = await client.get("/rag/collections")
        collections_after = list_res_after.json()["collections"]
        assert "finance" not in collections_after
        assert "health" in collections_after


@pytest.mark.asyncio
async def test_custom_rag_router_prefix_and_auth(test_rag_engine):
    """Test mounting RAG router with custom prefix and dependency security."""

    async def verify_api_key(x_api_key: str = Header(...)):
        if x_api_key != "secret-token":
            raise HTTPException(status_code=403, detail="Forbidden")
        return x_api_key

    custom_router = create_rag_router(
        engine=test_rag_engine,
        prefix="/api/v1/knowledge",
        tags=["KnowledgeBase"],
        dependencies=[Depends(verify_api_key)],
    )

    app = create_app(enable_healthcheck=False)
    app.include_router(custom_router)
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        # Without auth header -> 422 or 403
        unauth_res = await client.get("/api/v1/knowledge/collections")
        assert unauth_res.status_code in (403, 422)

        # With invalid header -> 403
        bad_auth = await client.get(
            "/api/v1/knowledge/collections", headers={"x-api-key": "invalid"}
        )
        assert bad_auth.status_code == 403

        # With valid header -> 200
        good_auth = await client.get(
            "/api/v1/knowledge/collections", headers={"x-api-key": "secret-token"}
        )
        assert good_auth.status_code == 200
