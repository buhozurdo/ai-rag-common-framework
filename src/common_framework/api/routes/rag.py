"""FastAPI router providing standard endpoints for RAG ingestion, retrieval, and query synthesis."""

from __future__ import annotations

import logging
from typing import Any, Literal, Optional, Sequence
from fastapi import APIRouter, Request, status
from pydantic import BaseModel, Field

from common_framework.rag.engine import RAGEngine

logger = logging.getLogger("common_framework.api.rag")


# ---------------------------------------------------------------------------
# Request & Response Models
# ---------------------------------------------------------------------------


class RAGQueryRequest(BaseModel):
    """Payload for full RAG retrieval and synthesis."""

    query: str = Field(..., min_length=1, description="User question or query string")
    collection: str = Field(default="default", description="Target collection/namespace name")
    top_k: int = Field(default=4, ge=1, le=100, description="Number of context chunks to retrieve")
    mode: Literal["hybrid", "dense"] = Field(
        default="hybrid", description="Search mode: 'hybrid' (vector + BM25) or 'dense' (vector only)"
    )
    filter_metadata: Optional[dict[str, Any]] = Field(
        default=None, description="Key-value filters that document metadata must match"
    )
    system_prompt: Optional[str] = Field(
        default=None, description="Optional custom system prompt instructing the LLM synthesis"
    )
    model: Optional[str] = Field(
        default=None, description="Optional LLM model override"
    )
    temperature: Optional[float] = Field(
        default=None, ge=0.0, le=2.0, description="Sampling temperature for synthesis"
    )


class RAGRetrieveRequest(BaseModel):
    """Payload for retrieving document chunks without LLM synthesis."""

    query: str = Field(..., min_length=1, description="Search query string")
    collection: str = Field(default="default", description="Target collection/namespace name")
    top_k: int = Field(default=4, ge=1, le=100, description="Number of results to retrieve")
    mode: Literal["hybrid", "dense"] = Field(
        default="hybrid", description="Search mode: 'hybrid' (vector + BM25) or 'dense' (vector only)"
    )
    filter_metadata: Optional[dict[str, Any]] = Field(
        default=None, description="Key-value filters that document metadata must match"
    )


class RAGIngestRequest(BaseModel):
    """Payload for ingesting raw text strings into a collection."""

    texts: list[str] = Field(..., min_length=1, description="List of text chunks to embed and store")
    collection: str = Field(default="default", description="Target collection/namespace name")
    metadatas: Optional[list[dict[str, Any]]] = Field(
        default=None, description="Optional list of metadata dicts corresponding to texts"
    )


class RAGSearchResultItem(BaseModel):
    """Individual retrieved search chunk item."""

    id: str = Field(..., description="Document or chunk unique identifier")
    content: str = Field(..., description="Text content of the retrieved chunk")
    metadata: dict[str, Any] = Field(default_factory=dict, description="Metadata key-values")
    score: float = Field(..., description="Relevance score (higher is more relevant)")


class RAGRetrieveResponse(BaseModel):
    """Response payload for document retrieval."""

    query: str
    collection: str
    results: list[RAGSearchResultItem]
    count: int


class RAGQueryResponse(BaseModel):
    """Response payload for RAG synthesis."""

    answer: str
    sources: list[RAGSearchResultItem]
    model: Optional[str] = None
    tokens_used: Optional[int] = None


class RAGIngestResponse(BaseModel):
    """Response payload for document ingestion."""

    document_ids: list[str]
    count: int
    collection: str


class RAGCollectionsResponse(BaseModel):
    """List of all collections in the vector store."""

    collections: list[str]
    count: int


class RAGCollectionDeleteResponse(BaseModel):
    """Response payload after dropping a collection."""

    collection: str
    deleted: bool


# ---------------------------------------------------------------------------
# Router Factory
# ---------------------------------------------------------------------------


