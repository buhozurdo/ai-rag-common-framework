"""Secure Gateway application demonstrating MCPAuthMiddleware with multiple transports and 401 enforcement."""

from __future__ import annotations

import logging
from typing import Any, Optional
from fastapi import FastAPI

from common_framework.api import create_app
from common_framework.api.mcp import mount_mcp_to_fastapi
from common_framework.mcp.server import FrameworkMCPServer, create_mcp_server

logger = logging.getLogger("examples.gateway.auth")


def create_secure_mcp_gateway(
    api_key: str = "secret-mcp-token-xyz",
    allow_query_param: bool = False,
    prefix: str = "/api/v1/mcp",
    mcp_server: Optional[FrameworkMCPServer] = None,
) -> FastAPI:
    """Build a FastAPI gateway with protected MCP SSE endpoints and MCPAuthMiddleware."""
    server = mcp_server or create_mcp_server(name="enterprise-rag-mcp", version="1.0.0")

    app = create_app(
        title="Secure Enterprise MCP Gateway",
        version="1.0.0",
        description="Protected gateway enforcing multi-transport authentication before MCP logic.",
        enable_healthcheck=True,
    )

    mount_mcp_to_fastapi(
        app=app,
        mcp_server=server,
        prefix=prefix,
        api_key=api_key,
        allow_query_param_auth=allow_query_param,
        disable_dns_protection=True,  # Permits testserver host header in unit tests
    )

    return app
