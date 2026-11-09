"""Model Context Protocol (MCP) bidirectional client implementation."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from contextlib import AsyncExitStack
from typing import Any, Callable, Optional, Sequence, Union
from pydantic import BaseModel, create_model

from common_framework.core.exceptions import (
    MCPClientError,
    MCPConnectionError,
    MCPToolCallError,
)
from common_framework.tools.base import Tool
from common_framework.tools.registry import ToolRegistry, global_tool_registry

logger = logging.getLogger("common_framework.mcp.client")


class MCPRemoteTool(Tool):
    """Dynamic Tool adapter delegating execution to a remote MCP server."""

    def __init__(
        self,
        name: str,
        description: str,
        input_schema: dict[str, Any],
        caller_fn: Callable[..., Any],
    ) -> None:
        self.name = name
        self.description = description or f"Remote MCP Tool: {name}"
        self.fn = caller_fn
        self.is_async = True
        self._parameters_schema = dict(input_schema or {})
        self._parameters_schema.pop("title", None)
        # Create dynamic Pydantic model for validation if properties are specified
        self._args_model = self._create_schema_model(self._parameters_schema)

    def _create_schema_model(self, schema: dict[str, Any]) -> type[BaseModel]:
        properties = schema.get("properties", {})
        required_fields = set(schema.get("required", []))
        fields: dict[str, tuple[Any, Any]] = {}

        for prop_name, prop_spec in properties.items():
            prop_type: Any = Any
            type_str = prop_spec.get("type")
            if type_str == "string":
                prop_type = str
            elif type_str == "integer":
                prop_type = int
            elif type_str == "number":
                prop_type = float
            elif type_str == "boolean":
                prop_type = bool
            elif type_str == "array":
                prop_type = list
            elif type_str == "object":
                prop_type = dict

            if prop_name in required_fields:
                fields[prop_name] = (prop_type, ...)
            else:
                default_val = prop_spec.get("default", None)
                fields[prop_name] = (Optional[prop_type], default_val)

        model_name = f"{self.name.capitalize()}Input"
        return create_model(model_name, **fields)

    async def execute(self, **kwargs: Any) -> Any:
        return await self.fn(**kwargs)

    def to_openai_schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self._parameters_schema,
            },
        }


class FrameworkMCPClient:
    """Universal asynchronous Model Context Protocol (MCP 2.x) client.

    Supports connecting to external MCP servers via standard I/O (stdio sub-processes)
    or Server-Sent Events (SSE / HTTP), discovering remote capabilities, and mounting
    remote tools directly into the framework's ToolRegistry.
    """

    def __init__(
        self,
        command: Optional[str] = None,
        args: Optional[Sequence[str]] = None,
        env: Optional[dict[str, str]] = None,
        url: Optional[str] = None,
        headers: Optional[dict[str, str]] = None,
        client_name: str = "ai-rag-common-framework-client",
        client_version: str = "0.7.0",
        read_stream: Any = None,
        write_stream: Any = None,
    ) -> None:
        self.command = command
        self.args = list(args or [])
        self.env = env
        self.url = url
        self.headers = headers or {}
        self.client_name = client_name
        self.client_version = client_version
        self._read_stream = read_stream
        self._write_stream = write_stream

        self._exit_stack: Optional[AsyncExitStack] = None
        self._session: Any = None
        self._is_connected = False
        self._mounted_tools: list[Tool] = []

    @property
    def is_connected(self) -> bool:
        """Return True if currently connected to the remote MCP server."""
        return self._is_connected and self._session is not None

    async def connect(self) -> FrameworkMCPClient:
        """Establish connection and perform initial protocol handshake with the MCP server."""
        if self._is_connected:
            return self

        self._exit_stack = AsyncExitStack()

        try:
            # Custom mock/direct stream mode (for tests and in-process testing)
            if self._read_stream is not None and self._write_stream is not None:
                from mcp import ClientSession

                self._session = await self._exit_stack.enter_async_context(
                    ClientSession(self._read_stream, self._write_stream)
                )

            # stdio transport (Subprocess)
            elif self.command:
                from mcp import ClientSession, StdioServerParameters
                from mcp.client.stdio import stdio_client

                full_env = dict(os.environ)
                if self.env:
                    full_env.update(self.env)

                server_params = StdioServerParameters(
                    command=self.command,
                    args=self.args,
                    env=full_env,
                )
                stdio_transport = await self._exit_stack.enter_async_context(
                    stdio_client(server_params)
                )
                read_stream, write_stream = stdio_transport
                self._session = await self._exit_stack.enter_async_context(
                    ClientSession(read_stream, write_stream)
                )

            # SSE / HTTP transport
            elif self.url:
                from mcp import ClientSession
                from mcp.client.sse import sse_client

                sse_transport = await self._exit_stack.enter_async_context(
                    sse_client(self.url, headers=self.headers)
                )
                read_stream, write_stream = sse_transport
                self._session = await self._exit_stack.enter_async_context(
                    ClientSession(read_stream, write_stream)
                )
            else:
                raise MCPConnectionError(
                    "FrameworkMCPClient requires either 'command' (stdio) or 'url' (SSE/HTTP) to connect."
                )

            # Perform MCP initialize handshake
            await self._session.initialize()
            self._is_connected = True
            logger.info("Successfully connected to MCP server via %s", "stdio" if self.command else "SSE")
            return self

        except Exception as exc:
            await self.aclose()
            raise MCPConnectionError(f"Failed to connect to MCP server: {exc}") from exc

    async def aclose(self) -> None:
        """Close connection to the remote MCP server and clean up process/network resources."""
        self._is_connected = False
        self._session = None
        if self._exit_stack is not None:
            try:
                await self._exit_stack.aclose()
            except Exception as exc:
                logger.debug("Error while closing MCP client exit stack: %s", exc)
            finally:
                self._exit_stack = None

    async def list_tools(self) -> list[dict[str, Any]]:
        """Retrieve list of available tools exposed by the connected MCP server."""
        self._ensure_connected()
        try:
            result = await self._session.list_tools()
            tools = []
            for t in result.tools:
                tools.append({
                    "name": t.name,
                    "description": t.description or "",
                    "inputSchema": t.inputSchema or {},
                })
            return tools
        except Exception as exc:
            raise MCPClientError(f"Failed to list remote MCP tools: {exc}") from exc

    async def call_tool(self, name: str, arguments: Optional[dict[str, Any]] = None) -> Any:
        """Call a remote tool on the MCP server and return parsed result content."""
        self._ensure_connected()
        args = arguments or {}
        try:
            res = await self._session.call_tool(name, args)
            if getattr(res, "isError", False):
                err_text = self._extract_content_text(res.content)
                raise MCPToolCallError(name, err_text or "Remote execution reported an error", details={"arguments": args})

            return self._extract_content_value(res.content)
        except MCPToolCallError:
            raise
        except Exception as exc:
            raise MCPToolCallError(name, str(exc), details={"arguments": args}) from exc

    async def list_resources(self) -> list[dict[str, Any]]:
        """Retrieve list of resources from the remote MCP server."""
        self._ensure_connected()
        try:
            result = await self._session.list_resources()
            return [
                {
                    "uri": str(r.uri),
                    "name": r.name,
                    "description": getattr(r, "description", None),
                    "mimeType": getattr(r, "mimeType", None),
                }
                for r in result.resources
            ]
        except Exception as exc:
            raise MCPClientError(f"Failed to list remote MCP resources: {exc}") from exc

    async def read_resource(self, uri: str) -> Any:
        """Read content of a specific resource by URI."""
        self._ensure_connected()
        try:
            from pydantic import AnyUrl
            res = await self._session.read_resource(AnyUrl(uri) if not isinstance(uri, AnyUrl) else uri)
            return [
                {
                    "uri": str(c.uri),
                    "mimeType": getattr(c, "mimeType", None),
                    "text": getattr(c, "text", None),
                    "blob": getattr(c, "blob", None),
                }
                for c in res.contents
            ]
        except Exception as exc:
            raise MCPClientError(f"Failed to read resource '{uri}': {exc}") from exc

    async def list_prompts(self) -> list[dict[str, Any]]:
        """Retrieve list of prompt templates available on the remote server."""
        self._ensure_connected()
        try:
            result = await self._session.list_prompts()
            return [
                {
                    "name": p.name,
                    "description": p.description,
                    "arguments": [
                        {
                            "name": a.name,
                            "description": a.description,
                            "required": a.required,
                        }
                        for a in getattr(p, "arguments", [])
                    ],
                }
                for p in result.prompts
            ]
        except Exception as exc:
            raise MCPClientError(f"Failed to list remote prompts: {exc}") from exc

    async def get_prompt(self, name: str, arguments: Optional[dict[str, str]] = None) -> list[dict[str, Any]]:
        """Retrieve rendered prompt messages for a specific prompt template."""
        self._ensure_connected()
        try:
            res = await self._session.get_prompt(name, arguments or {})
            return [
                {
                    "role": getattr(m, "role", "user"),
                    "content": getattr(m.content, "text", str(m.content)),
                }
                for m in res.messages
            ]
        except Exception as exc:
            raise MCPClientError(f"Failed to get prompt '{name}': {exc}") from exc

    async def mount_tools(
        self,
        registry: Optional[ToolRegistry] = None,
        prefix: str = "",
        register_globally: bool = False,
    ) -> list[Tool]:
        """Discover remote tools and register them into the framework's ToolRegistry.

        Each mounted tool is encapsulated as a native Tool object whose execution
        automatically forwards to `self.call_tool(...)`.

        Args:
            registry: Target ToolRegistry. Defaults to global_tool_registry if None and register_globally is True.
            prefix: Optional naming prefix (e.g., 'github_' -> 'github_create_issue').
            register_globally: Whether to register into global_tool_registry.

        Returns:
            List of dynamically created and mounted Tool instances.
        """
        if not self._is_connected:
            await self.connect()

        target_registry = registry
        if target_registry is None:
            target_registry = global_tool_registry if register_globally else ToolRegistry()

        remote_tool_specs = await self.list_tools()
        mounted: list[Tool] = []

        for spec in remote_tool_specs:
            remote_name = spec["name"]
            tool_name = f"{prefix}{remote_name}"
            description = spec.get("description", "")
            input_schema = spec.get("inputSchema", {})

            # Create closure for remote tool invocation
            def _make_caller(r_name: str) -> Callable[..., Any]:
                async def _caller(**kwargs: Any) -> Any:
                    return await self.call_tool(r_name, kwargs)
                return _caller

            tool_obj = MCPRemoteTool(
                name=tool_name,
                description=description,
                input_schema=input_schema,
                caller_fn=_make_caller(remote_name),
            )
            target_registry.register(tool_obj)
            mounted.append(tool_obj)

        self._mounted_tools.extend(mounted)
        logger.info("Mounted %d tools from remote MCP server (prefix=%r)", len(mounted), prefix)
        return mounted

    def _ensure_connected(self) -> None:
        if not self._is_connected or self._session is None:
            raise MCPConnectionError("FrameworkMCPClient is not connected. Call 'await client.connect()' first.")

    def _extract_content_text(self, content: Any) -> str:
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = []
            for item in content:
                text = getattr(item, "text", None)
                if text is not None:
                    parts.append(text)
                elif isinstance(item, dict) and "text" in item:
                    parts.append(item["text"])
                else:
                    parts.append(str(item))
            return "\n".join(parts)
        return str(content)

    def _extract_content_value(self, content: Any) -> Any:
        text = self._extract_content_text(content)
        # Attempt JSON decoding if the output is formatted as JSON
        if text.startswith("{") or text.startswith("["):
            try:
                return json.loads(text)
            except Exception:
                pass
        return text

    async def __aenter__(self) -> FrameworkMCPClient:
        return await self.connect()

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        await self.aclose()

    def __repr__(self) -> str:
        mode = f"command={self.command!r}" if self.command else f"url={self.url!r}"
        status = "connected" if self._is_connected else "disconnected"
        return f"<FrameworkMCPClient {mode} status={status}>"


def create_mcp_client(
    command: Optional[str] = None,
    args: Optional[Sequence[str]] = None,
    env: Optional[dict[str, str]] = None,
    url: Optional[str] = None,
    headers: Optional[dict[str, str]] = None,
) -> FrameworkMCPClient:
    """Convenience factory to instantiate a FrameworkMCPClient."""
    return FrameworkMCPClient(
        command=command,
        args=args,
        env=env,
        url=url,
        headers=headers,
    )
