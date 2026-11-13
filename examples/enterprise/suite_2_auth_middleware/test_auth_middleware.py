"""Suite 2: Seguridad y Autenticación (Middleware Tests).

Valida que la capa de red rechaza peticiones no autorizadas con HTTP 401 antes
de que toquen la lógica de negocio del servidor MCP, y que normaliza adecuadamente
múltiples transportes de credenciales (Bearer, X-API-Key y Query Parameter).
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
    """Pruebas de rechazo en capa de transporte cuando faltan o son inválidas las credenciales."""

    def test_unauthorized_rejection_missing_credentials_401(self) -> None:
        """Objetivo: Enviar petición HTTP al endpoint protegido (/api/v1/mcp/sse) sin cabecera

        Authorization ni parámetro ?api_key=. Afirmar que MCPAuthMiddleware bloquea la petición
        devolviendo HTTP 401 y que la lógica del servidor MCP nunca es invocada.
        """
        # Crear spy/tracker en el servidor MCP para certificar que su lógica interna nunca se ejecuta
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
            # Petición a /api/v1/mcp/sse sin credenciales
            response = client.get(PROTECTED_SSE_ENDPOINT)

            # 1. Validar código de estado 401 Unauthorized
            assert response.status_code == 401, (
                f"Fallo de seguridad: Endpoint protegido devolvió {response.status_code} "
                "en lugar de 401 Unauthorized."
            )

            # 2. Validar cabecera WWW-Authenticate según estándar RFC 6750 / MCP
            assert response.headers.get("www-authenticate") == 'Bearer realm="MCP"', (
                "Cabecera WWW-Authenticate faltante o no conforme con el estándar MCP."
            )

            # 3. Validar payload JSON estandarizado
            body = response.json()
            assert body.get("error") == "unauthorized"
            assert "Invalid or missing MCP authentication credential" in body.get("message", "")

            # 4. Aislamiento estricto: la lógica interna del MCP NUNCA fue invocada
            assert len(executed_tracker) == 0, (
                "Vulnerabilidad detectada: La herramienta interna del MCP fue invocada a pesar del 401."
            )

    def test_unauthorized_rejection_invalid_bearer_token_401(self) -> None:
        """Verifica que un token Bearer alterado o no coincidente es rechazado con 401."""
        app = create_secure_mcp_gateway(api_key=SECRET_TOKEN)
        with TestClient(app) as client:
            response = client.get(
                PROTECTED_SSE_ENDPOINT,
                headers={"Authorization": "Bearer token-falso-o-expirado"},
            )
            assert response.status_code == 401
            assert response.json()["error"] == "unauthorized"

    def test_unauthorized_rejection_invalid_x_api_key_401(self) -> None:
        """Verifica que una clave en X-API-Key inválida es rechazada con 401."""
        app = create_secure_mcp_gateway(api_key=SECRET_TOKEN)
        with TestClient(app) as client:
            response = client.get(
                PROTECTED_SSE_ENDPOINT,
                headers={"X-API-Key": "wrong-key-value"},
            )
            assert response.status_code == 401
            assert response.json()["error"] == "unauthorized"


class TestMCPAuthMiddlewareMultiTransport:
    """Pruebas de normalización exitosa de autenticación con múltiples transportes."""

    def test_successful_authentication_bearer_header(self) -> None:
        """Objetivo: Validar que una petición con header 'Authorization: Bearer <token>' pasa

        el middleware de autenticación y alcanza el router del protocolo MCP.
        """
        app = create_secure_mcp_gateway(api_key=SECRET_TOKEN)
        with TestClient(app) as client:
            # Enviamos POST a messages (endpoint del protocolo MCP) con credencial válida
            response = client.post(
                PROTECTED_MESSAGES_ENDPOINT,
                json={},
                headers={"Authorization": f"Bearer {SECRET_TOKEN}"},
            )

            # Si pasa la autenticación, la petición entra al handler del MCP.
            # El handler de SSE espera session_id y responde 400 Bad Request (NO 401 Unauthorized),
            # demostrando que superó la barrera de red perimetral.
            assert response.status_code != 401, "El token Bearer válido fue rechazado indebidamente con 401."
            assert response.status_code == 400
            assert "session_id is required" in response.text

    def test_successful_authentication_x_api_key_header(self) -> None:
        """Objetivo: Validar que una petición con header 'X-API-Key: <token>' es admitida

        normalizándose de forma transparente en el middleware.
        """
        app = create_secure_mcp_gateway(api_key=SECRET_TOKEN)
        with TestClient(app) as client:
            response = client.post(
                PROTECTED_MESSAGES_ENDPOINT,
                json={},
                headers={"X-API-Key": SECRET_TOKEN},
            )

            assert response.status_code != 401, "El header X-API-Key válido fue rechazado con 401."
            assert response.status_code == 400
            assert "session_id is required" in response.text

    def test_query_param_authentication_when_explicitly_enabled(self) -> None:
        """Objetivo: Validar que el parámetro en URL ?api_key=<token> es aceptado únicamente

        cuando se activa explícitamente allow_query_param_auth=True (para clientes EventSource de browser).
        """
        # 1. Con allow_query_param=True: pasa la autenticación
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

        # 2. Con allow_query_param=False (default estricto): el query param es rechazado con 401
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
