"""MCP module integrating the Model Context Protocol."""

from common_framework.mcp.client import (
    FrameworkMCPClient,
    MCPRemoteTool,
    create_mcp_client,
)
from common_framework.mcp.server import FrameworkMCPServer, create_mcp_server

__all__ = [
    "FrameworkMCPServer",
    "create_mcp_server",
    "FrameworkMCPClient",
    "create_mcp_client",
    "MCPRemoteTool",
]

