"""AI RAG Common Framework.

A lightweight, modular, and extensible framework designed to build
RAG systems, MCP (Model Context Protocol) servers, and AI microservices
with minimal boilerplate.
"""

from __future__ import annotations

import importlib
from typing import Any

from common_framework._version import __version__

_OPTIONAL_MODULES = frozenset(
    {
        "common_framework.api.app",
        "common_framework.api.mcp",
        "common_framework.api.routes.rag",
        "common_framework.mcp.client",
        "common_framework.mcp.server",
    }
)

_LAZY_IMPORTS: dict[str, tuple[str, str]] = {
    # Core config & deprecation
    "Settings": ("common_framework.core.config", "Settings"),
    "get_settings": ("common_framework.core.config", "get_settings"),
    "FrameworkDeprecationWarning": ("common_framework.core.deprecation", "FrameworkDeprecationWarning"),
    "deprecated": ("common_framework.core.deprecation", "deprecated"),
    "warn_deprecated_parameter": ("common_framework.core.deprecation", "warn_deprecated_parameter"),
    # Core cache & security
    "CachedLLMClient": ("common_framework.core.cache", "CachedLLMClient"),
    "SemanticCache": ("common_framework.core.cache", "SemanticCache"),
    "SemanticCacheEntry": ("common_framework.core.cache", "SemanticCacheEntry"),
    "SemanticCacheHit": ("common_framework.core.cache", "SemanticCacheHit"),
    "SemanticCacheStats": ("common_framework.core.cache", "SemanticCacheStats"),
    "validate_provider_base_url": ("common_framework.core.security", "validate_provider_base_url"),
    # Core exceptions
    "AllProvidersFailedError": ("common_framework.core.exceptions", "AllProvidersFailedError"),
    "AuthenticationError": ("common_framework.core.exceptions", "AuthenticationError"),
    "ConfigurationError": ("common_framework.core.exceptions", "ConfigurationError"),
    "FrameworkError": ("common_framework.core.exceptions", "FrameworkError"),
    "LLMError": ("common_framework.core.exceptions", "LLMError"),
    "LLMProviderError": ("common_framework.core.exceptions", "LLMProviderError"),
    "MCPClientError": ("common_framework.core.exceptions", "MCPClientError"),
    "MCPConnectionError": ("common_framework.core.exceptions", "MCPConnectionError"),
    "MCPToolCallError": ("common_framework.core.exceptions", "MCPToolCallError"),
    "ModelNotFoundError": ("common_framework.core.exceptions", "ModelNotFoundError"),
    "RateLimitError": ("common_framework.core.exceptions", "RateLimitError"),
    "StructuredOutputError": ("common_framework.core.exceptions", "StructuredOutputError"),
    # Core LLM
    "AnthropicClient": ("common_framework.core.llm", "AnthropicClient"),
    "BaseLLMClient": ("common_framework.core.llm", "BaseLLMClient"),
    "ChatMessage": ("common_framework.core.llm", "ChatMessage"),
    "ChatResponse": ("common_framework.core.llm", "ChatResponse"),
    "ChatStreamChunk": ("common_framework.core.llm", "ChatStreamChunk"),
    "FallbackCandidate": ("common_framework.core.llm", "FallbackCandidate"),
    "FallbackLLMClient": ("common_framework.core.llm", "FallbackLLMClient"),
    "LLMFactory": ("common_framework.core.llm", "LLMFactory"),
    "Role": ("common_framework.core.llm", "Role"),
    "TokenUsage": ("common_framework.core.llm", "TokenUsage"),
    # Agents
    "ApprovalDecision": ("common_framework.agents.hitl", "ApprovalDecision"),
    "ApprovalRequest": ("common_framework.agents.hitl", "ApprovalRequest"),
    "ApprovalResponse": ("common_framework.agents.hitl", "ApprovalResponse"),
    "AutoApprovalHook": ("common_framework.agents.hitl", "AutoApprovalHook"),
    "BaseApprovalHook": ("common_framework.agents.hitl", "BaseApprovalHook"),
    "CallableApprovalHook": ("common_framework.agents.hitl", "CallableApprovalHook"),
    "QueueApprovalHook": ("common_framework.agents.hitl", "QueueApprovalHook"),
    "BaseMemory": ("common_framework.agents.memory", "BaseMemory"),
    "ConversationBufferMemory": ("common_framework.agents.memory", "ConversationBufferMemory"),
    "VectorMemory": ("common_framework.agents.memory", "VectorMemory"),
    "AgentResponse": ("common_framework.agents.react", "AgentResponse"),
    "AgentStep": ("common_framework.agents.react", "AgentStep"),
    "ReActAgent": ("common_framework.agents.react", "ReActAgent"),
    "AgentBus": ("common_framework.agents.supervisor", "AgentBus"),
    "AgentSupervisor": ("common_framework.agents.supervisor", "AgentSupervisor"),
    "DelegationStep": ("common_framework.agents.supervisor", "DelegationStep"),
    "SupervisorResponse": ("common_framework.agents.supervisor", "SupervisorResponse"),
    "END": ("common_framework.agents.workflow", "END"),
    "START": ("common_framework.agents.workflow", "START"),
    "StateGraph": ("common_framework.agents.workflow", "StateGraph"),
    "WorkflowResult": ("common_framework.agents.workflow", "WorkflowResult"),
    "WorkflowStep": ("common_framework.agents.workflow", "WorkflowStep"),
    "BaseCheckpointSaver": ("common_framework.agents.checkpoints", "BaseCheckpointSaver"),
    "Checkpoint": ("common_framework.agents.checkpoints", "Checkpoint"),
    "SQLiteCheckpointSaver": ("common_framework.agents.checkpoints", "SQLiteCheckpointSaver"),
    # Eval
    "HallucinationDetector": ("common_framework.eval.hallucination", "HallucinationDetector"),
    "HallucinationReport": ("common_framework.eval.hallucination", "HallucinationReport"),
    "SentenceClaim": ("common_framework.eval.hallucination", "SentenceClaim"),
    "RAGTriadEvaluator": ("common_framework.eval.triad", "RAGTriadEvaluator"),
    "RAGTriadScore": ("common_framework.eval.triad", "RAGTriadScore"),
    # Guardrails
    "GuardrailResult": ("common_framework.guardrails.moderation", "GuardrailResult"),
    "GuardrailViolation": ("common_framework.guardrails.moderation", "GuardrailViolation"),
    "InjectionThreatLevel": ("common_framework.guardrails.moderation", "InjectionThreatLevel"),
    "PromptGuardrail": ("common_framework.guardrails.moderation", "PromptGuardrail"),
    "PIIEntity": ("common_framework.guardrails.sanitizer", "PIIEntity"),
    "PIISanitizer": ("common_framework.guardrails.sanitizer", "PIISanitizer"),
    "PIIType": ("common_framework.guardrails.sanitizer", "PIIType"),
    "RedactionStrategy": ("common_framework.guardrails.sanitizer", "RedactionStrategy"),
    "SanitizationResult": ("common_framework.guardrails.sanitizer", "SanitizationResult"),
    # Observability
    "CostRecord": ("common_framework.observability.cost", "CostRecord"),
    "ModelPricing": ("common_framework.observability.cost", "ModelPricing"),
    "TokenCostTracker": ("common_framework.observability.cost", "TokenCostTracker"),
    "get_global_cost_tracker": ("common_framework.observability.cost", "get_global_cost_tracker"),
    "global_cost_tracker": ("common_framework.observability.cost", "global_cost_tracker"),
    "MetricFamily": ("common_framework.observability.metrics", "MetricFamily"),
    "PrometheusRegistry": ("common_framework.observability.metrics", "PrometheusRegistry"),
    "create_metrics_router": ("common_framework.observability.metrics", "create_metrics_router"),
    "global_prometheus_registry": ("common_framework.observability.metrics", "global_prometheus_registry"),
    "InMemorySpanExporter": ("common_framework.observability.tracing", "InMemorySpanExporter"),
    "Span": ("common_framework.observability.tracing", "Span"),
    "TraceContext": ("common_framework.observability.tracing", "TraceContext"),
    "get_current_trace_context": ("common_framework.observability.tracing", "get_current_trace_context"),
    "global_span_exporter": ("common_framework.observability.tracing", "global_span_exporter"),
    "trace_span": ("common_framework.observability.tracing", "trace_span"),
    # API (optional [api] extra)
    "create_app": ("common_framework.api.app", "create_app"),
    "mount_mcp_to_fastapi": ("common_framework.api.mcp", "mount_mcp_to_fastapi"),
    "create_rag_router": ("common_framework.api.routes.rag", "create_rag_router"),
    # MCP (optional [mcp] extra)
    "FrameworkMCPClient": ("common_framework.mcp.client", "FrameworkMCPClient"),
    "MCPRemoteTool": ("common_framework.mcp.client", "MCPRemoteTool"),
    "create_mcp_client": ("common_framework.mcp.client", "create_mcp_client"),
    "FrameworkMCPServer": ("common_framework.mcp.server", "FrameworkMCPServer"),
    "create_mcp_server": ("common_framework.mcp.server", "create_mcp_server"),
    # RAG
    "BaseLoader": ("common_framework.rag.loaders", "BaseLoader"),
    "CSVLoader": ("common_framework.rag.loaders", "CSVLoader"),
    "DirectoryLoader": ("common_framework.rag.loaders", "DirectoryLoader"),
    "HTMLLoader": ("common_framework.rag.loaders", "HTMLLoader"),
    "MarkdownLoader": ("common_framework.rag.loaders", "MarkdownLoader"),
    "PDFLoader": ("common_framework.rag.loaders", "PDFLoader"),
    "TextLoader": ("common_framework.rag.loaders", "TextLoader"),
    "BM25Index": ("common_framework.rag.bm25", "BM25Index"),
    "reciprocal_rank_fusion": ("common_framework.rag.bm25", "reciprocal_rank_fusion"),
    "BaseEmbeddingProvider": ("common_framework.rag.embeddings", "BaseEmbeddingProvider"),
    "EmbeddingFactory": ("common_framework.rag.embeddings", "EmbeddingFactory"),
    "MockEmbeddingProvider": ("common_framework.rag.embeddings", "MockEmbeddingProvider"),
    "OpenAICompatibleEmbeddingProvider": (
        "common_framework.rag.embeddings",
        "OpenAICompatibleEmbeddingProvider",
    ),
    "RAGEngine": ("common_framework.rag.engine", "RAGEngine"),
    "Document": ("common_framework.rag.models", "Document"),
    "RAGResponse": ("common_framework.rag.models", "RAGResponse"),
    "SearchResult": ("common_framework.rag.models", "SearchResult"),
    "BaseReranker": ("common_framework.rag.rerankers", "BaseReranker"),
    "CohereReranker": ("common_framework.rag.rerankers", "CohereReranker"),
    "LocalCrossEncoderReranker": ("common_framework.rag.rerankers", "LocalCrossEncoderReranker"),
    "RerankerFactory": ("common_framework.rag.rerankers", "RerankerFactory"),
    "BaseTextSplitter": ("common_framework.rag.splitters", "BaseTextSplitter"),
    "CharacterTextSplitter": ("common_framework.rag.splitters", "CharacterTextSplitter"),
    "RecursiveTextSplitter": ("common_framework.rag.splitters", "RecursiveTextSplitter"),
    "BaseRetrievalStrategy": ("common_framework.rag.strategies", "BaseRetrievalStrategy"),
    "HyDEStrategy": ("common_framework.rag.strategies", "HyDEStrategy"),
    "MultiQueryStrategy": ("common_framework.rag.strategies", "MultiQueryStrategy"),
    "multi_list_rrf": ("common_framework.rag.strategies", "multi_list_rrf"),
    "BaseVectorStore": ("common_framework.rag.vectorstore", "BaseVectorStore"),
    "InMemoryVectorStore": ("common_framework.rag.vectorstore", "InMemoryVectorStore"),
    "cosine_similarity": ("common_framework.rag.vectorstore", "cosine_similarity"),
    "SQLiteVectorStore": ("common_framework.rag.storage.sqlite", "SQLiteVectorStore"),
    # Tools
    "Tool": ("common_framework.tools.base", "Tool"),
    "ToolExecutionError": ("common_framework.tools.base", "ToolExecutionError"),
    "tool": ("common_framework.tools.base", "tool"),
    "CodeInterpreter": ("common_framework.tools.interpreter", "CodeInterpreter"),
    "ExecutionResult": ("common_framework.tools.interpreter", "ExecutionResult"),
    "code_interpreter_tool": ("common_framework.tools.interpreter", "code_interpreter_tool"),
    "default_code_interpreter": ("common_framework.tools.interpreter", "default_code_interpreter"),
    "ToolNotFoundError": ("common_framework.tools.registry", "ToolNotFoundError"),
    "ToolRegistry": ("common_framework.tools.registry", "ToolRegistry"),
    "global_tool_registry": ("common_framework.tools.registry", "global_tool_registry"),
    # CLI
    "DiagnosticCheck": ("common_framework.cli", "DiagnosticCheck"),
    "DoctorReport": ("common_framework.cli", "DoctorReport"),
    "run_doctor": ("common_framework.cli", "run_doctor"),
    "CLIPlaygroundREPL": ("common_framework.cli", "CLIPlaygroundREPL"),
    "scaffold_project": ("common_framework.cli", "scaffold_project"),
    "create_parser": ("common_framework.cli", "create_parser"),
    "cli_main": ("common_framework.cli", "main"),
}

__all__ = ["__version__", *_LAZY_IMPORTS.keys()]


def __getattr__(name: str) -> Any:
    target = _LAZY_IMPORTS.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_path, attr_name = target
    try:
        module = importlib.import_module(module_path)
    except ImportError:
        if module_path in _OPTIONAL_MODULES:
            globals()[name] = None
            return None
        raise
    value = getattr(module, attr_name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(__all__)
