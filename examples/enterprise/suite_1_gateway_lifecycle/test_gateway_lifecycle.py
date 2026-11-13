"""Suite 1: Pruebas de Frontera y Ciclo de Vida (Gateway Tests).

Valida que el Gateway de la aplicación arranca correctamente, expone las sondas de vida
necesarias para la orquestación en Kubernetes (/healthz, /readyz) y gestiona sus recursos
de conexión y memoria de manera segura durante el ciclo de vida (lifespan).
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
    """Valida la inyección de sondas de liveness y readiness para Kubernetes."""

    def test_healthchecks_liveness_readiness(self) -> None:
        """Objetivo: Validar que la fábrica create_app inyectó correctamente las sondas de vida

        esperando HTTP 200 y la estructura estándar de observabilidad.
        """
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
        """Verifica que el Gateway permite deshabilitar las rutas si se requiere un router externo."""
        app = create_app(enable_healthcheck=False)
        with TestClient(app) as client:
            res_healthz = client.get("/healthz")
            assert res_healthz.status_code == 404
            res_readyz = client.get("/readyz")
            assert res_readyz.status_code == 404


class TestGatewayLifespan:
    """Valida la gestión de recursos de arranque (startup) y apagado limpio (shutdown)."""

    def test_lifespan_resource_cleanup_prevents_leaks(self) -> None:
        """Objetivo: Simular el ciclo completo de vida usando el contexto de TestClient,

        asegurando con mocks que las conexiones de base de datos se inicializan al arrancar
        y se cierran críticamente al apagar el servidor, evitando fugas de memoria y sockets.
        """
        mock_db = MagicMock(spec=DatabaseConnectionPool)
        mock_db.connect = AsyncMock()
        mock_db.disconnect = AsyncMock()
        mock_db.is_connected = True
        mock_db.active_connections = 10

        app = create_lifecycle_gateway(db_pool=mock_db)

        # Antes de iniciar la app, no se debe haber llamado a connect ni disconnect
        assert mock_db.connect.call_count == 0
        assert mock_db.disconnect.call_count == 0

        # Bloque de ciclo de vida con TestClient
        with TestClient(app) as client:
            # Al entrar, startup debió ser invocado
            assert mock_db.connect.call_count == 1
            assert mock_db.disconnect.call_count == 0

            # Consumir un endpoint dentro del ciclo activo
            res = client.get("/api/v1/system/status")
            assert res.status_code == 200
            assert res.json()["status"] == "operational"

        # Al salir del bloque with, shutdown debió ser ejecutado inmediatamente
        assert mock_db.connect.call_count == 1
        assert mock_db.disconnect.call_count == 1, (
            "CRÍTICO: El pool de conexiones de BD no fue cerrado al apagar el Gateway. "
            "Esto provoca fugas de sockets y memoria en pods de Kubernetes."
        )

    def test_lifespan_state_isolation_between_runs(self) -> None:
        """Verifica que múltiples ejecuciones del servidor crean ciclos de vida independientes y seguros."""
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
