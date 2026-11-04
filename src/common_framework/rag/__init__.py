"""RAG module providing document splitting, vector embeddings, storage, and orchestration."""

from common_framework.rag.embeddings import (
    BaseEmbeddingProvider,
    EmbeddingFactory,
    MockEmbeddingProvider,
    OpenAICompatibleEmbeddingProvider,
)
from common_framework.rag.engine import RAGEngine
from common_framework.rag.models import Document, RAGResponse, SearchResult
from common_framework.rag.splitters import (
    BaseTextSplitter,
    CharacterTextSplitter,
    RecursiveTextSplitter,
)
from common_framework.rag.vectorstore import (
    BaseVectorStore,
    InMemoryVectorStore,
    cosine_similarity,
)

__all__ = [
    "Document",
    "SearchResult",
    "RAGResponse",
    "BaseTextSplitter",
    "CharacterTextSplitter",
    "RecursiveTextSplitter",
    "BaseEmbeddingProvider",
    "MockEmbeddingProvider",
    "OpenAICompatibleEmbeddingProvider",
    "EmbeddingFactory",
    "BaseVectorStore",
    "InMemoryVectorStore",
    "cosine_similarity",
    "RAGEngine",
]
