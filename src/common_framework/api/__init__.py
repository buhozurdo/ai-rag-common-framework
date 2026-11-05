"""API module providing FastAPI integration, routes, and server factories."""

from common_framework.api.app import create_app, setup_exception_handlers
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
    "create_app",
    "setup_exception_handlers",
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
