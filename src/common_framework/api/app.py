"""Reusable FastAPI application factory with pre-configured middlewares and exception handlers."""

from __future__ import annotations

import logging
from typing import Any, Callable, Optional, Sequence
from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from common_framework.core.config import Settings, get_settings
from common_framework.core.exceptions import (
    AuthenticationError,
    ConfigurationError,
    FrameworkError,
    LLMProviderError,
    ModelNotFoundError,
    RateLimitError,
    StructuredOutputError,
)
from common_framework.tools.base import ToolExecutionError
from common_framework.tools.registry import ToolNotFoundError

logger = logging.getLogger("common_framework.api")


def setup_exception_handlers(app: FastAPI) -> None:
    """Register automatic exception handlers translating framework exceptions to standardized HTTP responses."""

    @app.exception_handler(RateLimitError)
    async def rate_limit_handler(request: Request, exc: RateLimitError) -> JSONResponse:
        logger.warning("Rate limit exceeded on %s %s: %s", request.method, request.url.path, exc.message)
        headers = {}
        if exc.retry_after is not None:
            headers["Retry-After"] = str(int(exc.retry_after))
        return JSONResponse(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            content={
                "error": "rate_limit_exceeded",
                "message": exc.message,
                "provider": exc.provider,
                "retry_after": exc.retry_after,
            },
            headers=headers,
        )

    @app.exception_handler(AuthenticationError)
    async def auth_error_handler(request: Request, exc: AuthenticationError) -> JSONResponse:
        logger.error("Authentication failure on %s %s: %s", request.method, request.url.path, exc.message)
        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content={
                "error": "authentication_failed",
                "message": exc.message,
                "provider": exc.provider,
            },
        )

    @app.exception_handler(ModelNotFoundError)
    async def model_not_found_handler(request: Request, exc: ModelNotFoundError) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={
                "error": "model_not_found",
                "message": exc.message,
                "provider": exc.provider,
            },
        )

    @app.exception_handler(ToolNotFoundError)
    async def tool_not_found_handler(request: Request, exc: ToolNotFoundError) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={
                "error": "tool_not_found",
                "message": exc.message,
                "tool_name": exc.tool_name,
            },
        )

    @app.exception_handler(ToolExecutionError)
    async def tool_execution_handler(request: Request, exc: ToolExecutionError) -> JSONResponse:
        logger.warning("Tool execution error: %s", exc.message)
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={
                "error": "tool_execution_failed",
                "message": exc.message,
                "tool_name": exc.tool_name,
                "details": exc.details,
            },
        )

    @app.exception_handler(StructuredOutputError)
    async def structured_output_handler(request: Request, exc: StructuredOutputError) -> JSONResponse:
        logger.error("Structured output parsing error: %s", exc.message)
        return JSONResponse(
            status_code=status.HTTP_502_BAD_GATEWAY,
            content={
                "error": "structured_output_validation_failed",
                "message": exc.message,
                "raw_output": exc.raw_output,
            },
        )

    @app.exception_handler(LLMProviderError)
    async def llm_provider_error_handler(request: Request, exc: LLMProviderError) -> JSONResponse:
        logger.error("Upstream LLM provider failure: %s", exc.message)
        return JSONResponse(
            status_code=status.HTTP_502_BAD_GATEWAY,
            content={
                "error": "upstream_provider_error",
                "message": exc.message,
                "provider": exc.provider,
            },
        )

    @app.exception_handler(ConfigurationError)
    async def configuration_error_handler(request: Request, exc: ConfigurationError) -> JSONResponse:
        logger.error("Configuration error: %s", exc.message)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={
                "error": "configuration_error",
                "message": exc.message,
            },
        )

    @app.exception_handler(FrameworkError)
    async def generic_framework_handler(request: Request, exc: FrameworkError) -> JSONResponse:
        logger.error("Internal framework error: %s", exc.message)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={
                "error": "internal_framework_error",
                "message": exc.message,
                "details": exc.details,
            },
        )


def create_app(
    title: str = "AI Common Framework Service",
    version: str = "0.4.0-alpha",
    description: str = "Production-ready REST and MCP service powered by AI RAG Common Framework.",
    cors_origins: Optional[Sequence[str]] = None,
    cors_methods: Optional[Sequence[str]] = None,
    cors_headers: Optional[Sequence[str]] = None,
    enable_healthcheck: bool = True,
    rag_engine: Optional[Any] = None,
    enable_rag_routes: bool = False,
    mcp_server: Optional[Any] = None,
    mcp_prefix: str = "/mcp",
    mcp_api_key: Optional[str] = None,
    settings: Optional[Settings] = None,
    **fastapi_kwargs: Any,
) -> FastAPI:
    """Create and configure a production-ready FastAPI application."""
    active_settings = settings or get_settings()

    app = FastAPI(
        title=title,
        version=version,
        description=description,
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
        **fastapi_kwargs,
    )

    # Attach framework settings and optional components to application state
    app.state.settings = active_settings
    app.state.rag_engine = rag_engine
    app.state.mcp_server = mcp_server

    # CORS Middleware configuration
    origins = list(cors_origins) if cors_origins is not None else ["*"]
    methods = list(cors_methods) if cors_methods is not None else ["*"]
    headers = list(cors_headers) if cors_headers is not None else ["*"]

    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=methods,
        allow_headers=headers,
    )

    # Global Exception Handlers
    setup_exception_handlers(app)

    # Health & Readiness Endpoints
    if enable_healthcheck:
        @app.get("/healthz", tags=["System"], summary="Health check")
        async def health_check() -> dict[str, Any]:
            return {
                "status": "ok",
                "version": version,
                "env": active_settings.framework_env,
            }

        @app.get("/readyz", tags=["System"], summary="Readiness probe")
        async def readiness_probe() -> dict[str, Any]:
            return {
                "ready": True,
                "default_provider": active_settings.default_provider,
                "default_model": active_settings.default_model,
            }

    # Mount optional standard RAG routes
    if enable_rag_routes:
        from common_framework.api.routes.rag import create_rag_router

        app.include_router(create_rag_router(engine=rag_engine))

    # Mount optional MCP server over SSE
    if mcp_server is not None:
        from common_framework.api.mcp import mount_mcp_to_fastapi

        mount_mcp_to_fastapi(
            app=app,
            mcp_server=mcp_server,
            prefix=mcp_prefix,
            api_key=mcp_api_key,
        )

    return app

