"""Unit tests for FastAPI application factory, CORS, and standard exception handlers."""

import httpx
import pytest
from common_framework.api.app import create_app
from common_framework.core.config import Settings
from common_framework.core.exceptions import (
    AuthenticationError,
    ConfigurationError,
    FrameworkError,
    ModelNotFoundError,
    RateLimitError,
    StructuredOutputError,
)
from common_framework.tools.base import ToolExecutionError
from common_framework.tools.registry import ToolNotFoundError


@pytest.fixture
def test_app():
    """Create a test FastAPI application with routes that trigger various framework errors."""
    app = create_app(
        title="Test API",
        version="0.4.0-test",
        settings=Settings(FRAMEWORK_ENV="test"),
    )

    @app.get("/trigger-rate-limit")
    async def trigger_rate_limit():
        raise RateLimitError("Quota reached", status_code=429, retry_after=12.0, provider="gemini")

    @app.get("/trigger-auth-error")
    async def trigger_auth_error():
        raise AuthenticationError("Invalid API key", provider="openai")

    @app.get("/trigger-model-not-found")
    async def trigger_model_not_found():
        raise ModelNotFoundError("Model gpt-99 not found", provider="openai")

    @app.get("/trigger-tool-not-found")
    async def trigger_tool_not_found():
        raise ToolNotFoundError("unknown_tool")

    @app.get("/trigger-tool-execution-error")
    async def trigger_tool_execution_error():
        raise ToolExecutionError("calc", "Division by zero", details={"x": 10, "y": 0})

    @app.get("/trigger-structured-error")
    async def trigger_structured_error():
        raise StructuredOutputError("Validation failed", raw_output="not a json")

    @app.get("/trigger-config-error")
    async def trigger_config_error():
        raise ConfigurationError("Missing essential config")

    @app.get("/trigger-framework-error")
    async def trigger_framework_error():
        raise FrameworkError("Generic internal failure", details={"code": 99})

    return app


@pytest.mark.asyncio
async def test_healthcheck_and_readiness(test_app):
    """Verify /healthz and /readyz endpoints return valid responses."""
    transport = httpx.ASGITransport(app=test_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        # /healthz
        res_health = await client.get("/healthz")
        assert res_health.status_code == 200
        data_health = res_health.json()
        assert data_health["status"] == "ok"
        assert data_health["version"] == "0.4.0-test"
        assert data_health["env"] == "test"

        # /readyz
        res_ready = await client.get("/readyz")
        assert res_ready.status_code == 200
        data_ready = res_ready.json()
        assert data_ready["ready"] is True
        assert "default_provider" in data_ready


@pytest.mark.asyncio
async def test_exception_handler_rate_limit(test_app):
    """Verify RateLimitError maps to 429 with Retry-After header."""
    transport = httpx.ASGITransport(app=test_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        res = await client.get("/trigger-rate-limit")
        assert res.status_code == 429
        assert res.headers.get("retry-after") == "12"
        data = res.json()
        assert data["error"] == "rate_limit_exceeded"
        assert data["provider"] == "gemini"


@pytest.mark.asyncio
async def test_exception_handler_auth_and_not_found(test_app):
    """Verify AuthenticationError maps to 401 and Model/ToolNotFoundError to 404."""
    transport = httpx.ASGITransport(app=test_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        # Auth error
        res_auth = await client.get("/trigger-auth-error")
        assert res_auth.status_code == 401
        assert res_auth.json()["error"] == "authentication_failed"

        # Model not found
        res_model = await client.get("/trigger-model-not-found")
        assert res_model.status_code == 404
        assert res_model.json()["error"] == "model_not_found"

        # Tool not found
        res_tool = await client.get("/trigger-tool-not-found")
        assert res_tool.status_code == 404
        assert res_tool.json()["tool_name"] == "unknown_tool"


@pytest.mark.asyncio
async def test_exception_handler_tool_execution_and_structured(test_app):
    """Verify ToolExecutionError maps to 400 and StructuredOutputError maps to 502."""
    transport = httpx.ASGITransport(app=test_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        # Tool execution error
        res_tool = await client.get("/trigger-tool-execution-error")
        assert res_tool.status_code == 400
        assert res_tool.json()["details"]["y"] == 0

        # Structured output error
        res_struct = await client.get("/trigger-structured-error")
        assert res_struct.status_code == 502
        assert res_struct.json()["raw_output"] == "not a json"


@pytest.mark.asyncio
async def test_exception_handler_internal_errors(test_app):
    """Verify ConfigurationError and generic FrameworkError map to 500."""
    transport = httpx.ASGITransport(app=test_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        # Config error
        res_config = await client.get("/trigger-config-error")
        assert res_config.status_code == 500
        assert res_config.json()["error"] == "configuration_error"

        # Generic framework error
        res_gen = await client.get("/trigger-framework-error")
        assert res_gen.status_code == 500
        assert res_gen.json()["details"]["code"] == 99


@pytest.mark.asyncio
async def test_cors_headers(test_app):
    """Verify CORS preflight headers are returned.

    With wildcard origin (default), allow_credentials must be False per the CORS spec.
    When explicit origins are provided, allow_credentials is enabled.
    """
    transport = httpx.ASGITransport(app=test_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        res = await client.options(
            "/healthz",
            headers={
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": "GET",
            },
        )
        # Wildcard origin is set by default — credentials must NOT be allowed
        assert res.headers.get("access-control-allow-origin") == "*"
        # allow_credentials must NOT be "true" when wildcard origin is active (CORS spec §8.6)
        assert res.headers.get("access-control-allow-credentials") != "true"


@pytest.mark.asyncio
async def test_cors_headers_with_explicit_origins():
    """Verify credentials are enabled when explicit origins are provided."""
    from common_framework.api.app import create_app as _create_app
    app = _create_app(cors_origins=["http://localhost:3000", "https://example.com"])
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        res = await client.options(
            "/healthz",
            headers={
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": "GET",
            },
        )
        assert res.headers.get("access-control-allow-credentials") == "true"
