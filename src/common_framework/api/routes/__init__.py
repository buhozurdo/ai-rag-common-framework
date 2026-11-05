"""API Route collections for the framework."""

from common_framework.api.routes.rag import (
    RAGCollectionDeleteResponse,
    RAGCollectionsResponse,
    RAGIngestRequest,
    RAGIngestResponse,
    RAGQueryRequest,
    RAGQueryResponse,
    RAGRetrieveRequest,
    RAGRetrieveResponse,
    RAGSearchResultItem,
    create_rag_router,
)

__all__ = [
    "create_rag_router",
    "RAGQueryRequest",
    "RAGQueryResponse",
    "RAGRetrieveRequest",
    "RAGRetrieveResponse",
    "RAGIngestRequest",
    "RAGIngestResponse",
    "RAGCollectionsResponse",
    "RAGCollectionDeleteResponse",
    "RAGSearchResultItem",
]
