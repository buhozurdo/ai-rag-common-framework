"""Suite 1: Gateway Boundary and Lifecycle Tests.

Validates application Gateway startup, Kubernetes orchestration health probes
(/healthz, /readyz), and safe connection/memory resource cleanup across the lifespan.
"""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock
import pytest
from fastapi.testclient import TestClient

from common_framework.api import create_app
from suite_1_gateway_lifecycle.gateway_app import (
    DatabaseConnectionPool,
    create_lifecycle_gateway,
)


class TestGatewayHealthchecks:
    """Validate Kubernetes liveness and readiness probe injection."""

    def test_healthchecks_liveness_readiness(self) -> None:
        """Verify create_app injects health probes returning HTTP 200 with standard observability payload."""
        app = create_app(
            title="K8s Gateway Test",
            version="1.0.0",
            enable_healthcheck=True,
        )

        with TestClient(app) as client:
            # 1. Liveness probe (/healthz)
            res_healthz = client.get("/healthz")
            assert res_healthz.status_code == 200, f"Expected 200 from /healthz, got {res_healthz.status_code}"
            data_healthz = res_healthz.json()
            assert data_healthz["status"] == "ok"
            assert data_healthz["version"] == "1.0.0"
            assert "env" in data_healthz

            # 2. Readiness probe (/readyz)
            res_readyz = client.get("/readyz")
            assert res_readyz.status_code == 200, f"Expected 200 from /readyz, got {res_readyz.status_code}"
            data_readyz = res_readyz.json()
            assert data_readyz["ready"] is True
            assert "default_provider" in data_readyz
            assert "default_model" in data_readyz

    def test_healthchecks_can_be_disabled(self) -> None:
        """Verify Gateway allows disabling healthcheck routes when external routing is required."""
        app = create_app(enable_healthcheck=False)
        with TestClient(app) as client:
            res_healthz = client.get("/healthz")
            assert res_healthz.status_code == 404
            res_readyz = client.get("/readyz")
            assert res_readyz.status_code == 404


class TestGatewayLifespan:
    """Validate resource startup and clean shutdown lifespan lifecycle."""

    def test_lifespan_resource_cleanup_prevents_leaks(self) -> None:
        """Simulate full lifecycle via TestClient context, verifying connections are cleaned up."""
        mock_db = MagicMock(spec=DatabaseConnectionPool)
        mock_db.connect = AsyncMock()
        mock_db.disconnect = AsyncMock()
        mock_db.is_connected = True
        mock_db.active_connections = 10

        app = create_lifecycle_gateway(db_pool=mock_db)

        # Before app startup, neither connect nor disconnect should be called
        assert mock_db.connect.call_count == 0
        assert mock_db.disconnect.call_count == 0

        # Lifespan block with TestClient
        with TestClient(app) as client:
            # Upon entering, startup must be invoked
            assert mock_db.connect.call_count == 1
            assert mock_db.disconnect.call_count == 0

            # Consume an endpoint during active lifecycle
            res = client.get("/api/v1/system/status")
            assert res.status_code == 200
            assert res.json()["status"] == "operational"

        # Upon exiting with block, shutdown must execute immediately
        assert mock_db.connect.call_count == 1
        assert mock_db.disconnect.call_count == 1, (
            "CRITICAL: Database connection pool was not closed when Gateway shut down. "
            "This leaks sockets and memory in Kubernetes pods."
        )

    def test_lifespan_state_isolation_between_runs(self) -> None:
        """Verify multiple server runs create isolated, safe lifespans."""
        pool_1 = DatabaseConnectionPool()
        app_1 = create_lifecycle_gateway(db_pool=pool_1)
        with TestClient(app_1):
            assert pool_1.is_connected is True
        assert pool_1.is_connected is False

        pool_2 = DatabaseConnectionPool()
        app_2 = create_lifecycle_gateway(db_pool=pool_2)
        with TestClient(app_2):
            assert pool_2.is_connected is True
        assert pool_2.is_connected is False


if __name__ == "__main__":
    pytest.main(["-v", __file__])
