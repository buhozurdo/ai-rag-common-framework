"""Gateway Application demonstrating Kubernetes health probes and graceful lifespan lifecycle."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Optional
from fastapi import FastAPI

from common_framework.api import create_app

logger = logging.getLogger("examples.gateway.lifecycle")


class DatabaseConnectionPool:
    """Simulated production database connection pool to track resource allocation/release."""

    def __init__(self, dsn: str = "postgresql://enterprise-ai:5432/rag_db") -> None:
        self.dsn = dsn
        self.is_connected = False
        self.active_connections = 0

    async def connect(self) -> None:
        """Simulate opening database pool and background workers."""
        logger.info("DatabaseConnectionPool: Connecting to %s...", self.dsn)
        self.is_connected = True
        self.active_connections = 10
        logger.info("DatabaseConnectionPool: Pool established (10 connections).")

    async def disconnect(self) -> None:
        """Simulate cleanly closing pool to prevent connection leaks."""
        logger.info("DatabaseConnectionPool: Draining and terminating connections...")
        self.is_connected = False
        self.active_connections = 0
        logger.info("DatabaseConnectionPool: Pool closed cleanly. No memory or socket leaks.")


def create_lifecycle_gateway(
    db_pool: Optional[DatabaseConnectionPool] = None,
    **app_overrides: Any,
) -> FastAPI:
    """Build a production Gateway application with lifespan management and health probes."""
    active_pool = db_pool or DatabaseConnectionPool()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # Startup phase
        await active_pool.connect()
        app.state.db_pool = active_pool
        try:
            yield
        finally:
            # Shutdown phase (guaranteed execution)
            await active_pool.disconnect()

    app = create_app(
        title="Enterprise AI Gateway",
        version="1.0.0",
        description="Gateway verifying Kubernetes probes and lifespan resource safety.",
        enable_healthcheck=True,
        lifespan=lifespan,
        **app_overrides,
    )

    @app.get("/api/v1/system/status", tags=["System"])
    async def system_status() -> dict[str, Any]:
        pool: DatabaseConnectionPool = app.state.db_pool
        return {
            "status": "operational",
            "db_connected": pool.is_connected,
            "active_connections": pool.active_connections,
        }

    return app
