"""API module providing FastAPI integration, routes, and server factories."""

from common_framework.api.app import create_app, setup_exception_handlers

__all__ = [
    "create_app",
    "setup_exception_handlers",
]
