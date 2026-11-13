"""Suite 4: Error Mapping and Fault Tolerance (Exception Translation).

Demonstrates how the Gateway handles internal component failures, translating Python
exceptions into standardized HTTP responses per RFC standards (429 Too Many Requests with
Retry-After, 502 Bad Gateway on upstream failures or malformed outputs).
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
    """Validate domain exception translation into HTTP status codes and headers."""

    def test_rate_limit_error_translated_to_http_429_with_retry_after(self) -> None:
        """Verify RateLimitError translates to HTTP 429 with 'Retry-After' header and structured payload."""
        # 1. Configure LLM client mock raising RateLimitError with explicit retry_after
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
            # 2. Consume endpoint
            response = client.post(
                "/api/v1/chat/completions",
                json={"prompt": "What is the expiration date of patent X?", "model": "gpt-4o"},
            )

            # 3. Validate HTTP 429 Too Many Requests
            assert response.status_code == 429, (
                f"Translation failure: Expected HTTP 429, got {response.status_code}."
            )

            # 4. Validate header 'Retry-After' per RFC 6585 / HTTP Specs
            assert response.headers.get("Retry-After") == "60", (
                f"Missing or invalid Retry-After header: {response.headers.get('Retry-After')}"
            )

            # 5. Validate normalized error body
            body = response.json()
            assert body["error"] == "rate_limit_exceeded"
            assert "OpenAI organization quota exceeded" in body["message"]
            assert body["provider"] == "openai"
            assert body["retry_after"] == 60.0

    def test_structured_output_error_translated_to_http_502(self) -> None:
        """Verify StructuredOutputError translates to HTTP 502 Bad Gateway without crashing the server process."""
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
                json={"prompt": "Extract fiscal data from invoice 9981.", "model": "claude-3-5-sonnet"},
            )

            # 1. Validate HTTP 502 Bad Gateway (upstream LLM generated defective payload)
            assert response.status_code == 502, (
                f"Translation failure: Expected HTTP 502 on StructuredOutputError, got {response.status_code}."
            )

            # 2. Validate debugging payload
            body = response.json()
            assert body["error"] == "structured_output_validation_failed"
            assert "Failed to validate JSON response" in body["message"]
            assert body["raw_output"] == corrupted_raw_output

    def test_upstream_llm_provider_error_translated_to_http_502(self) -> None:
        """Verify upstream LLM provider infrastructure failure (500 from provider) maps to 502."""
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
        """Verify invalid API key from upstream LLM provider translates to 401 with Bearer header."""
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
