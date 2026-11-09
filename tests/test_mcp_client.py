"""Unit tests for FrameworkMCPClient and remote MCP tool mounting."""

from __future__ import annotations

import sys
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from common_framework.core.exceptions import (
    MCPClientError,
    MCPConnectionError,
    MCPToolCallError,
)
from common_framework.mcp.client import (
    FrameworkMCPClient,
    MCPRemoteTool,
    create_mcp_client,
)
from common_framework.tools.registry import ToolRegistry


@pytest.fixture
def mock_session() -> MagicMock:
    session = MagicMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=None)
    session.initialize = AsyncMock()

    # Mock tools
    tool1 = MagicMock()
    tool1.name = "calculate_tax"
    tool1.description = "Calculate tax on amount"
    tool1.inputSchema = {
        "type": "object",
        "properties": {
            "amount": {"type": "number", "description": "Base amount"},
            "rate": {"type": "number", "default": 0.16},
        },
        "required": ["amount"],
    }

    tool2 = MagicMock()
    tool2.name = "echo"
    tool2.description = "Echo message back"
    tool2.inputSchema = {
        "type": "object",
        "properties": {
            "msg": {"type": "string"},
        },
        "required": ["msg"],
    }

    list_tools_result = MagicMock()
    list_tools_result.tools = [tool1, tool2]
    session.list_tools = AsyncMock(return_value=list_tools_result)

    # Mock tool call
    content_block = MagicMock()
    content_block.text = '{"tax": 16.0, "total": 116.0}'
    call_result = MagicMock()
    call_result.isError = False
    call_result.content = [content_block]
    session.call_tool = AsyncMock(return_value=call_result)

    # Mock resources
    res1 = MagicMock()
    res1.uri = "memo://notes/system"
    res1.name = "System Notes"
    res1.description = "Important system prompt notes"
    res1.mimeType = "text/plain"
    list_res_result = MagicMock()
    list_res_result.resources = [res1]
    session.list_resources = AsyncMock(return_value=list_res_result)

    res_content = MagicMock()
    res_content.uri = "memo://notes/system"
    res_content.text = "All systems operational."
    res_content.mimeType = "text/plain"
    read_res_result = MagicMock()
    read_res_result.contents = [res_content]
    session.read_resource = AsyncMock(return_value=read_res_result)

    # Mock prompts
    prompt1 = MagicMock()
    prompt1.name = "code_review"
    prompt1.description = "Review python code"
    arg1 = MagicMock()
    arg1.name = "code"
    arg1.description = "Code snippet"
    arg1.required = True
    prompt1.arguments = [arg1]
    list_prompts_result = MagicMock()
    list_prompts_result.prompts = [prompt1]
    session.list_prompts = AsyncMock(return_value=list_prompts_result)

    msg1 = MagicMock()
    msg1.role = "user"
    msg1.content.text = "Please review: def test(): pass"
    get_prompt_result = MagicMock()
    get_prompt_result.messages = [msg1]
    session.get_prompt = AsyncMock(return_value=get_prompt_result)

    return session


@pytest.mark.asyncio
async def test_mcp_client_validation_missing_endpoint():
    client = FrameworkMCPClient()
    with pytest.raises(MCPConnectionError) as exc_info:
        await client.connect()
    assert "requires either 'command' (stdio) or 'url' (SSE/HTTP)" in str(exc_info.value)


@pytest.mark.asyncio
async def test_mcp_client_disconnected_guard():
    client = FrameworkMCPClient(url="http://localhost:9000/sse")
    assert not client.is_connected
    with pytest.raises(MCPConnectionError) as exc_info:
        await client.list_tools()
    assert "is not connected" in str(exc_info.value)


@pytest.mark.asyncio
async def test_mcp_remote_tool_schema_and_execution():
    async def mock_remote_caller(amount: float, rate: float = 0.16) -> dict[str, float]:
        return {"tax": round(amount * rate, 2), "total": round(amount * (1.0 + rate), 2)}

    schema = {
        "type": "object",
        "properties": {
            "amount": {"type": "number"},
            "rate": {"type": "number", "default": 0.16},
        },
        "required": ["amount"],
    }

    tool = MCPRemoteTool(
        name="calculate_tax",
        description="Compute sales tax",
        input_schema=schema,
        caller_fn=mock_remote_caller,
    )

    assert tool.name == "calculate_tax"
    assert tool.is_async is True
    openai_schema = tool.to_openai_schema()
    assert openai_schema["type"] == "function"
    assert openai_schema["function"]["name"] == "calculate_tax"

    result = await tool.execute(amount=100.0)
    assert result == {"tax": 16.0, "total": 116.0}


@pytest.mark.asyncio
async def test_mcp_client_mock_session_operations(mock_session):
    client = FrameworkMCPClient(read_stream=object(), write_stream=object())

    with patch("mcp.ClientSession", return_value=mock_session):
        async with client:
            assert client.is_connected

            # 1. List tools
            tools = await client.list_tools()
            assert len(tools) == 2
            assert tools[0]["name"] == "calculate_tax"
            assert tools[1]["name"] == "echo"

            # 2. Call tool
            res = await client.call_tool("calculate_tax", {"amount": 100.0})
            assert res == {"tax": 16.0, "total": 116.0}
            mock_session.call_tool.assert_awaited_once_with("calculate_tax", {"amount": 100.0})

            # 3. Resources
            resources = await client.list_resources()
            assert len(resources) == 1
            assert resources[0]["uri"] == "memo://notes/system"

            res_content = await client.read_resource("memo://notes/system")
            assert len(res_content) == 1
            assert res_content[0]["text"] == "All systems operational."

            # 4. Prompts
            prompts = await client.list_prompts()
            assert len(prompts) == 1
            assert prompts[0]["name"] == "code_review"

            prompt_msgs = await client.get_prompt("code_review", {"code": "x = 1"})
            assert len(prompt_msgs) == 1
            assert "def test(): pass" in prompt_msgs[0]["content"]

    assert not client.is_connected


@pytest.mark.asyncio
async def test_mcp_client_mount_tools_to_registry(mock_session):
    client = FrameworkMCPClient(read_stream=object(), write_stream=object())
    registry = ToolRegistry()

    with patch("mcp.ClientSession", return_value=mock_session):
        async with client:
            mounted = await client.mount_tools(registry=registry, prefix="remote_")
            assert len(mounted) == 2
            assert "remote_calculate_tax" in registry
            assert "remote_echo" in registry

            # Execute mounted tool through ToolRegistry
            tool_res = await registry.execute("remote_calculate_tax", amount=200.0)
            assert tool_res == {"tax": 16.0, "total": 116.0}


@pytest.mark.asyncio
async def test_mcp_client_tool_call_error_propagation(mock_session):
    err_content = MagicMock()
    err_content.text = "Database connection timed out"
    err_result = MagicMock()
    err_result.isError = True
    err_result.content = [err_content]
    mock_session.call_tool = AsyncMock(return_value=err_result)

    client = FrameworkMCPClient(read_stream=object(), write_stream=object())

    with patch("mcp.ClientSession", return_value=mock_session):
        async with client:
            with pytest.raises(MCPToolCallError) as exc_info:
                await client.call_tool("failing_tool", {})
            assert "Database connection timed out" in str(exc_info.value)
            assert exc_info.value.tool_name == "failing_tool"


@pytest.mark.asyncio
async def test_create_mcp_client_factory():
    client = create_mcp_client(command=sys.executable, args=["-m", "http.server"])
    assert client.command == sys.executable
    assert client.args == ["-m", "http.server"]
    assert "command=" in repr(client)
