"""Unit tests for Model Context Protocol (MCP) server adapter."""

import pytest
from common_framework.mcp.server import FrameworkMCPServer, create_mcp_server
from common_framework.tools.base import tool
from common_framework.tools.registry import ToolRegistry


@pytest.mark.asyncio
async def test_mcp_server_decorator_registration():
    """Verify tool registration via @server.tool decorator."""
    custom_registry = ToolRegistry()
    server = FrameworkMCPServer(name="test-mcp-server", tool_registry=custom_registry)

    @server.tool(name="calc_add", description="Calculate sum of numbers")
    def calc_add(x: int, y: int) -> int:
        return x + y

    assert "calc_add" in custom_registry

    tools = await server.list_tools()
    tool_names = [t.name for t in tools]
    assert "calc_add" in tool_names


@pytest.mark.asyncio
async def test_mcp_server_add_existing_tool():
    """Verify adding an existing standalone Tool to the MCP server."""
    custom_registry = ToolRegistry()
    server = create_mcp_server(name="analytics-server")
    server.registry = custom_registry

    @tool(name="metrics_count", description="Count metrics")
    def metrics_count(prefix: str) -> int:
        return len(prefix)

    server.add_tool(metrics_count)

    tools = await server.list_tools()
    tool_names = [t.name for t in tools]
    assert "metrics_count" in tool_names


def test_mcp_server_sse_app_creation():
    """Verify ASGI SSE application can be generated from MCP server."""
    server = create_mcp_server(name="web-mcp-server")
    app = server.get_sse_app()
    assert app is not None
