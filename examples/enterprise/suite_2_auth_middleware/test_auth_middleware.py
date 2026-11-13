"""Suite 2: Security and Authentication (Middleware Tests).

Validates that the network layer rejects unauthorized requests with HTTP 401 before
reaching MCP server business logic, and transparently normalizes multiple credential
transports (Bearer, X-API-Key, and Query Parameter).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
import pytest
from fastapi.testclient import TestClient

from common_framework.mcp.server import create_mcp_server
from suite_2_auth_middleware.auth_server import create_secure_mcp_gateway

SECRET_TOKEN = "enterprise-mcp-key-2026"
PROTECTED_SSE_ENDPOINT = "/api/v1/mcp/sse"
PROTECTED_MESSAGES_ENDPOINT = "/api/v1/mcp/messages/"


class TestMCPAuthMiddlewareRejection:
    """Tests transport-layer rejection when credentials are missing or invalid."""

    def test_unauthorized_rejection_missing_credentials_401(self) -> None:
        """Verify request to protected endpoint without credentials returns HTTP 401 and prevents execution."""
        # Create spy/tracker on the MCP server to verify internal logic is never executed
        mock_server = create_mcp_server(name="secure-isolated-mcp")
        executed_tracker: list[str] = []

        @mock_server.tool(name="internal_rag_tool", description="Spy tool")
        def spy_tool() -> str:
            executed_tracker.append("called")
            return "executed"

        app = create_secure_mcp_gateway(
            api_key=SECRET_TOKEN,
            allow_query_param=False,
            mcp_server=mock_server,
        )

        with TestClient(app) as client:
            # Request to /api/v1/mcp/sse without credentials
            response = client.get(PROTECTED_SSE_ENDPOINT)

            # 1. Validate 401 Unauthorized status code
            assert response.status_code == 401, (
                f"Security failure: Protected endpoint returned {response.status_code} "
                "instead of 401 Unauthorized."
            )

            # 2. Validate WWW-Authenticate header per RFC 6750 / MCP standard
            assert response.headers.get("www-authenticate") == 'Bearer realm="MCP"', (
                "WWW-Authenticate header missing or non-compliant with MCP standard."
            )

            # 3. Validate standardized error JSON payload
            body = response.json()
            assert body.get("error") == "unauthorized"
            assert "Invalid or missing MCP authentication credential" in body.get("message", "")

            # 4. Strict isolation: MCP internal logic was NEVER invoked
            assert len(executed_tracker) == 0, (
                "Vulnerability detected: Internal MCP tool was invoked despite 401 rejection."
            )

    def test_unauthorized_rejection_invalid_bearer_token_401(self) -> None:
        """Verify altered or non-matching Bearer token is rejected with 401."""
        app = create_secure_mcp_gateway(api_key=SECRET_TOKEN)
        with TestClient(app) as client:
            response = client.get(
                PROTECTED_SSE_ENDPOINT,
                headers={"Authorization": "Bearer fake-or-expired-token"},
            )
            assert response.status_code == 401
            assert response.json()["error"] == "unauthorized"

    def test_unauthorized_rejection_invalid_x_api_key_401(self) -> None:
        """Verify invalid X-API-Key header value is rejected with 401."""
        app = create_secure_mcp_gateway(api_key=SECRET_TOKEN)
        with TestClient(app) as client:
            response = client.get(
                PROTECTED_SSE_ENDPOINT,
                headers={"X-API-Key": "wrong-key-value"},
            )
            assert response.status_code == 401
            assert response.json()["error"] == "unauthorized"


class TestMCPAuthMiddlewareMultiTransport:
    """Tests successful authentication normalization across multiple transports."""

    def test_successful_authentication_bearer_header(self) -> None:
        """Verify request with 'Authorization: Bearer <token>' passes middleware and reaches MCP router."""
        app = create_secure_mcp_gateway(api_key=SECRET_TOKEN)
        with TestClient(app) as client:
            # Send POST to messages endpoint with valid credential
            response = client.post(
                PROTECTED_MESSAGES_ENDPOINT,
                json={},
                headers={"Authorization": f"Bearer {SECRET_TOKEN}"},
            )

            # Passing authentication means request enters the MCP handler.
            # The SSE handler expects session_id and responds with 400 Bad Request (NOT 401 Unauthorized),
            # proving it cleared the perimeter network security barrier.
            assert response.status_code != 401, "Valid Bearer token was improperly rejected with 401."
            assert response.status_code == 400
            assert "session_id is required" in response.text

    def test_successful_authentication_x_api_key_header(self) -> None:
        """Verify request with 'X-API-Key: <token>' header is transparently admitted."""
        app = create_secure_mcp_gateway(api_key=SECRET_TOKEN)
        with TestClient(app) as client:
            response = client.post(
                PROTECTED_MESSAGES_ENDPOINT,
                json={},
                headers={"X-API-Key": SECRET_TOKEN},
            )

            assert response.status_code != 401, "Valid X-API-Key header was rejected with 401."
            assert response.status_code == 400
            assert "session_id is required" in response.text

    def test_query_param_authentication_when_explicitly_enabled(self) -> None:
        """Verify ?api_key=<token> URL parameter is accepted only when allow_query_param_auth=True."""
        # 1. With allow_query_param=True: authentication succeeds
        app_opt_in = create_secure_mcp_gateway(
            api_key=SECRET_TOKEN,
            allow_query_param=True,
        )
        with TestClient(app_opt_in) as client:
            response = client.post(
                f"{PROTECTED_MESSAGES_ENDPOINT}?api_key={SECRET_TOKEN}",
                json={},
            )
            assert response.status_code != 401
            assert response.status_code == 400
            assert "session_id is required" in response.text

        # 2. With allow_query_param=False (strict default): query param is rejected with 401
        app_strict = create_secure_mcp_gateway(
            api_key=SECRET_TOKEN,
            allow_query_param=False,
        )
        with TestClient(app_strict) as client:
            response_rejected = client.post(
                f"{PROTECTED_MESSAGES_ENDPOINT}?api_key={SECRET_TOKEN}",
                json={},
            )
            assert response_rejected.status_code == 401
            assert response_rejected.json()["error"] == "unauthorized"


if __name__ == "__main__":
    pytest.main(["-v", __file__])
