"""Model Context Protocol (MCP) server adapter and runner."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable, Optional, Union
from mcp.server.mcpserver import MCPServer

from common_framework.tools.base import Tool, tool
from common_framework.tools.registry import ToolRegistry, global_tool_registry

logger = logging.getLogger("common_framework.mcp")


class FrameworkMCPServer:
    """Wrapper and manager for Model Context Protocol (MCP) servers with dual-use tool support."""

    def __init__(
        self,
        name: str = "common-framework-mcp",
        version: Optional[str] = None,
        instructions: Optional[str] = None,
        tool_registry: Optional[ToolRegistry] = None,
    ) -> None:
        self.name = name
        self.version = version or "0.4.0"
        self.instructions = instructions
        self.registry = tool_registry if tool_registry is not None else global_tool_registry
        self.underlying_server = MCPServer(
            name=self.name,
            version=self.version,
            instructions=self.instructions,
        )

    def tool(
        self,
        fn_or_name: Union[Callable[..., Any], str, None] = None,
        *,
        name: Optional[str] = None,
        description: Optional[str] = None,
        register_globally: bool = True,
    ) -> Any:
        """Decorator to register a tool both in MCP and the framework's ToolRegistry.

        Usage:
            @mcp_server.tool
            def add(a: int, b: int) -> int: ...

            @mcp_server.tool(name="sum_numbers", description="Add numbers")
            def add(a: int, b: int) -> int: ...
        """
        def _register(fn: Callable[..., Any], tool_name: Optional[str], tool_desc: Optional[str]) -> Tool:
            t = Tool(fn, name=tool_name, description=tool_desc)
            if register_globally:
                self.registry.register(t)
            # Register in MCP server
            self.underlying_server.add_tool(
                fn=t.fn,
                name=t.name,
                description=t.description,
            )
            return t

        if callable(fn_or_name):
            return _register(fn_or_name, name, description)

        custom_name = name or (fn_or_name if isinstance(fn_or_name, str) else None)

        def decorator(fn: Callable[..., Any]) -> Tool:
            return _register(fn, custom_name, description)

        return decorator

    def add_tool(self, target: Tool | Callable[..., Any], register_globally: bool = True) -> Tool:
        """Attach an existing Tool or function to this MCP server."""
        t = target if isinstance(target, Tool) else Tool(target)
        if register_globally:
            self.registry.register(t)
        self.underlying_server.add_tool(
            fn=t.fn,
            name=t.name,
            description=t.description,
        )
        return t

    def add_tools_from_registry(self, registry: Optional[ToolRegistry] = None) -> None:
        """Batch-register all tools from a ToolRegistry into the MCP server."""
        source_registry = registry or self.registry
        for t in source_registry.list_tools():
            self.underlying_server.add_tool(
                fn=t.fn,
                name=t.name,
                description=t.description,
            )

    def resource(
        self,
        uri: str,
        *,
        name: Optional[str] = None,
        description: Optional[str] = None,
        mime_type: Optional[str] = None,
    ) -> Any:
        """Decorator to register an MCP Resource."""
        return self.underlying_server.resource(
            uri=uri,
            name=name,
            description=description,
            mime_type=mime_type,
        )

    def prompt(
        self,
        name: Optional[str] = None,
        description: Optional[str] = None,
    ) -> Any:
        """Decorator to register an MCP Prompt template."""
        return self.underlying_server.prompt(
            name=name,
            description=description,
        )

    async def list_tools(self) -> list[Any]:
        """List all tools exposed by the MCP server."""
        return await self.underlying_server.list_tools()

    async def list_resources(self) -> list[Any]:
        """List all resources exposed by the MCP server."""
        return await self.underlying_server.list_resources()

    async def list_prompts(self) -> list[Any]:
        """List all prompt templates exposed by the MCP server."""
        return await self.underlying_server.list_prompts()

    def run_stdio(self) -> None:
        """Run the MCP server over standard I/O (blocking). Ideal for Cursor, Claude Desktop, Antigravity."""
        asyncio.run(self.underlying_server.run_stdio_async())

    async def run_stdio_async(self) -> None:
        """Run the MCP server over standard I/O asynchronously."""
        await self.underlying_server.run_stdio_async()

    def run_sse(self, host: str = "127.0.0.1", port: int = 8000) -> None:
        """Run the MCP server over Server-Sent Events (SSE / HTTP)."""
        asyncio.run(self.underlying_server.run_sse_async(host=host, port=port))

    async def run_sse_async(self, host: str = "127.0.0.1", port: int = 8000) -> None:
        """Run the MCP server over Server-Sent Events asynchronously."""
        await self.underlying_server.run_sse_async(host=host, port=port)

    def get_sse_app(
        self,
        transport_security: Any = None,
        host: str = "127.0.0.1",
        disable_dns_protection: bool = False,
    ) -> Any:
        """Return the Starlette ASGI application for SSE mounting in FastAPI/ASGI servers.

        Args:
            transport_security: Explicit TransportSecuritySettings instance.
            host: Host interface to bind (default: '127.0.0.1').
            disable_dns_protection: Set to True to disable DNS rebinding protection.
                Defaults to False (secure default: DNS rebinding protection enabled).
        """
        if transport_security is None:
            try:
                from mcp.server.transport_security import TransportSecuritySettings

                if disable_dns_protection:
                    transport_security = TransportSecuritySettings(enable_dns_rebinding_protection=False)
                else:
                    hosts = [
                        "127.0.0.1:*",
                        "127.0.0.1",
                        "localhost:*",
                        "localhost",
                        "[::1]:*",
                        "::1",
                        "testserver",
                        "testserver:*",
                    ]
                    if host not in ("127.0.0.1", "localhost", "::1"):
                        hosts.extend([f"{host}:*", host])
                    origins = [
                        "http://127.0.0.1:*",
                        "http://127.0.0.1",
                        "http://localhost:*",
                        "http://localhost",
                        "http://[::1]:*",
                        "http://[::1]",
                    ]
                    if host not in ("127.0.0.1", "localhost", "::1"):
                        origins.extend([f"http://{host}:*", f"http://{host}"])

                    transport_security = TransportSecuritySettings(
                        enable_dns_rebinding_protection=True,
                        allowed_hosts=hosts,
                        allowed_origins=origins,
                    )
            except Exception:
                transport_security = None

        return self.underlying_server.sse_app(
            transport_security=transport_security,
            host=host,
        )

    def __repr__(self) -> str:
        return f"<FrameworkMCPServer name={self.name!r} version={self.version!r}>"


def create_mcp_server(
    name: str = "common-framework-mcp",
    version: Optional[str] = None,
    instructions: Optional[str] = None,
) -> FrameworkMCPServer:
    """Convenience factory to create a FrameworkMCPServer instance."""
    return FrameworkMCPServer(name=name, version=version, instructions=instructions)
