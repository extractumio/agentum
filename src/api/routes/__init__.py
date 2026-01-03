"""
API Routes package for Agentum.

Contains all FastAPI route handlers organized by domain.
"""
from .auth import router as auth_router
from .health import router as health_router
from .sessions import router as sessions_router

__all__ = [
    "auth_router",
    "health_router",
    "sessions_router",
]

