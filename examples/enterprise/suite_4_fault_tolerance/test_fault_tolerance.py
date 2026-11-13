"""Suite 4: Mapeo de Errores y Tolerancia a Fallos (Exception Translation).

Demuestra cómo el Gateway reacciona cuando los componentes internos fallan,
traduciendo excepciones de Python en respuestas HTTP estandarizadas conforme a RFC
(429 Too Many Requests con Retry-After, 502 Bad Gateway ante alucinaciones o fallos de upstream).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch
import pytest
from fastapi.testclient import TestClient

from common_framework.core.exceptions import (
    AuthenticationError,
    LLMProviderError,
    RateLimitError,
    StructuredOutputError,
)
from common_framework.core.llm import BaseLLMClient
from suite_4_fault_tolerance.gateway_resilience import (
    DocumentExtractionSchema,
    create_resilient_gateway,
)


class TestExceptionTranslationHTTP:
    """Valida la traducción de excepciones del dominio hacia códigos de estado y headers HTTP."""

    def test_rate_limit_error_translated_to_http_429_with_retry_after(self) -> None:
        """Objetivo: Configurar el Mock del LLM para que lance deliberadamente un RateLimitError

        (simulando cuota de API agotada o throttling por el proveedor). Validar mediante TestClient
        que el Gateway intercepta la excepción y devuelve HTTP 429 con el header 'Retry-After'
        y el cuerpo estructurado 'rate_limit_exceeded'.
        """
        # 1. Configurar mock de cliente LLM lanzando RateLimitError con retry_after explícito
        mock_client = AsyncMock(spec=BaseLLMClient)
        mock_client.complete = AsyncMock(
            side_effect=RateLimitError(
                message="OpenAI organization quota exceeded. Please reduce rate of requests.",
                provider="openai",
                retry_after=60.0,
            )
        )

        app = create_resilient_gateway(llm_client=mock_client)

        with TestClient(app) as client:
            # 2. Consumir endpoint
            response = client.post(
                "/api/v1/chat/completions",
                json={"prompt": "¿Cuál es la vigencia de la patente X?", "model": "gpt-4o"},
            )

            # 3. Validar HTTP 429 Too Many Requests
            assert response.status_code == 429, (
                f"Fallo de traducción: Se esperaba HTTP 429, pero se obtuvo {response.status_code}."
            )

            # 4. Validar header 'Retry-After' según RFC 6585 / HTTP Specs
            assert response.headers.get("Retry-After") == "60", (
                f"Falta el header Retry-After o su valor es incorrecto: {response.headers.get('Retry-After')}"
            )

            # 5. Validar cuerpo de error normalizado
            body = response.json()
            assert body["error"] == "rate_limit_exceeded"
            assert "OpenAI organization quota exceeded" in body["message"]
            assert body["provider"] == "openai"
            assert body["retry_after"] == 60.0

    def test_structured_output_error_translated_to_http_502(self) -> None:
        """Objetivo: Configurar el Mock del LLM para que lance deliberadamente un StructuredOutputError

        (simulando que el LLM alucinó un formato inválido o devolvió un JSON corrompido que no coincide
        con el esquema Pydantic). Validar que el Gateway devuelve HTTP 502 Bad Gateway sin tumbar el proceso.
        """
        corrupted_raw_output = '{"entity_name": "Acme Corp", "tax_id": null, "annual_revenue": "NaN"}'
        mock_client = AsyncMock(spec=BaseLLMClient)
        mock_client.structured_complete = AsyncMock(
            side_effect=StructuredOutputError(
                message="Failed to validate JSON response against DocumentExtractionSchema.",
                raw_output=corrupted_raw_output,
            )
        )

        app = create_resilient_gateway(llm_client=mock_client)

        with TestClient(app) as client:
            response = client.post(
                "/api/v1/extract/structured",
                json={"prompt": "Extrae los datos fiscales de la factura 9981.", "model": "claude-3-5-sonnet"},
            )

            # 1. Validar HTTP 502 Bad Gateway (upstream LLM generó payload defectuoso)
            assert response.status_code == 502, (
                f"Fallo de traducción: Se esperaba HTTP 502 ante StructuredOutputError, se obtuvo {response.status_code}."
            )

            # 2. Validar payload JSON informativo para depuración
            body = response.json()
            assert body["error"] == "structured_output_validation_failed"
            assert "Failed to validate JSON response" in body["message"]
            assert body["raw_output"] == corrupted_raw_output

    def test_upstream_llm_provider_error_translated_to_http_502(self) -> None:
        """Verifica que fallos de infraestructura del proveedor LLM (500 de OpenAI o Anthropic) se mapean a 502."""
        mock_client = AsyncMock(spec=BaseLLMClient)
        mock_client.complete = AsyncMock(
            side_effect=LLMProviderError(
                message="Service Unavailable from upstream provider cluster.",
                status_code=503,
                provider="anthropic",
            )
        )

        app = create_resilient_gateway(llm_client=mock_client)

        with TestClient(app) as client:
            response = client.post(
                "/api/v1/chat/completions",
                json={"prompt": "Test prompt"},
            )
            assert response.status_code == 502
            body = response.json()
            assert body["error"] == "upstream_provider_error"
            assert body["provider"] == "anthropic"

    def test_provider_authentication_error_translated_to_http_401(self) -> None:
        """Verifica que una clave de API inválida del proveedor LLM se traduce en 401 con header Bearer."""
        mock_client = AsyncMock(spec=BaseLLMClient)
        mock_client.complete = AsyncMock(
            side_effect=AuthenticationError(
                message="Incorrect API key provided for OpenAI provider.",
                provider="openai",
            )
        )

        app = create_resilient_gateway(llm_client=mock_client)

        with TestClient(app) as client:
            response = client.post(
                "/api/v1/chat/completions",
                json={"prompt": "Test auth"},
            )
            assert response.status_code == 401
            assert response.headers.get("WWW-Authenticate") == "Bearer"
            body = response.json()
            assert body["error"] == "authentication_failed"


if __name__ == "__main__":
    pytest.main(["-v", __file__])