def create_rag_router(
    engine: Optional[RAGEngine] = None,
    prefix: str = "/rag",
    tags: Optional[Sequence[str]] = None,
    dependencies: Optional[Sequence[Any]] = None,
) -> APIRouter:
    """Build and return a FastAPI APIRouter configured with standard RAG endpoints.

    If an `engine` is passed, it is used directly. Otherwise, the router dynamically checks
    `request.app.state.rag_engine`, or lazily creates a default RAGEngine.
    """
    router_tags = list(tags) if tags is not None else ["RAG"]
    router = APIRouter(prefix=prefix, tags=router_tags, dependencies=dependencies)

    def _resolve_engine(request: Request) -> RAGEngine:
        if engine is not None:
            return engine
        if hasattr(request.app.state, "rag_engine") and request.app.state.rag_engine is not None:
            return request.app.state.rag_engine
        if not hasattr(router, "_default_engine"):
            router._default_engine = RAGEngine()  # type: ignore[attr-defined]
        return router._default_engine  # type: ignore[attr-defined]

    @router.post(
        "/query",
        response_model=RAGQueryResponse,
        summary="Query RAG pipeline",
        description="Retrieve relevant context chunks and synthesize an answer using the configured LLM.",
    )
    async def query_rag(req: RAGQueryRequest, request: Request) -> RAGQueryResponse:
        rag = _resolve_engine(request)
        rag_res = await rag.query(
            query=req.query,
            top_k=req.top_k,
            collection=req.collection,
            filter_metadata=req.filter_metadata,
            mode=req.mode,
            system_prompt=req.system_prompt,
            model=req.model,
            temperature=req.temperature,
        )

        sources = [
            RAGSearchResultItem(
                id=s.document.id,
                content=s.document.content,
                metadata=s.document.metadata,
                score=s.score,
            )
            for s in rag_res.sources
        ]
        return RAGQueryResponse(
            answer=rag_res.answer,
            sources=sources,
            model=rag_res.model,
            tokens_used=rag_res.usage.total_tokens if rag_res.usage else None,
        )

    @router.post(
        "/retrieve",
        response_model=RAGRetrieveResponse,
        summary="Retrieve document chunks",
        description="Search for document chunks using vector or hybrid search without LLM synthesis.",
    )
    async def retrieve_chunks(req: RAGRetrieveRequest, request: Request) -> RAGRetrieveResponse:
        rag = _resolve_engine(request)
        results = await rag.retrieve(
            query=req.query,
            top_k=req.top_k,
            collection=req.collection,
            filter_metadata=req.filter_metadata,
            mode=req.mode,
        )
        items = [
            RAGSearchResultItem(
                id=r.document.id,
                content=r.document.content,
                metadata=r.document.metadata,
                score=r.score,
            )
            for r in results
        ]
        return RAGRetrieveResponse(
            query=req.query,
            collection=req.collection,
            results=items,
            count=len(items),
        )

    @router.post(
        "/ingest",
        response_model=RAGIngestResponse,
        status_code=status.HTTP_201_CREATED,
        summary="Ingest texts",
        description="Embed and store raw text documents into a collection.",
    )
    async def ingest_texts(req: RAGIngestRequest, request: Request) -> RAGIngestResponse:
        rag = _resolve_engine(request)
        doc_ids = await rag.ingest_texts(
            texts=req.texts,
            collection=req.collection,
            metadatas=req.metadatas,
        )
        return RAGIngestResponse(
            document_ids=doc_ids,
            count=len(doc_ids),
            collection=req.collection,
        )

    @router.get(
        "/collections",
        response_model=RAGCollectionsResponse,
        summary="List collections",
        description="List all available collections in the vector store.",
    )
    async def list_collections(request: Request) -> RAGCollectionsResponse:
        rag = _resolve_engine(request)
        if hasattr(rag.vector_store, "list_collections"):
            cols = await rag.vector_store.list_collections()
        else:
            cols = ["default"]
        return RAGCollectionsResponse(collections=cols, count=len(cols))

    @router.delete(
        "/collections/{name}",
        response_model=RAGCollectionDeleteResponse,
        summary="Delete a collection",
        description="Remove a collection and all its indexed documents from the vector store.",
    )
    async def delete_collection(name: str, request: Request) -> RAGCollectionDeleteResponse:
        rag = _resolve_engine(request)
        if hasattr(rag.vector_store, "delete_collection"):
            await rag.vector_store.delete_collection(name)
            return RAGCollectionDeleteResponse(collection=name, deleted=True)
        return RAGCollectionDeleteResponse(collection=name, deleted=False)

    return router
