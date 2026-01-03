"""
Authentication endpoints for Agentum API.
"""
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from ...db.database import get_db
from ...services.auth_service import auth_service
from ..models import TokenResponse

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/token", response_model=TokenResponse)
async def get_anonymous_token(
    db: AsyncSession = Depends(get_db),
) -> TokenResponse:
    """
    Get an anonymous JWT token.

    Creates a new anonymous user and returns a JWT token for authentication.
    The token is valid for 7 days.
    """
    user, token, expires_in = await auth_service.get_or_create_anonymous_user(db)

    return TokenResponse(
        access_token=token,
        token_type="bearer",
        user_id=user.id,
        expires_in=expires_in,
    )

