"""Unit tests for mounting Model Context Protocol (MCP) servers into FastAPI with authentication."""

from __future__ import annotations

import httpx
import pytest
from fastapi import FastAPI

from common_framework.api import create_app, mount_mcp_to_fastapi
from common_framework.mcp.server import create_mcp_server
from common_framework.tools.base import tool


@pytest.fixture
def sample_mcp_server():
    """Create a sample MCP server with a simple tool."""
    server = create_mcp_server(name="test-mcp-gateway", version="0.4.0")

    @server.tool(name="ping", description="Ping health check")
    def ping() -> str:
        return "pong"

    return server


@pytest.mark.asyncio
async def test_mount_mcp_unauthenticated(sample_mcp_server):
    """Verify MCP server mounted without auth allows direct access."""
    app = create_app(mcp_server=sample_mcp_server, mcp_prefix="/mcp")
    assert app.state.mcp_server == sample_mcp_server

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://localhost") as client:
        # POST to /mcp/messages/ without auth should reach MCP handler
        res = await client.post("/mcp/messages/", json={})
        assert res.status_code == 400
        assert "session_id is required" in res.text


@pytest.mark.asyncio
async def test_mount_mcp_auth_missing_and_invalid(sample_mcp_server):
    """Verify 401 Unauthorized when credential is missing or invalid."""
    app = create_app(
        mcp_server=sample_mcp_server,
        mcp_prefix="/mcp",
        mcp_api_key="super-secret-mcp-token",
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://localhost") as client:
        # Missing auth
        res_missing = await client.post("/mcp/messages/", json={})
        assert res_missing.status_code == 401
        assert res_missing.headers.get("www-authenticate") == 'Bearer realm="MCP"'
        assert res_missing.json()["error"] == "unauthorized"

        # Invalid Bearer
        res_invalid = await client.post(
            "/mcp/messages/",
            json={},
            headers={"Authorization": "Bearer wrong-token"},
        )
        assert res_invalid.status_code == 401

        # Invalid X-API-Key
        res_bad_key = await client.post(
            "/mcp/messages/",
            json={},
            headers={"X-API-Key": "wrong-token"},
        )
        assert res_bad_key.status_code == 401


@pytest.mark.asyncio
async def test_mount_mcp_auth_valid_methods(sample_mcp_server):
    """Verify Bearer and X-API-Key credentials succeed; query param is disabled by default."""
    secret = "production-token-987"
    app = create_app(
        mcp_server=sample_mcp_server,
        mcp_prefix="/mcp",
        mcp_api_key=secret,
        # allow_query_param_auth defaults to False (secure default)
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://localhost") as client:
        # 1. Bearer token
        res_bearer = await client.post(
            "/mcp/messages/",
            json={},
            headers={"Authorization": f"Bearer {secret}"},
        )
        assert res_bearer.status_code == 400
        assert "session_id is required" in res_bearer.text

        # 2. X-API-Key header
        res_x_key = await client.post(
            "/mcp/messages/",
            json={},
            headers={"X-API-Key": secret},
        )
        assert res_x_key.status_code == 400
        assert "session_id is required" in res_x_key.text

        # 3. Query parameter — rejected by default (allow_query_param_auth=False)
        res_query = await client.post(
            f"/mcp/messages/?api_key={secret}",
            json={},
        )
        assert res_query.status_code == 401


@pytest.mark.asyncio
async def test_mount_mcp_auth_query_param_opt_in(sample_mcp_server):
    """Verify query parameter auth works when explicitly opted in."""
    from common_framework.api.mcp import mount_mcp_to_fastapi as _mount
    secret = "opt-in-query-token"
    app = FastAPI()
    _mount(app=app, mcp_server=sample_mcp_server, prefix="/mcp",
           api_key=secret, allow_query_param_auth=True)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://localhost") as client:
        res_query = await client.post(f"/mcp/messages/?api_key={secret}", json={})
        assert res_query.status_code == 400  # Passes auth, fails at SSE session validation
        assert "session_id is required" in res_query.text


@pytest.mark.asyncio
async def test_mount_mcp_query_param_auth_disabled(sample_mcp_server):
    """Verify query parameter auth can be disabled for stricter environments."""
    app = FastAPI()
    mount_mcp_to_fastapi(
        app=app,
        mcp_server=sample_mcp_server,
        prefix="/mcp",
        api_key="strict-key",
        allow_query_param_auth=False,
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://localhost") as client:
        # Query param should be rejected
        res = await client.post("/mcp/messages/?api_key=strict-key", json={})
        assert res.status_code == 401

        # Header auth still works
        res_header = await client.post(
            "/mcp/messages/",
            json={},
            headers={"X-API-Key": "strict-key"},
        )
        assert res_header.status_code == 400


@pytest.mark.asyncio
async def test_mount_mcp_custom_prefix(sample_mcp_server):
    """Verify mounting MCP under custom prefixes."""
    app = FastAPI()
    mount_mcp_to_fastapi(
        app=app,
        mcp_server=sample_mcp_server,
        prefix="/api/v2/agents/mcp/",
        api_key="key-123",
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://localhost") as client:
        res = await client.post(
            "/api/v2/agents/mcp/messages/",
            json={},
            headers={"X-API-Key": "key-123"},
        )
        assert res.status_code == 400
        assert "session_id is required" in res.text
