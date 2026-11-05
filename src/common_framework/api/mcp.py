"""FastAPI integration and mounting for Model Context Protocol (MCP) servers."""

from __future__ import annotations

import hmac
import json
import logging
from typing import Any, Optional
from urllib.parse import parse_qs
from fastapi import FastAPI

from common_framework.mcp.server import FrameworkMCPServer

logger = logging.getLogger("common_framework.api.mcp")


class MCPAuthMiddleware:
    """ASGI middleware enforcing API Key / Bearer authentication for MCP endpoints.

    Supports:
    - Authorization: Bearer <key>
    - X-API-Key: <key>
    - Query parameter: ?api_key=<key> (essential for browser-based EventSource SSE clients)
    """

    def __init__(
        self,
        app: Any,
        expected_api_key: str,
        allow_query_param: bool = True,
    ) -> None:
        self.app = app
        self.expected_api_key = expected_api_key
        self.allow_query_param = allow_query_param

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = dict(scope.get("headers", []))
        auth_header = headers.get(b"authorization", b"").decode("latin-1")
        x_api_key = headers.get(b"x-api-key", b"").decode("latin-1")

        provided_key: Optional[str] = None
        if auth_header:
            if auth_header.lower().startswith("bearer "):
                provided_key = auth_header[7:].strip()
            else:
                provided_key = auth_header.strip()
        elif x_api_key:
            provided_key = x_api_key.strip()
        elif self.allow_query_param:
            query_string = scope.get("query_string", b"").decode("latin-1")
            parsed = parse_qs(query_string)
            if "api_key" in parsed and parsed["api_key"]:
                provided_key = parsed["api_key"][0]

        if not provided_key or not hmac.compare_digest(provided_key, self.expected_api_key):
            response_body = json.dumps(
                {
                    "error": "unauthorized",
                    "message": "Invalid or missing MCP authentication credential",
                }
            ).encode("utf-8")

            await send(
                {
                    "type": "http.response.start",
                    "status": 401,
                    "headers": [
                        (b"content-type", b"application/json"),
                        (b"content-length", str(len(response_body)).encode("ascii")),
                        (b"www-authenticate", b'Bearer realm="MCP"'),
                    ],
                }
            )
            await send(
                {
                    "type": "http.response.body",
                    "body": response_body,
                }
            )
            return

        await self.app(scope, receive, send)


def mount_mcp_to_fastapi(
    app: FastAPI,
    mcp_server: FrameworkMCPServer,
    prefix: str = "/mcp",
    api_key: Optional[str] = None,
    allow_query_param_auth: bool = True,
) -> None:
    """Mount a FrameworkMCPServer as an SSE ASGI sub-application into FastAPI.

    Args:
        app: The target FastAPI application.
        mcp_server: The FrameworkMCPServer to mount.
        prefix: URL path prefix where MCP SSE routes will be served (default: '/mcp').
        api_key: Optional secret key required to access the MCP endpoints.
        allow_query_param_auth: Whether to accept ?api_key= query parameter (useful for EventSource).
    """
    normalized_prefix = "/" + prefix.strip("/")
    sse_app = mcp_server.get_sse_app()

    target_app = sse_app
    if api_key:
        target_app = MCPAuthMiddleware(
            app=sse_app,
            expected_api_key=api_key,
            allow_query_param=allow_query_param_auth,
        )

    app.mount(normalized_prefix, target_app, name="mcp_sse")
    app.state.mcp_server = mcp_server
    logger.info("Mounted MCP server %r at %s (auth=%s)", mcp_server.name, normalized_prefix, bool(api_key))
