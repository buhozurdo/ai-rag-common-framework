"""Unit tests for tool definitions, schema generation, and tool registry."""

import pytest
from pydantic import BaseModel, Field

from common_framework.tools.base import Tool, ToolExecutionError, tool
from common_framework.tools.registry import (
    ToolNotFoundError,
    ToolRegistry,
    global_tool_registry,
)


def test_sync_tool_creation_and_schema():
    """Verify synchronous tool schema generation and execution."""
    @tool
    def add(a: int, b: int = 10) -> int:
        """Add two integers together."""
        return a + b

    assert isinstance(add, Tool)
    assert add.name == "add"
    assert add.description == "Add two integers together."
    assert add.is_async is False

    schema = add.parameters_schema
    assert schema["type"] == "object"
    assert "a" in schema["properties"]
    assert "b" in schema["properties"]
    assert "a" in schema["required"]
    assert "b" not in schema["required"]

    openai_schema = add.to_openai_schema()
    assert openai_schema["type"] == "function"
    assert openai_schema["function"]["name"] == "add"


@pytest.mark.asyncio
async def test_tool_execution():
    """Verify direct invocation and argument coercion."""
    @tool
    def multiply(x: int, y: int) -> int:
        return x * y

    # Named kwargs
    result = await multiply(x=3, y=4)
    assert result == 12

    # Positional args
    result_pos = await multiply(5, 6)
    assert result_pos == 30

    # Type coercion from string
    result_coerced = await multiply(x="7", y="8")
    assert result_coerced == 56


@pytest.mark.asyncio
async def test_async_tool_execution():
    """Verify asynchronous tool execution."""
    @tool(name="fetch_status", description="Simulate async fetch")
    async def fetch_status(service_id: str) -> str:
        return f"status-ok-{service_id}"

    assert fetch_status.is_async is True
    assert fetch_status.name == "fetch_status"
    assert fetch_status.description == "Simulate async fetch"

    result = await fetch_status(service_id="svc-100")
    assert result == "status-ok-svc-100"


@pytest.mark.asyncio
async def test_tool_validation_error():
    """Verify ToolExecutionError is raised when required arguments are invalid or missing."""
    @tool
    def strict_func(code: int) -> str:
        return f"Code: {code}"

    with pytest.raises(ToolExecutionError):
        await strict_func(code="invalid-not-an-int")


@pytest.mark.asyncio
async def test_tool_registry():
    """Verify ToolRegistry operations."""
    registry = ToolRegistry()

    @tool
    def echo(text: str) -> str:
        return text

    registry.register(echo)
    assert "echo" in registry
    assert len(registry) == 1

    fetched = registry.get("echo")
    assert fetched.name == "echo"

    # Execution via registry
    res = await registry.execute("echo", text="Framework Registry Test")
    assert res == "Framework Registry Test"

    # OpenAI schemas
    schemas = registry.to_openai_schemas()
    assert len(schemas) == 1
    assert schemas[0]["function"]["name"] == "echo"

    # Unregister
    registry.unregister("echo")
    assert "echo" not in registry
    with pytest.raises(ToolNotFoundError):
        registry.get("echo")
