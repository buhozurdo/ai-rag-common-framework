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
from common_framework.mcp.server import FrameworkMCPServer, create_mcp_server
from common_framework.tools.base import Tool, ToolExecutionError, tool
from common_framework.tools.registry import (
    ToolNotFoundError,
    ToolRegistry,
    global_tool_registry,
)

__version__ = "0.2.0-alpha"

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
]
