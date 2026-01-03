"""
Health check endpoint for Agentum API.
"""
from datetime import datetime, timezone

from fastapi import APIRouter

from ..models import HealthResponse

router = APIRouter(tags=["health"])

API_VERSION = "1.0.0"


@router.get("/health", response_model=HealthResponse)
async def health_check() -> HealthResponse:
    """
    Check API health status.

    Returns basic health information including version and timestamp.
    """
    return HealthResponse(
        status="ok",
        version=API_VERSION,
        timestamp=datetime.now(timezone.utc),
    )

