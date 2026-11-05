"""AI RAG Common Framework.

A lightweight, modular, and extensible framework designed to build
RAG systems, MCP (Model Context Protocol) servers, and AI microservices
with minimal boilerplate.
"""

from common_framework.core.config import Settings, get_settings
from common_framework.core.exceptions import (
    AuthenticationError,
    ConfigurationError,
    FrameworkError,
    LLMError,
    LLMProviderError,
    ModelNotFoundError,
    RateLimitError,
    StructuredOutputError,
)
from common_framework.core.llm import (
    BaseLLMClient,
    ChatMessage,
    ChatResponse,
    LLMFactory,
    Role,
    TokenUsage,
)
from common_framework.api.app import create_app
from common_framework.mcp.server import FrameworkMCPServer, create_mcp_server
from common_framework.rag.bm25 import BM25Index, reciprocal_rank_fusion, tokenize
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
from common_framework.tools.base import Tool, ToolExecutionError, tool
from common_framework.tools.registry import (
    ToolNotFoundError,
    ToolRegistry,
    global_tool_registry,
)

__version__ = "0.3.0"

__all__ = [
    "__version__",
    "Settings",
    "get_settings",
    "FrameworkError",
    "ConfigurationError",
    "LLMError",
    "LLMProviderError",
    "AuthenticationError",
    "RateLimitError",
    "ModelNotFoundError",
    "StructuredOutputError",
    "BaseLLMClient",
    "ChatMessage",
    "ChatResponse",
    "TokenUsage",
    "Role",
    "LLMFactory",
    "Tool",
    "ToolExecutionError",
    "tool",
    "ToolRegistry",
    "ToolNotFoundError",
    "global_tool_registry",
    "FrameworkMCPServer",
    "create_mcp_server",
    "create_app",
    "Document",
    "SearchResult",
    "RAGResponse",
    "BM25Index",
    "reciprocal_rank_fusion",
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
