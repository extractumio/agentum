"""
FastAPI dependencies for Agentum API.

Provides dependency injection for authentication, database sessions, etc.
"""
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from ..services.auth_service import auth_service

# HTTP Bearer authentication scheme
bearer_scheme = HTTPBearer(auto_error=True)


async def get_current_user_id(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
) -> str:
    """
    Dependency that extracts and validates the JWT token.

    Returns the user_id from the token.

    Raises:
        HTTPException: If token is invalid or expired.
    """
    token = credentials.credentials
    user_id = auth_service.validate_token(token)

    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return user_id

