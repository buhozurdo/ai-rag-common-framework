"""AI RAG Common Framework.

A lightweight, modular, and extensible framework designed to build
RAG systems, MCP (Model Context Protocol) servers, and AI microservices
with minimal boilerplate.
"""

from common_framework.core.config import Settings, get_settings
from common_framework.core.cache import (
    CachedLLMClient,
    SemanticCache,
    SemanticCacheEntry,
    SemanticCacheHit,
    SemanticCacheStats,
)
from common_framework.core.exceptions import (
    AllProvidersFailedError,
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
    AnthropicClient,
    BaseLLMClient,
    ChatMessage,
    ChatResponse,
    ChatStreamChunk,
    FallbackCandidate,
    FallbackLLMClient,
    LLMFactory,
    Role,
    TokenUsage,
)
from common_framework.agents.hitl import (
    ApprovalDecision,
    ApprovalRequest,
    ApprovalResponse,
    AutoApprovalHook,
    BaseApprovalHook,
    CallableApprovalHook,
    QueueApprovalHook,
)
from common_framework.agents.memory import (
    BaseMemory,
    ConversationBufferMemory,
    VectorMemory,
)
from common_framework.agents.react import (
    AgentResponse,
    AgentStep,
    ReActAgent,
)
from common_framework.guardrails.moderation import (
    GuardrailResult,
    GuardrailViolation,
    InjectionThreatLevel,
    PromptGuardrail,
)
from common_framework.guardrails.sanitizer import (
    PIIEntity,
    PIISanitizer,
    PIIType,
    RedactionStrategy,
    SanitizationResult,
)
from common_framework.observability.cost import (
    CostRecord,
    ModelPricing,
    TokenCostTracker,
    global_cost_tracker,
)
from common_framework.observability.metrics import (
    MetricFamily,
    PrometheusRegistry,
    create_metrics_router,
    global_prometheus_registry,
)
from common_framework.observability.tracing import (
    InMemorySpanExporter,
    Span,
    TraceContext,
    get_current_trace_context,
    global_span_exporter,
    trace_span,
)
from common_framework.api.app import create_app
from common_framework.api.mcp import mount_mcp_to_fastapi
from common_framework.api.routes.rag import create_rag_router
from common_framework.mcp.client import (
    FrameworkMCPClient,
    MCPRemoteTool,
    create_mcp_client,
)
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
from common_framework.rag.rerankers import (
    BaseReranker,
    CohereReranker,
    LocalCrossEncoderReranker,
    RerankerFactory,
)
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

__version__ = "0.11.0"

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
    "AllProvidersFailedError",
    "BaseLLMClient",
    "AnthropicClient",
    "FallbackCandidate",
    "FallbackLLMClient",
    "ChatMessage",
    "ChatResponse",
    "ChatStreamChunk",
    "TokenUsage",
    "Role",
    "LLMFactory",
    "CachedLLMClient",
    "SemanticCache",
    "SemanticCacheEntry",
    "SemanticCacheHit",
    "SemanticCacheStats",
    "Tool",
    "ToolExecutionError",
    "tool",
    "ToolRegistry",
    "ToolNotFoundError",
    "global_tool_registry",
    "FrameworkMCPServer",
    "create_mcp_server",
    "FrameworkMCPClient",
    "create_mcp_client",
    "MCPRemoteTool",
    "create_app",
    "mount_mcp_to_fastapi",
    "create_rag_router",
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
    "BaseReranker",
    "LocalCrossEncoderReranker",
    "CohereReranker",
    "RerankerFactory",
    "ReActAgent",
    "AgentStep",
    "AgentResponse",
    "BaseMemory",
    "ConversationBufferMemory",
    "VectorMemory",
    "ApprovalDecision",
    "ApprovalRequest",
    "ApprovalResponse",
    "BaseApprovalHook",
    "AutoApprovalHook",
    "CallableApprovalHook",
    "QueueApprovalHook",
    "PIIType",
    "RedactionStrategy",
    "PIIEntity",
    "SanitizationResult",
    "PIISanitizer",
    "InjectionThreatLevel",
    "GuardrailViolation",
    "GuardrailResult",
    "PromptGuardrail",
    "ModelPricing",
    "CostRecord",
    "TokenCostTracker",
    "global_cost_tracker",
    "MetricFamily",
    "PrometheusRegistry",
    "create_metrics_router",
    "global_prometheus_registry",
    "TraceContext",
    "Span",
    "InMemorySpanExporter",
    "global_span_exporter",
    "trace_span",
    "get_current_trace_context",
]
